import tkinter as tk
from tkinter import ttk

# --- Telemetry Header UI Widgets (Place above log text window) ---
root = tk.Tk()
root.title("Ragnarok Database Scraper")

# --- Header Frame (Single Line Layout) ---
header_frame = ttk.Frame(root)
header_frame.pack(fill="x", padx=20, pady=(15, 10))

# Cyan/Blue Main Title on the left
lbl_title = ttk.Label(
    header_frame,
    text="ระบบบันทึกการทำงาน (System Logs)",
    font=("Segoe UI", 14, "bold"),
    foreground="#00d2ff"  # Cyan / Blue accent text
)
lbl_title.pack(side="left")

# Live Telemetry Label right next to the title
lbl_header_telemetry = ttk.Label(
    header_frame,
    text="  |  ⏱️ Running: 00m 00s  |  ⏳ Est. Finish: --:--",
    font=("Segoe UI", 11, "bold"),
    foreground="#a0aec0"  # Subtle grey/cyan contrast
)
lbl_header_telemetry.pack(side="left", padx=(10, 0))

# --- Telemetry Header UI Widgets ---
frame_stats = ttk.LabelFrame(root, text=" Live Execution Telemetry ")
frame_stats.pack(fill="x", padx=10, pady=5)

lbl_running_time = ttk.Label(frame_stats, text="Running Time: 00m 00s", font=("Arial", 10, "bold"))
lbl_running_time.grid(row=0, column=0, padx=15, pady=5)

lbl_eta = ttk.Label(frame_stats, text="Est. Completion: --:--", font=("Arial", 10, "bold"))
lbl_eta.grid(row=0, column=1, padx=15, pady=5)

lbl_remaining = ttk.Label(frame_stats, text="Remaining Categories: 16/16", font=("Arial", 10))
lbl_remaining.grid(row=0, column=2, padx=15, pady=5)

progress_bar = ttk.Progressbar(frame_stats, orient="horizontal", mode="determinate", length=200)
progress_bar.grid(row=0, column=3, padx=15, pady=5)

def update_header_telemetry(data: dict):
    """Updates the telemetry display right next to the cyan header title."""
    running = data.get("running_time", "00m 00s")
    clock = data.get("eta_clock", "--:--")
    remaining = data.get("remaining_tasks", 0)
    
    if remaining > 0:
        display_text = f"  |  ⏱️ {running}  |  ⏳ Est. Finish: {clock} ({remaining} left)"
    else:
        display_text = f"  |  ⏱️ Total: {running}  |  ✅ Complete"

    # Thread-safe UI update
    header_frame.after(0, lambda: lbl_header_telemetry.config(text=display_text))

def update_ui_telemetry(data: dict):
    """Updates dedicated GUI widgets directly from the backend worker thread."""
    frame_stats.after(0, lambda: _apply_ui_updates(data))


def _apply_ui_updates(data: dict):
    lbl_running_time.config(text=f"Running Time: {data['running_time']}")
    lbl_eta.config(text=f"Est. Completion: {data['eta_clock']} ({data['eta_duration']})")
    lbl_remaining.config(text=f"Remaining: {data['remaining_tasks']} / {data['total_tasks']}")
    progress_bar["value"] = data["percent"]


# Trigger update with both callbacks:
# fetch_multilingual_database(log_fn=append_to_log_window, progress_fn=update_ui_telemetry)