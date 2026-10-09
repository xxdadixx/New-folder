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

# Playwright Web Scraper Engine
try:
    from playwright.sync_api import sync_playwright

    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

# Global Keyboard Hotkey
try:
    from pynput import keyboard

    HAS_PYNPUT = True
except ImportError:
    HAS_PYNPUT = False

# --- Configurations & Constants ---
GAME_WINDOW_TITLE = "Ragnarok"
CONFIG_FILE = "roi_config.json"
DATABASE_FILE = "qa_database.json"

SUPPORTED_LANGUAGES = [
    {"code": "th-TH", "name": "ไทย (TH)"},
    {"code": "en-US", "name": "English (EN)"},
    {"code": "zh-CN", "name": "中文 (CN)"},
    {"code": "id-ID", "name": "Bahasa Indonesia (ID)"},
]

CATEGORIES = [
    {"id": "lucky-rabbit", "name": "Hoppy Quiz"},
    {"id": "guild-banquet", "name": "งานเลี้ยงกิลด์ / Guild Banquet"},
    {"id": "scholar-exam", "name": "Scholar Exam + Sage Selection"},
    {"id": "moon-riddle", "name": "เช็กอินใต้แสงจันทร์ / Moon Riddle"},
]

# --- Global States ---
raw_database = {}
roi_presets = {}
ocr_readers = {}
auto_scan_active = False

# Liquid Glass UI Color Palette
GLASS_BG = "#13151f"
GLASS_CARD = "#1c1f2e"
GLASS_CARD_BORDER = "#2e344d"
TEXT_PRIMARY = "#ffffff"
TEXT_SECONDARY = "#8e9bb0"
ACCENT_CYAN = "#00e5ff"
ACCENT_GREEN = "#00e676"
ACCENT_RED = "#ff5252"
ACCENT_BLUE = "#2979ff"


# --- Live Log Window (Liquid Glass) ---
class LogWindow:

    def __init__(self, parent):
        self.window = tk.Toplevel(parent)
        self.window.title("📋 Live System Logs - Update Database")
        self.window.geometry("640x420+100+100")
        self.window.configure(bg=GLASS_BG)
        self.window.attributes("-topmost", True)
        self.window.attributes("-alpha", 0.95)

        card = tk.Frame(
            self.window,
            bg=GLASS_CARD,
            highlightbackground=GLASS_CARD_BORDER,
            highlightthickness=1,
        )
        card.pack(fill="both", expand=True, padx=12, pady=12)

        lbl_title = tk.Label(
            card,
            text="ระบบบันทึกการทำงาน (Scraper Logs)",
            fg=ACCENT_CYAN,
            bg=GLASS_CARD,
            font=("Segoe UI", 11, "bold"),
        )
        lbl_title.pack(anchor="w", padx=12, pady=(10, 5))

        self.text_area = tk.Text(
            card,
            bg="#0d0e15",
            fg="#d1d5db",
            font=("Consolas", 9),
            relief="flat",
            bd=0,
            wrap="word",
        )
        self.scrollbar = ttk.Scrollbar(card, command=self.text_area.yview)
        self.text_area.configure(yscrollcommand=self.scrollbar.set)

        self.scrollbar.pack(side="right", fill="y", padx=(0, 8), pady=8)
        self.text_area.pack(side="left", fill="both", expand=True, padx=(12, 0), pady=8)

        self.text_area.tag_config("INFO", foreground="#00e5ff")
        self.text_area.tag_config("SUCCESS", foreground="#00e676")
        self.text_area.tag_config("ERROR", foreground="#ff5252")
        self.text_area.tag_config("WARN", foreground="#ffb74d")
        self.text_area.tag_config("DEFAULT", foreground="#d1d5db")

    def write_log(self, message):
        self.window.after(0, self._append_text, message)

    def _append_text(self, message):
        self.text_area.config(state="normal")

        tag = "DEFAULT"
        if "✅" in message or "สำเร็จ" in message:
            tag = "SUCCESS"
        elif "❌" in message or "Error" in message or "เกิดข้อผิดพลาด" in message:
            tag = "ERROR"
        elif "⚠️" in message:
            tag = "WARN"
        elif "🌐" in message or "URL:" in message or "🚀" in message:
            tag = "INFO"

        self.text_area.insert(tk.END, message + "\n", tag)
        self.text_area.see(tk.END)
        self.text_area.config(state="disabled")


