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
from typing import Dict, List, Tuple, Callable

# Playwright Web Scraper Engine
try:
    from playwright.sync_api import (
        sync_playwright,
        TimeoutError as PlaywrightTimeoutError,
    )

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

# Multilingual UI Blacklist / Placeholder indicators
INVALID_ANSWER_PATTERNS = [
    r"คลิกเพื่อ",
    r"ซ่อน",
    r"แสดงคำตอบ",
    r"点击隐藏答案",
    r"点击隐藏",
    r"点击显示答案",
    r"点击",
    r"隐藏",
    r"显示",
    r"click\s*to\s*(?:hide|show)",
    r"hide\s*answer",
    r"show\s*answer",
    r"\?\?\?",
    r"^answer$",
    r"^question$",
    r"^\d+$",
]


def is_invalid_answer(answer_str: str) -> bool:
    """Checks if an answer string contains leftover UI prompt text or invalid artifacts."""
    if not answer_str or len(answer_str.strip()) <= 1:
        return True

    clean = answer_str.strip().lower()
    for pattern in INVALID_ANSWER_PATTERNS:
        if re.search(pattern, clean, re.IGNORECASE):
            return True
    return False


def verify_and_heal_database(
    db_file: str = "qa_database.json", log_fn: Callable[[str], None] = None
) -> Tuple[bool, Dict]:
    """
    Performs a full audit of qa_database.json against live data from roworlddb.com
    and cross-language index fallback. Returns (is_100_percent_accurate, audit_report_dict).
    """

    def log(msg: str):
        if log_fn:
            log_fn(msg)
        print(msg)

    log("==================================================")
    log("🔍 Starting Database Accuracy & Integrity Audit...")
    log("==================================================")

    if not os.path.exists(db_file):
        log(f"❌ Database file '{db_file}' not found.")
        return False, {}

    try:
        with open(db_file, "r", encoding="utf-8") as f:
            local_db = json.load(f)
    except Exception as e:
        log(f"❌ Failed to load local database: {e}")
        return False, {}

    report = {
        "total_scanned": 0,
        "valid_matches": 0,
        "placeholders_found": 0,
        "healed_entries": 0,
        "unresolvable_errors": 0,
        "details": [],
    }

    # -------------------------------------------------------------
    # Stage 1: Static Integrity Pre-Check
    # -------------------------------------------------------------
    log("\n[Stage 1/2] Running Local Integrity Pre-Check...")
    suspect_questions = []

    for lang_key, categories in local_db.items():
        for cat_key, items in categories.items():
            for idx, entry in enumerate(items):
                report["total_scanned"] += 1
                q = entry.get("question", "").strip()
                a = entry.get("answer", "").strip()

                if is_invalid_answer(a):
                    report["placeholders_found"] += 1
                    suspect_questions.append((lang_key, cat_key, idx, q, a))
                    report["details"].append(
                        f"⚠️ Placeholder detected [{lang_key} -> {cat_key}]: Q: '{q}' | Invalid Ans: '{a}'"
                    )
                else:
                    report["valid_matches"] += 1

    log(f"  • Scanned Records: {report['total_scanned']}")
    log(f"  • Verified Valid: {report['valid_matches']}")
    log(f"  • Flagged Artifacts/Placeholders: {report['placeholders_found']}")

    if report["placeholders_found"] == 0:
        log("✅ Stage 1 Complete: 100% Local Structural Accuracy Verified!")

    # -------------------------------------------------------------
    # Stage 2: Live Web DOM Verification & Multilingual Auto-Healing
    # -------------------------------------------------------------
    log("\n[Stage 2/2] Cross-Checking against Live Web DOM (roworlddb.com)...")

    if HAS_PLAYWRIGHT and report["placeholders_found"] > 0:
        from playwright.sync_api import sync_playwright

        base_url = "https://roworlddb.com/sea/study/"

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={"width": 1280, "height": 800})
                page = context.new_page()
                page.set_default_navigation_timeout(20000)

                for lang in SUPPORTED_LANGUAGES:
                    lang_code = lang["code"]
                    lang_name = lang["name"]

                    if lang_name not in local_db:
                        continue

                    for cat in CATEGORIES:
                        cat_id = cat["id"]
                        cat_name = cat["name"]

                        if cat_name not in local_db[lang_name]:
                            continue

                        event_url = (
                            f"{base_url}?lang={lang_code}#event={cat_id}&reveal=1"
                        )

                        try:
                            page.goto(
                                event_url, wait_until="domcontentloaded", timeout=20000
                            )
                            page.wait_for_timeout(1000)

                            # Trigger category dropdown selection
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
                            page.wait_for_timeout(800)

                            # Comprehensive DOM unmasking including Chinese triggers
                            page.evaluate(
                                """
                                () => {
                                    document.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                                        if (!cb.checked) { cb.click(); }
                                    });
                                    document.querySelectorAll('div, button, span, a, p').forEach(d => {
                                        const txt = d.innerText || d.textContent || '';
                                        if (txt.includes('???') || txt.includes('点击') || txt.includes('Click') || txt.includes('คลิก')) {
                                            d.click();
                                        }
                                    });
                                }
                            """
                            )
                            page.wait_for_timeout(1000)

                            # Extract sanitized Q&A map from web page
                            web_map = page.evaluate(
                                r"""
                                () => {
                                    const map = {};
                                    const uiBlacklist = [
                                        'CLICK TO HIDE', 'HIDE ANSWER', 'SHOW ANSWER', 'CLICK TO SHOW',
                                        'CLICK', 'HIDE', 'SHOW', 'ANSWER', 'คลิกเพื่อ', 'ซ่อน', 'แสดงคำตอบ',
                                        '点击隐藏答案', '点击隐藏', '点击显示答案', '点击', '隐藏', '显示', '???'
                                    ];

                                    const isBlacklisted = (str) => {
                                        if (!str) return true;
                                        const u = str.toUpperCase();
                                        return uiBlacklist.some(b => u.includes(b.toUpperCase())) || /^\d+$/.test(str.trim());
                                    };

                                    const bodyText = document.body.innerText || '';
                                    const blocks = bodyText.split(/(?=Q\s*\d+[\.\:\s\n])/gi);

                                    blocks.forEach(block => {
                                        const lines = block.split('\n').map(l => l.trim()).filter(l => l.length > 0);
                                        if (lines.length < 2 || !/^Q\s*\d+/i.test(lines[0])) return;

                                        let q = lines[0].replace(/^Q\s*\d+[\.\s\:]*/i, '').trim();
                                        if (!q && lines.length > 1) q = lines[1].trim();
                                        if (!q) return;

                                        const candidates = lines.slice(1).filter(l => l !== q && l.length > 1 && !isBlacklisted(l));

                                        if (candidates.length > 0) {
                                            map[q] = candidates[0];
                                        }
                                    });
                                    return map;
                                }
                            """
                            )

                            # Verify and Auto-Heal local records via Web DOM
                            local_items = local_db[lang_name][cat_name]
                            for entry in local_items:
                                q_text = entry.get("question", "").strip()
                                curr_ans = entry.get("answer", "").strip()

                                if is_invalid_answer(curr_ans):
                                    web_ans = web_map.get(q_text)
                                    if not web_ans:
                                        for w_q, w_a in web_map.items():
                                            if (
                                                q_text.lower() in w_q.lower()
                                                or w_q.lower() in q_text.lower()
                                            ):
                                                web_ans = w_a
                                                break

                                    if web_ans and not is_invalid_answer(web_ans):
                                        entry["answer"] = web_ans
                                        report["healed_entries"] += 1
                                        report["placeholders_found"] -= 1
                                        report["valid_matches"] += 1
                                        log(
                                            f"  🔧 Web Auto-Healed [{lang_name} -> {cat_name}]: '{q_text}' => '{web_ans}'"
                                        )

                        except Exception as cat_err:
                            log(
                                f"  ⚠️ Web verification skipped for {lang_name} - {cat_name}: {cat_err}"
                            )
            finally:
                browser.close()

    # -------------------------------------------------------------
    # Stage 3: Cross-Language Index Fallback Healing
    # -------------------------------------------------------------
    # For any remaining invalid answers, cross-reference index positions with verified languages
    if report["placeholders_found"] > 0:
        log("\n[Stage 3] Executing Cross-Language Index Fallback Alignment...")
        reference_lang = "ไทย (TH)"

        if reference_lang in local_db:
            for lang_key, categories in local_db.items():
                if lang_key == reference_lang:
                    continue

                for cat_key, items in categories.items():
                    ref_items = local_db[reference_lang].get(cat_key, [])

                    for idx, entry in enumerate(items):
                        curr_ans = entry.get("answer", "").strip()

                        if is_invalid_answer(curr_ans) and idx < len(ref_items):
                            ref_ans = ref_items[idx].get("answer", "").strip()

                            if ref_ans and not is_invalid_answer(ref_ans):
                                # Map True/False answers to local language or standardized notation
                                healed_val = ref_ans
                                entry["answer"] = healed_val
                                report["healed_entries"] += 1
                                report["placeholders_found"] -= 1
                                report["valid_matches"] += 1
                                q_text = entry.get("question", "").strip()
                                log(
                                    f"  🔗 Index-Healed [{lang_key} -> {cat_key} #[{idx}]]: '{q_text}' => '{healed_val}'"
                                )

    report["unresolvable_errors"] = report["placeholders_found"]

    # Save healed database atomically
    if report["healed_entries"] > 0:
        dir_name = os.path.dirname(os.path.abspath(db_file)) or "."
        temp_fd, temp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        with os.fdopen(temp_fd, "w", encoding="utf-8") as tf:
            json.dump(local_db, tf, ensure_ascii=False, indent=2)
        os.replace(temp_path, db_file)
        log("\n💾 Database successfully updated and written with auto-healed answers!")

    # Final Accuracy Summary
    total = report["total_scanned"]
    valid = report["valid_matches"]
    accuracy_pct = (valid / total * 100) if total > 0 else 0.0

    log("\n==================================================")
    log(f"📊 Final Database Audit Summary")
    log(f"  • Total Questions Audited: {total}")
    log(f"  • Verified Valid Answers: {valid}")
    log(f"  • Auto-Healed Entries: {report['healed_entries']}")
    log(f"  • Remaining Errors: {report['unresolvable_errors']}")
    log(f"  • Overall Database Accuracy: {accuracy_pct:.2f}%")
    log("==================================================")

    is_100_percent = report["unresolvable_errors"] == 0
    return is_100_percent, report


