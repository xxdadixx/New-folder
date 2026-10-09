import gc
import json
import os
import queue
import re
import tempfile
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
from typing import Dict, List, Tuple, Callable, Optional
from datetime import datetime, timedelta

# Pillow Image Processing Engine
try:
    from PIL import Image, ImageTk

    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# Global Keyboard Hotkey
try:
    from pynput import keyboard

    HAS_PYNPUT = True
except ImportError:
    HAS_PYNPUT = False

# Import database scraper module
from update_db import fetch_multilingual_database, is_invalid_answer

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


# --- Live Scraper Log Window ---
class LogWindow:
    """Live system log window rendering real-time web scraping progress and continuous header telemetry."""

    def __init__(self, parent: tk.Tk):
        self.window = tk.Toplevel(parent)
        self.window.title("📋 Live System Logs - Update & Audit")
        self.window.geometry("680x440+100+100")
        self.window.configure(bg="#13151f")
        self.window.attributes("-topmost", True)

        self.start_time = None
        self.is_running = False
        self.remaining_tasks = 16
        self.total_tasks = 16
        self.eta_seconds = 0
        self.timer_job = None

        card = tk.Frame(
            self.window,
            bg="#1c1f2e",
            highlightbackground="#2e344d",
            highlightthickness=1,
        )
        card.pack(fill="both", expand=True, padx=12, pady=12)

        header_frame = tk.Frame(card, bg="#1c1f2e")
        header_frame.pack(fill="x", padx=12, pady=(10, 5))

        lbl_title = tk.Label(
            header_frame,
            text="ระบบบันทึกการทำงาน (System Logs)",
            fg="#00e5ff",
            bg="#1c1f2e",
            font=("Segoe UI", 11, "bold"),
        )
        lbl_title.pack(side="left")

        self.lbl_telemetry = tk.Label(
            header_frame,
            text="  |  ⏱️ Running: 00m 00s  |  ⏳ Est. Finish: --:--",
            fg="#a0aec0",
            bg="#1c1f2e",
            font=("Segoe UI", 10, "bold"),
        )
        self.lbl_telemetry.pack(side="left", padx=(8, 0))

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

    def start_timer(self, total_tasks: int = 16):
        """Starts the continuous 1-second GUI ticker."""
        self.start_time = time.time()
        self.is_running = True
        self.total_tasks = total_tasks
        self.remaining_tasks = total_tasks
        self.eta_seconds = 0
        self._tick_timer()

    def stop_timer(self):
        """Stops the GUI clock ticker when operations finish."""
        self.is_running = False
        if self.timer_job:
            self.window.after_cancel(self.timer_job)
            self.timer_job = None

    def _tick_timer(self):
        """Ticks continuously every 1 second directly on the Tkinter main thread."""
        if not self.window.winfo_exists() or not self.is_running:
            return

        elapsed = int(time.time() - self.start_time)
        mins, secs = divmod(elapsed, 60)
        hrs, mins = divmod(mins, 60)
        running_str = (
            f"{hrs:02d}h {mins:02d}m {secs:02d}s"
            if hrs > 0
            else f"{mins:02d}m {secs:02d}s"
        )

        if self.remaining_tasks > 0:
            if self.eta_seconds > 0:
                finish_dt = datetime.now() + timedelta(seconds=self.eta_seconds)
                clock_str = finish_dt.strftime("%I:%M:%S %p")
            else:
                clock_str = "--:--"
            display_text = f"  |  ⏱️ {running_str}  |  ⏳ Est. Finish: {clock_str} ({self.remaining_tasks} left)"
        else:
            display_text = f"  |  ⏱️ Total: {running_str}  |  ✅ Complete"

        self.lbl_telemetry.config(text=display_text)
        self.timer_job = self.window.after(1000, self._tick_timer)

    def write_log(self, message: str):
        self.window.after(0, self._append_text, message)

    def update_telemetry(self, data: dict):
        """Synchronizes backend metadata without interrupting the 1-second GUI ticker loop."""
        if not self.window.winfo_exists():
            return

        def _apply():
            self.remaining_tasks = data.get("remaining_tasks", self.remaining_tasks)
            self.total_tasks = data.get("total_tasks", self.total_tasks)
            completed = data.get("completed_tasks", 0)
            if completed > 0 and self.start_time:
                elapsed = time.time() - self.start_time
                avg_sec = elapsed / completed
                self.eta_seconds = avg_sec * self.remaining_tasks

        self.window.after(0, _apply)

    def _append_text(self, message: str):
        if not self.window.winfo_exists():
            return
        self.text_area.config(state="normal")
        tag = "DEFAULT"
        if "✅" in message or "สำเร็จ" in message or "Healed" in message:
            tag = "SUCCESS"
        elif "❌" in message or "Error" in message or "เกิดข้อผิดพลาด" in message:
            tag = "ERROR"
        elif "⚠️" in message:
            tag = "WARN"
        elif "🌐" in message or "URL:" in message or "🚀" in message or "🔍" in message:
            tag = "INFO"

        self.text_area.insert(tk.END, message + "\n", tag)
        self.text_area.see(tk.END)
        self.text_area.config(state="disabled")