# --- Database Scraper Module ---
def fetch_multilingual_database(log_fn=None):
    def log(msg):
        if log_fn:
            log_fn(msg)
        print(msg)

    if not HAS_PLAYWRIGHT:
        log("❌ Error: ไม่พบไลบรารี Playwright กรุณาติดตั้ง playwright ก่อน")
        return False

    base_url = "https://roworlddb.com/sea/study/"
    all_db = {}

    log("==================================================")
    log("🚀 เริ่มต้นกระบวนการดึงข้อมูลเฉลยจาก roworlddb.com...")
    log("==================================================")

    try:
        with sync_playwright() as p:
            log("🌐 กำลังเปิด Chromium Browser (Headless Mode)...")
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            for lang in SUPPORTED_LANGUAGES:
                lang_code = lang["code"]
                lang_name = lang["name"]
                all_db[lang_name] = {}

                log(f"\n--------------------------------------------------")
                log(f"🌐 กำลังประมวลผลภาษา: {lang_name} [{lang_code}]")
                log(f"--------------------------------------------------")

                for cat in CATEGORIES:
                    cat_id = cat["id"]
                    cat_name = cat["name"]
                    event_url = f"{base_url}?lang={lang_code}#event={cat_id}&reveal=1"

                    log(f"  🔍 หมวดกิจกรรม: '{cat_name}'")
                    log(f"     URL: {event_url}")

                    page.goto(event_url, wait_until="networkidle")
                    page.wait_for_timeout(1500)

                    # สลับหมวดกิจกรรม
                    page.evaluate(
                        f"""
                        () => {{
                            const selects = Array.from(document.querySelectorAll('select'));
                            for (const s of selects) {{
                                const opt = Array.from(s.options).find(o => o.value === '{cat_id}' || o.value.includes('{cat_id}'));
                                if (opt) {{
                                    s.value = opt.value;
                                    s.dispatchEvent(new Event('change', {{ bubbles: true }}));
                                    break;
                                }}
                            }}
                        }}
                    """
                    )
                    page.wait_for_timeout(1000)

                    # บังคับกดปุ่ม "แสดงคำตอบทั้งหมด"
                    for attempt in range(5):
                        try:
                            cb = page.locator('input[type="checkbox"]')
                            if cb.count() > 0 and not cb.is_checked():
                                cb.click(force=True)
                                page.wait_for_timeout(1000)

                            body_text = page.inner_text("body")
                            if "???" not in body_text:
                                log("     🔓 เปิดแสดงเฉลยเรียบร้อยแล้ว!")
                                break
                            else:
                                page.evaluate(
                                    """
                                    () => {
                                        document.querySelectorAll('div').forEach(d => {
                                            if (d.innerText && d.innerText.includes('???')) {
                                                d.click();
                                            }
                                        });
                                    }
                                """
                                )
                                page.wait_for_timeout(800)
                        except Exception:
                            pass

                    # สกัดคำถามและเฉลย พร้อมคัดกรองปุ่ม UI ออก
                    qa_items = page.evaluate(
                        r"""
                    () => {
                        const results = [];
                        const text = document.body.innerText || '';
                        
                        const blocks = text.split(/(?=Q\s*\d+[\.\:\s\n])/gi);

                        // Blacklist สำหรับกรองข้อความ UI และปุ่มกด
                        const uiBlacklist = [
                            'CLICK TO HIDE', 'HIDE ANSWER', 'SHOW ANSWER', 'CLICK TO SHOW',
                            'CLICK', 'HIDE', 'SHOW', 'ANSWER', 'คลิกเพื่อ', 'ซ่อน', 'แสดงคำตอบ',
                            'QUESTION', 'SCORE', 'STUDY', 'กิจกรรม', 'REWARD', 'POINTS', '???'
                        ];

                        const isBlacklisted = (str) => {
                            if (!str) return True;
                            const upper = str.toUpperCase();
                            return uiBlacklist.some(b => upper.includes(b));
                        };

                        // รวบรวมองค์ประกอบเฉลยสีเขียว
                        const greenElems = Array.from(document.querySelectorAll('*')).filter(el => {
                            if (!el.innerText || el.innerText.trim().length === 0) return false;
                            const style = window.getComputedStyle(el);
                            const color = style.color || '';
                            const classStr = el.className || '';
                            
                            let isG = false;
                            if (typeof classStr === 'string' && (
                                classStr.includes('green') || classStr.includes('emerald') || 
                                classStr.includes('teal') || classStr.includes('success')
                            )) {
                                isG = true;
                            } else if (color.startsWith('rgb')) {
                                const rgb = color.match(/\d+/g);
                                if (rgb && rgb.length >= 3) {
                                    const r = parseInt(rgb[0]), g = parseInt(rgb[1]), b = parseInt(rgb[2]);
                                    if (g > 120 && g > r * 1.15 && g > b * 1.15) isG = true;
                                }
                            }
                            return isG;
                        });

                        const greenTexts = greenElems.map(el => {
                            return (el.innerText || el.textContent || '')
                                .replace(/[\uE000-\uF8FF\u2700-\u27BF\u2600-\u26FF✓✔✅]/g, '')
                                .trim();
                        }).filter(t => t.length > 0 && !/^Q\s*\d+/i.test(t) && !isBlacklisted(t));

                        blocks.forEach(block => {
                            const lines = block.split('\n').map(l => l.trim()).filter(l => l.length > 0);
                            if (lines.length < 2) return;

                            if (!/^Q\s*\d+/i.test(lines[0])) return;

                            let question = lines[0].replace(/^Q\s*\d+[\.\s\:]*/i, '').trim();
                            let startIdx = 1;
                            if (!question && lines.length > 1) {
                                question = lines[1];
                                startIdx = 2;
                            }

                            question = question.trim();
                            if (question.length < 2) return;

                            let answer = "";

                            // 1. หาคำตอบจากข้อความไฮไลต์สีเขียว (ที่ไม่ใช่ข้อความ UI)
                            for (const gt of greenTexts) {
                                if (gt && gt !== question && block.includes(gt) && !/^Q\s*\d+/i.test(gt) && !isBlacklisted(gt)) {
                                    answer = gt;
                                    break;
                                }
                            }

                            // 2. เช็คกรณีข้อสอบ จริง / เท็จ (O/X)
                            if (!answer) {
                                for (let i = startIdx; i < lines.length; i++) {
                                    const cleanL = lines[i].replace(/[\uE000-\uF8FF\u2700-\u27BF\u2600-\u26FF✓✔✅]/g, '').trim();
                                    if (['จริง', 'O', 'True', '正确', 'Benar'].includes(cleanL)) {
                                        answer = 'จริง / True (O)';
                                        break;
                                    } else if (['เท็จ', 'X', 'False', '錯誤', 'Salah'].includes(cleanL)) {
                                        answer = 'เท็จ / False (X)';
                                        break;
                                    }
                                }
                            }

                            // 3. สำรองกรณีตัวเลือกข้อความ
                            if (!answer) {
                                const candidates = lines.slice(startIdx).map(l => 
                                    l.replace(/[\uE000-\uF8FF\u2700-\u27BF\u2600-\u26FF✓✔✅]/g, '').trim()
                                ).filter(l => 
                                    l.length > 0 && 
                                    l !== question && 
                                    !/^Q\s*\d+/i.test(l) &&
                                    !isBlacklisted(l)
                                );

                                const textCandidates = candidates.filter(c => !/^\d+$/.test(c));
                                if (textCandidates.length > 0) {
                                    answer = textCandidates[0];
                                } else if (candidates.length > 0) {
                                    answer = candidates[candidates.length - 1];
                                }
                            }

                            if (question && answer && !isBlacklisted(answer)) {
                                results.push({ question, answer });
                            }
                        });

                        const unique = [];
                        const seen = new Set();
                        for (const item of results) {
                            if (!seen.has(item.question)) {
                                seen.add(item.question);
                                unique.push(item);
                            }
                        }
                        return unique;
                    }
                    """
                    )

                    all_db[lang_name][cat_name] = qa_items
                    log(f"     ✅ สำเร็จ! ดึงมาได้ {len(qa_items)} รายการ")

            browser.close()

        log("\n💾 กำลังบันทึกข้อมูลลงไฟล์ 'qa_database.json'...")
        with open(DATABASE_FILE, "w", encoding="utf-8") as f:
            json.dump(all_db, f, ensure_ascii=False, indent=2)

        log("==================================================")
        log("✅ อัปเดตฐานข้อมูลสำเร็จเรียบร้อยแล้ว!")
        log("==================================================")
        return True

    except Exception as e:
        log(f"\n❌ เกิดข้อผิดพลาดระหว่างดึงข้อมูล: {e}")
        return False


