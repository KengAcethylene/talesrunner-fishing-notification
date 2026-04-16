import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import os


class SettingsTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.cfg = app.cfg

        # StringVars
        self.ndi_source_var = tk.StringVar()
        self.token_var = tk.StringVar()
        self.chat_id_var = tk.StringVar()
        self.canvas_size_var = tk.StringVar()
        self.obs_res_var = tk.StringVar()

        # IntVars
        self.quota_limit_var = tk.IntVar()
        self.alert_buffer_var = tk.IntVar()

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

        # ---- NDI Source ----
        ndi_frame = ttk.LabelFrame(self, text="NDI Source")
        ndi_frame.pack(fill="x", padx=12, pady=(12, 4))

        ttk.Label(ndi_frame, text="Source name (leave blank = first found):").grid(
            row=0, column=0, sticky="w", **pad)
        self._ndi_entry = ttk.Entry(ndi_frame, textvariable=self.ndi_source_var, width=38)
        self._ndi_entry.grid(row=0, column=1, sticky="ew", **pad)

        self._scan_btn = ttk.Button(ndi_frame, text="Scan Sources (10s)",
                                    command=self._on_scan_sources)
        self._scan_btn.grid(row=0, column=2, **pad)

        ttk.Label(ndi_frame, text="Discovered sources:").grid(
            row=1, column=0, sticky="nw", **pad)
        list_frame = ttk.Frame(ndi_frame)
        list_frame.grid(row=1, column=1, columnspan=2, sticky="ew", **pad)
        self._sources_listbox = tk.Listbox(list_frame, height=4, selectmode=tk.SINGLE)
        sb = ttk.Scrollbar(list_frame, orient="vertical",
                           command=self._sources_listbox.yview)
        self._sources_listbox.configure(yscrollcommand=sb.set)
        self._sources_listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._sources_listbox.bind("<<ListboxSelect>>", self._on_source_select)

        self._scan_status = ttk.Label(ndi_frame, text="", foreground="gray")
        self._scan_status.grid(row=2, column=0, columnspan=3, sticky="w", **pad)

        ndi_frame.columnconfigure(1, weight=1)

        # ---- Telegram ----
        tg_frame = ttk.LabelFrame(self, text="Telegram Notifications")
        tg_frame.pack(fill="x", padx=12, pady=4)

        ttk.Label(tg_frame, text="Bot Token:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(tg_frame, textvariable=self.token_var, width=45, show="*").grid(
            row=0, column=1, sticky="ew", **pad)

        ttk.Label(tg_frame, text="Chat ID:").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(tg_frame, textvariable=self.chat_id_var, width=45).grid(
            row=1, column=1, sticky="ew", **pad)

        ttk.Button(tg_frame, text="Test Telegram", command=self._on_test_telegram).grid(
            row=1, column=2, **pad)

        tg_frame.columnconfigure(1, weight=1)

        # ---- Quota Settings ----
        quota_frame = ttk.LabelFrame(self, text="Quota Settings")
        quota_frame.pack(fill="x", padx=12, pady=4)

        ttk.Label(quota_frame, text="Quota Limit:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Spinbox(quota_frame, from_=1, to=9999, textvariable=self.quota_limit_var,
                    width=8).grid(row=0, column=1, sticky="w", **pad)

        ttk.Label(quota_frame, text="Alert Buffer:").grid(row=0, column=2, sticky="w", **pad)
        ttk.Spinbox(quota_frame, from_=0, to=999, textvariable=self.alert_buffer_var,
                    width=8).grid(row=0, column=3, sticky="w", **pad)
        ttk.Label(quota_frame, text="(send alert when quota ≥ limit − buffer)").grid(
            row=0, column=4, sticky="w", **pad)

        # ---- Canvas Resolution ----
        canvas_frame = ttk.LabelFrame(self, text="Processing Canvas Resolution")
        canvas_frame.pack(fill="x", padx=12, pady=4)

        ttk.Label(canvas_frame, text="Canvas Size:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Combobox(canvas_frame, textvariable=self.canvas_size_var,
                     values=["1280x720", "640x360"],
                     state="readonly", width=12).grid(row=0, column=1, sticky="w", **pad)
        ttk.Label(canvas_frame, text="(resize NDI frame to this before ROI lookup)").grid(
            row=0, column=2, sticky="w", **pad)

        # ---- OBS Export ----
        obs_frame = ttk.LabelFrame(self, text="OBS Profile Export")
        obs_frame.pack(fill="x", padx=12, pady=4)

        ttk.Label(obs_frame, text="OBS Output Resolution:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Combobox(obs_frame, textvariable=self.obs_res_var,
                     values=["1280x720", "640x360"],
                     state="readonly", width=12).grid(row=0, column=1, sticky="w", **pad)
        ttk.Label(obs_frame, text="@ 1 FPS  (low FPS is fine for fish monitoring)").grid(
            row=0, column=2, sticky="w", **pad)

        ttk.Button(obs_frame, text="Export OBS Profile…",
                   command=self._on_export_obs).grid(row=0, column=3, **pad)

        obs_frame.columnconfigure(2, weight=1)

        # ---- Save Button ----
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", padx=12, pady=(8, 12))
        ttk.Button(btn_frame, text="Save Settings", command=self._on_save).pack(side="left")
        self._save_status = ttk.Label(btn_frame, text="", foreground="green")
        self._save_status.pack(side="left", padx=8)

    # ------------------------------------------------------------------
    def _load_from_config(self):
        self.ndi_source_var.set(self.cfg["ndi_source_name"])
        self.token_var.set(self.cfg["telegram_bot_token"])
        self.chat_id_var.set(self.cfg["telegram_chat_id"])
        self.quota_limit_var.set(self.cfg["quota_limit"])
        self.alert_buffer_var.set(self.cfg["quota_alert_buffer"])
        w, h = self.cfg.canvas_size
        self.canvas_size_var.set(f"{w}x{h}")
        self.obs_res_var.set(self.cfg["obs_export_resolution"])

    # ------------------------------------------------------------------
    def _on_scan_sources(self):
        self._scan_btn.configure(state="disabled")
        self._scan_status.configure(text="Scanning… (10s)", foreground="orange")
        self._sources_listbox.delete(0, tk.END)

        def _worker():
            from core import scan_sources
            import NDIlib as ndi
            ndi.initialize()
            try:
                names = scan_sources(10)
            finally:
                ndi.destroy()
            self.after(0, lambda: self._populate_sources(names))

        threading.Thread(target=_worker, daemon=True).start()

    def _populate_sources(self, names):
        self._sources_listbox.delete(0, tk.END)
        for n in names:
            self._sources_listbox.insert(tk.END, n)
        count = len(names)
        msg = f"Found {count} source(s)" if count else "No sources found"
        self._scan_status.configure(
            text=msg, foreground="green" if count else "red")
        self._scan_btn.configure(state="normal")

    def _on_source_select(self, event):
        sel = self._sources_listbox.curselection()
        if sel:
            self.ndi_source_var.set(self._sources_listbox.get(sel[0]))

    # ------------------------------------------------------------------
    def _on_test_telegram(self):
        from core import send_telegram
        token = self.token_var.get().strip()
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
        self.cfg.set("ndi_source_name", self.ndi_source_var.get().strip())
        self.cfg.set("telegram_bot_token", self.token_var.get().strip())
        self.cfg.set("telegram_chat_id", self.chat_id_var.get().strip())
        self.cfg.set("quota_limit", int(self.quota_limit_var.get()))
        self.cfg.set("quota_alert_buffer", int(self.alert_buffer_var.get()))
        self.cfg.set("obs_export_resolution", self.obs_res_var.get())

        # Canvas size
        cs = self.canvas_size_var.get()
        try:
            w, h = (int(v) for v in cs.split("x"))
            self.cfg.set("canvas_size", [w, h])
        except ValueError:
            messagebox.showerror("Error", f"Invalid canvas size: {cs}")
            return

        self.cfg.save()
        self._save_status.configure(text="Saved!")
        self.after(2000, lambda: self._save_status.configure(text=""))

    # ------------------------------------------------------------------
    def _on_export_obs(self):
        res = self.obs_res_var.get()
        try:
            w, h = (int(v) for v in res.split("x"))
        except ValueError:
            messagebox.showerror("Error", f"Invalid resolution: {res}")
            return

        path = filedialog.asksaveasfilename(
            defaultextension=".ini",
            filetypes=[("OBS Profile INI", "*.ini"), ("All files", "*.*")],
            initialfile="basic.ini",
            title="Save OBS Profile",
        )
        if not path:
            return

        content = (
            "[General]\n"
            "Name=TalesRunner\n"
            "\n"
            "[Video]\n"
            f"BaseCX={w}\n"
            f"BaseCY={h}\n"
            f"OutputCX={w}\n"
            f"OutputCY={h}\n"
            "FPSType=1\n"
            "FPSInt=1\n"
        )
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            messagebox.showinfo(
                "Exported",
                f"OBS profile saved to:\n{path}\n\n"
                "In OBS: Profile → Import → select this file.",
            )
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save: {e}")
