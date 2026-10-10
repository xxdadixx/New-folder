import tkinter as tk
from tkinter import ttk


class TelemetryHeaderUI:
    """Encapsulated telemetry header UI components for system logging and status display."""

    def __init__(self, parent):
        self.parent = parent

        # --- Header Frame (Single Line Layout) ---
        self.header_frame = ttk.Frame(parent)
        self.header_frame.pack(fill="x", padx=20, pady=(15, 10))

        # Cyan/Blue Main Title on the left
        self.lbl_title = ttk.Label(
            self.header_frame,
            text="ระบบบันทึกการทำงาน (System Logs)",
            font=("Segoe UI", 14, "bold"),
            foreground="#00d2ff",
        )
        self.lbl_title.pack(side="left")

        # Live Telemetry Label right next to the title
        self.lbl_header_telemetry = ttk.Label(
            self.header_frame,
            text="  |  ⏱️ Running: 00m 00s  |  ⏳ Est. Finish: --:--",
            font=("Segoe UI", 11, "bold"),
            foreground="#a0aec0",
        )
        self.lbl_header_telemetry.pack(side="left", padx=(10, 0))

        # --- Telemetry Stats Frame ---
        self.frame_stats = ttk.LabelFrame(parent, text=" Live Execution Telemetry ")
        self.frame_stats.pack(fill="x", padx=10, pady=5)

        self.lbl_running_time = ttk.Label(
            self.frame_stats, text="Running Time: 00m 00s", font=("Arial", 10, "bold")
        )
        self.lbl_running_time.grid(row=0, column=0, padx=15, pady=5)

        self.lbl_eta = ttk.Label(
            self.frame_stats, text="Est. Completion: --:--", font=("Arial", 10, "bold")
        )
        self.lbl_eta.grid(row=0, column=1, padx=15, pady=5)

        self.lbl_remaining = ttk.Label(
            self.frame_stats, text="Remaining Categories: 16/16", font=("Arial", 10)
        )
        self.lbl_remaining.grid(row=0, column=2, padx=15, pady=5)

        self.progress_bar = ttk.Progressbar(
            self.frame_stats, orient="horizontal", mode="determinate", length=200
        )
        self.progress_bar.grid(row=0, column=3, padx=15, pady=5)

    def update_header_telemetry(self, data: dict):
        """Updates the telemetry display right next to the cyan header title."""
        running = data.get("running_time", "00m 00s")
        clock = data.get("eta_clock", "--:--")
        remaining = data.get("remaining_tasks", 0)

        if remaining > 0:
            display_text = (
                f"  |  ⏱️ {running}  |  ⏳ Est. Finish: {clock} ({remaining} left)"
            )
        else:
            display_text = f"  |  ⏱️ Total: {running}  |  ✅ Complete"

        self.header_frame.after(
            0, lambda: self.lbl_header_telemetry.config(text=display_text)
        )

    def update_ui_telemetry(self, data: dict):
        """Updates dedicated GUI widgets directly from the backend worker thread."""
        self.frame_stats.after(0, lambda: self._apply_ui_updates(data))

    def _apply_ui_updates(self, data: dict):
        self.lbl_running_time.config(
            text=f"Running Time: {data.get('running_time', '00m 00s')}"
        )
        self.lbl_eta.config(
            text=f"Est. Completion: {data.get('eta_clock', '--:--')} ({data.get('eta_duration', '--')})"
        )
        self.lbl_remaining.config(
            text=f"Remaining: {data.get('remaining_tasks', 0)} / {data.get('total_tasks', 16)}"
        )
        self.progress_bar["value"] = data.get("percent", 0)
