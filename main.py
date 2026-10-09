import json
import os
import re
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
import cv2
import easyocr
import mss
import numpy as np
import pygetwindow as gw
from rapidfuzz import fuzz, process

try:
    from pynput import keyboard

    HAS_PYNPUT = True
except ImportError:
    HAS_PYNPUT = False

GAME_WINDOW_TITLE = "Ragnarok"
CONFIG_FILE = "roi_config.json"

raw_database = {}
roi_presets = {}
lang_combobox = None
category_combobox = None
auto_scan_active = False

ocr_readers = {}


def get_ocr_reader(lang_label):
    if lang_label in ocr_readers:
        return ocr_readers[lang_label]

    print(f"Loading EasyOCR Engine for [{lang_label}]...")

    if "CN" in lang_label or "中文" in lang_label:
        lang_list = ["ch_sim", "en"]
    elif "ID" in lang_label or "Bahasa" in lang_label:
        lang_list = ["id", "en"]
    elif "EN" in lang_label or "English" in lang_label:
        lang_list = ["en"]
    else:
        lang_list = ["th", "en"]

    reader = easyocr.Reader(lang_list, gpu=False)
    ocr_readers[lang_label] = reader
    return reader


def load_config():
    global roi_presets
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                roi_presets = json.load(f)
        except Exception:
            roi_presets = {}


