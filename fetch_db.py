import json
import time
from playwright.sync_api import sync_playwright


def fetch_quiz_data():
    url = "https://roworlddb.com/sea/study/?lang=th-TH#event=10&reveal=1"
    print("กำลังเชื่อมต่อเว็บไซต์ roworlddb.com เพื่อดึงเฉลยข้อสอบทั้งหมด...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle")

        # รอให้ตารางข้อมูลโหลดสำเร็จ
        time.sleep(2)

        # สกัดคำถามและเฉลยจาก DOM ของหน้าเว็บ
        qa_pairs = page.evaluate("""
            () => {
                const results = [];
                // ค้นหาแถวหรือการ์ดข้อสอบบนเว็บ roworlddb
                const rows = document.querySelectorAll('.study-item, tr, .card, .q-item'); 
                
                rows.forEach(row => {
                    const text = row.innerText;
                    if (text && text.includes('Q')) {
                        // แยกบรรทัดคำถามและเฉลย
                        results.push(text);
                    }
                });
                
                return Array.from(document.body.innerText.split('\\n'))
                    .filter(line => line.trim().length > 0);
            }
        """)

        browser.close()

        # นำมาจัดโครงสร้างคำถาม-เฉลย
        structured_data = []
        raw_text = "\n".join(qa_pairs)

        # แยกข้อความคำถามและเฉลย (ตามแพตเทิร์นบนเว็บ)
        lines = [line.strip() for line in raw_text.split("\n") if line.strip()]
        for i in range(len(lines)):
            line = lines[i]
            # ตรวจหาบรรทัดที่เป็นคำถาม (ขึ้นต้นด้วย Q หรือข้อความคำถาม)
            if (
                line.startswith("Q")
                or "เท่ากับ" in line
                or "คือ" in line
                or "ใช่หรือไม่" in line
            ):
                q = line
                # ค้นหาคำตอบในบรรทัดถัดๆ ไป (O หรือ X)
                a = "O" if "O" in " ".join(lines[i : i + 3]) else "X"
                structured_data.append({"question": q, "answer": a})

        print(f"ดึงข้อมูลสำเร็จ! พบทั้งหมด {len(structured_data)} ข้อ")

        # บันทึกลงไฟล์ qa_database.json
        with open("qa_database.json", "w", encoding="utf-8") as f:
            json.dump(structured_data, f, ensure_ascii=False, indent=2)

        print("สร้างไฟล์ qa_database.json เรียบร้อยแล้ว!")


if __name__ == "__main__":
    fetch_quiz_data()