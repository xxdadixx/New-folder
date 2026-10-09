import mss
import cv2
import numpy as np

print("กำลังแคปหน้าจอ...")

# แคปภาพหน้าจอทั้งหมด
with mss.mss() as sct:
    monitor = sct.monitors[1]  # หน้าจอหลัก
    screenshot = np.array(sct.grab(monitor))

# เปลี่ยนสีภาพให้แสดงผลใน OpenCV ถูกต้อง
screenshot_bgr = cv2.cvtColor(screenshot, cv2.COLOR_BGRA2BGR)

print("--------------------------------------------------")
print("วิธีใช้งาน:")
print("1. ลากเมาส์ครอบเฉพาะ 'กล่องข้อความคำถาม' ในเกม")
print("2. กดปุ่ม ENTER หรือ SPACEBAR เมื่อเลือกเสร็จ")
print("3. หากต้องการยกเลิก ให้กดปุ่ม c")
print("--------------------------------------------------")

# เปิดหน้าต่างให้กดลากเมาส์เลือกพื้นที่
roi = cv2.selectROI("Drag mouse to select Question Box", screenshot_bgr, showCrosshair=True)
cv2.destroyAllWindows()

x, y, w, h = [int(v) for v in roi]

if w > 0 and h > 0:
    print("\n✅ สำเร็จ! นำบรรทัดด้านล่างนี้ไปก๊อปปี้วางใน main.py ได้เลย:\n")
    print(f'ROI = {{"top": {y}, "left": {x}, "width": {w}, "height": {h}}}')
else:
    print("\n❌ ไม่ได้เลือกพื้นที่")