def save_config():
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(roi_presets, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving config: {e}")


def load_database():
    global raw_database
    try:
        with open("qa_database.json", "r", encoding="utf-8") as f:
            raw_database = json.load(f)
        return True
    except Exception as e:
        print(f"Database loading error: {e}")
        raw_database = {}
        return False


def get_game_window():
    windows = gw.getWindowsWithTitle(GAME_WINDOW_TITLE)
    return windows[0] if windows else None


def get_absolute_roi_for_category(category_name):
    win = get_game_window()
    if not win or category_name not in roi_presets:
        return None

    preset = roi_presets[category_name]
    abs_x = win.left + preset["rel_x"]
    abs_y = win.top + preset["rel_y"]

    return {
        "top": int(abs_y),
        "left": int(abs_x),
        "width": int(preset["w"]),
        "height": int(preset["h"]),
    }


class SnippingTool:

    def __init__(self, parent, current_category):
        self.parent = parent
        self.current_category = current_category
        self.snip_surface = tk.Toplevel(parent)
        self.snip_surface.attributes("-fullscreen", True)
        self.snip_surface.attributes("-alpha", 0.3)
        self.snip_surface.config(cursor="cross")

        self.canvas = tk.Canvas(self.snip_surface, cursor="cross", bg="grey")
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<ButtonPress-1>", self.on_button_press)
        self.canvas.bind("<B1-Motion>", self.on_move_press)
        self.canvas.bind("<ButtonRelease-1>", self.on_button_release)

        self.start_x = None
        self.start_y = None
        self.rect = None

    def on_button_press(self, event):
        self.start_x = event.x
        self.start_y = event.y
        self.rect = self.canvas.create_rectangle(
            self.start_x, self.start_y, 1, 1, outline="red", width=2
        )

    def on_move_press(self, event):
        cur_x, cur_y = (event.x, event.y)
        self.canvas.coords(self.rect, self.start_x, self.start_y, cur_x, cur_y)

    def on_button_release(self, event):
        end_x, end_y = (event.x, event.y)

        x1 = min(self.start_x, end_x)
        y1 = min(self.start_y, end_y)
        w = abs(end_x - self.start_x)
        h = abs(end_y - self.start_y)

        win = get_game_window()
        if w > 10 and h > 10 and win:
            rel_x = x1 - win.left
            rel_y = y1 - win.top

            roi_presets[self.current_category] = {
                "rel_x": rel_x,
                "rel_y": rel_y,
                "w": w,
                "h": h,
            }
            save_config()

            messagebox.showinfo(
                "สำเร็จ",
                f"บันทึกตำแหน่งกรอบสำหรับหมวด '{self.current_category}' เรียบร้อย!",
            )

        self.snip_surface.destroy()


class AnswerOverlay:

    def __init__(self):
        self.window = tk.Toplevel()
        self.window.title("เฉลย")
        self.window.geometry("440x180+50+50")
        self.window.attributes("-topmost", True)
        self.window.configure(bg="#222222")

        self.current_question_text = ""
        self.font_family = "Segoe UI"

        self.label_question = tk.Label(
            self.window,
            text="คำถาม: -",
            fg="#AAAAAA",
            bg="#222222",
            font=(self.font_family, 10),
            wraplength=420,
        )
        self.label_question.pack(pady=(5, 2))

        self.label_answer = tk.Label(
            self.window,
            text="รอสแกนคำถาม...",
            fg="#00FF00",
            bg="#222222",
            font=(self.font_family, 15, "bold"),
            wraplength=420,
        )
        self.label_answer.pack(pady=2)

        self.btn_copy = tk.Button(
            self.window,
            text="📋 คัดลอกคำถาม (Copy)",
            command=self.copy_to_clipboard,
            bg="#333333",
            fg="#00E5FF",
            activebackground="#444444",
            activeforeground="#00E5FF",
            font=(self.font_family, 9, "bold"),
            relief="groove",
            cursor="hand2",
            padx=10,
            pady=2,
        )
        self.btn_copy.pack(pady=(5, 5))

    def update_display(self, question_text, answer_text, raw_question=""):
        # ทำความสะอาดข้อความ ลบอักขระแปลกปลอมก่อนแสดงผล
        clean_ans = re.sub(
            r"[\uE000-\uF8FF\u2700-\u27BF\u2600-\u26FF✓✔✅]", "", answer_text
        ).strip()
        if not clean_ans:
            clean_ans = answer_text.strip()

        self.current_question_text = (
            raw_question if raw_question else question_text
        )
        self.label_question.config(text=f"คำถามที่พบ: {question_text}")
        self.label_answer.config(text=f"เฉลย: {clean_ans}")

    def copy_to_clipboard(self):
        if self.current_question_text:
            self.window.clipboard_clear()
            self.window.clipboard_append(self.current_question_text)
            self.btn_copy.config(text="✓ คัดลอกแล้ว!", fg="#00FF00")
            self.window.after(
                1500,
                lambda: self.btn_copy.config(
                    text="📋 คัดลอกคำถาม (Copy)", fg="#00E5FF"
                ),
            )


def perform_scan(overlay_window, silent=False):
    win = get_game_window()
    if not win:
        if not silent:
            messagebox.showwarning(
                "แจ้งเตือน", f"ไม่พบหน้าต่างเกม '{GAME_WINDOW_TITLE}'!"
            )
        return

    selected_lang = lang_combobox.get() if lang_combobox else "ไทย (TH)"
    selected_cat = (
        category_combobox.get() if category_combobox else "ทุกหมวดหมู่"
    )

    roi = get_absolute_roi_for_category(selected_cat)
    if not roi:
        if not silent:
            messagebox.showwarning(
                "แจ้งเตือน",
                f"ยังไม่ได้ตั้งค่ากรอบสำหรับหมวด '{selected_cat}'",
            )
        return

    lang_db = raw_database.get(selected_lang, {})
    if selected_cat == "ทุกหมวดหมู่" or selected_cat not in lang_db:
        active_qa = []
        for items in lang_db.values():
            active_qa.extend(items)
    else:
        active_qa = lang_db[selected_cat]

    active_questions = [item["question"] for item in active_qa]
    reader = get_ocr_reader(selected_lang)

    with mss.mss() as sct:
        sct_img = sct.grab(roi)
        img = np.array(sct_img)

        gray = cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
        _, thresh = cv2.threshold(
            gray, 180, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
        )

        results = reader.readtext(thresh, detail=0)
        captured_text = " ".join(results).strip()

        if captured_text and active_questions:
            match, score, index = process.extractOne(
                captured_text, active_questions, scorer=fuzz.token_sort_ratio
            )
            if score >= 50:
                answer = active_qa[index]["answer"]
                overlay_window.update_display(
                    f"{match} ({score:.0f}%)", f"{answer}", raw_question=match
                )
            else:
                overlay_window.update_display(
                    f"อ่านได้: {captured_text}",
                    "ไม่พบคำถามนี้ในหมวด/ภาษาที่เลือก",
                    raw_question=captured_text,
                )


def main():
    global lang_combobox, category_combobox, auto_scan_active

    load_config()
    load_database()

    root = tk.Tk()
    root.title("RO Auto Answer Helper (Multi-Lang)")
    root.geometry("360x410")
    root.attributes("-topmost", True)

    overlay = AnswerOverlay()

    label_lang = tk.Label(
        root, text="1. เลือกภาษาของเกม:", font=("Segoe UI", 10, "bold")
    )
    label_lang.pack(pady=(10, 2))

    languages = list(raw_database.keys()) if raw_database else ["ไทย (TH)"]
    lang_combobox = ttk.Combobox(
        root, values=languages, state="readonly", font=("Segoe UI", 10)
    )
    if languages:
        lang_combobox.current(0)
    lang_combobox.pack(fill="x", padx=20, pady=2)

    label_cat = tk.Label(
        root, text="2. เลือกหมวดกิจกรรม:", font=("Segoe UI", 10, "bold")
    )
    label_cat.pack(pady=(10, 2))

    category_combobox = ttk.Combobox(
        root, values=["ทุกหมวดหมู่"], state="readonly", font=("Segoe UI", 10)
    )
    category_combobox.pack(fill="x", padx=20, pady=2)

    def update_category_options(event=None):
        current_lang = lang_combobox.get()
        cats = ["ทุกหมวดหมู่"] + list(raw_database.get(current_lang, {}).keys())
        category_combobox["values"] = cats
        if cats:
            category_combobox.current(0)

    lang_combobox.bind("<<ComboboxSelected>>", update_category_options)
    update_category_options()

    lbl_status = tk.Label(
        root,
        text="สถานะ: พร้อมใช้งาน (กด F9 เพื่อสแกน)",
        fg="#555555",
        font=("Segoe UI", 9),
    )
    lbl_status.pack(pady=5)

    btn_set_roi = tk.Button(
        root,
        text="🎯 ตั้งค่ากรอบสแกน (ครั้งแรก)",
        command=lambda: SnippingTool(root, category_combobox.get()),
        bg="#e1e1e1",
        height=2,
    )
    btn_set_roi.pack(fill="x", padx=20, pady=4)

    btn_scan = tk.Button(
        root,
        text="⚡ สแกนคำถามทันที (หรือกด F9)",
        command=lambda: perform_scan(overlay),
        bg="#4CAF50",
        fg="white",
        font=("Segoe UI", 11, "bold"),
        height=2,
    )
    btn_scan.pack(fill="x", padx=20, pady=4)

    def toggle_auto_scan():
        global auto_scan_active
        auto_scan_active = not auto_scan_active
        if auto_scan_active:
            btn_auto.config(text="⏹️ หยุดสแกนอัตโนมัติ", bg="#f44336")
            lbl_status.config(text="สถานะ: กำลังสแกนอัตโนมัติทุก 2 วินาที...")

            def auto_loop():
                while auto_scan_active:
                    perform_scan(overlay, silent=True)
                    time.sleep(2)

            threading.Thread(target=auto_loop, daemon=True).start()
        else:
            btn_auto.config(
                text="🔄 เปิดสแกนอัตโนมัติ (Auto-Scan)", bg="#2196F3"
            )
            lbl_status.config(text="สถานะ: หยุดสแกนอัตโนมัติแล้ว")

    btn_auto = tk.Button(
        root,
        text="🔄 เปิดสแกนอัตโนมัติ (Auto-Scan)",
        command=toggle_auto_scan,
        bg="#2196F3",
        fg="white",
        font=("Segoe UI", 10, "bold"),
        height=2,
    )
    btn_auto.pack(fill="x", padx=20, pady=4)

    if HAS_PYNPUT:

        def on_press(key):
            try:
                if key == keyboard.Key.f9:
                    perform_scan(overlay, silent=True)
            except Exception:
                pass

        listener = keyboard.Listener(on_press=on_press)
        listener.start()

    root.mainloop()


if __name__ == "__main__":
    main()