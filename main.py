import json
import os
import queue
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
DATABASE_FILE = "qa_database.json"


class OCRManager:
    """Lazy loader and memory-efficient cache for EasyOCR instances."""

    def __init__(self):
        self._readers = {}

    def get_reader(self, lang_label: str) -> easyocr.Reader:
        if lang_label in self._readers:
            return self._readers[lang_label]

        if "CN" in lang_label or "中文" in lang_label:
            lang_list = ["ch_sim", "en"]
        elif "ID" in lang_label or "Bahasa" in lang_label:
            lang_list = ["id", "en"]
        elif "EN" in lang_label or "English" in lang_label:
            lang_list = ["en"]
        else:
            lang_list = ["th", "en"]

        reader = easyocr.Reader(lang_list, gpu=False)
        self._readers[lang_label] = reader
        return reader


class SnippingTool:
    """Interactive screen region selector with relative coordinate mapping."""

    def __init__(self, parent: tk.Tk, current_category: str, on_save_callback):
        self.parent = parent
        self.current_category = current_category
        self.on_save_callback = on_save_callback

        self.snip_surface = tk.Toplevel(parent)
        self.snip_surface.attributes("-fullscreen", True)
        self.snip_surface.attributes("-alpha", 0.3)
        self.snip_surface.attributes("-topmost", True)
        self.snip_surface.config(cursor="cross")

        self.canvas = tk.Canvas(self.snip_surface, cursor="cross", bg="grey")
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<ButtonPress-1>", self.on_button_press)
        self.canvas.bind("<B1-Motion>", self.on_move_press)
        self.canvas.bind("<ButtonRelease-1>", self.on_button_release)

        self.start_x = 0
        self.start_y = 0
        self.rect = None

    def on_button_press(self, event):
        self.start_x = event.x
        self.start_y = event.y
        self.rect = self.canvas.create_rectangle(
            self.start_x, self.start_y, 1, 1, outline="red", width=2
        )

    def on_move_press(self, event):
        cur_x, cur_y = event.x, event.y
        self.canvas.coords(self.rect, self.start_x, self.start_y, cur_x, cur_y)

    def on_button_release(self, event):
        end_x, end_y = event.x, event.y
        x1 = min(self.start_x, end_x)
        y1 = min(self.start_y, end_y)
        w = abs(end_x - self.start_x)
        h = abs(end_y - self.start_y)

        windows = gw.getWindowsWithTitle(GAME_WINDOW_TITLE)
        win = windows[0] if windows else None

        if w > 10 and h > 10 and win:
            if win.isMinimized:
                messagebox.showwarning("Warning", f"Game window '{GAME_WINDOW_TITLE}' is currently minimized!")
                self.snip_surface.destroy()
                return

            rel_x = x1 - win.left
            rel_y = y1 - win.top
            self.on_save_callback(self.current_category, rel_x, rel_y, w, h)
            messagebox.showinfo(
                "Success",
                f"Saved bounding box ROI for category '{self.current_category}'!",
            )
        elif not win:
            messagebox.showerror("Error", f"Could not locate game window titled '{GAME_WINDOW_TITLE}'!")

        self.snip_surface.destroy()


class AnswerOverlay:
    """Thread-safe UI overlay displaying OCR question and matched answer."""

    def __init__(self, parent: tk.Tk):
        self.window = tk.Toplevel(parent)
        self.window.title("RO Answer")
        self.window.geometry("440x180+50+50")
        self.window.attributes("-topmost", True)
        self.window.configure(bg="#222222")

        self.current_question_text = ""
        font_family = "Segoe UI"

        self.label_question = tk.Label(
            self.window,
            text="Question: -",
            fg="#AAAAAA",
            bg="#222222",
            font=(font_family, 10),
            wraplength=420,
        )
        self.label_question.pack(pady=(5, 2))

        self.label_answer = tk.Label(
            self.window,
            text="Awaiting scan...",
            fg="#00FF00",
            bg="#222222",
            font=(font_family, 15, "bold"),
            wraplength=420,
        )
        self.label_answer.pack(pady=2)

        self.btn_copy = tk.Button(
            self.window,
            text="📋 Copy Question",
            command=self.copy_to_clipboard,
            bg="#333333",
            fg="#00E5FF",
            activebackground="#444444",
            activeforeground="#00E5FF",
            font=(font_family, 9, "bold"),
            relief="groove",
            cursor="hand2",
            padx=10,
            pady=2,
        )
        self.btn_copy.pack(pady=(5, 5))

    def update_display(self, question_text: str, answer_text: str, raw_question: str = ""):
        clean_ans = re.sub(
            r"[\uE000-\uF8FF\u2700-\u27BF\u2600-\u26FF✓✔✅]", "", answer_text
        ).strip() or answer_text.strip()

        self.current_question_text = raw_question if raw_question else question_text
        self.label_question.config(text=f"Detected Question: {question_text}")
        self.label_answer.config(text=f"Answer: {clean_ans}")

    def copy_to_clipboard(self):
        if self.current_question_text:
            self.window.clipboard_clear()
            self.window.clipboard_append(self.current_question_text)
            self.btn_copy.config(text="✓ Copied!", fg="#00FF00")
            self.window.after(
                1500,
                lambda: self.btn_copy.config(
                    text="📋 Copy Question", fg="#00E5FF"
                ),
            )


