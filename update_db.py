import hashlib
import json
import os
import re
import tempfile
import time
from datetime import datetime, timedelta
from typing import Callable, Optional
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

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


def format_duration(seconds: float) -> str:
    """Formats raw seconds into human-readable duration string (e.g. '02m 15s' or '01h 04m 12s')."""
    total_sec = max(0, int(round(seconds)))
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    secs = total_sec % 60

    if hours > 0:
        return f"{hours:02d}h {minutes:02d}m {secs:02d}s"
    return f"{minutes:02d}m {secs:02d}s"


def is_invalid_answer(answer_str: str) -> bool:
    """Checks if an answer string contains leftover UI prompt text or invalid artifacts."""
    if not answer_str or len(answer_str.strip()) <= 1:
        return True

    clean = answer_str.strip().lower()
    for pattern in INVALID_ANSWER_PATTERNS:
        if re.search(pattern, clean, re.IGNORECASE):
            return True
    return False


def fetch_multilingual_database(
    log_fn: Optional[Callable[[str], None]] = None,
    progress_fn: Optional[Callable[[dict], None]] = None,
) -> bool:
    """Executes multi-language web extraction with real-time runtime tracking and telemetry callbacks."""

    def log(msg: str):
        if log_fn:
            log_fn(msg)
        print(msg)

    def emit_progress(running_sec: float, completed: int, total: int):
        if not progress_fn:
            return
        remaining = total - completed
        avg_sec = running_sec / max(1, completed)
        eta_sec = avg_sec * remaining
        finish_dt = datetime.now() + timedelta(seconds=eta_sec)

        progress_fn(
            {
                "running_time": format_duration(running_sec),
                "remaining_tasks": remaining,
                "completed_tasks": completed,
                "total_tasks": total,
                "percent": int((completed / total) * 100),
                "eta_duration": (
                    format_duration(eta_sec) if remaining > 0 else "00m 00s"
                ),
                "eta_clock": (
                    finish_dt.strftime("%I:%M:%S %p") if remaining > 0 else "Complete"
                ),
            }
        )

    base_url = "https://roworlddb.com/sea/study/"
    all_db = {}

    total_tasks = len(SUPPORTED_LANGUAGES) * len(CATEGORIES)
    completed_tasks = 0
    global_start_time = time.perf_counter()

    log("==================================================")
    log(f"🚀 Starting Database Scrape ({total_tasks} Total Categories)")
    log(f"⏰ Start Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log("==================================================")

    try:
        with sync_playwright() as p:
            log("🌐 Launching Chromium Browser (Headless Mode)...")
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={"width": 1280, "height": 1000})
                page = context.new_page()
                page.set_default_navigation_timeout(35000)

                for lang in SUPPORTED_LANGUAGES:
                    lang_code = lang["code"]
                    lang_name = lang["name"]
                    all_db[lang_name] = {}

                    log("\n--------------------------------------------------")
                    log(f"🌐 Language Route: {lang_name} [{lang_code}]")
                    log("--------------------------------------------------")

                    for cat in CATEGORIES:
                        cat_id = cat["id"]
                        cat_name = cat["name"]
                        event_url = (
                            f"{base_url}?lang={lang_code}#event={cat_id}&reveal=1"
                        )
                        task_start_time = time.perf_counter()
                        remaining_categories = total_tasks - completed_tasks

                        log(
                            f"\n📂 [{completed_tasks + 1}/{total_tasks}] Category: '{cat_name}' ({remaining_categories} remaining)"
                        )
                        log(f"   ↳ URL: {event_url}")

                        img_dir = os.path.join("qa_images", lang_code, cat_id)
                        os.makedirs(img_dir, exist_ok=True)

                        # Auto-retry navigation loop
                        nav_success = False
                        for attempt in range(1, 4):
                            try:
                                log(
                                    f"   [Step 1/5] Navigating to route (Attempt {attempt}/3)..."
                                )
                                page.goto(
                                    event_url,
                                    wait_until="domcontentloaded",
                                    timeout=30000,
                                )
                                page.wait_for_timeout(1500)
                                nav_success = True
                                break
                            except Exception as nav_err:
                                log(
                                    f"   ⚠️ Navigation attempt {attempt} failed: {nav_err}"
                                )
                                if attempt < 3:
                                    time.sleep(2)

                        if not nav_success:
                            log(f"   ❌ Navigation failed for '{cat_name}'. Skipping.")
                            all_db[lang_name][cat_name] = []
                            completed_tasks += 1
                            emit_progress(
                                time.perf_counter() - global_start_time,
                                completed_tasks,
                                total_tasks,
                            )
                            continue

                        log("   [Step 2/5] Synchronizing SPA dropdown state...")
                        page.evaluate(
                            f"""
                            () => {{
                                const selects = Array.from(document.querySelectorAll('select'));
                                for (const s of selects) {{
                                    const opt = Array.from(s.options).find(o => o.value === '{cat_id}' || o.value.includes('{cat_id}'));
                                    if (opt) {{
                                        s.value = opt.value;
                                        s.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                        s.dispatchEvent(new Event('change', {{ bubbles: true }}));
                                        s.dispatchEvent(new Event('blur', {{ bubbles: true }}));
                                        break;
                                    }}
                                }}
                            }}
                        """
                        )
                        page.wait_for_timeout(1500)
                        page.evaluate("window.scrollTo(0, 0);")
                        page.wait_for_timeout(500)

                        log(
                            "   [Step 3/5] Streaming extraction with real-time telemetry..."
                        )
                        accumulated_qa = {}
                        saved_screenshots = 0
                        scroll_pass = 0
                        at_bottom = False
                        last_y = -1

                        while scroll_pass < 120 and not at_bottom:
                            scroll_pass += 1

                            # Unmask visible answer overlays
                            page.evaluate(
                                """
                                () => {
                                    document.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                                        if (!cb.checked) { cb.click(); }
                                    });
                                    document.querySelectorAll('div, button, span, a, p').forEach(d => {
                                        const txt = (d.innerText || d.textContent || '').trim().toLowerCase();
                                        if (
                                            txt === '???' || 
                                            txt.includes('แสดงคำตอบ') || 
                                            txt.includes('click') || 
                                            txt.includes('show') || 
                                            txt.includes('reveal') || 
                                            txt.includes('点击') || 
                                            txt.includes('显示') || 
                                            txt.includes('คลิก') || 
                                            txt.includes('lihat')
                                        ) {
                                            try { d.click(); } catch(e) {}
                                        }
                                    });
                                }
                            """
                            )

                            # Stream-extract cards currently mounted in the viewport
                            current_batch = page.evaluate(
                                r"""
                            () => {
                                const results = [];
                                const cleanText = (str) => {
                                    if (!str) return '';
                                    return str
                                        .replace(/[\uE000-\uF8FF\u2700-\u27BF\u2600-\u26FF✓✔✅]/g, '')
                                        .replace(/^(?:Ans|Answer|Option)\s*[\:\.-]?\s*/i, '')
                                        .replace(/^\d+[\.\:\)\s]+\s*/, '')
                                        .trim();
                                };

                                const candidates = Array.from(document.querySelectorAll('*')).filter(el => {
                                    const txt = (el.innerText || '').trim();
                                    const lines = txt.split('\n').map(l => l.trim()).filter(l => l.length > 0);
                                    return /(?:Q\s*\.?\s*\d+|第?\s*\d+\s*[题條\.、\:\-]|^\s*\d+[\.\、\:\-\s])/i.test(txt) && lines.length >= 2;
                                });

                                const qMap = new Map();
                                candidates.forEach(el => {
                                    const txt = (el.innerText || '').trim();
                                    const match = txt.match(/Q\s*(\d+)/i) || txt.match(/(?:第?\s*(\d+)\s*[题條\.、\:\-]|^\s*(\d+)[\.\、\:\-\s])/i);
                                    if (!match) return;

                                    const numStr = match[1] || match[2];
                                    if (!numStr) return;
                                    const qKey = 'Q' + parseInt(numStr, 10);

                                    if (!qMap.has(qKey) || el.querySelectorAll('*').length < qMap.get(qKey).querySelectorAll('*').length) {
                                        qMap.set(qKey, el);
                                    }
                                });

                                let passIdx = 0;
                                qMap.forEach((card, qKey) => {
                                    const rawText = card.innerText || '';
                                    const lines = rawText.split('\n').map(l => l.trim()).filter(l => l.length > 0);

                                    let qLineIdx = lines.findIndex(l => /(?:Q\s*\.?\s*\d+|第?\s*\d+\s*[题條\.、\:\-]|^\s*\d+[\.\、\:\-\s])/i.test(l));
                                    let question = "";
                                    let startIdx = 1;

                                    if (qLineIdx !== -1) {
                                        question = lines[qLineIdx]
                                            .replace(/^.*?Q\s*\.?\s*\d+[\.\s\:]*/i, '')
                                            .replace(/^(?:第?\s*\d+\s*[题條\.、\:\-]|^\s*\d+[\.\、\:\-\s])\s*/i, '')
                                            .trim();
                                        startIdx = qLineIdx + 1;
                                        if (!question && startIdx < lines.length) {
                                            question = lines[startIdx];
                                            startIdx++;
                                        }
                                    }

                                    if (!question) {
                                        question = lines[0].trim();
                                        startIdx = 1;
                                    }

                                    question = question.trim();
                                    if (question.length < 2) return;

                                    let answer = "";
                                    const childElems = Array.from(card.querySelectorAll('*'));
                                    for (const el of childElems) {
                                        const style = window.getComputedStyle(el);
                                        const color = style.color || '';
                                        const classStr = (el.className || '').toString();

                                        let isG = false;
                                        if (classStr && (
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

                                        if (isG) {
                                            let candidate = cleanText(el.innerText || el.textContent || '');
                                            if (candidate && candidate !== question && !/Q\s*\d+/i.test(candidate) && candidate.length > 1) {
                                                answer = candidate;
                                                break;
                                            }
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
                                            .filter(l => l && l !== question && !/^\d+$/.test(l) && l.length > 1);
                                        if (candidates.length > 0) {
                                            answer = candidates[0];
                                        }
                                    }

                                    if (!answer) {
                                        answer = "See Proof Image / คลิกเพื่อดูเฉลย";
                                    }

                                    const cardId = 'stream-card-' + qKey + '-' + passIdx;
                                    card.setAttribute('data-qa-index', cardId);
                                    results.push({ qKey, index: cardId, question, answer });
                                    passIdx++;
                                });

                                return results;
                            }
                            """
                            )

                            # Accumulate batch & save proof images
                            for item in current_batch:
                                q_key = item["qKey"]
                                q_text = item["question"]
                                a_text = item["answer"]
                                idx_tag = item["index"]

                                if (
                                    q_key not in accumulated_qa
                                    or accumulated_qa[q_key]["answer"]
                                    == "See Proof Image / คลิกเพื่อดูเฉลย"
                                ):
                                    q_hash = hashlib.md5(
                                        q_text.encode("utf-8")
                                    ).hexdigest()[:10]
                                    img_filename = f"q_{q_hash}.png"
                                    rel_img_path = os.path.join(img_dir, img_filename)

                                    try:
                                        loc = page.locator(
                                            f'[data-qa-index="{idx_tag}"]'
                                        )
                                        if loc.count() > 0:
                                            loc.first.scroll_into_view_if_needed(
                                                timeout=1000
                                            )
                                            loc.first.screenshot(
                                                path=rel_img_path, timeout=1500
                                            )
                                            saved_screenshots += 1
                                        else:
                                            rel_img_path = ""
                                    except Exception:
                                        rel_img_path = ""

                                    accumulated_qa[q_key] = {
                                        "qKey": q_key,
                                        "question": q_text,
                                        "answer": a_text,
                                        "image_path": rel_img_path,
                                    }

                            page.evaluate("window.scrollBy(0, 500);")
                            page.wait_for_timeout(200)

                            pos = page.evaluate(
                                """
                                () => {
                                    const scrollY = window.scrollY || window.pageYOffset || 0;
                                    const innerH = window.innerHeight || 0;
                                    const totalH = document.body.scrollHeight || 0;
                                    const reached = (scrollY + innerH) >= (totalH - 50);
                                    return { scrollY, totalH, reached };
                                }
                            """
                            )

                            current_elapsed = time.perf_counter() - global_start_time
                            log(
                                f"   ↳ Pass {scroll_pass:02d}: Pos = {pos['scrollY'] + 1000}px / {pos['totalH']}px | "
                                f"Streamed = {len(accumulated_qa)} items | Running Time = {format_duration(current_elapsed)}"
                            )
                            emit_progress(current_elapsed, completed_tasks, total_tasks)

                            if (pos["reached"] or pos["scrollY"] == last_y) and pos[
                                "totalH"
                            ] > 1200:
                                at_bottom = True

                            last_y = pos["scrollY"]

                        final_list = list(accumulated_qa.values())
                        all_db[lang_name][cat_name] = final_list

                        task_duration = time.perf_counter() - task_start_time
                        completed_tasks += 1
                        total_elapsed = time.perf_counter() - global_start_time

                        emit_progress(total_elapsed, completed_tasks, total_tasks)

                        log(
                            f"   ✅ Category '{cat_name}' Complete in {format_duration(task_duration)}"
                        )
                        log(
                            f"   📊 Yield: {len(final_list)} items extracted | {saved_screenshots} screenshots saved"
                        )

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
                    img_p = entry.get("image_path", "")

                    a = re.sub(
                        r"^(?:Ans\s*:\s*|\d+[\.\:\)]\s*|[A-Da-d][\.\:\)]\s*)", "", a
                    ).strip()

                    if a and q.lower() != a.lower():
                        clean_list.append(
                            {"question": q, "answer": a, "image_path": img_p}
                        )
                        total_entries += 1
                sanitized_db[lang_key][cat_key] = clean_list

        global_total_time = time.perf_counter() - global_start_time
        log(
            f"✅ Sanitization complete: {total_entries} verified entries across all categories."
        )

        target_path = "qa_database.json"
        dir_name = os.path.dirname(os.path.abspath(target_path)) or "."
        temp_fd, temp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        file_written = False
        try:
            with os.fdopen(temp_fd, "w", encoding="utf-8") as tf:
                json.dump(sanitized_db, tf, ensure_ascii=False, indent=2)
            file_written = True
            os.replace(temp_path, target_path)
            log("==================================================")
            log("🎉 Database and proof images update complete!")
            log(f"⏱️ Total Running Time: {format_duration(global_total_time)}")
            log(f"🏁 Finished at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            log("==================================================")
            return True
        except Exception as write_err:
            log(f"❌ Failed to write JSON database: {write_err}")
            return False
        finally:
            if not file_written:
                try:
                    os.close(temp_fd)
                except OSError:
                    pass
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    except Exception as e:
        log(f"\n❌ Scraping exception: {e}")
        return False


if __name__ == "__main__":
    fetch_multilingual_database()
