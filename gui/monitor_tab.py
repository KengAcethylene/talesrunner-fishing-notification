import tkinter as tk
import customtkinter as ctk
import threading
import time
import os
from datetime import datetime
from collections import deque

from gui import labeled_frame


class MonitorTab(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        self.cfg = app.cfg

        # Thread control
        self._monitor_thread = None
        self._stop_event     = threading.Event()
        self._is_running     = False

        # Night mode
        self._night_mode       = False
        self._shutdown_after_id = None

        # Display vars
        self.quota_var  = tk.StringVar(value="QUOTA: --/--")
        self.uptime_var = tk.StringVar(value="Uptime: 00:00:00")
        self.status_var = tk.StringVar(value="Stopped")

        self._log_lines = deque(maxlen=200)

        self._built = False

    def ensure_built(self):
        if self._built:
            return
        self._built = True
        self._build_ui()
        self.check_lock()

    # ------------------------------------------------------------------
    def _build_ui(self):
        # ---- Lock overlay (placed on top, hidden when unlocked) ----
        self._lock_frame = ctk.CTkFrame(self, fg_color="#cc3333", corner_radius=0)
        self._lock_label = ctk.CTkLabel(
            self._lock_frame,
            text="",
            text_color="white",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self._lock_label.pack(expand=True)

        # ---- Main content ----
        self._content_frame = ctk.CTkFrame(self, fg_color="transparent")

        # Top stats row
        stats_outer, stats_frame = labeled_frame(self._content_frame, "Live Stats")
        stats_outer.pack(fill="x", padx=12, pady=(10, 4))

        ctk.CTkLabel(stats_frame, textvariable=self.quota_var,
                     font=ctk.CTkFont(size=36, weight="bold"),
                     text_color="#1a7abf").pack(side="left", padx=20, pady=8)

        ctk.CTkLabel(stats_frame, textvariable=self.uptime_var,
                     font=ctk.CTkFont(size=16),
                     text_color="gray").pack(side="left", padx=20, pady=8)

        # Progress bar
        pb_frame = ctk.CTkFrame(self._content_frame, fg_color="transparent")
        pb_frame.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(pb_frame, text="Quota Progress:").pack(side="left")
        self._progress = ctk.CTkProgressBar(pb_frame, width=400)
        self._progress.set(0)
        self._progress.pack(side="left", padx=8)
        self._pct_label = ctk.CTkLabel(pb_frame, text="0%")
        self._pct_label.pack(side="left")

        # Status line
        status_frame = ctk.CTkFrame(self._content_frame, fg_color="transparent")
        status_frame.pack(fill="x", padx=12, pady=2)
        ctk.CTkLabel(status_frame, text="Status:").pack(side="left")
        self._status_label = ctk.CTkLabel(status_frame, textvariable=self.status_var,
                                          text_color="gray")
        self._status_label.pack(side="left", padx=6)

        # ---- Buttons ----
        btn_frame = ctk.CTkFrame(self._content_frame, fg_color="transparent")
        btn_frame.pack(fill="x", padx=12, pady=8)

        self._start_btn = ctk.CTkButton(btn_frame, text="▶  Start", width=100,
                                        command=self._on_start)
        self._start_btn.pack(side="left", padx=4)

        self._stop_btn = ctk.CTkButton(btn_frame, text="■  Stop", width=100,
                                       command=self._on_stop, state="disabled",
                                       fg_color="#8b2222", hover_color="#a03333")
        self._stop_btn.pack(side="left", padx=4)

        self._night_btn = ctk.CTkButton(
            btn_frame, text="🌙  Night Mode: OFF", width=160,
            command=self._on_toggle_night,
            fg_color="#333333", hover_color="#444444",
        )
        self._night_btn.pack(side="left", padx=4)

        # ---- Log section ----
        log_outer, log_frame = labeled_frame(self._content_frame, "Log")
        log_outer.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self._log_text = ctk.CTkTextbox(
            log_frame,
            state="disabled",
            wrap="word",
            font=ctk.CTkFont(family="Consolas", size=9),
            fg_color="#1e1e1e",
            text_color="#d4d4d4",
        )
        self._log_text.pack(fill="both", expand=True, padx=4, pady=4)

        # Coloured tags via the underlying tk.Text widget
        tb = self._log_text._textbox
        tb.tag_configure("ALERT",    foreground="#ff9900")
        tb.tag_configure("CRITICAL", foreground="#ff4444")
        tb.tag_configure("ERROR",    foreground="#ff4444")
        tb.tag_configure("WARNING",  foreground="#ffcc00")
        tb.tag_configure("OK",       foreground="#44cc44")
        tb.tag_configure("RETRY",    foreground="#aaaaaa")
        tb.tag_configure("EXIT",     foreground="#aaaaaa")

        # Pack main content
        self._content_frame.pack(fill="both", expand=True)

        # Initial lock check
        self.check_lock()

    # ------------------------------------------------------------------
    def check_lock(self):
        if not self._built:
            return
        tdir  = self.cfg.templates_dir
        count = sum(
            1 for d in "0123456789"
            if os.path.exists(os.path.join(tdir, f"{d}.png"))
        )
        if count < 10:
            self._lock_label.configure(
                text=f"Complete calibration first  ({count}/10 digits).\n\n"
                     "Go to the Calibration tab to capture and label digit templates.")
            self._lock_frame.place(relx=0, rely=0, relwidth=1, relheight=1)
            self._start_btn.configure(state="disabled")
        else:
            self._lock_frame.place_forget()
            if not self._is_running:
                self._start_btn.configure(state="normal")

    # ------------------------------------------------------------------
    def _on_start(self):
        self._stop_event.clear()
        self._is_running = True
        self._start_btn.configure(state="disabled")
        self._stop_btn.configure(state="normal")
        self._set_status("Running…", "green")
        self._clear_log()

        self._monitor_thread = threading.Thread(
            target=self._monitor_worker, daemon=True)
        self._monitor_thread.start()

    def _on_stop(self):
        self._stop_event.set()
        self._cancel_shutdown()
        self._stop_btn.configure(state="disabled")
        self._set_status("Stopping…", "orange")

    def _on_stopped(self):
        self._is_running = False
        self._start_btn.configure(state="normal")
        self._stop_btn.configure(state="disabled")
        self._set_status("Stopped", "gray")

    def _on_toggle_night(self):
        from core import log as core_log
        self._night_mode = not self._night_mode
        if self._night_mode:
            self._night_btn.configure(text="🌙  Night Mode: ON", fg_color="#1a4a7a",
                                      hover_color="#1e5a96")
            msg = "Night mode enabled — all alerts suppressed. App will auto-shutdown 5 minutes after limit is reached."
        else:
            self._night_btn.configure(text="🌙  Night Mode: OFF", fg_color="#333333",
                                      hover_color="#444444")
            self._cancel_shutdown()
            msg = "Night mode disabled — alerts restored. Auto-shutdown is disabled."
        core_log(msg, "INFO")
        ts = datetime.now().strftime('%H:%M:%S')
        self._append_log(f"[{ts}] [INFO] {msg}", "INFO")

    def _cancel_shutdown(self):
        if self._shutdown_after_id is not None:
            self.after_cancel(self._shutdown_after_id)
            self._shutdown_after_id = None

    def _night_shutdown(self):
        self._shutdown_after_id = None
        import subprocess
        subprocess.Popen(["shutdown", "/s", "/t", "30"])
        self.app._on_close()

    # ------------------------------------------------------------------
    def _set_status(self, text, color="gray"):
        self.status_var.set(text)
        self._status_label.configure(text_color=color)

    # ------------------------------------------------------------------
    def _monitor_worker(self):
        from core import (
            MonitorSession, run_inference, open_input_stream, send_telegram, log as core_log,
        )

        from datetime import datetime

        cfg       = self.cfg
        templates = self.app.templates
        session   = MonitorSession()

        def _log(msg, level="INFO"):
            core_log(msg, level)
            self._post("log", (msg, level))

        _log("Monitor started.")

        reader       = self.app.frame_reader
        owned_reader = False
        if reader is None:
            try:
                reader = open_input_stream(cfg)
            except Exception as e:
                _log(f"Failed to connect to input source: {e}", "ERROR")
                self.after(0, self._on_stopped)
                return
            owned_reader = True

        try:
            while not self._stop_event.is_set():
                ret, frame = reader.read()
                if not ret or frame is None:
                    _log("Lost stream — reconnecting…", "WARNING")
                    if owned_reader:
                        reader.release()
                        reader = open_input_stream(cfg)
                    else:
                        time.sleep(1)
                    continue

                try:
                    result = run_inference(
                        frame, templates,
                        roi_quota=cfg.roi_quota,
                        canvas_size=cfg.canvas_size,
                    )
                except Exception as e:
                    _log(f"Inference error: {e}", "ERROR")
                    time.sleep(cfg["capture_interval"])
                    continue

                current = result["quota_current"]

                if current is None:
                    session.no_read_seconds += cfg["capture_interval"]
                    _log(f"No numbers detected. ({session.no_read_seconds}s)", "WARNING")
                    time.sleep(cfg["capture_interval"])
                    continue

                session.no_read_seconds = 0

                # Session reset detection: count dropped to < 50% of last reading
                if session.prev_current > 0 and current < session.prev_current // 2:
                    _log(f"Quota reset detected ({session.prev_current} → {current}). New session.")
                    self._post("cancel_shutdown", None)
                    session.start_count         = current
                    session.last_reported_count = current
                    session.alert_sent          = False
                    session.limit_sent          = False
                    session.no_read_seconds     = 0
                    session.session_start_time  = datetime.now()
                elif session.start_count == -1:
                    session.start_count = current
                    _log(f"Session started. Initial count: {session.start_count}")

                caught_now = current - session.start_count
                limit      = result["quota_limit"] or cfg["quota_limit"]
                threshold  = limit - cfg["quota_alert_buffer"]

                self._post("quota", (current, limit))
                uptime = str(datetime.now() - session.session_start_time).split('.')[0]
                self._post("uptime", uptime)

                if current >= session.last_reported_count + cfg["report_interval"]:
                    _log(f"FISH: {current}/{limit} (+{caught_now}) | UPTIME: {uptime}")
                    session.last_reported_count = current

                if current >= threshold and not session.alert_sent:
                    msg = f"QUOTA ALMOST FULL: {current}/{limit}"
                    _log(msg, "ALERT")
                    if not self._night_mode:
                        send_telegram(cfg["telegram_bot_token"], cfg["telegram_chat_id"], msg)
                    session.alert_sent = True

                if current >= limit and not session.limit_sent:
                    msg = f"LIMIT REACHED: {current}/{limit}"
                    _log(msg, "CRITICAL")
                    if not self._night_mode:
                        send_telegram(cfg["telegram_bot_token"], cfg["telegram_chat_id"], msg)
                    else:
                        _log("Night mode: app will close in 5 minutes.", "WARNING")
                        self._post("night_shutdown", 300_000)
                    session.limit_sent = True

                session.prev_current = current
                time.sleep(cfg["capture_interval"])

        finally:
            if owned_reader:
                reader.release()
            _log("Monitor stopped.")
            self.after(0, self._on_stopped)

    # ------------------------------------------------------------------
    def _post(self, msg_type, payload):
        self.app.log_queue.put((msg_type, payload))

    def _apply_update(self, msg_type, payload):
        if not self._built:
            return
        if msg_type == "log":
            msg, level = payload
            self._append_log(msg, level)
        elif msg_type == "quota":
            current, limit = payload
            self.quota_var.set(f"QUOTA: {current}/{limit}")
            pct = min(100.0, current / limit * 100) if limit else 0
            self._progress.set(pct / 100)
            self._pct_label.configure(text=f"{pct:.0f}%")
            if self._night_mode and limit and current >= limit and self._shutdown_after_id is None:
                self._set_status("Night mode: closing in 5m…", "orange")
                self._shutdown_after_id = self.after(300_000, self._night_shutdown)
        elif msg_type == "uptime":
            self.uptime_var.set(f"Uptime: {payload}")
        elif msg_type == "night_shutdown":
            delay_ms = payload
            minutes = delay_ms // 60_000
            self._set_status(f"Night mode: closing in {minutes}m…", "orange")
            self._shutdown_after_id = self.after(delay_ms, self._night_shutdown)
        elif msg_type == "cancel_shutdown":
            self._cancel_shutdown()

    # ------------------------------------------------------------------
    def _append_log(self, line: str, level: str = "INFO"):
        self._log_lines.append((line, level))
        tb = self._log_text._textbox
        tb.configure(state="normal")
        tag = level if level in ("ALERT", "CRITICAL", "ERROR", "WARNING",
                                 "OK", "RETRY", "EXIT") else None
        if tag:
            tb.insert("end", line + "\n", tag)
        else:
            tb.insert("end", line + "\n")

        num_lines = int(tb.index("end-1c").split(".")[0])
        if num_lines > 200:
            tb.delete("1.0", f"{num_lines - 200}.0")

        tb.configure(state="disabled")
        tb.see("end")

    def _clear_log(self):
        self._log_lines.clear()
        tb = self._log_text._textbox
        tb.configure(state="normal")
        tb.delete("1.0", "end")
        tb.configure(state="disabled")
