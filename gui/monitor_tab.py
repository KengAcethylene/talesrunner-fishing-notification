import tkinter as tk
from tkinter import ttk
import threading
import time
import os
from datetime import datetime
from collections import deque


class MonitorTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.cfg = app.cfg

        # Thread control
        self._monitor_thread = None
        self._stop_event = threading.Event()
        self._is_running = False

        # Display vars
        self.quota_var = tk.StringVar(value="QUOTA: --/--")
        self.time_var = tk.StringVar(value="GAME TIME: --:--:--")
        self.uptime_var = tk.StringVar(value="Uptime: 00:00:00")
        self.progress_var = tk.DoubleVar(value=0.0)
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
        # ---- Lock overlay (packed on top, hidden when unlocked) ----
        self._lock_frame = tk.Frame(self, bg="#cc3333")
        self._lock_label = tk.Label(
            self._lock_frame,
            text="",
            bg="#cc3333", fg="white",
            font=("Arial", 14, "bold"),
        )
        self._lock_label.pack(expand=True)

        # ---- Main content ----
        self._content_frame = ttk.Frame(self)

        # Top stats row
        stats_frame = ttk.LabelFrame(self._content_frame, text="Live Stats")
        stats_frame.pack(fill="x", padx=12, pady=(10, 4))

        # Quota display
        ttk.Label(stats_frame, textvariable=self.quota_var,
                  font=("Arial", 36, "bold"), foreground="#1a7abf").pack(
            side="left", padx=20, pady=8)

        right_stats = ttk.Frame(stats_frame)
        right_stats.pack(side="left", padx=20, pady=8)
        ttk.Label(right_stats, textvariable=self.time_var,
                  font=("Arial", 22)).pack(anchor="w")
        ttk.Label(right_stats, textvariable=self.uptime_var,
                  font=("Arial", 13), foreground="gray").pack(anchor="w", pady=(4, 0))

        # Progress bar
        pb_frame = ttk.Frame(self._content_frame)
        pb_frame.pack(fill="x", padx=12, pady=4)
        ttk.Label(pb_frame, text="Quota Progress:").pack(side="left")
        self._progress = ttk.Progressbar(pb_frame, variable=self.progress_var,
                                         maximum=100, length=400, mode="determinate")
        self._progress.pack(side="left", padx=8)
        self._pct_label = ttk.Label(pb_frame, text="0%")
        self._pct_label.pack(side="left")

        # Status line
        status_frame = ttk.Frame(self._content_frame)
        status_frame.pack(fill="x", padx=12, pady=2)
        ttk.Label(status_frame, text="Status:").pack(side="left")
        self._status_label = ttk.Label(status_frame, textvariable=self.status_var,
                                       foreground="gray")
        self._status_label.pack(side="left", padx=6)

        # ---- Buttons ----
        btn_frame = ttk.Frame(self._content_frame)
        btn_frame.pack(fill="x", padx=12, pady=8)

        self._start_btn = ttk.Button(btn_frame, text="▶  Start",
                                     command=self._on_start)
        self._start_btn.pack(side="left", padx=4)

        self._stop_btn = ttk.Button(btn_frame, text="■  Stop",
                                    command=self._on_stop, state="disabled")
        self._stop_btn.pack(side="left", padx=4)

        # ---- Log section ----
        log_frame = ttk.LabelFrame(self._content_frame, text="Log")
        log_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self._log_text = tk.Text(log_frame, state="disabled",
                                 wrap="word", height=14,
                                 bg="#1e1e1e", fg="#d4d4d4",
                                 font=("Consolas", 9),
                                 relief="flat")
        sb = ttk.Scrollbar(log_frame, orient="vertical",
                           command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=sb.set)
        self._log_text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # Text tags for coloured levels
        self._log_text.tag_configure("ALERT",    foreground="#ff9900")
        self._log_text.tag_configure("CRITICAL", foreground="#ff4444")
        self._log_text.tag_configure("ERROR",    foreground="#ff4444")
        self._log_text.tag_configure("WARNING",  foreground="#ffcc00")
        self._log_text.tag_configure("OK",       foreground="#44cc44")
        self._log_text.tag_configure("RETRY",    foreground="#aaaaaa")
        self._log_text.tag_configure("EXIT",     foreground="#aaaaaa")

        # Pack main content
        self._content_frame.pack(fill="both", expand=True)

        # Initial lock check
        self.check_lock()

    # ------------------------------------------------------------------
    def check_lock(self):
        """Show/hide the lock overlay based on calibration completeness."""
        if not self._built:
            return
        tdir = self.cfg.templates_dir
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
        self._stop_btn.configure(state="disabled")
        self._set_status("Stopping…", "orange")

    def _on_stopped(self):
        """Called from main thread when worker exits."""
        self._is_running = False
        self._start_btn.configure(state="normal")
        self._stop_btn.configure(state="disabled")
        self._set_status("Stopped", "gray")

    # ------------------------------------------------------------------
    def _set_status(self, text, color="gray"):
        self.status_var.set(text)
        self._status_label.configure(foreground=color)

    # ------------------------------------------------------------------
    # Monitor worker (runs in daemon thread)
    # ------------------------------------------------------------------
    def _monitor_worker(self):
        from core import (
            MonitorSession, run_inference, open_stream, send_telegram, log as core_log,
        )
        from datetime import datetime

        cfg = self.cfg
        templates = self.app.templates
        session = MonitorSession()

        def _log(msg, level="INFO"):
            core_log(msg, level)
            self._post("log", (msg, level))

        _log("Monitor started.")

        # Try to reuse existing reader, otherwise open a new one
        reader = self.app.frame_reader
        owned_reader = False
        if reader is None:
            import NDIlib as ndi
            ndi.initialize()
            try:
                reader = open_stream(cfg["ndi_source_name"], cfg["reconnect_delay"],
                                     copy_interval=cfg["capture_interval"])
            except Exception as e:
                _log(f"Failed to connect to NDI: {e}", "ERROR")
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
                        reader = open_stream(cfg["ndi_source_name"],
                                             cfg["reconnect_delay"],
                                             copy_interval=cfg["capture_interval"])
                    else:
                        time.sleep(1)
                    continue

                try:
                    result = run_inference(
                        frame, templates,
                        roi_quota=cfg.roi_quota,
                        roi_time=cfg.roi_time,
                        canvas_size=cfg.canvas_size,
                    )
                except Exception as e:
                    _log(f"Inference error: {e}", "ERROR")
                    time.sleep(cfg["capture_interval"])
                    continue

                current = result["quota_current"]
                raw_time = result.get("time_raw") or ""

                if current is None:
                    session.no_read_seconds += cfg["capture_interval"]
                    _log(f"No numbers detected. ({session.no_read_seconds}/{cfg['no_read_timeout']}s)",
                         "WARNING")
                    if (cfg["no_read_timeout"] != float('inf') and
                            session.no_read_seconds >= cfg["no_read_timeout"]):
                        msg = f"No quota numbers detected for {cfg['no_read_timeout']}s — stopping."
                        _log(msg, "ERROR")
                        send_telegram(cfg["telegram_bot_token"],
                                      cfg["telegram_chat_id"], msg)
                        break
                    time.sleep(cfg["capture_interval"])
                    continue

                session.no_read_seconds = 0

                # Session reset detection: count dropped to < 50% of last reading.
                # Using prev_current (not start_count) so a reset back to the
                # starting value (e.g. 0 → 0) is still caught.
                if session.prev_current > 0 and current < session.prev_current // 2:
                    _log(f"Quota reset detected ({session.prev_current} → {current}). New session.")
                    session.start_count = current
                    session.last_reported_count = current
                    session.alert_sent = False
                    session.limit_sent = False
                    session.no_read_seconds = 0
                    session.session_start_time = datetime.now()
                elif session.start_count == -1:
                    session.start_count = current
                    _log(f"Session started. Initial count: {session.start_count}")

                caught_now = current - session.start_count
                limit = cfg["quota_limit"]
                threshold = limit - cfg["quota_alert_buffer"]

                # Update UI
                self._post("quota", (current, limit))
                if raw_time:
                    self._post("time", raw_time)
                uptime = str(datetime.now() - session.session_start_time).split('.')[0]
                self._post("uptime", uptime)

                # Log on interval
                if current >= session.last_reported_count + cfg["report_interval"]:
                    game_time = raw_time or "--:--:--"
                    _log(f"FISH: {current}/{limit} (+{caught_now}) | GAME: {game_time} | UPTIME: {uptime}")
                    session.last_reported_count = current

                # Alerts
                if current >= threshold and not session.alert_sent:
                    msg = f"QUOTA ALMOST FULL: {current}/{limit}"
                    _log(msg, "ALERT")
                    send_telegram(cfg["telegram_bot_token"], cfg["telegram_chat_id"], msg)
                    session.alert_sent = True

                if current >= limit and not session.limit_sent:
                    msg = f"LIMIT REACHED: {current}/{limit}"
                    _log(msg, "CRITICAL")
                    send_telegram(cfg["telegram_bot_token"], cfg["telegram_chat_id"], msg)
                    session.limit_sent = True

                session.prev_current = current
                time.sleep(cfg["capture_interval"])

        finally:
            if owned_reader:
                reader.release()
            _log("Monitor stopped.")
            self.after(0, self._on_stopped)

    # ------------------------------------------------------------------
    # Thread → UI bridge
    # ------------------------------------------------------------------
    def _post(self, msg_type, payload):
        """Put a message into app's log queue for GUI thread to process."""
        self.app.log_queue.put((msg_type, payload))

    def _apply_update(self, msg_type, payload):
        """Called on main thread by App._poll_log_queue."""
        if not self._built:
            return
        if msg_type == "log":
            msg, level = payload
            self._append_log(msg, level)
        elif msg_type == "quota":
            current, limit = payload
            self.quota_var.set(f"QUOTA: {current}/{limit}")
            pct = min(100.0, current / limit * 100) if limit else 0
            self.progress_var.set(pct)
            self._pct_label.configure(text=f"{pct:.0f}%")
        elif msg_type == "time":
            self.time_var.set(f"GAME TIME: {payload}")
        elif msg_type == "uptime":
            self.uptime_var.set(f"Uptime: {payload}")

    # ------------------------------------------------------------------
    def _append_log(self, line: str, level: str = "INFO"):
        self._log_lines.append((line, level))
        self._log_text.configure(state="normal")
        tag = level if level in ("ALERT", "CRITICAL", "ERROR", "WARNING",
                                 "OK", "RETRY", "EXIT") else None
        if tag:
            self._log_text.insert("end", line + "\n", tag)
        else:
            self._log_text.insert("end", line + "\n")

        # Trim to maxlen
        num_lines = int(self._log_text.index("end-1c").split(".")[0])
        if num_lines > 200:
            self._log_text.delete("1.0", f"{num_lines - 200}.0")

        self._log_text.configure(state="disabled")
        self._log_text.see("end")

    def _clear_log(self):
        self._log_lines.clear()
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.configure(state="disabled")
