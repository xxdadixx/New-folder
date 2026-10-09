import json
import os
import tempfile
import time
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


def fetch_multilingual_database():
    base_url = "https://roworlddb.com/sea/study/"
    print("Connecting to roworlddb.com for Multi-Language Export...")

    all_db = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        page.set_default_navigation_timeout(15000)

        for lang in SUPPORTED_LANGUAGES:
            lang_code = lang["code"]
            lang_name = lang["name"]
            all_db[lang_name] = {}

            print(f"\n==========================================")
            print(f"Fetching Language: {lang_name} ({lang_code})")
            print(f"==========================================")

            for cat in CATEGORIES:
                cat_id = cat["id"]
                cat_name = cat["name"]
                event_url = f"{base_url}?lang={lang_code}#event={cat_id}&reveal=1"

                print(f"URL: {event_url}")
                try:
                    page.goto(event_url, wait_until="domcontentloaded", timeout=15000)
                    page.wait_for_timeout(2000)
                except PlaywrightTimeoutError:
                    print(f"  [Warning] Timeout loading {event_url}. Attempting extraction on current DOM.")
                except Exception as e:
                    print(f"  [Error] Navigation failed for {event_url}: {e}")
                    all_db[lang_name][cat_name] = []
                    continue

                try:
                    page.evaluate("""
                        () => {
                            const check = document.querySelector('input[type="checkbox"]');
                            if (check && !check.checked) { check.click(); }
                        }
                    """)
                    page.wait_for_timeout(1000)
                except Exception as e:
                    print(f"  [Warning] Checkbox reveal click failed: {e}")

                try:
                    qa_items = page.evaluate(r"""
                    () => {
                        const results = [];
                        const allDivs = Array.from(document.querySelectorAll('div, article, section, li'));

                        const qCards = allDivs.filter(el => {
                            const txt = (el.innerText || '').trim();
                            if (!/^Q\d+[\.\s\:\n]/i.test(txt) && !/^Q\d+$/i.test(txt.split('\n')[0])) return false;
                            
                            const children = Array.from(el.querySelectorAll('div, article, section, li'));
                            return !children.some(c => c !== el && (/^Q\d+[\.\s\:\n]/i.test(c.innerText || '') || /^Q\d+$/i.test((c.innerText || '').split('\n')[0])));
                        });

                        qCards.forEach(card => {
                            const rawText = card.innerText || '';
                            const lines = rawText.split('\n').map(l => l.trim()).filter(l => l.length > 0);
                            if (lines.length < 2) return;

                            let question = "";
                            let answer = "";

                            for (let i = 0; i < lines.length; i++) {
                                if (/^Q\d+/i.test(lines[i])) {
                                    let cleaned = lines[i].replace(/^Q\d+[\.\s\:]*/i, '').trim();
                                    if (cleaned.length > 3) {
                                        question = cleaned;
                                    } else if (i + 1 < lines.length) {
                                        question = lines[i + 1];
                                    }
                                    break;
                                }
                            }

                            if (!question) return;

                            const allChildElems = Array.from(card.querySelectorAll('*'));
                            for (const el of allChildElems) {
                                const style = window.getComputedStyle(el);
                                const color = style.color || '';
                                const classStr = el.className || '';
                                
                                let isGreen = false;
                                if (typeof classStr === 'string' && (
                                    classStr.includes('green') || 
                                    classStr.includes('emerald') || 
                                    classStr.includes('teal') || 
                                    classStr.includes('success')
                                )) {
                                    isGreen = true;
                                } else if (color.startsWith('rgb')) {
                                    const rgb = color.match(/\d+/g);
                                    if (rgb && rgb.length >= 3) {
                                        const r = parseInt(rgb[0]), g = parseInt(rgb[1]), b = parseInt(rgb[2]);
                                        if (g > 120 && g > r * 1.2 && g > b * 1.2) {
                                            isGreen = true;
                                        }
                                    }
                                }

                                if (isGreen) {
                                    let t = (el.innerText || el.textContent || '').replace(/[\uE000-\uF8FF\u2700-\u27BF\u2600-\u26FF✓✔✅]/g, '').trim();
                                    if (t && t !== question && !/^Q\d+/i.test(t) && t.length > 0) {
                                        if (!/^\d+$/.test(t) || lines.length <= 3) {
                                            answer = t;
                                            break;
                                        }
                                    }
                                }
                            }

                            if (!answer) {
                                for (const l of lines) {
                                    const cleanL = l.replace(/[\uE000-\uF8FF\u2700-\u27BF\u2600-\u26FF✓✔✅]/g, '').trim();
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
                                const candidates = lines.map(l => l.replace(/[\uE000-\uF8FF\u2700-\u27BF\u2600-\u26FF✓✔✅]/g, '').trim())
                                                     .filter(l => l && !/^Q\d+/i.test(l) && l !== question && !l.includes('Study') && !l.includes('กิจกรรม') && !l.includes('Question') && !l.includes('Score'));
                                
                                const textCandidates = candidates.filter(c => !/^\d+$/.test(c));
                                if (textCandidates.length > 0) {
                                    answer = textCandidates[0];
                                } else if (candidates.length > 0) {
                                    answer = candidates[candidates.length - 1];
                                }
                            }

                            if (question && answer) {
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
                    """)
                    all_db[lang_name][cat_name] = qa_items
                    print(f"  -> Category '{cat_name}': Loaded {len(qa_items)} items.")
                except Exception as eval_err:
                    print(f"  [Error] DOM Extraction failed on category '{cat_name}': {eval_err}")
                    all_db[lang_name][cat_name] = []

        browser.close()

    # Atomic write to protect database integrity from interruption or crashes
    target_path = "qa_database.json"
    dir_name = os.path.dirname(os.path.abspath(target_path))
    with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding="utf-8") as tf:
        json.dump(all_db, tf, ensure_ascii=False, indent=2)
        temp_name = tf.name

    os.replace(temp_name, target_path)
    print("\n✅ Multi-language database update complete!")


if __name__ == "__main__":
    fetch_multilingual_database()