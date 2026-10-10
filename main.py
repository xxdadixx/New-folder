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
import unicodedata
import math
from fractions import Fraction
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

import cv2
import easyocr
import mss
import numpy as np
import pygetwindow as gw
from rapidfuzz import fuzz, process

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
    """Live system log window rendering real-time web scraping progress, copy logs button, and IDM-style continuous telemetry."""

    def __init__(self, parent: tk.Tk):
        self.window = tk.Toplevel(parent)
        self.window.title("📋 Live System Logs - Update & Audit")
        self.window.geometry("780x480+100+100")
        self.window.configure(bg="#13151f")
        self.window.attributes("-topmost", True)

        self.start_time = None
        self.is_running = False
        self.remaining_tasks = 16
        self.total_tasks = 16
        self.target_finish_time: Optional[datetime] = None
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
            text="  |  ⏱️ Elapsed: 00m 00s  |  ⏳ Time Left: Calculating...",
            fg="#a0aec0",
            bg="#1c1f2e",
            font=("Segoe UI", 10, "bold"),
        )
        self.lbl_telemetry.pack(side="left", padx=(8, 0))

        self.btn_copy_logs = tk.Button(
            header_frame,
            text="📋 Copy Logs",
            command=self.copy_logs,
            bg="#252a3e",
            fg="#00e5ff",
            activebackground="#2e354f",
            activeforeground="#00e5ff",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            bd=0,
            cursor="hand2",
            padx=10,
            pady=2,
        )
        self.btn_copy_logs.pack(side="right")

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

    def copy_logs(self):
        if not self.window.winfo_exists():
            return
        logs_text = self.text_area.get("1.0", tk.END).strip()
        if logs_text:
            self.window.clipboard_clear()
            self.window.clipboard_append(logs_text)
            self.btn_copy_logs.config(text="✓ Copied!", fg="#00e676")
            self.window.after(
                1500,
                lambda: (
                    self.btn_copy_logs.config(text="📋 Copy Logs", fg="#00e5ff")
                    if self.window.winfo_exists()
                    else None
                ),
            )

    def start_timer(self, total_tasks: int = 16):
        self.start_time = time.time()
        self.is_running = True
        self.total_tasks = total_tasks
        self.remaining_tasks = total_tasks
        self.target_finish_time = None
        self._tick_timer()

    def stop_timer(self):
        self.is_running = False
        if self.timer_job:
            self.window.after_cancel(self.timer_job)
            self.timer_job = None

    def _tick_timer(self):
        if not self.window.winfo_exists() or not self.is_running:
            return

        now = datetime.now()
        elapsed = int(time.time() - self.start_time)
        mins, secs = divmod(elapsed, 60)
        hrs, mins = divmod(mins, 60)
        elapsed_str = (
            f"{hrs:02d}h {mins:02d}m {secs:02d}s"
            if hrs > 0
            else f"{mins:02d}m {secs:02d}s"
        )

        if self.remaining_tasks > 0:
            if self.target_finish_time and self.target_finish_time > now:
                time_left_sec = max(
                    0, int((self.target_finish_time - now).total_seconds())
                )
                r_mins, r_secs = divmod(time_left_sec, 60)
                r_hrs, r_mins = divmod(r_mins, 60)
                time_left_str = (
                    f"{r_hrs:02d}h {r_mins:02d}m {r_secs:02d}s"
                    if r_hrs > 0
                    else f"{r_mins:02d}m {r_secs:02d}s"
                )
                finish_clock_str = self.target_finish_time.strftime("%I:%M:%S %p")

                display_text = (
                    f"  |  ⏱️ Elapsed: {elapsed_str}  |  ⏳ Time Left: {time_left_str}  "
                    f"|  🏁 Est. Finish: {finish_clock_str}"
                )
            else:
                display_text = f"  |  ⏱️ Elapsed: {elapsed_str}  |  ⏳ Time Left: Estimating speed..."
        else:
            display_text = f"  |  ⏱️ Total Time: {elapsed_str}  |  ✅ Complete"

        self.lbl_telemetry.config(text=display_text)
        self.timer_job = self.window.after(1000, self._tick_timer)

    def write_log(self, message: str):
        self.window.after(0, self._append_text, message)

    def update_telemetry(self, data: dict):
        if not self.window.winfo_exists():
            return

        def _apply():
            self.remaining_tasks = data.get("remaining_tasks", self.remaining_tasks)
            self.total_tasks = data.get("total_tasks", self.total_tasks)

            eta_sec = data.get("eta_seconds", None)
            if eta_sec is not None and eta_sec > 0:
                self.target_finish_time = datetime.now() + timedelta(seconds=eta_sec)

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
    """Bounded, memory-aware loader and CPU-optimized cache for EasyOCR instances."""

    def __init__(self, max_cached_readers: int = 2):
        self._readers = {}
        self.max_cached_readers = max_cached_readers

        try:
            import torch

            torch.set_num_threads(max(1, min(4, os.cpu_count() or 4)))
        except Exception:
            pass

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