class ROHelperApp:
    """Main application orchestrator ensuring thread safety and clean state management."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("RO Auto Answer Helper (Multi-Lang)")
        self.root.geometry("360x410")
        self.root.attributes("-topmost", True)

        self.raw_database = {}
        self.roi_presets = {}
        self.auto_scan_active = False
        self.scan_queue = queue.Queue()
        self.ocr_manager = OCRManager()

        self.load_config()
        self.load_database()

        self.overlay = AnswerOverlay(self.root)
        self.build_ui()

        # Thread-safe queue monitor loop on the Tkinter main thread
        self.root.after(100, self.process_ui_queue)

        if HAS_PYNPUT:
            self.start_keyboard_listener()

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    self.roi_presets = json.load(f)
            except Exception as e:
                print(f"[Error] Failed to load config: {e}")
                self.roi_presets = {}

    def save_config(self):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.roi_presets, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Error] Failed to save config: {e}")

    def save_roi_preset(self, category: str, rel_x: int, rel_y: int, w: int, h: int):
        self.roi_presets[category] = {
            "rel_x": rel_x,
            "rel_y": rel_y,
            "w": w,
            "h": h,
        }
        self.save_config()

    def load_database(self) -> bool:
        if os.path.exists(DATABASE_FILE):
            try:
                with open(DATABASE_FILE, "r", encoding="utf-8") as f:
                    self.raw_database = json.load(f)
                return True
            except Exception as e:
                print(f"[Error] Database loading error: {e}")
        self.raw_database = {}
        return False

    def build_ui(self):
        label_lang = tk.Label(
            self.root, text="1. Select Game Language:", font=("Segoe UI", 10, "bold")
        )
        label_lang.pack(pady=(10, 2))

        languages = list(self.raw_database.keys()) if self.raw_database else ["ไทย (TH)"]
        self.lang_combobox = ttk.Combobox(
            self.root, values=languages, state="readonly", font=("Segoe UI", 10)
        )
        if languages:
            self.lang_combobox.current(0)
        self.lang_combobox.pack(fill="x", padx=20, pady=2)

        label_cat = tk.Label(
            self.root, text="2. Select Event Category:", font=("Segoe UI", 10, "bold")
        )
        label_cat.pack(pady=(10, 2))

        self.category_combobox = ttk.Combobox(
            self.root, values=["ทุกหมวดหมู่"], state="readonly", font=("Segoe UI", 10)
        )
        self.category_combobox.pack(fill="x", padx=20, pady=2)

        self.lang_combobox.bind("<<ComboboxSelected>>", self.update_category_options)
        self.update_category_options()

        self.lbl_status = tk.Label(
            self.root,
            text="Status: Ready (Press F9 to scan)",
            fg="#555555",
            font=("Segoe UI", 9),
        )
        self.lbl_status.pack(pady=5)

        self.btn_set_roi = tk.Button(
            self.root,
            text="🎯 Set ROI Bounding Box",
            command=lambda: SnippingTool(self.root, self.category_combobox.get(), self.save_roi_preset),
            bg="#e1e1e1",
            height=2,
        )
        self.btn_set_roi.pack(fill="x", padx=20, pady=4)

        self.btn_scan = tk.Button(
            self.root,
            text="⚡ Scan Now (or press F9)",
            command=lambda: self.trigger_scan(silent=False),
            bg="#4CAF50",
            fg="white",
            font=("Segoe UI", 11, "bold"),
            height=2,
        )
        self.btn_scan.pack(fill="x", padx=20, pady=4)

        self.btn_auto = tk.Button(
            self.root,
            text="🔄 Toggle Auto-Scan",
            command=self.toggle_auto_scan,
            bg="#2196F3",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            height=2,
        )
        self.btn_auto.pack(fill="x", padx=20, pady=4)

    def update_category_options(self, event=None):
        current_lang = self.lang_combobox.get()
        cats = ["ทุกหมวดหมู่"] + list(self.raw_database.get(current_lang, {}).keys())
        self.category_combobox["values"] = cats
        if cats:
            self.category_combobox.current(0)

    def get_game_window(self):
        windows = gw.getWindowsWithTitle(GAME_WINDOW_TITLE)
        if windows and not windows[0].isMinimized and windows[0].visible:
            return windows[0]
        return None

    def get_absolute_roi_for_category(self, category_name: str):
        win = self.get_game_window()
        if not win or category_name not in self.roi_presets:
            return None

        preset = self.roi_presets[category_name]
        abs_x = win.left + preset["rel_x"]
        abs_y = win.top + preset["rel_y"]

        return {
            "top": int(abs_y),
            "left": int(abs_x),
            "width": int(preset["w"]),
            "height": int(preset["h"]),
        }

    def trigger_scan(self, silent=False):
        """Worker dispatch method safely offloading execution to a background thread."""
        threading.Thread(target=self._execute_scan, args=(silent,), daemon=True).start()

    def _execute_scan(self, silent: bool):
        selected_lang = self.lang_combobox.get() if self.lang_combobox else "ไทย (TH)"
        selected_cat = self.category_combobox.get() if self.category_combobox else "ทุกหมวดหมู่"

        win = self.get_game_window()
        if not win:
            if not silent:
                self.scan_queue.put(("warning", f"Game window '{GAME_WINDOW_TITLE}' not found or minimized!"))
            return

        roi = self.get_absolute_roi_for_category(selected_cat)
        if not roi:
            if not silent:
                self.scan_queue.put(("warning", f"Bounding box ROI not configured for category '{selected_cat}'"))
            return

        lang_db = self.raw_database.get(selected_lang, {})
        if selected_cat == "ทุกหมวดหมู่" or selected_cat not in lang_db:
            active_qa = []
            for items in lang_db.values():
                if isinstance(items, list):
                    active_qa.extend(items)
        else:
            active_qa = lang_db.get(selected_cat, [])

        if not active_qa:
            if not silent:
                self.scan_queue.put(("warning", "No Q&A records found in database for selected scope."))
            return

        active_questions = [item["question"] for item in active_qa if isinstance(item, dict) and "question" in item]
        if not active_questions:
            return

        try:
            reader = self.ocr_manager.get_reader(selected_lang)
            with mss.mss() as sct:
                sct_img = sct.grab(roi)
                img = np.array(sct_img)

                gray = cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
                _, thresh = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

                results = reader.readtext(thresh, detail=0)
                captured_text = " ".join(results).strip()

                if captured_text:
                    match_res = process.extractOne(
                        captured_text, active_questions, scorer=fuzz.token_sort_ratio
                    )
                    if match_res:
                        match, score, index = match_res[0], match_res[1], match_res[2]
                        if score >= 50:
                            answer = active_qa[index].get("answer", "")
                            self.scan_queue.put(("display", (f"{match} ({score:.0f}%)", answer, match)))
                        else:
                            self.scan_queue.put(("display", (f"Scanned: {captured_text}", "No matching question found.", captured_text)))
                elif not silent:
                    self.scan_queue.put(("display", ("OCR Failed to read text", "Try adjusting ROI or clarity.", "")))

        except Exception as e:
            print(f"[Error] OCR scanning exception: {e}")
            if not silent:
                self.scan_queue.put(("warning", f"Scan exception: {str(e)}"))

    def process_ui_queue(self):
        """Processes enqueued background updates safely on the Tkinter main thread."""
        while not self.scan_queue.empty():
            try:
                msg_type, payload = self.scan_queue.get_nowait()
                if msg_type == "display":
                    q_text, a_text, raw_q = payload
                    self.overlay.update_display(q_text, a_text, raw_q)
                elif msg_type == "warning":
                    messagebox.showwarning("Notice", payload)
            except queue.Empty:
                break
        self.root.after(100, self.process_ui_queue)

    def toggle_auto_scan(self):
        self.auto_scan_active = not self.auto_scan_active
        if self.auto_scan_active:
            self.btn_auto.config(text="⏹️ Stop Auto-Scan", bg="#f44336")
            self.lbl_status.config(text="Status: Auto-scanning every 2s...")

            def auto_loop():
                while self.auto_scan_active:
                    self._execute_scan(silent=True)
                    time.sleep(2)

            threading.Thread(target=auto_loop, daemon=True).start()
        else:
            self.btn_auto.config(text="🔄 Toggle Auto-Scan", bg="#2196F3")
            self.lbl_status.config(text="Status: Auto-scan stopped.")

    def start_keyboard_listener(self):
        def on_press(key):
            try:
                if key == keyboard.Key.f9:
                    self.trigger_scan(silent=True)
            except Exception:
                pass

        listener = keyboard.Listener(on_press=on_press)
        listener.daemon = True
        listener.start()


def main():
    root = tk.Tk()
    app = ROHelperApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()