# --- EasyOCR Manager ---
def get_ocr_reader(lang_label):
    if lang_label in ocr_readers:
        return ocr_readers[lang_label]

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


# --- Helper Methods ---
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
    if os.path.exists(DATABASE_FILE):
        try:
            with open(DATABASE_FILE, "r", encoding="utf-8") as f:
                raw_database = json.load(f)
            return True
        except Exception:
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


# --- Snipping Tool Overlay ---
class SnippingTool:

    def __init__(self, parent, current_category):
        self.parent = parent
        self.current_category = current_category
        self.snip_surface = tk.Toplevel(parent)
        self.snip_surface.attributes("-fullscreen", True)
        self.snip_surface.attributes("-alpha", 0.25)
        self.snip_surface.config(cursor="cross")

        self.canvas = tk.Canvas(
            self.snip_surface,
            cursor="cross",
            bg="#0d0e15",
            highlightthickness=0,
        )
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
            self.start_x, self.start_y, 1, 1, outline=ACCENT_CYAN, width=2
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


# --- Liquid Glass Answer Overlay GUI ---
class AnswerOverlay:

    def __init__(self):
        self.window = tk.Toplevel()
        self.window.title("เฉลยคำถาม")
        self.window.geometry("440x180+60+60")
        self.window.attributes("-topmost", True)
        self.window.attributes("-alpha", 0.92)
        self.window.configure(bg=GLASS_BG)

        self.current_question_text = ""
        self.font_family = "Segoe UI"

        self.card_frame = tk.Frame(
            self.window,
            bg=GLASS_CARD,
            highlightbackground=GLASS_CARD_BORDER,
            highlightthickness=1,
        )
        self.card_frame.pack(fill="both", expand=True, padx=12, pady=12)

        self.label_question = tk.Label(
            self.card_frame,
            text="คำถาม: -",
            fg=TEXT_SECONDARY,
            bg=GLASS_CARD,
            font=(self.font_family, 10),
            wraplength=400,
            justify="center",
        )
        self.label_question.pack(pady=(12, 4), padx=10)

        self.label_answer = tk.Label(
            self.card_frame,
            text="รอสแกนคำถาม...",
            fg=ACCENT_GREEN,
            bg=GLASS_CARD,
            font=(self.font_family, 15, "bold"),
            wraplength=400,
            justify="center",
        )
        self.label_answer.pack(pady=4, padx=10)

        self.btn_copy = tk.Button(
            self.card_frame,
            text="📋 คัดลอกคำถาม (Copy)",
            command=self.copy_to_clipboard,
            bg="#252a3e",
            fg=ACCENT_CYAN,
            activebackground="#2e354f",
            activeforeground=ACCENT_CYAN,
            font=(self.font_family, 9, "bold"),
            relief="flat",
            bd=0,
            cursor="hand2",
            padx=12,
            pady=4,
        )
        self.btn_copy.pack(pady=(6, 12))

    def update_display(self, question_text, answer_text, raw_question=""):
        clean_ans = re.sub(
            r"[\uE000-\uF8FF\u2700-\u27BF\u2600-\u26FF✓✔✅]", "", answer_text
        ).strip()
        if not clean_ans:
            clean_ans = answer_text.strip()

        self.current_question_text = raw_question if raw_question else question_text
        self.label_question.config(text=f"คำถามที่พบ: {question_text}")
        self.label_answer.config(text=f"เฉลย: {clean_ans}")

    def copy_to_clipboard(self):
        if self.current_question_text:
            self.window.clipboard_clear()
            self.window.clipboard_append(self.current_question_text)
            self.btn_copy.config(text="✓ คัดลอกแล้ว!", fg=ACCENT_GREEN)
            self.window.after(
                1500,
                lambda: self.btn_copy.config(
                    text="📋 คัดลอกคำถาม (Copy)", fg=ACCENT_CYAN
                ),
            )


