import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk
import threading

from gui import labeled_frame


class SettingsTab(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        self.cfg = app.cfg

        self.cam_display_var  = tk.StringVar()   # "Name (index N)" or ""
        self._cam_choices     = []               # [(index, name), ...]
        self.token_var        = tk.StringVar()
        self.chat_id_var      = tk.StringVar()
        self.quota_limit_var  = tk.StringVar()
        self.alert_buffer_var = tk.StringVar()

        self._built = False

    def ensure_built(self):
        if self._built:
            return
        self._built = True
        self._build_ui()
        self._load_from_config()

    # ------------------------------------------------------------------
    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        # ---- OBS Virtual Camera ----
        cam_outer, cam_frame = labeled_frame(self, "OBS Virtual Camera")
        cam_outer.pack(fill="x", padx=12, pady=(12, 4))

        ctk.CTkLabel(cam_frame, text="Camera:").grid(
            row=0, column=0, sticky="w", **pad)
        self._cam_combo = ctk.CTkComboBox(
            cam_frame, variable=self.cam_display_var,
            values=[], width=320,
            command=lambda v: self.cam_display_var.set(v),
        )
        self._cam_combo.grid(row=0, column=1, sticky="ew", **pad)
        self._scan_cam_btn = ctk.CTkButton(cam_frame, text="Scan Cameras",
                                           width=130, command=self._on_scan_cameras)
        self._scan_cam_btn.grid(row=0, column=2, **pad)
        self._cam_status = ctk.CTkLabel(cam_frame,
                                        text="Enable OBS Virtual Camera in OBS first, then Scan.",
                                        text_color="gray")
        self._cam_status.grid(row=1, column=0, columnspan=3, sticky="w", **pad)
        cam_frame.grid_columnconfigure(1, weight=1)

        # ---- Telegram ----
        tg_outer, tg_frame = labeled_frame(self, "Telegram Notifications")
        tg_outer.pack(fill="x", padx=12, pady=4)

        ctk.CTkLabel(tg_frame, text="Bot Token:").grid(row=0, column=0, sticky="w", **pad)
        ctk.CTkEntry(tg_frame, textvariable=self.token_var, width=350, show="*").grid(
            row=0, column=1, sticky="ew", **pad)

        ctk.CTkLabel(tg_frame, text="Chat ID:").grid(row=1, column=0, sticky="w", **pad)
        ctk.CTkEntry(tg_frame, textvariable=self.chat_id_var, width=350).grid(
            row=1, column=1, sticky="ew", **pad)

        ctk.CTkButton(tg_frame, text="Test Telegram", width=130,
                      command=self._on_test_telegram).grid(row=1, column=2, **pad)

        tg_frame.grid_columnconfigure(1, weight=1)

        # ---- Quota Settings ----
        quota_outer, quota_frame = labeled_frame(self, "Quota Settings")
        quota_outer.pack(fill="x", padx=12, pady=4)

        ctk.CTkLabel(quota_frame, text="Quota Limit:").grid(row=0, column=0, sticky="w", **pad)
        ctk.CTkEntry(quota_frame, textvariable=self.quota_limit_var, width=80).grid(
            row=0, column=1, sticky="w", **pad)

        ctk.CTkLabel(quota_frame, text="Alert Buffer:").grid(row=0, column=2, sticky="w", **pad)
        ctk.CTkEntry(quota_frame, textvariable=self.alert_buffer_var, width=80).grid(
            row=0, column=3, sticky="w", **pad)
        ctk.CTkLabel(quota_frame,
                     text="(send alert when quota ≥ limit − buffer)",
                     text_color="gray").grid(row=0, column=4, sticky="w", **pad)

        # ---- Save Button ----
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=12, pady=(8, 12))
        ctk.CTkButton(btn_frame, text="Save Settings", width=130,
                      command=self._on_save).pack(side="left")
        self._save_status = ctk.CTkLabel(btn_frame, text="", text_color="green")
        self._save_status.pack(side="left", padx=8)

    # ------------------------------------------------------------------
    def _load_from_config(self):
        idx  = self.cfg.get("virtual_camera_index", 0)
        name = self.cfg.get("virtual_camera_name", "").strip()
        display = f"{name}  (index {idx})" if name else (f"Camera {idx}" if idx else "")
        self.cam_display_var.set(display)
        if display:
            self._cam_combo.configure(values=[display])
        self.token_var.set(self.cfg["telegram_bot_token"])
        self.chat_id_var.set(self.cfg["telegram_chat_id"])
        self.quota_limit_var.set(str(self.cfg["quota_limit"]))
        self.alert_buffer_var.set(str(self.cfg["quota_alert_buffer"]))

    # ------------------------------------------------------------------
    def _on_scan_cameras(self):
        self._scan_cam_btn.configure(state="disabled")
        self._cam_status.configure(text="Scanning cameras 0–5…", text_color="orange")

        def _worker():
            from core import scan_cameras
            cameras = scan_cameras()
            self.after(0, lambda: self._populate_cameras(cameras))

        threading.Thread(target=_worker, daemon=True).start()

    def _populate_cameras(self, cameras):
        # cameras = [(index, name), ...]
        self._scan_cam_btn.configure(state="normal")
        self._cam_choices = cameras
        if not cameras:
            self._cam_status.configure(text="No cameras found.", text_color="red")
            self._cam_combo.configure(values=[])
            return

        values = [f"{name}  (index {idx})" for idx, name in cameras]
        self._cam_combo.configure(values=values)

        # Keep current selection if it still exists, else pick first
        current = self.cam_display_var.get()
        if current not in values:
            self._cam_combo.set(values[0])
        else:
            self._cam_combo.set(current)

        self._cam_status.configure(
            text=f"Found {len(cameras)} camera(s). Select the OBS Virtual Camera.",
            text_color="green")

    # ------------------------------------------------------------------
    def _on_test_telegram(self):
        from core import send_telegram
        token   = self.token_var.get().strip()
        chat_id = self.chat_id_var.get().strip()
        if not token or not chat_id:
            messagebox.showwarning("Missing", "Enter both Bot Token and Chat ID first.")
            return
        try:
            send_telegram(token, chat_id, "TalesRunner Monitor: Telegram test OK!")
            messagebox.showinfo("Sent", "Test message sent successfully.")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    # ------------------------------------------------------------------
    def _on_save(self):
        self.cfg.set("telegram_bot_token", self.token_var.get().strip())
        self.cfg.set("telegram_chat_id",   self.chat_id_var.get().strip())

        # Parse camera index and name from display string "Name  (index N)"
        import re
        cam_display = self.cam_display_var.get().strip()
        m = re.search(r'\(index\s+(\d+)\)\s*$', cam_display)
        if m:
            cam_idx  = int(m.group(1))
            cam_name = cam_display[:m.start()].strip().rstrip()
        else:
            # Fallback: display string is just a plain number
            try:
                cam_idx  = int(cam_display) if cam_display else 0
                cam_name = ""
            except ValueError:
                messagebox.showerror("Error", "Select a camera from the dropdown or scan first.")
                return
        self.cfg.set("virtual_camera_index", cam_idx)
        self.cfg.set("virtual_camera_name",  cam_name)

        try:
            self.cfg.set("quota_limit",        int(self.quota_limit_var.get()))
            self.cfg.set("quota_alert_buffer", int(self.alert_buffer_var.get()))
        except ValueError:
            messagebox.showerror("Error", "Quota Limit and Alert Buffer must be integers.")
            return

        self.cfg.save()
        for tab in (self.app.roi_tab, self.app.calibration_tab):
            tab.refresh_source_label()
        self._save_status.configure(text="Saved!", text_color="green")
        self.after(2000, lambda: self._save_status.configure(text=""))

