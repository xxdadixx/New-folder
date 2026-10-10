import asyncio
import hashlib
import json
import os
import re
import tempfile
import time
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional
from playwright.async_api import (
    async_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

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

MAX_CONCURRENT_WORKERS = 6  # 6 Parallel workers to process all heavy tasks at t=0


def format_duration(seconds: float) -> str:
    """Formats raw seconds into human-readable duration string."""
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


async def _async_fetch_multilingual_database(
    log_fn: Optional[Callable[[str], None]] = None,
    progress_fn: Optional[Callable[[dict], None]] = None,
) -> bool:
    """Sub-60s multi-worker priority scraper with accurate atomic task logging."""

    def log(msg: str):
        if log_fn:
            log_fn(msg)
        print(msg)

    total_tasks = len(SUPPORTED_LANGUAGES) * len(CATEGORIES)
    global_start_time = time.perf_counter()
    telemetry_lock = asyncio.Lock()

    completed_task_count = 0
    started_task_count = 0  # Atomic tracker for active execution order
    task_progress_tracker: Dict[str, float] = {}

    async def emit_progress():
        if not progress_fn:
            return
        async with telemetry_lock:
            running_sec = time.perf_counter() - global_start_time
            fractional_completed = completed_task_count + sum(
                task_progress_tracker.values()
            )
            fractional_completed = max(
                0.01, min(float(fractional_completed), float(total_tasks))
            )

            percent = int((fractional_completed / total_tasks) * 100)
            remaining_tasks_display = max(0, total_tasks - int(fractional_completed))

            velocity_sec_per_task = running_sec / fractional_completed
            remaining_fraction = max(0.0, total_tasks - fractional_completed)
            eta_sec = velocity_sec_per_task * remaining_fraction

            finish_dt = datetime.now() + timedelta(seconds=eta_sec)

            progress_fn(
                {
                    "running_time": format_duration(running_sec),
                    "remaining_tasks": remaining_tasks_display,
                    "completed_tasks": int(fractional_completed),
                    "total_tasks": total_tasks,
                    "percent": min(100, percent),
                    "eta_seconds": eta_sec,
                    "eta_duration": (
                        format_duration(eta_sec)
                        if remaining_fraction > 0.05
                        else "00m 00s"
                    ),
                    "eta_clock": (
                        finish_dt.strftime("%I:%M:%S %p")
                        if remaining_fraction > 0.05
                        else "Complete"
                    ),
                }
            )

    base_url = "https://roworlddb.com/sea/study/"
    all_db: Dict[str, Dict[str, List[dict]]] = {
        lang["name"]: {} for lang in SUPPORTED_LANGUAGES
    }

    log("==================================================")
    log(
        f"🚀 High-Speed Priority Scrape ({total_tasks} Tasks | {MAX_CONCURRENT_WORKERS} Workers)"
    )
    log(f"⏰ Start Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log("==================================================")

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_WORKERS)

    async def scrape_category_task(browser, lang: dict, cat: dict):
        nonlocal completed_task_count, started_task_count
        lang_code = lang["code"]
        lang_name = lang["name"]
        cat_id = cat["id"]
        cat_name = cat["name"]
        task_key = f"{lang_code}_{cat_id}"

        event_url = f"{base_url}?lang={lang_code}#event={cat_id}&reveal=1"
        img_dir = os.path.join("qa_images", lang_code, cat_id)
        os.makedirs(img_dir, exist_ok=True)

        async with semaphore:
            async with telemetry_lock:
                started_task_count += 1
                current_start_num = started_task_count

            task_start_time = time.perf_counter()
            log(
                f"📂 [Task {current_start_num}/{total_tasks}] Starting '{cat_name}' [{lang_name}]"
            )

            context = await browser.new_context(
                viewport={"width": 1280, "height": 1000}
            )
            page = await context.new_page()
            page.set_default_navigation_timeout(15000)

            await page.route(
                "**/*.{mp4,webm,avi,woff,woff2,ttf,otf,analytics,google-analytics,doubleclick,facebook}*",
                lambda route: route.abort(),
            )

            nav_success = False
            for attempt in range(1, 4):
                try:
                    await page.goto(
                        event_url, wait_until="domcontentloaded", timeout=12000
                    )
                    try:
                        await page.wait_for_selector(
                            ".card, article, tr, li, [class*='question']",
                            timeout=2500,
                        )
                    except Exception:
                        pass
                    nav_success = True
                    break
                except Exception:
                    if attempt < 3:
                        await asyncio.sleep(0.5)

            if not nav_success:
                log(f"❌ Navigation failed for '{cat_name}' [{lang_name}]. Skipping.")
                all_db[lang_name][cat_name] = []
                async with telemetry_lock:
                    completed_task_count += 1
                    task_progress_tracker[task_key] = 0.0
                await emit_progress()
                await context.close()
                return

            await page.evaluate(
                f"""
                () => {{
                    const selects = Array.from(document.querySelectorAll('select'));
                    for (const s of selects) {{
                        const opt = Array.from(s.options).find(o => o.value === '{cat_id}' || o.value.includes('{cat_id}'));
                        if (opt) {{
                            s.value = opt.value;
                            s.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            s.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            break;
                        }}
                    }}
                }}
            """
            )
            await asyncio.sleep(0.15)

            accumulated_qa = {}
            saved_screenshots = 0
            scroll_pass = 0
            unchanged_passes = 0
            last_y = -1
            max_passes = 35

            while scroll_pass < max_passes and unchanged_passes < 3:
                scroll_pass += 1

                await page.evaluate(
                    """
                    () => {
                        document.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                            if (!cb.checked) { cb.click(); }
                        });
                        document.querySelectorAll('div, button, span, a, p').forEach(d => {
                            const txt = (d.innerText || d.textContent || '').trim().toLowerCase();
                            if (txt.includes('hide') || txt.includes('ซ่อน') || txt.includes('隐藏')) return;
                            if (
                                txt === '???' || 
                                txt.includes('แสดงคำตอบ') || 
                                txt.includes('show answer') || 
                                txt.includes('click to show') || 
                                txt.includes('点击显示')
                            ) {
                                try { d.click(); } catch(e) {}
                            }
                        });
                    }
                """
                )

                current_batch = await page.evaluate(
                    r"""
                () => {
                    const results = [];
                    const invalidPatterns = [
                        /คลิกเพื่อ/i, /ซ่อน/i, /แสดงคำตอบ/i, /点击隐藏/i, /点击显示/i,
                        /click\s*to/i, /hide\s*answer/i, /show\s*answer/i, /\?\?\?/
                    ];

                    const isInvalid = (str) => {
                        if (!str || str.trim().length <= 1) return true;
                        return invalidPatterns.some(pat => pat.test(str));
                    };

                    const cleanText = (str) => {
                        if (!str) return '';
                        return str
                            .replace(/[\uE000-\uF8FF\u2700-\u27BF\u2600-\u26FF✓✔✅]/g, '')
                            .replace(/^(?:Ans|Answer|Option|เฉลย|คำตอบ)\s*[\:\.-]?\s*/i, '')
                            .replace(/^\d+[\.\:\)\s]+\s*/, '')
                            .trim();
                    };

                    let cardElements = Array.from(document.querySelectorAll(
                        '.card, .q-card, .qa-card, .question-card, [class*="card"], [class*="item"], [class*="question"], article, tr, li'
                    ));

                    if (cardElements.length === 0) {
                        cardElements = Array.from(document.querySelectorAll('div')).filter(el => {
                            const txt = (el.innerText || '').trim();
                            const lines = txt.split('\n').map(l => l.trim()).filter(l => l.length > 0);
                            return lines.length >= 2 && lines.length <= 20;
                        });
                    }

                    let cardIndex = 0;
                    cardElements.forEach(card => {
                        const rawText = (card.innerText || '').trim();
                        if (!rawText || rawText.length < 4) return;

                        const lines = rawText.split('\n').map(l => l.trim()).filter(l => l.length > 0);
                        if (lines.length < 2) return;

                        let question = "";
                        let startIdx = 1;

                        let qLineIdx = lines.findIndex(l => 
                            /(?:Q\s*\.?\s*\d+|ข้อ\s*\d+|第?\s*\d+\s*[题條\.、\:\-]|^\s*\d+[\.\、\:\-\s]|\?|อะไร|คือ|ข้อใด)/i.test(l)
                        );

                        if (qLineIdx !== -1) {
                            question = lines[qLineIdx]
                                .replace(/^.*?Q\s*\.?\s*\d+[\.\s\:]*/i, '')
                                .replace(/^(?:ข้อ\s*\d+|第?\s*\d+\s*[题條\.、\:\-]|^\s*\d+[\.\、\:\-\s])\s*/i, '')
                                .trim();
                            startIdx = qLineIdx + 1;
                            if (!question && startIdx < lines.length) {
                                question = lines[startIdx];
                                startIdx++;
                            }
                        }

                        if (!question) {
                            question = lines[0].replace(/^\d+[\.\:\)\s]+\s*/, '').trim();
                            startIdx = 1;
                        }

                        if (question.length < 2 || isInvalid(question)) return;

                        let answer = "";
                        const childElems = Array.from(card.querySelectorAll('*'));
                        for (const el of childElems) {
                            const style = window.getComputedStyle(el);
                            const color = style.color || '';
                            const classStr = (el.className || '').toString();

                            let isGreen = false;
                            if (classStr && (
                                classStr.includes('green') || classStr.includes('emerald') ||
                                classStr.includes('teal') || classStr.includes('success') || 
                                classStr.includes('correct') || classStr.includes('ans')
                            )) {
                                isGreen = true;
                            } else if (color.startsWith('rgb')) {
                                const rgb = color.match(/\d+/g);
                                if (rgb && rgb.length >= 3) {
                                    const r = parseInt(rgb[0]), g = parseInt(rgb[1]), b = parseInt(rgb[2]);
                                    if (g > 120 && g > r * 1.15 && g > b * 1.15) isGreen = true;
                                }
                            }

                            if (isGreen) {
                                let candidate = cleanText(el.innerText || el.textContent || '');
                                if (candidate && candidate !== question && !isInvalid(candidate)) {
                                    answer = candidate;
                                    break;
                                }
                            }
                        }

                        if (!answer) {
                            for (let i = startIdx; i < lines.length; i++) {
                                const cleanL = cleanText(lines[i]);
                                if (!isInvalid(cleanL) && cleanL !== question) {
                                    if (['จริง', 'O', 'True', '正确', 'Benar'].includes(cleanL)) {
                                        answer = 'จริง / True (O)';
                                        break;
                                    } else if (['เท็จ', 'X', 'False', '錯誤', 'Salah'].includes(cleanL)) {
                                        answer = 'เท็จ / False (X)';
                                        break;
                                    } else if (cleanL.length >= 1) {
                                        answer = cleanL;
                                        break;
                                    }
                                }
                            }
                        }

                        if (!answer || isInvalid(answer)) {
                            answer = "See Proof Image / คลิกเพื่อดูเฉลย";
                        }

                        const qKey = question.toLowerCase().replace(/\s+/g, '');
                        const cardId = 'stream-card-' + cardIndex;
                        card.setAttribute('data-qa-index', cardId);
                        results.push({ qKey, index: cardId, question, answer });
                        cardIndex++;
                    });

                    return results;
                }
                """
                )

                async def capture_single_screenshot(item_data):
                    idx_tag, rel_p = item_data
                    try:
                        loc = page.locator(f'[data-qa-index="{idx_tag}"]')
                        if await loc.count() > 0:
                            await loc.first.screenshot(path=rel_p, timeout=500)
                            return True
                    except Exception:
                        pass
                    return False

                screenshot_promises = []
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
                        q_hash = hashlib.md5(q_text.encode("utf-8")).hexdigest()[:10]
                        img_filename = f"q_{q_hash}.png"
                        rel_img_path = os.path.join(img_dir, img_filename)

                        screenshot_promises.append(
                            capture_single_screenshot((idx_tag, rel_img_path))
                        )

                        accumulated_qa[q_key] = {
                            "qKey": q_key,
                            "question": q_text,
                            "answer": a_text,
                            "image_path": rel_img_path,
                        }

                if screenshot_promises:
                    shot_results = await asyncio.gather(
                        *screenshot_promises, return_exceptions=True
                    )
                    saved_screenshots += sum(
                        1 for r in shot_results if isinstance(r, bool) and r is True
                    )

                await page.evaluate("window.scrollBy(0, 1500);")
                await asyncio.sleep(0.06)

                pos = await page.evaluate(
                    """
                    () => {
                        const scrollY = window.scrollY || window.pageYOffset || 0;
                        const totalH = document.body.scrollHeight || 0;
                        return { scrollY, totalH };
                    }
                """
                )

                if pos["scrollY"] == last_y:
                    unchanged_passes += 1
                else:
                    unchanged_passes = 0

                last_y = pos["scrollY"]

                async with telemetry_lock:
                    task_progress_tracker[task_key] = min(
                        0.95, scroll_pass / max_passes
                    )
                await emit_progress()

            final_list = list(accumulated_qa.values())
            all_db[lang_name][cat_name] = final_list

            task_duration = time.perf_counter() - task_start_time
            async with telemetry_lock:
                completed_task_count += 1
                curr_done = completed_task_count
                task_progress_tracker[task_key] = 0.0
            await emit_progress()

            log(
                f"   ✅ [Done {curr_done}/{total_tasks}] '{cat_name}' [{lang_name}] in {format_duration(task_duration)} "
                f"({len(final_list)} items | {saved_screenshots} screenshots)"
            )
            await context.close()

    try:
        async with async_playwright() as p:
            log("🌐 Launching Chromium Async Engine...")
            browser = await p.chromium.launch(headless=True)
            try:

                def get_task_priority(item):
                    lang, cat = item
                    priority = 0
                    if cat["id"] == "scholar-exam":
                        priority += 100
                    if cat["id"] == "lucky-rabbit" and lang["code"] == "zh-CN":
                        priority += 80
                    if cat["id"] == "lucky-rabbit":
                        priority += 40
                    if cat["id"] == "guild-banquet":
                        priority += 20
                    return priority

                task_pairs = []
                for cat in CATEGORIES:
                    for lang in SUPPORTED_LANGUAGES:
                        task_pairs.append((lang, cat))

                task_pairs.sort(key=get_task_priority, reverse=True)

                tasks = []
                for lang, cat in task_pairs:
                    tasks.append(scrape_category_task(browser, lang, cat))

                await asyncio.gather(*tasks)
            finally:
                await browser.close()

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


def fetch_multilingual_database(
    log_fn: Optional[Callable[[str], None]] = None,
    progress_fn: Optional[Callable[[dict], None]] = None,
) -> bool:
    """Synchronous entry point that executes the asynchronous scraper event loop."""
    return asyncio.run(_async_fetch_multilingual_database(log_fn, progress_fn))


if __name__ == "__main__":
    fetch_multilingual_database()
