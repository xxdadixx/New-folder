import json
import requests
from bs4 import BeautifulSoup

# ดึงข้อมูลจาก API หรือหน้าเว็บ roworlddb
url = "https://roworlddb.com/sea/study/?lang=th-TH"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

try:
    response = requests.get(url, headers=headers)
    response.encoding = 'utf-8'
    
    # หมายเหตุ: หากหน้าเว็บใช้อินเทอร์เฟซหลักผ่าน API ให้ปรับโครงสร้างดึงค่า JSON ตาม API ของเว็บ
    print("กำลังสกัดข้อมูล...")
    
    # เมื่อได้ข้อมูลเรียบร้อย นำมาเขียนลงไฟล์
    # data = [{"question": "...", "answer": "..."}]
    
except Exception as e:
    print(f"เกิดข้อผิดพลาดในการดึงข้อมูล: {e}")