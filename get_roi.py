import ctypes
import json
import os
import cv2
import mss
import numpy as np

CONFIG_FILE = "roi_config.json"


def init_dpi_awareness():
    """Enable High-DPI process awareness on Windows to synchronize screen coordinates."""
    if os.name == "nt":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor DPI aware
        except Exception:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                pass


def capture_and_select_roi():
    """Captures primary monitor image and saves non-distorted DPI-aware ROI bounding box."""
    init_dpi_awareness()
    print("Capturing primary screen display...")

    try:
        with mss.mss() as sct:
            if len(sct.monitors) < 2:
                print("❌ Error: No valid monitors detected by mss.")
                return

            monitor = sct.monitors[1]  # Primary monitor context
            screenshot = np.array(sct.grab(monitor))

        screenshot_bgr = cv2.cvtColor(screenshot, cv2.COLOR_BGRA2BGR)

        print("--------------------------------------------------")
        print("Usage Instructions:")
        print("1. Click and drag mouse to select Question Box ROI")
        print("2. Press ENTER or SPACEBAR to confirm selection")
        print("3. Press 'c' or ESC to cancel")
        print("--------------------------------------------------")

        window_name = "RO Auto Answer - Select Question Box ROI"
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        roi = cv2.selectROI(window_name, screenshot_bgr, showCrosshair=True)
        cv2.destroyAllWindows()

        x, y, w, h = [int(v) for v in roi]

        if w > 0 and h > 0:
            roi_dict = {"rel_x": x, "rel_y": y, "w": w, "h": h}
            print("\n✅ ROI bounding box acquired successfully!")
            print(f"Extracted Coordinates: {roi_dict}")

            config_data = {}
            if os.path.exists(CONFIG_FILE):
                try:
                    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                        config_data = json.load(f)
                except Exception:
                    config_data = {}

            if "roi_presets" in config_data and isinstance(
                config_data["roi_presets"], dict
            ):
                config_data["roi_presets"]["ทุกหมวดหมู่"] = roi_dict
            else:
                config_data["roi_presets"] = {"ทุกหมวดหมู่": roi_dict}

            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config_data, f, ensure_ascii=False, indent=2)
            print(f"✅ Automatically updated default category in '{CONFIG_FILE}'!")
        else:
            print("\n❌ ROI selection cancelled or invalid geometry.")

    except Exception as err:
        cv2.destroyAllWindows()
        print(f"\n❌ Unexpected error during ROI selection: {err}")


if __name__ == "__main__":
    capture_and_select_roi()