# --- Bounded OCR Manager ---
class OCRManager:
    """Bounded, memory-aware loader and cache for EasyOCR instances."""

    def __init__(self, max_cached_readers: int = 2):
        self._readers = {}
        self.max_cached_readers = max_cached_readers

    def get_reader(self, lang_label: str) -> easyocr.Reader:
        if lang_label in self._readers:
            reader = self._readers.pop(lang_label)
            self._readers[lang_label] = reader
            return reader

        if len(self._readers) >= self.max_cached_readers:
            oldest_key = next(iter(self._readers))
            del self._readers[oldest_key]
            gc.collect()

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


# --- Interactive Snipping Tool Overlay ---
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
                messagebox.showwarning(
                    "Warning",
                    f"Game window '{GAME_WINDOW_TITLE}' is currently minimized!",
                )
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
            messagebox.showerror(
                "Error", f"Could not locate game window titled '{GAME_WINDOW_TITLE}'!"
            )

        self.snip_surface.destroy()


# --- Liquid Glass Answer Overlay GUI ---
class AnswerOverlay:
    """Thread-safe UI overlay displaying OCR question, matched answer, and live website proof screenshot."""

    def __init__(self, parent: tk.Tk, initial_geometry: str = None):
        self.parent = parent
        self.window = tk.Toplevel(parent)
        self.window.title("RO Answer Verification")

        if initial_geometry:
            try:
                self.window.geometry(initial_geometry)
            except Exception:
                self.window.geometry("460x380+50+50")
        else:
            self.window.geometry("460x380+50+50")

        self.window.attributes("-topmost", True)
        self.window.configure(bg="#13151f")
        self.window.protocol("WM_DELETE_WINDOW", self.hide_window)

        self.current_question_text = ""
        self._current_photo = None
        font_family = "Segoe UI"

        card_frame = tk.Frame(
            self.window,
            bg="#1c1f2e",
            highlightbackground="#2e344d",
            highlightthickness=1,
        )
        card_frame.pack(fill="both", expand=True, padx=12, pady=12)

        self.label_question = tk.Label(
            card_frame,
            text="Question: -",
            fg="#8e9bb0",
            bg="#1c1f2e",
            font=(font_family, 10),
            wraplength=420,
            justify="center",
        )
        self.label_question.pack(pady=(12, 4), padx=10)

        self.label_answer = tk.Label(
            card_frame,
            text="Awaiting scan...",
            fg="#00e676",
            bg="#1c1f2e",
            font=(font_family, 15, "bold"),
            wraplength=420,
            justify="center",
        )
        self.label_answer.pack(pady=4, padx=10)

        self.label_proof_title = tk.Label(
            card_frame,
            text="📷 Website Source Proof:",
            fg="#00e5ff",
            bg="#1c1f2e",
            font=(font_family, 9, "bold"),
        )

        self.label_image = tk.Label(card_frame, bg="#1c1f2e")

        self.btn_copy = tk.Button(
            card_frame,
            text="📋 Copy Question",
            command=self.copy_to_clipboard,
            bg="#252a3e",
            fg="#00e5ff",
            activebackground="#2e354f",
            activeforeground="#00e5ff",
            font=(font_family, 9, "bold"),
            relief="flat",
            bd=0,
            cursor="hand2",
            padx=12,
            pady=4,
        )
        self.btn_copy.pack(pady=(6, 12))

    def hide_window(self):
        if self.window.winfo_exists():
            self.window.withdraw()

    def show_window(self):
        if self.window.winfo_exists():
            self.window.deiconify()
            self.window.attributes("-topmost", True)

    def update_display(
        self,
        question_text: str,
        answer_text: str,
        raw_question: str = "",
        image_path: str = "",
    ):
        if not self.window.winfo_exists():
            return

        clean_ans = (
            re.sub(
                r"[\uE000-\uF8FF\u2700-\u27BF\u2600-\u26FF✓✔✅]", "", answer_text
            ).strip()
            or answer_text.strip()
        )

        self.current_question_text = raw_question if raw_question else question_text
        self.label_question.config(text=f"Detected Question: {question_text}")
        self.label_answer.config(text=f"Answer: {clean_ans}")

        if HAS_PIL and image_path and os.path.exists(image_path):
            try:
                with Image.open(image_path) as pil_img:
                    w, h = pil_img.size
                    max_w = 400
                    if w > max_w:
                        h = int(h * (max_w / w))
                        w = max_w
                        resized_img = pil_img.resize((w, h), Image.Resampling.LANCZOS)
                    else:
                        resized_img = pil_img.copy()

                    self._current_photo = ImageTk.PhotoImage(resized_img)
                    resized_img.close()

                self.label_image.config(image=self._current_photo)
                self.label_proof_title.pack(before=self.btn_copy, pady=(6, 2))
                self.label_image.pack(before=self.btn_copy, pady=(2, 6), padx=10)
            except Exception as img_err:
                print(f"[Warning] Proof image render error: {img_err}")
                self.label_proof_title.pack_forget()
                self.label_image.pack_forget()
        else:
            self.label_proof_title.pack_forget()
            self.label_image.pack_forget()

        self.show_window()

    def copy_to_clipboard(self):
        if self.current_question_text and self.window.winfo_exists():
            self.window.clipboard_clear()
            self.window.clipboard_append(self.current_question_text)
            self.btn_copy.config(text="✓ Copied!", fg="#00e676")
            self.window.after(
                1500,
                lambda: (
                    self.btn_copy.config(text="📋 Copy Question", fg="#00e5ff")
                    if self.window.winfo_exists()
                    else None
                ),
            )