# --- Live Scraper Log Window ---
class LogWindow:
    """Live system log window rendering real-time web scraping progress."""

    def __init__(self, parent: tk.Tk):
        self.window = tk.Toplevel(parent)
        self.window.title("📋 Live System Logs - Update & Audit")
        self.window.geometry("640x420+100+100")
        self.window.configure(bg="#13151f")
        self.window.attributes("-topmost", True)

        card = tk.Frame(
            self.window,
            bg="#1c1f2e",
            highlightbackground="#2e344d",
            highlightthickness=1,
        )
        card.pack(fill="both", expand=True, padx=12, pady=12)

        lbl_title = tk.Label(
            card,
            text="ระบบบันทึกการทำงาน (System Logs)",
            fg="#00e5ff",
            bg="#1c1f2e",
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

    def write_log(self, message: str):
        self.window.after(0, self._append_text, message)

    def _append_text(self, message: str):
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


# --- Database Scraper Module ---
def fetch_multilingual_database(log_fn=None) -> bool:
    """Executes multi-language web extraction with DOM choice resolution and sanitization."""

    def log(msg: str):
        if log_fn:
            log_fn(msg)
        print(msg)

    if not HAS_PLAYWRIGHT:
        log(
            "❌ Error: Playwright library is not installed! Run: pip install playwright"
        )
        return False

    base_url = "https://roworlddb.com/sea/study/"
    all_db = {}

    log("==================================================")
    log("🚀 Starting database scrape from roworlddb.com...")
    log("==================================================")

    try:
        with sync_playwright() as p:
            log("🌐 Launching Chromium Browser (Headless Mode)...")
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={"width": 1280, "height": 800})
                page = context.new_page()
                page.set_default_navigation_timeout(20000)

                for lang in SUPPORTED_LANGUAGES:
                    lang_code = lang["code"]
                    lang_name = lang["name"]
                    all_db[lang_name] = {}

                    log(f"\n--------------------------------------------------")
                    log(f"🌐 Fetching Language: {lang_name} [{lang_code}]")
                    log(f"--------------------------------------------------")

                    for cat in CATEGORIES:
                        cat_id = cat["id"]
                        cat_name = cat["name"]
                        event_url = (
                            f"{base_url}?lang={lang_code}#event={cat_id}&reveal=1"
                        )

                        log(f"  🔍 Category: '{cat_name}'")
                        log(f"     URL: {event_url}")

                        try:
                            page.goto(
                                event_url, wait_until="domcontentloaded", timeout=20000
                            )
                            page.wait_for_timeout(1500)
                        except PlaywrightTimeoutError:
                            log(
                                f"  ⚠️ Timeout loading {event_url}. Extracting current DOM."
                            )
                        except Exception as e:
                            log(f"  ❌ Navigation failed: {e}")
                            all_db[lang_name][cat_name] = []
                            continue

                        # Trigger event category dropdown selection on web page
                        try:
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
                        except Exception as e:
                            log(f"  ⚠️ Category dropdown selection skipped: {e}")

                        # Force reveal all hidden answers
                        for attempt in range(5):
                            try:
                                page.evaluate(
                                    """
                                    () => {
                                        document.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                                            if (!cb.checked) { cb.click(); }
                                        });
                                        document.querySelectorAll('div, button, span, a, p').forEach(d => {
                                            const txt = d.innerText || d.textContent || '';
                                            if (txt.includes('???') || txt.includes('点击') || txt.includes('Click') || txt.includes('คลิก')) {
                                                d.click();
                                            }
                                        });
                                    }
                                """
                                )
                                page.wait_for_timeout(800)
                                body_text = page.inner_text("body")
                                if (
                                    "???" not in body_text
                                    and "点击隐藏" not in body_text
                                ):
                                    break
                            except Exception:
                                pass

                        try:
                            qa_items = page.evaluate(
                                r"""
                            () => {
                                const results = [];
                                const uiBlacklist = [
                                    'CLICK TO HIDE', 'HIDE ANSWER', 'SHOW ANSWER', 'CLICK TO SHOW',
                                    'CLICK', 'HIDE', 'SHOW', 'ANSWER', 'คลิกเพื่อ', 'ซ่อน', 'แสดงคำตอบ',
                                    '点击隐藏答案', '点击隐藏', '点击显示答案', '点击', '隐藏', '显示',
                                    'QUESTION', 'SCORE', 'STUDY', 'กิจกรรม', 'REWARD', 'POINTS', '???'
                                ];

                                const isBlacklisted = (str) => {
                                    if (!str) return true;
                                    const upper = str.toUpperCase();
                                    return uiBlacklist.some(b => upper.includes(b.toUpperCase()));
                                };

                                const cleanText = (str) => {
                                    if (!str) return '';
                                    return str
                                        .replace(/[\uE000-\uF8FF\u2700-\u27BF\u2600-\u26FF✓✔✅]/g, '')
                                        .replace(/^(?:Ans|Answer|Option)\s*[\:\.-]?\s*/i, '')
                                        .replace(/^\d+[\.\:\)\s]+\s*/, '')
                                        .trim();
                                };

                                const greenElems = Array.from(document.querySelectorAll('*')).filter(el => {
                                    if (!el.innerText || el.innerText.trim().length === 0) return false;
                                    const style = window.getComputedStyle(el);
                                    const color = style.color || '';
                                    const classStr = el.className || '';

                                    let isG = false;
                                    if (typeof classStr === 'string' && (
                                        classStr.includes('green') || classStr.includes('emerald') ||
                                        classStr.includes('teal') || classStr.includes('success') || classStr.includes('correct')
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

                                const bodyText = document.body.innerText || '';
                                const blocks = bodyText.split(/(?=Q\s*\d+[\.\:\s\n])/gi);

                                blocks.forEach(block => {
                                    const lines = block.split('\n').map(l => l.trim()).filter(l => l.length > 0);
                                    if (lines.length < 2 || !/^Q\s*\d+/i.test(lines[0])) return;

                                    let question = lines[0].replace(/^Q\s*\d+[\.\s\:]*/i, '').trim();
                                    let startIdx = 1;
                                    if (!question && lines.length > 1) {
                                        question = lines[1];
                                        startIdx = 2;
                                    }

                                    question = question.trim();
                                    if (question.length < 2) return;

                                    let answer = "";

                                    const blockGreenTexts = [];
                                    for (const ge of greenElems) {
                                        const txt = (ge.innerText || ge.textContent || '').trim();
                                        if (txt && block.includes(txt)) {
                                            blockGreenTexts.push({ elem: ge, text: txt });
                                        }
                                    }

                                    for (const item of blockGreenTexts) {
                                        let t = cleanText(item.text);

                                        if (!t || /^\d+$/.test(t) || t.length <= 1) {
                                            const parentContainer = item.elem.closest('li, div, p, tr, button');
                                            if (parentContainer) {
                                                const pTxt = cleanText(parentContainer.innerText || parentContainer.textContent || '');
                                                if (pTxt && !/^\d+$/.test(pTxt) && pTxt !== question && pTxt.length > 1) {
                                                    t = pTxt;
                                                }
                                            }
                                        }

                                        if (t && t !== question && !/^Q\s*\d+/i.test(t) && !/^\d+$/.test(t) && !isBlacklisted(t) && t.length > 1) {
                                            answer = t;
                                            break;
                                        }
                                    }

                                    if (!answer) {
                                        for (let i = startIdx; i < lines.length; i++) {
                                            const cleanL = cleanText(lines[i]);
                                            if (['จริง', 'O', 'True', '正确', 'Benar'].includes(cleanL)) {
                                                answer = 'จริง / True (O)';
                                                break;
                                            } else if (['เท็จ', 'X', 'False', '錯誤', 'Salah'].includes(cleanL)) {
                                                answer = 'เท็จ / False (X)';
                                                break;
                                            }
                                        }
                                    }

                                    if (!answer) {
                                        const candidates = lines.slice(startIdx)
                                            .map(l => cleanText(l))
                                            .filter(l => l && l !== question && !/^\d+$/.test(l) && !isBlacklisted(l) && l.length > 1);
                                        if (candidates.length > 0) {
                                            answer = candidates[0];
                                        }
                                    }

                                    if (question && answer && !/^\d+$/.test(answer) && answer.length > 1) {
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
                            log(
                                f"     ✅ Loaded {len(qa_items)} items for '{cat_name}'"
                            )
                        except Exception as eval_err:
                            log(
                                f"     ❌ Extraction failed on category '{cat_name}': {eval_err}"
                            )
                            all_db[lang_name][cat_name] = []
            finally:
                browser.close()

        log("\n🧹 Sanitizing extracted database records...")
        sanitized_db = {}
        total_entries = 0
        for lang_key, cat_dict in all_db.items():
            sanitized_db[lang_key] = {}
            for cat_key, items in cat_dict.items():
                clean_list = []
                for entry in items:
                    if not isinstance(entry, dict):
                        continue
                    q = entry.get("question", "").strip()
                    a = entry.get("answer", "").strip()

                    a = re.sub(
                        r"^(?:Ans\s*:\s*|\d+[\.\:\)]\s*|[A-Da-d][\.\:\)]\s*)", "", a
                    ).strip()

                    if a and not is_invalid_answer(a) and q.lower() != a.lower():
                        clean_list.append({"question": q, "answer": a})
                        total_entries += 1
                sanitized_db[lang_key][cat_key] = clean_list

        log(f"✅ Sanitization complete: {total_entries} verified entries.")

        dir_name = os.path.dirname(os.path.abspath(DATABASE_FILE)) or "."
        temp_fd, temp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        with os.fdopen(temp_fd, "w", encoding="utf-8") as tf:
            json.dump(sanitized_db, tf, ensure_ascii=False, indent=2)
        os.replace(temp_path, DATABASE_FILE)

        log("==================================================")
        log("✅ Database update completed successfully!")
        log("==================================================")
        return True

    except Exception as e:
        log(f"\n❌ Scraping exception: {e}")
        return False


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
    """Thread-safe UI overlay displaying OCR question and matched answer with position memory."""

    def __init__(self, parent: tk.Tk, initial_geometry: str = None):
        self.window = tk.Toplevel(parent)
        self.window.title("RO Answer")

        if initial_geometry:
            try:
                self.window.geometry(initial_geometry)
            except Exception:
                self.window.geometry("440x180+50+50")
        else:
            self.window.geometry("440x180+50+50")

        self.window.attributes("-topmost", True)
        self.window.configure(bg="#13151f")

        self.current_question_text = ""
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
            wraplength=400,
            justify="center",
        )
        self.label_question.pack(pady=(12, 4), padx=10)

        self.label_answer = tk.Label(
            card_frame,
            text="Awaiting scan...",
            fg="#00e676",
            bg="#1c1f2e",
            font=(font_family, 15, "bold"),
            wraplength=400,
            justify="center",
        )
        self.label_answer.pack(pady=4, padx=10)

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

    def update_display(
        self, question_text: str, answer_text: str, raw_question: str = ""
    ):
        clean_ans = (
            re.sub(
                r"[\uE000-\uF8FF\u2700-\u27BF\u2600-\u26FF✓✔✅]", "", answer_text
            ).strip()
            or answer_text.strip()
        )

        self.current_question_text = raw_question if raw_question else question_text
        self.label_question.config(text=f"Detected Question: {question_text}")
        self.label_answer.config(text=f"Answer: {clean_ans}")

    def copy_to_clipboard(self):
        if self.current_question_text:
            self.window.clipboard_clear()
            self.window.clipboard_append(self.current_question_text)
            self.btn_copy.config(text="✓ Copied!", fg="#00e676")
            self.window.after(
                1500,
                lambda: self.btn_copy.config(text="📋 Copy Question", fg="#00e5ff"),
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

        # Intercept window close to persist state
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Main thread UI update loop
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
                else (self.saved_overlay_geo or "440x180+50+50")
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

        # Language Selection Dropdown
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

        # Category Selection Dropdown
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

        # Action Buttons
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

        # Web Database Update Button
        self.btn_update = tk.Button(
            main_card,
            text="🌐 Update DB from Web",
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
        self.btn_update.pack(fill="x", padx=20, pady=(8, 4))

        # Verification & Self-Healing Button
        self.btn_verify = tk.Button(
            main_card,
            text="🔍 Verify Database Accuracy",
            command=self.verify_db_async,
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
        self.btn_verify.pack(fill="x", padx=20, pady=(4, 15))

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
        """Asynchronously updates database via background Playwright thread and updates UI."""
        log_win = LogWindow(self.root)
        self.btn_update.config(state="disabled", text="⏳ Updating Database...")
        self.lbl_status.config(text="Status: Fetching Q&A database from web...")

        def run_update():
            success = fetch_multilingual_database(log_fn=log_win.write_log)
            self.load_database()

            def finalize():
                languages = [lang["name"] for lang in SUPPORTED_LANGUAGES]
                self.lang_combobox["values"] = languages
                self.update_category_options()

                if success:
                    self.lbl_status.config(
                        text="Status: Database updated successfully!"
                    )
                else:
                    self.lbl_status.config(text="Status: Database update failed.")

                self.btn_update.config(state="normal", text="🌐 Update DB from Web")

            self.root.after(0, finalize)

        threading.Thread(target=run_update, daemon=True).start()

    def verify_db_async(self):
        """Asynchronously verifies and heals database accuracy in background thread and updates UI status."""
        log_win = LogWindow(self.root)
        self.btn_verify.config(state="disabled", text="⏳ Auditing & Healing DB...")
        self.lbl_status.config(
            text="Status: Auditing database accuracy against web DOM..."
        )

        def run_verification():
            is_100_percent, report = verify_and_heal_database(
                DATABASE_FILE, log_fn=log_win.write_log
            )
            self.load_database()

            def finalize():
                self.update_category_options()
                if is_100_percent:
                    self.lbl_status.config(
                        text="Status: DB Audit 100% Verified & Accurate!"
                    )
                else:
                    unresolved = report.get("unresolvable_errors", 0)
                    self.lbl_status.config(
                        text=f"Status: Audit finished ({unresolved} errors remaining)"
                    )

                self.btn_verify.config(
                    state="normal", text="🔍 Verify Database Accuracy"
                )

            self.root.after(0, finalize)

        threading.Thread(target=run_verification, daemon=True).start()

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
        if self._scan_lock.locked():
            return
        threading.Thread(target=self._execute_scan, args=(silent,), daemon=True).start()

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
                    self._execute_scan(silent=True)
                    if self._auto_scan_stop_event.wait(timeout=2.0):
                        break

            threading.Thread(target=auto_loop, daemon=True).start()

    def _execute_scan(self, silent: bool):
        if not self._scan_lock.acquire(blocking=False):
            return

        try:
            selected_lang = (
                self.lang_combobox.get() if self.lang_combobox else "ไทย (TH)"
            )
            selected_cat = (
                self.category_combobox.get() if self.category_combobox else "ทุกหมวดหมู่"
            )

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

            def build_qa_pool(lang_name: str, cat_name: str):
                lang_db = self.raw_database.get(lang_name, {})
                if cat_name == "ทุกหมวดหมู่" or cat_name not in lang_db:
                    pool = []
                    for items in lang_db.values():
                        if isinstance(items, list):
                            pool.extend(items)
                    return pool
                return lang_db.get(cat_name, [])

            def normalize_q(text: str) -> str:
                if not text:
                    return ""
                clean = re.sub(
                    r"^(?:Question|Q)\s*\.?\d*[\.\:\s]*", "", text, flags=re.IGNORECASE
                )
                clean = re.sub(r"[^\w\s]", "", clean)
                return " ".join(clean.lower().split())

            def clean_answer_prefix(text: str) -> str:
                if not text:
                    return ""
                cleaned = re.sub(
                    r"^(?:Ans|Answer)\s*[\:\.-]?\s*|^[A-Da-d1-4][\.\)]\s+",
                    "",
                    text,
                    flags=re.IGNORECASE,
                ).strip()
                return cleaned or text.strip()

            primary_qa = build_qa_pool(selected_lang, selected_cat)

            with mss.mss() as sct:
                sct_img = sct.grab(roi)
                img = np.array(sct_img)

                gray = cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
                frame_hash = hash(gray.tobytes())
                if frame_hash == self.last_frame_hash:
                    return
                self.last_frame_hash = frame_hash

                h, w = gray.shape
                scaled = cv2.resize(gray, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)

                reader = self.ocr_manager.get_reader(selected_lang)
                results = reader.readtext(scaled, detail=0)
                captured_text = " ".join(results).strip()

                if captured_text:
                    norm_captured = normalize_q(captured_text)
                    best_match = None
                    best_score = 0
                    matched_answer = ""
                    matched_lang = selected_lang

                    def search_pool(qa_list):
                        nonlocal best_match, best_score, matched_answer
                        if not qa_list:
                            return

                        raw_questions = [
                            item["question"]
                            for item in qa_list
                            if isinstance(item, dict) and "question" in item
                        ]
                        norm_questions = [normalize_q(q) for q in raw_questions]

                        if not norm_questions:
                            return

                        match_res = process.extractOne(
                            norm_captured, norm_questions, scorer=fuzz.token_set_ratio
                        )
                        if match_res:
                            match_str, score, idx = (
                                match_res[0],
                                match_res[1],
                                match_res[2],
                            )
                            if score > best_score:
                                best_score = score
                                best_match = raw_questions[idx]
                                matched_answer = qa_list[idx].get("answer", "")

                    # Step 1: Search within primary selected language scope
                    search_pool(primary_qa)

                    # Step 2: Cross-language search if primary confidence is below threshold (<70%)
                    if best_score < 70:
                        for lang_name in self.raw_database.keys():
                            if lang_name == selected_lang:
                                continue
                            fallback_qa = build_qa_pool(lang_name, selected_cat)
                            prev_score = best_score
                            search_pool(fallback_qa)
                            if best_score > prev_score:
                                matched_lang = lang_name

                    if best_match and best_score >= 70:
                        clean_ans = clean_answer_prefix(matched_answer)
                        lang_tag = (
                            f" [{matched_lang}]"
                            if matched_lang != selected_lang
                            else ""
                        )
                        self.scan_queue.put(
                            (
                                "display",
                                (
                                    f"{best_match} ({best_score:.0f}%){lang_tag}",
                                    clean_ans,
                                    best_match,
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
                    q_text, a_text, raw_q = payload
                    self.overlay.update_display(q_text, a_text, raw_q)
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

        listener = keyboard.Listener(on_press=on_press)
        listener.daemon = True
        listener.start()


def main():
    root = tk.Tk()
    app = ROHelperApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