# --- Core OCR Processor ---
def perform_scan(overlay_window, lang_combobox, category_combobox, silent=False):
    win = get_game_window()
    if not win:
        if not silent:
            messagebox.showwarning("แจ้งเตือน", f"ไม่พบหน้าต่างเกม '{GAME_WINDOW_TITLE}'!")
        return

    selected_lang = lang_combobox.get() if lang_combobox else "ไทย (TH)"
    selected_cat = category_combobox.get() if category_combobox else "ทุกหมวดหมู่"

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


# --- Main Application Window ---
def main():
    global auto_scan_active

    load_config()
    load_database()

    root = tk.Tk()
    root.title("RO Auto Answer Helper")
    root.geometry("380x520")
    root.configure(bg=GLASS_BG)
    root.attributes("-topmost", True)

    font_main = ("Segoe UI", 10)
    font_bold = ("Segoe UI", 10, "bold")

    overlay = AnswerOverlay()

    main_card = tk.Frame(
        root,
        bg=GLASS_CARD,
        highlightbackground=GLASS_CARD_BORDER,
        highlightthickness=1,
    )
    main_card.pack(fill="both", expand=True, padx=15, pady=15)

    lbl_title = tk.Label(
        main_card,
        text="RO Trivia Helper",
        fg=ACCENT_CYAN,
        bg=GLASS_CARD,
        font=("Segoe UI", 14, "bold"),
    )
    lbl_title.pack(pady=(15, 10))

    # Dropdown 1: Language
    lbl_lang = tk.Label(
        main_card,
        text="1. เลือกภาษาของเกม:",
        fg=TEXT_PRIMARY,
        bg=GLASS_CARD,
        font=font_bold,
        anchor="w",
    )
    lbl_lang.pack(fill="x", padx=20, pady=(5, 2))

    languages = list(raw_database.keys()) if raw_database else ["ไทย (TH)"]
    lang_combobox = ttk.Combobox(
        main_card, values=languages, state="readonly", font=font_main
    )
    if languages:
        lang_combobox.current(0)
    lang_combobox.pack(fill="x", padx=20, pady=2)

    # Dropdown 2: Category
    lbl_cat = tk.Label(
        main_card,
        text="2. เลือกหมวดกิจกรรม:",
        fg=TEXT_PRIMARY,
        bg=GLASS_CARD,
        font=font_bold,
        anchor="w",
    )
    lbl_cat.pack(fill="x", padx=20, pady=(10, 2))

    category_combobox = ttk.Combobox(
        main_card, values=["ทุกหมวดหมู่"], state="readonly", font=font_main
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
        main_card,
        text="สถานะ: พร้อมใช้งาน (กด F9 เพื่อสแกน)",
        fg=TEXT_SECONDARY,
        bg=GLASS_CARD,
        font=("Segoe UI", 9),
    )
    lbl_status.pack(pady=8)

    # Action Buttons
    btn_set_roi = tk.Button(
        main_card,
        text="🎯 ตั้งค่ากรอบสแกน (ครั้งแรก)",
        command=lambda: SnippingTool(root, category_combobox.get()),
        bg="#282d42",
        fg=TEXT_PRIMARY,
        activebackground="#333a54",
        activeforeground=TEXT_PRIMARY,
        font=font_main,
        relief="flat",
        bd=0,
        height=2,
        cursor="hand2",
    )
    btn_set_roi.pack(fill="x", padx=20, pady=4)

    btn_scan = tk.Button(
        main_card,
        text="⚡ สแกนคำถามทันที (หรือกด F9)",
        command=lambda: perform_scan(overlay, lang_combobox, category_combobox),
        bg=ACCENT_GREEN,
        fg="#0d0e15",
        activebackground="#00c853",
        activeforeground="#0d0e15",
        font=font_bold,
        relief="flat",
        bd=0,
        height=2,
        cursor="hand2",
    )
    btn_scan.pack(fill="x", padx=20, pady=4)

    def toggle_auto_scan():
        global auto_scan_active
        auto_scan_active = not auto_scan_active
        if auto_scan_active:
            btn_auto.config(text="⏹️ หยุดสแกนอัตโนมัติ", bg=ACCENT_RED)
            lbl_status.config(text="สถานะ: กำลังสแกนอัตโนมัติทุก 2 วินาที...")

            def auto_loop():
                while auto_scan_active:
                    perform_scan(overlay, lang_combobox, category_combobox, silent=True)
                    time.sleep(2)

            threading.Thread(target=auto_loop, daemon=True).start()
        else:
            btn_auto.config(text="🔄 เปิดสแกนอัตโนมัติ (Auto-Scan)", bg=ACCENT_BLUE)
            lbl_status.config(text="สถานะ: หยุดสแกนอัตโนมัติแล้ว")

    btn_auto = tk.Button(
        main_card,
        text="🔄 เปิดสแกนอัตโนมัติ (Auto-Scan)",
        command=toggle_auto_scan,
        bg=ACCENT_BLUE,
        fg=TEXT_PRIMARY,
        activebackground="#1565c0",
        activeforeground=TEXT_PRIMARY,
        font=font_bold,
        relief="flat",
        bd=0,
        height=2,
        cursor="hand2",
    )
    btn_auto.pack(fill="x", padx=20, pady=4)

    # Update DB Button with Live Log Window
    def update_db_async():
        log_win = LogWindow(root)
        btn_update.config(state="disabled", text="⏳ กำลังดึงข้อมูล...")
        lbl_status.config(text="สถานะ: กำลังอัปเดตเฉลยข้อสอบ...")

        def run_update():
            success = fetch_multilingual_database(log_fn=log_win.write_log)
            load_database()

            root.after(0, update_category_options)

            if success:
                lbl_status.config(text="สถานะ: อัปเดตเฉลยเรียบร้อยแล้ว!")
            else:
                lbl_status.config(text="สถานะ: เกิดข้อผิดพลาดขณะอัปเดต")

            btn_update.config(state="normal", text="🌐 ดึงเฉลยจากเว็บเพิ่ม (Update DB)")

        threading.Thread(target=run_update, daemon=True).start()

    btn_update = tk.Button(
        main_card,
        text="🌐 ดึงเฉลยจากเว็บเพิ่ม (Update DB)",
        command=update_db_async,
        bg="#252a3e",
        fg=ACCENT_CYAN,
        activebackground="#2e354f",
        activeforeground=ACCENT_CYAN,
        font=("Segoe UI", 9, "bold"),
        relief="flat",
        bd=0,
        height=2,
        cursor="hand2",
    )
    btn_update.pack(fill="x", padx=20, pady=(10, 15))

    # Global Hotkey Listener
    if HAS_PYNPUT:

        def on_press(key):
            try:
                if key == keyboard.Key.f9:
                    perform_scan(overlay, lang_combobox, category_combobox, silent=True)
            except Exception:
                pass

        listener = keyboard.Listener(on_press=on_press)
        listener.start()

    root.mainloop()


if __name__ == "__main__":
    main()
