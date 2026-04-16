import tkinter as tk
from tkinter import ttk
import queue
import sys
import os

import NDIlib as ndi

from core import Config, load_templates
from gui.settings_tab import SettingsTab
from gui.roi_tab import ROITab
from gui.calibration_tab import CalibrationTab
from gui.monitor_tab import MonitorTab


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TalesRunner Fish Monitor")
        self.geometry("1100x750")
        self.minsize(900, 650)

        # Shared state
        self.cfg = Config()
        self.log_queue: queue.Queue = queue.Queue()
        self.frame_reader = None        # FrameReader | None  (shared across tabs)
        self.templates: dict = {}

        # Apply a clean theme
        style = ttk.Style(self)
        available = style.theme_names()
        for preferred in ("clam", "alt", "default"):
            if preferred in available:
                style.theme_use(preferred)
                break

        self._build_ui()
        # Build the first (visible) tab immediately; all others build on first visit
        self.settings_tab.ensure_built()
        self.reload_templates()
        self._poll_job = None
        self._poll_log_queue()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        ndi.initialize()

    # ------------------------------------------------------------------
    def _build_ui(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=4, pady=4)

        self.settings_tab    = SettingsTab(self.notebook, self)
        self.roi_tab         = ROITab(self.notebook, self)
        self.calibration_tab = CalibrationTab(self.notebook, self)
        self.monitor_tab     = MonitorTab(self.notebook, self)

        self.notebook.add(self.settings_tab,    text="  Settings  ")
        self.notebook.add(self.roi_tab,         text="  ROI Setup  ")
        self.notebook.add(self.calibration_tab, text="  Calibration  ")
        self.notebook.add(self.monitor_tab,     text="  Monitor  ")

        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    # ------------------------------------------------------------------
    def _on_tab_changed(self, event):
        idx = self.notebook.index("current")
        tabs = [self.settings_tab, self.roi_tab,
                self.calibration_tab, self.monitor_tab]
        tabs[idx].ensure_built()

    # ------------------------------------------------------------------
    def get_frame(self):
        """Thread-safe frame read from the shared FrameReader."""
        if self.frame_reader is None:
            return False, None
        return self.frame_reader.read()

    # ------------------------------------------------------------------
    def reload_templates(self):
        """Re-load templates dict and update monitor tab lock state."""
        self.templates = load_templates(self.cfg.templates_dir)
        if hasattr(self, "monitor_tab"):
            self.monitor_tab.check_lock()

    # ------------------------------------------------------------------
    def _poll_log_queue(self):
        """Drain the queue and dispatch updates to monitor tab (main thread)."""
        try:
            while True:
                msg_type, payload = self.log_queue.get_nowait()
                if hasattr(self, "monitor_tab"):
                    self.monitor_tab._apply_update(msg_type, payload)
        except queue.Empty:
            pass
        self._poll_job = self.after(100, self._poll_log_queue)

    # ------------------------------------------------------------------
    def _on_close(self):
        # 1. Cancel the log-queue polling loop so no after() fires after destroy()
        if self._poll_job is not None:
            self.after_cancel(self._poll_job)
            self._poll_job = None

        # 2. Signal the monitor worker to stop, then wait for it (max 3s)
        #    Do NOT call _on_stop() here — that tries to update widgets
        if hasattr(self, "monitor_tab"):
            self.monitor_tab._stop_event.set()
            thread = self.monitor_tab._monitor_thread
            if thread is not None and thread.is_alive():
                thread.join(timeout=3.0)

        # 3. Release the shared NDI reader (unblocks any stuck recv call in the thread)
        if self.frame_reader is not None:
            try:
                self.frame_reader.release()
            except Exception:
                pass
            self.frame_reader = None

        # 4. Save config
        try:
            self.cfg.save()
        except Exception:
            pass

        # 5. Tear down NDI runtime (safe now — worker thread has exited or timed out)
        try:
            ndi.destroy()
        except Exception:
            pass

        # 6. Destroy the window
        self.destroy()


# ------------------------------------------------------------------
def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