class LiquidGlassSpinner(tk.Canvas):
    """
    Custom Tkinter Canvas widget rendering a Liquid Glass orbital spinner.
    """

    def __init__(
        self,
        parent,
        size: int = 70,
        bg: str = "#1c1f2e",
        cyan_color: str = "#00e5ff",
        track_color: str = "#2b3044",
        **kwargs,
    ):
        super().__init__(
            parent,
            width=size,
            height=size,
            bg=bg,
            highlightthickness=0,
            bd=0,
            **kwargs,
        )
        self.size = size
        self.cyan_color = cyan_color
        self.track_color = track_color
        self.angle = 0
        self.extent = 90
        self.anim_job: Optional[str] = None
        self.is_animating = False

        self.cx = size / 2.0
        self.cy = size / 2.0
        self.radius = (size / 2.0) - 10.0

    def start(self):
        if not self.is_animating:
            self.is_animating = True
            self._animate()

    def stop(self):
        self.is_animating = False
        if self.anim_job:
            self.after_cancel(self.anim_job)
            self.anim_job = None
        if self.winfo_exists():
            self.delete("all")

    def _animate(self):
        if not self.winfo_exists() or not self.is_animating:
            return

        self.delete("all")

        self.create_oval(
            self.cx - self.radius,
            self.cy - self.radius,
            self.cx + self.radius,
            self.cy + self.radius,
            outline=self.track_color,
            width=5,
        )

        self.create_arc(
            self.cx - self.radius,
            self.cy - self.radius,
            self.cx + self.radius,
            self.cy + self.radius,
            start=self.angle,
            extent=self.extent,
            outline="#005b66",
            width=9,
            style=tk.ARC,
        )

        self.create_arc(
            self.cx - self.radius,
            self.cy - self.radius,
            self.cx + self.radius,
            self.cy + self.radius,
            start=self.angle,
            extent=self.extent,
            outline=self.cyan_color,
            width=5,
            style=tk.ARC,
        )

        tip_angle_rad = math.radians(self.angle + self.extent)
        orb_x = self.cx + self.radius * math.cos(tip_angle_rad)
        orb_y = self.cy - self.radius * math.sin(tip_angle_rad)
        orb_r = 4.5

        self.create_oval(
            orb_x - orb_r - 2,
            orb_y - orb_r - 2,
            orb_x + orb_r + 2,
            orb_y + orb_r + 2,
            fill="#00e5ff",
            outline="",
        )
        self.create_oval(
            orb_x - orb_r,
            orb_y - orb_r,
            orb_x + orb_r,
            orb_y + orb_r,
            fill="#ffffff",
            outline=self.cyan_color,
            width=1,
        )

        self.angle = (self.angle - 8) % 360
        self.anim_job = self.after(25, self._animate)