# --- Consolidated ROHelper Application ---
class ROHelperApp:
    """Master application orchestrator consolidating OCR, position persistence, and web updates."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("RO Auto Answer Helper (Unified)")
        self.root.geometry("380x570")
        self.root.configure(bg="#13151f")
        self.root.attributes("-topmost", True)

        self.raw_database = {}
        self.normalized_db_cache = {}
        self.roi_presets = {}
        self.saved_main_geo = None
        self.saved_overlay_geo = None
        self.saved_lang = None
        self.saved_cat = None

        self.auto_scan_active = False
        self._auto_scan_stop_event = threading.Event()
        self._scan_lock = threading.Lock()
        self.last_frame_hash = None
        self.scan_queue = queue.Queue()
        self.ocr_manager = OCRManager()

        self.load_config()
        self.load_database()

        if self.saved_main_geo:
            try:
                self.root.geometry(self.saved_main_geo)
            except Exception as e:
                print(f"[Warning] Could not restore main geometry: {e}")

        self.overlay = AnswerOverlay(self.root, initial_geometry=self.saved_overlay_geo)
        self.build_ui()
        self.restore_ui_selections()

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.root.after(100, self.process_ui_queue)

        if HAS_PYNPUT:
            self.start_keyboard_listener()

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)

                if "roi_presets" in data and isinstance(data["roi_presets"], dict):
                    self.roi_presets = data.get("roi_presets", {})
                    self.saved_main_geo = data.get("main_window_geometry")
                    self.saved_overlay_geo = data.get("overlay_window_geometry")
                    self.saved_lang = data.get("selected_language")
                    self.saved_cat = data.get("selected_category")
                else:
                    self.roi_presets = data
            except Exception as e:
                print(f"[Error] Failed to load config: {e}")
                self.roi_presets = {}

    def save_config(self):
        try:
            overlay_geo = (
                self.overlay.window.geometry()
                if hasattr(self, "overlay") and self.overlay.window.winfo_exists()
                else (self.saved_overlay_geo or "460x380+50+50")
            )
            config_payload = {
                "main_window_geometry": self.root.geometry(),
                "overlay_window_geometry": overlay_geo,
                "selected_language": (
                    self.lang_combobox.get() if hasattr(self, "lang_combobox") else ""
                ),
                "selected_category": (
                    self.category_combobox.get()
                    if hasattr(self, "category_combobox")
                    else ""
                ),
                "roi_presets": self.roi_presets,
            }
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config_payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Error] Failed to save config: {e}")

    def on_closing(self):
        self.auto_scan_active = False
        self._auto_scan_stop_event.set()
        if hasattr(self, "keyboard_listener") and self.keyboard_listener:
            try:
                self.keyboard_listener.stop()
            except Exception as e:
                print(f"[Warning] Error stopping keyboard listener: {e}")
        self.save_config()
        self.root.destroy()

    def save_roi_preset(self, category: str, rel_x: int, rel_y: int, w: int, h: int):
        self.roi_presets[category] = {
            "rel_x": rel_x,
            "rel_y": rel_y,
            "w": w,
            "h": h,
        }
        self.save_config()

    @staticmethod
    def _normalize_q(text: str) -> str:
        if not text:
            return ""
        clean = re.sub(
            r"^(?:Question|Q)\s*\.?\d*[\.\:\s]*", "", text, flags=re.IGNORECASE
        )
        clean = re.sub(r"[^\w\s]", "", clean)
        return " ".join(clean.lower().split())

    def _build_normalized_cache(self):
        self.normalized_db_cache = {}
        for lang, categories in self.raw_database.items():
            self.normalized_db_cache[lang] = {}
            for cat, items in categories.items():
                if not isinstance(items, list):
                    continue
                cat_tuples = []
                for entry in items:
                    if isinstance(entry, dict) and "question" in entry:
                        q_raw = entry["question"]
                        a_raw = entry.get("answer", "")
                        img_path = entry.get("image_path", "")
                        q_norm = self._normalize_q(q_raw)
                        if q_norm:
                            cat_tuples.append((q_raw, q_norm, a_raw, img_path))
                self.normalized_db_cache[lang][cat] = cat_tuples

    def load_database(self) -> bool:
        if os.path.exists(DATABASE_FILE):
            try:
                with open(DATABASE_FILE, "r", encoding="utf-8") as f:
                    self.raw_database = json.load(f)
                self._build_normalized_cache()
                return True
            except Exception as e:
                print(f"[Error] Database loading error: {e}")
        self.raw_database = {}
        self.normalized_db_cache = {}
        return False

    def build_ui(self):
        font_main = ("Segoe UI", 10)
        font_bold = ("Segoe UI", 10, "bold")

        main_card = tk.Frame(
            self.root,
            bg="#1c1f2e",
            highlightbackground="#2e344d",
            highlightthickness=1,
        )
        main_card.pack(fill="both", expand=True, padx=15, pady=15)

        lbl_title = tk.Label(
            main_card,
            text="RO Trivia Helper",
            fg="#00e5ff",
            bg="#1c1f2e",
            font=("Segoe UI", 14, "bold"),
        )
        lbl_title.pack(pady=(15, 10))

        lbl_lang = tk.Label(
            main_card,
            text="1. Select Game Language:",
            fg="#ffffff",
            bg="#1c1f2e",
            font=font_bold,
            anchor="w",
        )
        lbl_lang.pack(fill="x", padx=20, pady=(5, 2))

        languages = [lang["name"] for lang in SUPPORTED_LANGUAGES]
        self.lang_combobox = ttk.Combobox(
            main_card, values=languages, state="readonly", font=font_main
        )
        self.lang_combobox.current(0)
        self.lang_combobox.pack(fill="x", padx=20, pady=2)

        lbl_cat = tk.Label(
            main_card,
            text="2. Select Event Category:",
            fg="#ffffff",
            bg="#1c1f2e",
            font=font_bold,
            anchor="w",
        )
        lbl_cat.pack(fill="x", padx=20, pady=(10, 2))

        self.category_combobox = ttk.Combobox(
            main_card, values=["ทุกหมวดหมู่"], state="readonly", font=font_main
        )
        self.category_combobox.pack(fill="x", padx=20, pady=2)

        self.lang_combobox.bind("<<ComboboxSelected>>", self.update_category_options)
        self.update_category_options()

        self.lbl_status = tk.Label(
            main_card,
            text="Status: Ready (Press F9 to scan)",
            fg="#8e9bb0",
            bg="#1c1f2e",
            font=("Segoe UI", 9),
        )
        self.lbl_status.pack(pady=8)

        self.btn_set_roi = tk.Button(
            main_card,
            text="🎯 Set ROI Bounding Box",
            command=lambda: SnippingTool(
                self.root, self.category_combobox.get(), self.save_roi_preset
            ),
            bg="#282d42",
            fg="#ffffff",
            activebackground="#333a54",
            activeforeground="#ffffff",
            font=font_main,
            relief="flat",
            bd=0,
            height=2,
            cursor="hand2",
        )
        self.btn_set_roi.pack(fill="x", padx=20, pady=4)

        self.btn_scan = tk.Button(
            main_card,
            text="⚡ Scan Now (or press F9)",
            command=lambda: self.trigger_scan(silent=False),
            bg="#00e676",
            fg="#0d0e15",
            activebackground="#00c853",
            activeforeground="#0d0e15",
            font=font_bold,
            relief="flat",
            bd=0,
            height=2,
            cursor="hand2",
        )
        self.btn_scan.pack(fill="x", padx=20, pady=4)

        self.btn_auto = tk.Button(
            main_card,
            text="🔄 Toggle Auto-Scan",
            command=self.toggle_auto_scan,
            bg="#2979ff",
            fg="#ffffff",
            activebackground="#1565c0",
            activeforeground="#ffffff",
            font=font_bold,
            relief="flat",
            bd=0,
            height=2,
            cursor="hand2",
        )
        self.btn_auto.pack(fill="x", padx=20, pady=4)

        self.btn_update = tk.Button(
            main_card,
            text="🌐 Update DB & Proof Images from Web",
            command=self.update_db_async,
            bg="#252a3e",
            fg="#00e5ff",
            activebackground="#2e354f",
            activeforeground="#00e5ff",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            bd=0,
            height=2,
            cursor="hand2",
        )
        self.btn_update.pack(fill="x", padx=20, pady=(8, 15))

    def restore_ui_selections(self):
        if self.saved_lang and self.saved_lang in self.lang_combobox["values"]:
            self.lang_combobox.set(self.saved_lang)
            self.update_category_options()
            if self.saved_cat and self.saved_cat in self.category_combobox["values"]:
                self.category_combobox.set(self.saved_cat)

    def update_category_options(self, event=None):
        current_lang = self.lang_combobox.get()
        cats = ["ทุกหมวดหมู่"] + list(self.raw_database.get(current_lang, {}).keys())
        self.category_combobox["values"] = cats
        if cats:
            self.category_combobox.current(0)

    def update_db_async(self):
        log_win = LogWindow(self.root)
        log_win.start_timer(total_tasks=16)
        self.btn_update.config(state="disabled", text="⏳ Updating Database...")
        self.lbl_status.config(text="Status: Fetching Q&A database and screenshots...")

        def run_update():
            success = fetch_multilingual_database(
                log_fn=log_win.write_log,
                progress_fn=log_win.update_telemetry,
            )
            self.load_database()

            def finalize():
                log_win.stop_timer()
                languages = [lang["name"] for lang in SUPPORTED_LANGUAGES]
                self.lang_combobox["values"] = languages
                self.update_category_options()

                if success:
                    self.lbl_status.config(
                        text="Status: Database updated successfully!"
                    )
                else:
                    self.lbl_status.config(text="Status: Database update failed.")

                self.btn_update.config(
                    state="normal", text="🌐 Update DB & Proof Images from Web"
                )

            self.root.after(0, finalize)

        threading.Thread(target=run_update, daemon=True).start()

    def get_game_window(self):
        windows = gw.getWindowsWithTitle(GAME_WINDOW_TITLE)
        if windows and not windows[0].isMinimized and windows[0].visible:
            return windows[0]
        return None

    def get_absolute_roi_for_category(self, category_name: str) -> Optional[Dict[str, int]]:
        win = self.get_game_window()
        if not win or category_name not in self.roi_presets:
            return None

        preset = self.roi_presets[category_name]
        abs_x = win.left + preset["rel_x"]
        abs_y = win.top + preset["rel_y"]
        w = preset["w"]
        h = preset["h"]

        with mss.mss() as sct:
            v_mon = sct.monitors[0]
            v_left, v_top = v_mon["left"], v_mon["top"]
            v_right = v_left + v_mon["width"]
            v_bottom = v_top + v_mon["height"]

        clamped_x = max(v_left, min(abs_x, v_right - 10))
        clamped_y = max(v_top, min(abs_y, v_bottom - 10))
        clamped_w = max(10, min(w, v_right - clamped_x))
        clamped_h = max(10, min(h, v_bottom - clamped_y))

        return {
            "top": int(clamped_y),
            "left": int(clamped_x),
            "width": int(clamped_w),
            "height": int(clamped_h),
        }

    def trigger_scan(self, silent: bool = False):
        if self._scan_lock.locked():
            return
        selected_lang = (
            self.lang_combobox.get()
            if hasattr(self, "lang_combobox") and self.lang_combobox
            else "ไทย (TH)"
        )
        selected_cat = (
            self.category_combobox.get()
            if hasattr(self, "category_combobox") and self.category_combobox
            else "ทุกหมวดหมู่"
        )
        threading.Thread(
            target=self._execute_scan,
            args=(selected_lang, selected_cat, silent),
            daemon=True,
        ).start()

    def toggle_auto_scan(self):
        if self.auto_scan_active:
            self.auto_scan_active = False
            self._auto_scan_stop_event.set()
            self.btn_auto.config(text="🔄 Toggle Auto-Scan", bg="#2979ff")
            self.lbl_status.config(text="Status: Auto-scan stopped.")
        else:
            self.auto_scan_active = True
            self._auto_scan_stop_event.clear()
            self.btn_auto.config(text="⏹️ Stop Auto-Scan", bg="#ff5252")
            self.lbl_status.config(text="Status: Auto-scanning every 2s...")

            def auto_loop():
                while not self._auto_scan_stop_event.is_set():
                    selected_lang = self.saved_lang or "ไทย (TH)"
                    selected_cat = self.saved_cat or "ทุกหมวดหมู่"
                    if hasattr(self, "lang_combobox") and self.lang_combobox:
                        try:
                            selected_lang = self.lang_combobox.get()
                            selected_cat = self.category_combobox.get()
                        except Exception:
                            pass
                    self._execute_scan(selected_lang, selected_cat, silent=True)
                    if self._auto_scan_stop_event.wait(timeout=2.0):
                        break

            threading.Thread(target=auto_loop, daemon=True).start()

    def _execute_scan(self, selected_lang: str, selected_cat: str, silent: bool):
        if not self._scan_lock.acquire(blocking=False):
            return

        try:
            win = self.get_game_window()
            if not win:
                if not silent:
                    self.scan_queue.put(
                        (
                            "warning",
                            f"Game window '{GAME_WINDOW_TITLE}' not found or minimized!",
                        )
                    )
                return

            roi = self.get_absolute_roi_for_category(selected_cat)
            if not roi:
                if not silent:
                    self.scan_queue.put(
                        (
                            "warning",
                            f"Bounding box ROI not configured for category '{selected_cat}'",
                        )
                    )
                return

            def get_cached_tuples(lang_name: str, cat_name: str):
                lang_cache = self.normalized_db_cache.get(lang_name, {})
                if cat_name == "ทุกหมวดหมู่" or cat_name not in lang_cache:
                    pool = []
                    for tuples in lang_cache.values():
                        pool.extend(tuples)
                    return pool
                return lang_cache.get(cat_name, [])

            primary_tuples = get_cached_tuples(selected_lang, selected_cat)

            with mss.mss() as sct:
                sct_img = sct.grab(roi)
                img = np.array(sct_img)

            gray = cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
            frame_hash = hash(gray.tobytes())
            if frame_hash == self.last_frame_hash:
                return
            self.last_frame_hash = frame_hash

            _, bin_img = cv2.threshold(
                gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
            )
            h, w = bin_img.shape
            scaled = cv2.resize(bin_img, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)

            reader = self.ocr_manager.get_reader(selected_lang)
            results = reader.readtext(scaled, detail=0)
            captured_text = " ".join(results).strip()

            if captured_text:
                norm_captured = self._normalize_q(captured_text)
                best_match = None
                best_score = 0
                matched_answer = ""
                matched_img_path = ""
                matched_lang = selected_lang

                def search_pool(tuple_list):
                    nonlocal best_match, best_score, matched_answer, matched_img_path
                    if not tuple_list:
                        return

                    norm_questions = [t[1] for t in tuple_list]
                    match_res = process.extractOne(
                        norm_captured, norm_questions, scorer=fuzz.token_set_ratio
                    )
                    if match_res:
                        _, score, idx = match_res[0], match_res[1], match_res[2]
                        if score > best_score:
                            best_score = score
                            best_match = tuple_list[idx][0]
                            matched_answer = tuple_list[idx][2]
                            matched_img_path = tuple_list[idx][3]

                search_pool(primary_tuples)

                if best_score < 70:
                    for lang_name in self.normalized_db_cache.keys():
                        if lang_name == selected_lang:
                            continue
                        fallback_tuples = get_cached_tuples(lang_name, selected_cat)
                        prev_score = best_score
                        search_pool(fallback_tuples)
                        if best_score > prev_score:
                            matched_lang = lang_name

                if best_match and best_score >= 70:
                    clean_ans = (
                        re.sub(
                            r"^(?:Ans|Answer)\s*[\:\.-]?\s*|^[A-Da-d1-4][\.\)]\s+",
                            "",
                            matched_answer,
                            flags=re.IGNORECASE,
                        ).strip()
                        or matched_answer.strip()
                    )

                    lang_tag = (
                        f" [{matched_lang}]" if matched_lang != selected_lang else ""
                    )
                    self.scan_queue.put(
                        (
                            "display",
                            (
                                f"{best_match} ({best_score:.0f}%){lang_tag}",
                                clean_ans,
                                best_match,
                                matched_img_path,
                            ),
                        )
                    )
                else:
                    self.scan_queue.put(
                        (
                            "display",
                            (
                                f"Scanned: {captured_text}",
                                "No matching question found.",
                                captured_text,
                                "",
                            ),
                        )
                    )
            elif not silent:
                self.scan_queue.put(
                    (
                        "display",
                        (
                            "OCR Failed to read text",
                            "Try adjusting ROI or game display resolution.",
                            "",
                            "",
                        ),
                    )
                )

        except Exception as e:
            print(f"[Error] OCR scanning exception: {e}")
            if not silent:
                self.scan_queue.put(("warning", f"Scan exception: {str(e)}"))
        finally:
            self._scan_lock.release()

    def process_ui_queue(self):
        while not self.scan_queue.empty():
            try:
                msg_type, payload = self.scan_queue.get_nowait()
                if msg_type == "display":
                    q_text, a_text, raw_q, img_p = payload
                    self.overlay.update_display(q_text, a_text, raw_q, img_p)
                elif msg_type == "warning":
                    messagebox.showwarning("Notice", payload)
            except queue.Empty:
                break
        self.root.after(100, self.process_ui_queue)

    def start_keyboard_listener(self):
        def on_press(key):
            try:
                if key == keyboard.Key.f9:
                    self.trigger_scan(silent=True)
            except Exception:
                pass

        self.keyboard_listener = keyboard.Listener(on_press=on_press)
        self.keyboard_listener.daemon = True
        self.keyboard_listener.start()


def init_windows_dpi():
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                import ctypes

                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                pass


def main():
    init_windows_dpi()
    root = tk.Tk()
    app = ROHelperApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
