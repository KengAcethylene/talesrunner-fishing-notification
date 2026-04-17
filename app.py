import tkinter as tk
import customtkinter as ctk
import queue
import os

from core import Config, load_templates
from gui.settings_tab import SettingsTab
from gui.roi_tab import ROITab
from gui.calibration_tab import CalibrationTab
from gui.monitor_tab import MonitorTab

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

_TAB_NAMES = ("  Settings  ", "  ROI Setup  ", "  Calibration  ", "  Monitor  ")


class App(ctk.CTk):
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

        self._build_ui()
        # Build the first (visible) tab immediately; all others build on first visit
        self.settings_tab.ensure_built()
        self.reload_templates()
        self._poll_job = None
        self._poll_log_queue()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    def _build_ui(self):
        self.tabview = ctk.CTkTabview(self, command=self._on_tab_changed)
        self.tabview.pack(fill="both", expand=True, padx=4, pady=4)

        tab_frames = [self.tabview.add(name) for name in _TAB_NAMES]

        self.settings_tab    = SettingsTab(tab_frames[0], self)
        self.roi_tab         = ROITab(tab_frames[1], self)
        self.calibration_tab = CalibrationTab(tab_frames[2], self)
        self.monitor_tab     = MonitorTab(tab_frames[3], self)

        for tab in (self.settings_tab, self.roi_tab,
                    self.calibration_tab, self.monitor_tab):
            tab.pack(fill="both", expand=True)

    # ------------------------------------------------------------------
    def _on_tab_changed(self):
        name = self.tabview.get()
        mapping = dict(zip(_TAB_NAMES, (
            self.settings_tab, self.roi_tab,
            self.calibration_tab, self.monitor_tab,
        )))
        tab = mapping.get(name)
        if tab:
            tab.ensure_built()

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
        # 1. Cancel the log-queue polling loop
        if self._poll_job is not None:
            self.after_cancel(self._poll_job)
            self._poll_job = None

        # 2. Signal monitor worker to stop, then wait (max 3s)
        if hasattr(self, "monitor_tab"):
            self.monitor_tab._stop_event.set()
            thread = self.monitor_tab._monitor_thread
            if thread is not None and thread.is_alive():
                thread.join(timeout=3.0)

        # 3. Release the shared NDI reader
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

        # 5. Destroy the window
        self.destroy()


# ------------------------------------------------------------------
def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