class AnswerOverlay:
    """Thread-safe liquid glass overlay displaying OCR results, answers, proof screenshots, and live benchmarks."""

    def __init__(self, parent: tk.Tk, initial_geometry: str = None):
        self.parent = parent
        self.window = tk.Toplevel(parent)
        self.window.title("RO Answer Verification")

        if initial_geometry:
            try:
                self.window.geometry(initial_geometry)
            except Exception:
                self.window.geometry("460x440+50+50")
        else:
            self.window.geometry("460x440+50+50")

        self.window.attributes("-topmost", True)
        self.window.configure(bg="#13151f")
        self.window.protocol("WM_DELETE_WINDOW", self.hide_window)

        self.current_question_text = ""
        self.current_image_path = ""
        self._current_photo = None
        self._modal_photo = None
        self.last_telemetry_str = ""
        font_family = "Segoe UI"

        self.card_frame = tk.Frame(
            self.window,
            bg="#1c1f2e",
            highlightbackground="#2e344d",
            highlightthickness=1,
        )
        self.card_frame.pack(fill="both", expand=True, padx=12, pady=12)

        self.spinner = LiquidGlassSpinner(self.card_frame, size=65, bg="#1c1f2e")

        self.label_question = tk.Label(
            self.card_frame,
            text="Question: -",
            fg="#8e9bb0",
            bg="#1c1f2e",
            font=(font_family, 10),
            wraplength=420,
            justify="center",
        )
        self.label_question.pack(pady=(12, 4), padx=10)

        self.label_answer = tk.Label(
            self.card_frame,
            text="Awaiting scan...",
            fg="#00e676",
            bg="#1c1f2e",
            font=(font_family, 15, "bold"),
            wraplength=420,
            justify="center",
        )
        self.label_answer.pack(pady=4, padx=10)

        self.label_proof_title = tk.Label(
            self.card_frame,
            text="📷 Website Source Proof (Click image to expand):",
            fg="#00e5ff",
            bg="#1c1f2e",
            font=(font_family, 9, "bold"),
            cursor="hand2",
        )

        self.label_image = tk.Label(self.card_frame, bg="#1c1f2e", cursor="hand2")
        self.label_image.bind("<Button-1>", lambda e: self.open_full_image_modal())
        self.label_proof_title.bind(
            "<Button-1>", lambda e: self.open_full_image_modal()
        )

        self.btn_copy = tk.Button(
            self.card_frame,
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
        self.btn_copy.pack(pady=(6, 4))

        self.label_telemetry = tk.Label(
            self.card_frame,
            text="",
            fg="#a0aec0",
            bg="#1c1f2e",
            font=("Consolas", 8),
            justify="center",
        )
        self.label_telemetry.pack(pady=(2, 8))

    def open_full_image_modal(self):
        if (
            not HAS_PIL
            or not self.current_image_path
            or not os.path.exists(self.current_image_path)
        ):
            return

        modal = tk.Toplevel(self.window)
        modal.title("🔍 Full Proof Image Viewer")
        modal.geometry("640x600")
        modal.configure(bg="#13151f")
        modal.attributes("-topmost", True)

        try:
            with Image.open(self.current_image_path) as full_img:
                w, h = full_img.size
                max_w = 600
                if w > max_w:
                    h = int(h * (max_w / float(w)))
                    w = max_w
                    resized = full_img.resize((w, h), Image.Resampling.LANCZOS)
                else:
                    resized = full_img.copy()

                self._modal_photo = ImageTk.PhotoImage(resized)
                resized.close()

            canvas = tk.Canvas(modal, bg="#13151f", highlightthickness=0)
            v_scroll = ttk.Scrollbar(modal, orient="vertical", command=canvas.yview)
            h_scroll = ttk.Scrollbar(modal, orient="horizontal", command=canvas.xview)

            canvas.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)

            v_scroll.pack(side="right", fill="y")
            h_scroll.pack(side="bottom", fill="x")
            canvas.pack(side="left", fill="both", expand=True)

            canvas.create_image(0, 0, image=self._modal_photo, anchor="nw")
            canvas.config(scrollregion=(0, 0, w, h))

            canvas.bind_all(
                "<MouseWheel>",
                lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"),
            )
        except Exception as err:
            print(f"[Error] Modal image preview error: {err}")

    def _auto_fit_window_geometry(self):
        if not self.window.winfo_exists():
            return

        self.window.update_idletasks()
        req_w = 460
        req_h = self.card_frame.winfo_reqheight() + 28
        screen_h = self.window.winfo_screenheight()
        target_h = min(req_h, screen_h - 100)

        curr_x = self.window.winfo_x()
        curr_y = self.window.winfo_y()
        if curr_x <= 0 and curr_y <= 0:
            curr_x, curr_y = 50, 50

        self.window.geometry(f"{req_w}x{target_h}+{curr_x}+{curr_y}")

    def hide_window(self):
        if self.window.winfo_exists():
            self.window.withdraw()

    def show_window(self):
        if self.window.winfo_exists():
            self.window.deiconify()
            self.window.attributes("-topmost", True)

    def show_loading(self):
        if not self.window.winfo_exists():
            return

        self.spinner.pack(before=self.label_question, pady=(8, 4))
        self.spinner.start()

        self.label_question.config(text="Question: ⏳ Capturing ROI & executing OCR...")
        self.label_answer.config(text="⚡ Scanning in progress...", fg="#ffb74d")

        if self.last_telemetry_str:
            self.label_telemetry.config(
                text=f"🔄 Active | Last: {self.last_telemetry_str}"
            )
        else:
            self.label_telemetry.config(
                text="⏱️ Measuring: Capture ➔ Preprocess ➔ Neural OCR ➔ Match"
            )

        self.show_window()
        self._auto_fit_window_geometry()

    def update_display(
        self,
        question_text: str,
        answer_text: str,
        raw_question: str = "",
        image_path: str = "",
        telemetry: Optional[Dict[str, float]] = None,
    ):
        if not self.window.winfo_exists():
            return

        self.spinner.stop()
        self.spinner.pack_forget()

        clean_ans = (
            re.sub(
                r"[\uE000-\uF8FF\u2700-\u27BF\u2600-\u26FF✓✔✅]", "", answer_text
            ).strip()
            or answer_text.strip()
        )

        self.current_question_text = raw_question if raw_question else question_text
        self.current_image_path = image_path
        self.label_question.config(text=f"Detected Question: {question_text}")
        self.label_answer.config(text=f"Answer: {clean_ans}", fg="#00e676")

        if telemetry:
            total_ms = telemetry.get("total_ms", 0)
            cap_ms = telemetry.get("capture_ms", 0)
            prep_ms = telemetry.get("prep_ms", 0)
            ocr_ms = telemetry.get("ocr_ms", 0)
            match_ms = telemetry.get("match_ms", 0)
            self.last_telemetry_str = (
                f"⚡ Total: {total_ms:.0f}ms | Cap: {cap_ms:.0f}ms | Prep: {prep_ms:.0f}ms | "
                f"OCR: {ocr_ms:.0f}ms | Match: {match_ms:.0f}ms"
            )
            self.label_telemetry.config(text=self.last_telemetry_str)
        elif self.last_telemetry_str:
            self.label_telemetry.config(text=self.last_telemetry_str)
        else:
            self.label_telemetry.config(text="")

        valid_img_rendered = False
        if HAS_PIL and image_path and os.path.exists(image_path):
            try:
                if os.path.getsize(image_path) > 100:
                    with Image.open(image_path) as pil_img:
                        w, h = pil_img.size
                        max_w = 400
                        max_h = 180

                        scale = min(max_w / float(w), max_h / float(h), 1.0)
                        new_w = max(10, int(w * scale))
                        new_h = max(10, int(h * scale))

                        resized_img = pil_img.resize(
                            (new_w, new_h), Image.Resampling.LANCZOS
                        )
                        self._current_photo = ImageTk.PhotoImage(resized_img)
                        resized_img.close()

                    self.label_image.config(image=self._current_photo)
                    self.label_proof_title.pack(before=self.btn_copy, pady=(4, 2))
                    self.label_image.pack(before=self.btn_copy, pady=(2, 4), padx=10)
                    valid_img_rendered = True
            except Exception as img_err:
                print(f"[Warning] Proof image render error: {img_err}")

        if not valid_img_rendered:
            self.label_proof_title.pack_forget()
            self.label_image.pack_forget()

        self.show_window()
        self._auto_fit_window_geometry()

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

        text = unicodedata.normalize("NFC", text)
        text = re.sub(r"^[\s\W_0-9]+", "", text)
        clean = re.sub(
            r"^(?:Question|Q|ข้อที่|ข้อ)\s*\.?\d*[\.\:\s]*",
            "",
            text,
            flags=re.IGNORECASE,
        )
        clean = re.sub(r"[^\w\s\u0E00-\u0E7F]", " ", clean)
        clean = re.sub(r"\b[a-zA-Z]\b", " ", clean)
        clean = " ".join(clean.lower().split())

        return clean

    def _try_solve_math_expression(self, text: str) -> Optional[str]:
        """Detects and evaluates arithmetic expressions, fractions, metric unit conversions, and word problems with intelligent quotient inference."""
        if not text:
            return None

        text_lower = text.lower()

        # 1. Geometric Volume Word Problem Solver (Cube Edge Length -> Volume: s^3)
        if (
            "cube" in text_lower
            and "edge length" in text_lower
            and "volume" in text_lower
        ):
            try:
                edge_match = re.search(r"edge length of\s*(\d+(?:\.\d+)?)", text_lower)
                if not edge_match:
                    edge_match = re.search(r"edge of\s*(\d+(?:\.\d+)?)", text_lower)
                if not edge_match:
                    edge_match = re.search(r"(\d+(?:\.\d+)?)\s*cm", text_lower)

                if edge_match:
                    s = float(edge_match.group(1))
                    volume = s**3
                    if volume.is_integer():
                        return str(int(volume))
                    return str(volume)
            except Exception:
                pass

        # 2. Cube Edge Length from Volume Solver (Volume -> Edge Length: cube root)
        if (
            "cube" in text_lower
            and "volume of" in text_lower
            and ("edge" in text_lower or "length" in text_lower)
        ):
            try:
                vol_match = re.search(r"volume of\s*(\d+(?:\.\d+)?)", text_lower)
                if vol_match:
                    vol = float(vol_match.group(1))
                    edge = round(vol ** (1 / 3), 4)
                    if abs(round(edge) ** 3 - vol) < 0.001:
                        edge = round(edge)
                    if isinstance(edge, float) and edge.is_integer():
                        return str(int(edge))
                    return str(edge)
            except Exception:
                pass

        # 3. Unit Cost / Pricing Multiplication Word Problem Solver
        if "cost" in text_lower or "costs" in text_lower or "price" in text_lower:
            try:
                nums = re.findall(r"(\d+(?:\.\d+)?)", text_lower)
                if len(nums) >= 2:
                    v1, v2 = float(nums[0]), float(nums[1])
                    res = v1 * v2
                    if res.is_integer():
                        return str(int(res))
                    return str(round(res, 2))
            except Exception:
                pass

        # 4. Transport / Trip Division Word Problem Solver
        if (
            "transport" in text_lower
            or "trip" in text_lower
            or "per trip" in text_lower
            or "how many trips" in text_lower
        ):
            try:
                nums = re.findall(r"(\d+(?:\.\d+)?)", text_lower)
                if len(nums) >= 2:
                    v1, v2 = float(nums[0]), float(nums[1])
                    if v2 != 0:
                        res = v1 / v2
                        if res.is_integer():
                            return str(int(res))
                        return str(round(res, 4))
            except Exception:
                pass

        # 5. Compound Metric Unit Addition Solver
        compound_match = re.search(
            r"(\d+(?:\.\d+)?)\s*([a-zA-Z\u0E00-\u0E7F]+)\s+and\s+(\d+(?:\.\d+)?)\s*([a-zA-Z\u0E00-\u0E7F]+)\s+(?:equals|is|are|to|เป็น|เท่า|เท่ากับ)\s+(?:how many\s+)?([a-zA-Z\u0E00-\u0E7F]+)\?",
            text_lower,
        )
        if compound_match:
            val1_str, unit1, val2_str, unit2, tgt_unit = compound_match.groups()
            all_units = {
                "ml": 0.001,
                "milliliter": 0.001,
                "milliliters": 0.001,
                "มิลลิลิตร": 0.001,
                "l": 1.0,
                "liter": 1.0,
                "liters": 1.0,
                "litre": 1.0,
                "litres": 1.0,
                "ลิตร": 1.0,
                "mm": 0.001,
                "millimeter": 0.001,
                "millimeters": 0.001,
                "มิลลิเมตร": 0.001,
                "cm": 0.01,
                "centimeter": 0.01,
                "centimeters": 0.01,
                "เซนติเมตร": 0.01,
                "m": 1.0,
                "meter": 1.0,
                "meters": 1.0,
                "เมตร": 1.0,
                "km": 1000.0,
                "kilometer": 1000.0,
                "kilometers": 1000.0,
                "กิโลเมตร": 1000.0,
                "g": 1.0,
                "gram": 1.0,
                "grams": 1.0,
                "กรัม": 1.0,
                "kg": 1000.0,
                "kilogram": 1000.0,
                "kilograms": 1000.0,
                "กิโลกรัม": 1000.0,
            }
            if unit1 in all_units and unit2 in all_units and tgt_unit in all_units:
                try:
                    base_val = (float(val1_str) * all_units[unit1]) + (
                        float(val2_str) * all_units[unit2]
                    )
                    result = base_val / all_units[tgt_unit]
                    if result.is_integer():
                        return str(int(result))
                    return str(result)
                except Exception:
                    pass

        # 6. Rectangle Area Word Problem Solver
        if "rectangle" in text_lower and "area" in text_lower:
            try:
                nums = re.findall(r"(\d+(?:\.\d+)?)", text_lower)
                if len(nums) >= 2:
                    val1 = float(nums[0])
                    val2 = float(nums[1])
                    area = val1 * val2
                    if area.is_integer():
                        return str(int(area))
                    return str(round(area, 4))
            except Exception:
                pass

        # 7. Geometric Area Word Problem Solver (Circle)
        if (
            "area of a circle" in text_lower
            or ("r =" in text_lower or "r=" in text_lower)
            and ("pi" in text_lower or "π" in text_lower)
        ):
            try:
                r_match = re.search(r"r\s*=\s*(\d+(?:\.\d+)?)", text_lower)
                pi_match = re.search(r"(?:pi|π)\s*=\s*(\d+(?:\.\d+)?)", text_lower)

                if r_match:
                    r = float(r_match.group(1))
                    pi = float(pi_match.group(1)) if pi_match else 3.14

                    area = pi * (r**2)
                    if area.is_integer():
                        return str(int(area))
                    return str(round(area, 1) if round(area, 1) == area else area)
            except Exception:
                pass

        # 8. Metric Unit Conversion Solver
        length_units = {
            "mm": 0.001,
            "millimeter": 0.001,
            "millimeters": 0.001,
            "มิลลิเมตร": 0.001,
            "cm": 0.01,
            "centimeter": 0.01,
            "centimeters": 0.01,
            "เซนติเมตร": 0.01,
            "dm": 0.1,
            "decimeter": 0.1,
            "decimeters": 0.1,
            "เดซิเมตร": 0.1,
            "m": 1.0,
            "meter": 1.0,
            "meters": 1.0,
            "เมตร": 1.0,
            "km": 1000.0,
            "kilometer": 1000.0,
            "kilometers": 1000.0,
            "กิโลเมตร": 1000.0,
        }
        mass_units = {
            "mg": 0.001,
            "milligram": 0.001,
            "milligrams": 0.001,
            "g": 1.0,
            "gram": 1.0,
            "grams": 1.0,
            "กรัม": 1.0,
            "kg": 1000.0,
            "kilogram": 1000.0,
            "kilograms": 1000.0,
            "กิโลกรัม": 1000.0,
        }

        match_a = re.search(
            r"(\d+(?:\.\d+)?)\s*([a-zA-Z\u0E00-\u0E7F]+)\s+(?:equals|is|are|to|เป็น|เท่า|เท่ากับ)\s+(?:how many\s+)?([a-zA-Z\u0E00-\u0E7F]+)\?",
            text_lower,
        )
        match_b = re.search(
            r"how many\s+([a-zA-Z\u0E00-\u0E7F]+)\s+(?:in|are|is)\s+(\d+(?:\.\d+)?)\s*([a-zA-Z\u0E00-\u0E7F]+)\?",
            text_lower,
        )

        if match_a:
            val_str, src_unit, tgt_unit = match_a.groups()
            try:
                val = float(val_str)
                if src_unit in length_units and tgt_unit in length_units:
                    base_val = val * length_units[src_unit]
                    result = base_val / length_units[tgt_unit]
                    if result.is_integer():
                        return str(int(result))
                    return str(result)
                elif src_unit in mass_units and tgt_unit in mass_units:
                    base_val = val * mass_units[src_unit]
                    result = base_val / mass_units[tgt_unit]
                    if result.is_integer():
                        return str(int(result))
                    return str(result)
            except Exception:
                pass
        elif match_b:
            tgt_unit, val_str, src_unit = match_b.groups()
            try:
                val = float(val_str)
                if src_unit in length_units and tgt_unit in length_units:
                    base_val = val * length_units[src_unit]
                    result = base_val / length_units[tgt_unit]
                    if result.is_integer():
                        return str(int(result))
                    return str(result)
                elif src_unit in mass_units and tgt_unit in mass_units:
                    base_val = val * mass_units[src_unit]
                    result = base_val / mass_units[tgt_unit]
                    if result.is_integer():
                        return str(int(result))
                    return str(result)
            except Exception:
                pass

        # 9. Standard Arithmetic & Fraction Expression Solver with Smart Quotient Inference
        clean = (
            text.replace("×", "*")
            .replace("x", "*")
            .replace("X", "*")
            .replace("÷", "/")
            .replace("➗", "/")
            .replace(":", "/")
            .replace("=", "")
            .replace("?", "")
            .strip()
        )

        nums_in_text = re.findall(r"(\d+(?:\.\d+)?)", clean)
        if len(nums_in_text) == 2:
            try:
                n1, n2 = float(nums_in_text[0]), float(nums_in_text[1])
                if n2 != 0:
                    quotient = n1 / n2
                    if (
                        quotient.is_integer()
                        or (quotient * 10).is_integer()
                        or (quotient * 100).is_integer()
                    ):
                        clean = f"{nums_in_text[0]} / {nums_in_text[1]}"
            except Exception:
                pass

        if not any(op in clean for op in ["+", "-", "*", "/"]):
            return None

        if not re.match(r"^[\d\.\s\+\-\*\/\(\)]+$", clean):
            return None

        try:
            result = eval(clean, {"__builtins__": {}}, {})
            if isinstance(result, float):
                if result.is_integer():
                    return str(int(result))
                rounded = round(result, 6)
                if rounded.is_integer():
                    return str(int(rounded))
                frac = Fraction(result).limit_denominator(20)
                if frac.denominator != 1 and frac.denominator <= 20:
                    return f"{rounded} ({frac.numerator}/{frac.denominator})"
                return str(rounded)
            return str(result)
        except Exception:
            return None

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

    def get_absolute_roi_for_category(
        self, category_name: str
    ) -> Optional[Dict[str, int]]:
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

    def _preprocess_roi_image(
        self, img_bgra: np.ndarray
    ) -> Tuple[np.ndarray, np.ndarray]:
        if len(img_bgra.shape) == 3 and img_bgra.shape[2] == 4:
            gray = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2GRAY)
        elif len(img_bgra.shape) == 3 and img_bgra.shape[2] == 3:
            gray = cv2.cvtColor(img_bgra, cv2.COLOR_BGR2GRAY)
        else:
            gray = img_bgra.copy()

        h, w = gray.shape[:2]
        scaled_gray = cv2.resize(
            gray, (int(w * 1.5), int(h * 1.5)), interpolation=cv2.INTER_CUBIC
        )

        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced_gray = clahe.apply(scaled_gray)

        gaussian = cv2.GaussianBlur(enhanced_gray, (0, 0), sigmaX=1.5)
        sharpened_gray = cv2.addWeighted(enhanced_gray, 1.5, gaussian, -0.5, 0)

        adaptive_bin = cv2.adaptiveThreshold(
            sharpened_gray,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            19,
            3,
        )

        return sharpened_gray, adaptive_bin

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

            t_start_total = time.perf_counter()

            t0_cap = time.perf_counter()
            with mss.mss() as sct:
                sct_img = sct.grab(roi)
                img = np.array(sct_img)
            t_capture_ms = (time.perf_counter() - t0_cap) * 1000.0

            t0_prep = time.perf_counter()
            enhanced_gray, adaptive_bin = self._preprocess_roi_image(img)
            t_prep_ms = (time.perf_counter() - t0_prep) * 1000.0

            frame_hash = hash(enhanced_gray.tobytes())
            if frame_hash == self.last_frame_hash:
                return
            self.last_frame_hash = frame_hash

            if not silent:
                self.scan_queue.put(("loading", None))

            def get_cached_tuples(lang_name: str, cat_name: str):
                lang_cache = self.normalized_db_cache.get(lang_name, {})
                if cat_name == "ทุกหมวดหมู่" or cat_name not in lang_cache:
                    pool = []
                    for tuples in lang_cache.values():
                        pool.extend(tuples)
                    return pool
                return lang_cache.get(cat_name, [])

            primary_tuples = get_cached_tuples(selected_lang, selected_cat)

            t0_ocr = time.perf_counter()
            reader = self.ocr_manager.get_reader(selected_lang)

            results = reader.readtext(
                enhanced_gray,
                detail=0,
                paragraph=True,
                canvas_size=800,
                mag_ratio=1.0,
                text_threshold=0.5,
                low_text=0.3,
                link_threshold=0.4,
                batch_size=4,
            )
            captured_text = " ".join(results).strip()

            if len(captured_text) < 3:
                results_bin = reader.readtext(
                    adaptive_bin,
                    detail=0,
                    paragraph=True,
                    canvas_size=800,
                    mag_ratio=1.0,
                    text_threshold=0.4,
                    low_text=0.3,
                    batch_size=4,
                )
                captured_text_bin = " ".join(results_bin).strip()
                if len(captured_text_bin) > len(captured_text):
                    captured_text = captured_text_bin
            t_ocr_ms = (time.perf_counter() - t0_ocr) * 1000.0

            t0_match = time.perf_counter()
            best_match = None
            best_score = 0.0
            matched_answer = ""
            matched_img_path = ""
            matched_lang = selected_lang

            if captured_text:
                # 1. Prioritize Math Expression Solver first to prevent false fuzzy matches
                math_result = self._try_solve_math_expression(captured_text)
                if math_result:
                    best_match = captured_text
                    matched_answer = math_result
                    best_score = 100.0
                    matched_img_path = ""  # Prevent pulling random unrelated images from other database entries
                else:
                    norm_captured = self._normalize_q(captured_text)
                    norm_captured_nospace = norm_captured.replace(" ", "")

                    def search_pool(tuple_list):
                        nonlocal best_match, best_score, matched_answer, matched_img_path
                        if not tuple_list or not norm_captured:
                            return

                        for q_raw, q_norm, a_raw, img_path in tuple_list:
                            if not q_norm:
                                continue

                            score_token = fuzz.token_set_ratio(norm_captured, q_norm)
                            score_wratio = fuzz.WRatio(norm_captured, q_norm)
                            q_norm_nospace = q_norm.replace(" ", "")
                            score_partial = (
                                fuzz.partial_ratio(
                                    norm_captured_nospace, q_norm_nospace
                                )
                                if norm_captured_nospace and q_norm_nospace
                                else 0
                            )

                            composite_score = max(
                                score_token, score_wratio, score_partial
                            )

                            if composite_score > best_score:
                                best_score = composite_score
                                best_match = q_raw
                                matched_answer = a_raw
                                matched_img_path = img_path

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

            t_match_ms = (time.perf_counter() - t0_match) * 1000.0
            t_total_ms = (time.perf_counter() - t_start_total) * 1000.0

            telemetry = {
                "total_ms": t_total_ms,
                "capture_ms": t_capture_ms,
                "prep_ms": t_prep_ms,
                "ocr_ms": t_ocr_ms,
                "match_ms": t_match_ms,
            }

            if best_match and best_score >= 50:
                clean_ans = (
                    re.sub(
                        r"^(?:Ans|Answer)\s*[\:\.-]?\s*|^[A-Da-d1-4][\.\)]\s+",
                        "",
                        matched_answer,
                        flags=re.IGNORECASE,
                    ).strip()
                    or matched_answer.strip()
                )

                lang_tag = f" [{matched_lang}]" if matched_lang != selected_lang else ""
                self.scan_queue.put(
                    (
                        "display",
                        (
                            f"{best_match} ({best_score:.0f}%){lang_tag}",
                            clean_ans,
                            best_match,
                            matched_img_path,
                            telemetry,
                        ),
                    )
                )
            elif captured_text:
                self.scan_queue.put(
                    (
                        "display",
                        (
                            f"Scanned: {captured_text}",
                            "No matching question found.",
                            captured_text,
                            "",
                            telemetry,
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
                            telemetry,
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
                if msg_type == "loading":
                    self.lbl_status.config(
                        text="Status: ⏳ Executing OCR & benchmarking..."
                    )
                    self.overlay.show_loading()
                elif msg_type == "display":
                    q_text, a_text, raw_q, img_p, telemetry = payload
                    total_ms = telemetry.get("total_ms", 0) if telemetry else 0
                    self.lbl_status.config(
                        text=f"Status: Scan finished in {total_ms:.0f}ms"
                    )
                    self.overlay.update_display(q_text, a_text, raw_q, img_p, telemetry)
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
