import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk
import os
import cv2
import numpy as np
import threading

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = ImageTk = None

from gui import labeled_frame

THRESH_PREVIEW_W = 600
THRESH_PREVIEW_H = 160
CROP_THUMB_W = 80
CROP_THUMB_H = 100


class CalibrationTab(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        self.cfg = app.cfg

        # State
        self._captured_frame = None
        self._crops          = []
        self._assign_vars    = []
        self._thresh_photo   = None
        self._crop_photos    = []

        self._built = False

    def ensure_built(self):
        if self._built:
            return
        self._built = True
        self._build_ui()
        self.refresh_digit_grid()
        self.refresh_source_label()

    def refresh_source_label(self):
        if not self._built:
            return
        from core import get_source_label
        self._source_label.configure(text=f"Source:  {get_source_label(self.cfg)}")

    # ------------------------------------------------------------------
    def _build_ui(self):
        pad = {"padx": 6, "pady": 4}

        # ---- Capture section ----
        cap_outer, cap_frame = labeled_frame(self, "Step 1 — Capture Frame")
        cap_outer.pack(fill="x", padx=12, pady=(12, 4))
        self._cap_outer = cap_outer

        self._source_label = ctk.CTkLabel(cap_frame, text="", text_color="gray")
        self._source_label.grid(row=0, column=0, columnspan=2, sticky="w", **pad)

        self._capture_btn = ctk.CTkButton(cap_frame, text="Capture Frame",
                                          width=160, command=self._on_capture_frame)
        self._capture_btn.grid(row=1, column=0, **pad)
        self._capture_status = ctk.CTkLabel(cap_frame, text="No frame captured yet.",
                                            text_color="gray")
        self._capture_status.grid(row=1, column=1, sticky="w", **pad)

        ctk.CTkLabel(cap_frame, text="Preprocessed Quota ROI:").grid(
            row=2, column=0, sticky="nw", **pad)
        self._thresh_canvas = tk.Canvas(cap_frame,
                                        width=THRESH_PREVIEW_W, height=THRESH_PREVIEW_H,
                                        bg="#222", highlightthickness=1,
                                        highlightbackground="#555")
        self._thresh_canvas.grid(row=2, column=1, sticky="w", **pad)
        self._thresh_canvas.create_text(
            THRESH_PREVIEW_W // 2, THRESH_PREVIEW_H // 2,
            text="Capture a frame to see the threshold image",
            fill="white", font=("Arial", 11), tags="placeholder",
        )

        # ---- Assign section ----
        assign_outer, assign_inner = labeled_frame(
            self, "Step 2 — Assign Labels to Detected Character Crops")
        assign_outer.pack(fill="x", padx=12, pady=4)

        ctk.CTkLabel(assign_inner,
                     text="Each box is one detected character. Set the correct digit (0–9) or 'skip'.\n"
                          "The '/' separator is detected automatically and shown as a label.",
                     text_color="gray").pack(anchor="w", padx=6, pady=2)

        self._crops_outer = ctk.CTkFrame(assign_inner, fg_color="transparent")
        self._crops_outer.pack(fill="x", padx=6, pady=4)

        self._no_crops_label = ctk.CTkLabel(
            self._crops_outer, text="No crops detected yet.", text_color="gray")
        self._no_crops_label.pack()

        # ---- Save / Clear buttons ----
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=12, pady=4)
        ctk.CTkButton(btn_frame, text="Save Selected Templates", width=180,
                      command=self._on_save_templates).pack(side="left")
        ctk.CTkButton(btn_frame, text="Clear All Templates", width=150,
                      fg_color="#8b2222", hover_color="#a03333",
                      command=self._on_clear_all).pack(side="left", padx=8)
        self._save_status = ctk.CTkLabel(btn_frame, text="", text_color="green")
        self._save_status.pack(side="left", padx=8)

        # ---- Digit status grid ----
        grid_outer, grid_inner = labeled_frame(
            self, "Template Status  (all 10 digits required to unlock Monitor)")
        grid_outer.pack(fill="x", padx=12, pady=(4, 12))

        self._digit_status_labels = {}
        row_frame = ctk.CTkFrame(grid_inner, fg_color="transparent")
        row_frame.pack(padx=8, pady=6)
        for d in "0123456789":
            col = ctk.CTkFrame(row_frame, width=58, height=70)
            col.pack(side="left", padx=3)
            col.pack_propagate(False)
            ctk.CTkLabel(col, text=d, font=ctk.CTkFont(size=16, weight="bold")).pack()
            lbl = ctk.CTkLabel(col, text="✗", text_color="red",
                               font=ctk.CTkFont(size=14))
            lbl.pack()
            self._digit_status_labels[d] = lbl

        self._calibration_count_var = tk.StringVar(value="0/10 digits calibrated")
        ctk.CTkLabel(grid_inner, textvariable=self._calibration_count_var,
                     font=ctk.CTkFont(size=11)).pack(pady=(0, 4))

    # ------------------------------------------------------------------
    def refresh_digit_grid(self):
        if not self._built:
            return
        tdir  = self.cfg.templates_dir
        count = 0
        for d in "0123456789":
            path   = os.path.join(tdir, f"{d}.png")
            exists = os.path.exists(path)
            lbl    = self._digit_status_labels[d]
            if exists:
                lbl.configure(text="✓", text_color="green")
                count += 1
            else:
                lbl.configure(text="✗", text_color="red")
        self._calibration_count_var.set(f"{count}/10 digits calibrated")
        self.app.reload_templates()

    # ------------------------------------------------------------------
    def _on_capture_frame(self):
        self._capture_btn.configure(state="disabled")
        self._capture_status.configure(text="Capturing…", text_color="orange")

        def _worker():
            ret, frame = self.app.get_frame()
            if not ret or frame is None:
                from core import open_input_stream
                import time
                try:
                    reader = open_input_stream(self.cfg, max_retries=1)
                    time.sleep(0.5)
                    ret, frame = reader.read()
                    self.app.frame_reader = reader
                except Exception:
                    self.after(0, lambda: self._process_captured(None))
                    return
            self.after(0, lambda: self._process_captured(frame if ret else None))

        threading.Thread(target=_worker, daemon=True).start()

    def _process_captured(self, frame):
        self._capture_btn.configure(state="normal")
        if frame is None:
            self._capture_status.configure(
                text="Failed to capture frame. Check NDI source.", text_color="red")
            return

        from core import preprocess_quota, extract_char_crops, crop as core_crop

        self._captured_frame = frame
        resized = cv2.resize(frame, self.cfg.canvas_size)
        roi_img = core_crop(resized, self.cfg.roi_quota)
        thresh  = preprocess_quota(roi_img)
        chars   = extract_char_crops(thresh)

        self._crops = chars
        self._capture_status.configure(
            text=f"Captured. Detected {len(chars)} components.", text_color="green")

        self._display_thresh(thresh)
        self._display_crops(chars)

    # ------------------------------------------------------------------
    def _display_thresh(self, thresh):
        if Image is None:
            return
        h, w   = thresh.shape
        scale  = min(THRESH_PREVIEW_W / w, THRESH_PREVIEW_H / h)
        disp_w = int(w * scale)
        disp_h = int(h * scale)
        resized = cv2.resize(thresh, (disp_w, disp_h))
        rgb     = cv2.cvtColor(resized, cv2.COLOR_GRAY2RGB)
        pil_img = Image.fromarray(rgb)
        padded  = Image.new("RGB", (THRESH_PREVIEW_W, THRESH_PREVIEW_H), (34, 34, 34))
        padded.paste(pil_img, ((THRESH_PREVIEW_W - disp_w) // 2,
                                (THRESH_PREVIEW_H - disp_h) // 2))

        self._thresh_photo = ImageTk.PhotoImage(padded)
        self._thresh_canvas.delete("all")
        self._thresh_canvas.create_image(0, 0, anchor="nw", image=self._thresh_photo)

    def _display_crops(self, chars):
        for w in self._crops_outer.winfo_children():
            w.destroy()
        self._assign_vars = []
        self._crop_photos = []

        if not chars:
            ctk.CTkLabel(self._crops_outer, text="No crops detected.",
                         text_color="gray").pack()
            return

        self._no_crops_label = None

        for idx, (cx, is_slash, crop_img) in enumerate(chars):
            col = ctk.CTkFrame(self._crops_outer)
            col.pack(side="left", padx=4, pady=4)

            if is_slash:
                ctk.CTkLabel(col, text="/", font=ctk.CTkFont(size=20, weight="bold"),
                             text_color="orange", width=40).pack(pady=4)
                ctk.CTkLabel(col, text="separator", text_color="gray",
                             font=ctk.CTkFont(size=8)).pack()
                self._assign_vars.append(None)
            else:
                if Image is not None:
                    h_c, w_c = crop_img.shape[:2]
                    scale = min(CROP_THUMB_W / w_c, CROP_THUMB_H / h_c)
                    dw = max(1, int(w_c * scale))
                    dh = max(1, int(h_c * scale))
                    thumb = cv2.resize(crop_img, (dw, dh))
                    if len(thumb.shape) == 2:
                        thumb_rgb = cv2.cvtColor(thumb, cv2.COLOR_GRAY2RGB)
                    else:
                        thumb_rgb = cv2.cvtColor(thumb, cv2.COLOR_BGR2RGB)
                    pil_thumb = Image.fromarray(thumb_rgb)
                    padded = Image.new("RGB", (CROP_THUMB_W, CROP_THUMB_H), (200, 200, 200))
                    padded.paste(pil_thumb, ((CROP_THUMB_W - dw) // 2,
                                             (CROP_THUMB_H - dh) // 2))
                    photo = ImageTk.PhotoImage(padded)
                    self._crop_photos.append(photo)

                    lbl = tk.Label(col, image=photo, borderwidth=0, bg="#2b2b2b")
                    lbl.pack()

                var = tk.StringVar(value="")
                values = [""] + [str(i) for i in range(10)] + ["skip"]
                cb = ctk.CTkComboBox(col, variable=var, values=values,
                                     width=80, state="readonly")
                cb.pack(pady=2)
                self._assign_vars.append(var)

    # ------------------------------------------------------------------
    def _on_save_templates(self):
        if not self._crops:
            messagebox.showinfo("Nothing to save", "Capture a frame first.")
            return

        from core import save_template

        saved = 0
        for idx, (cx, is_slash, crop_img) in enumerate(self._crops):
            if is_slash:
                continue
            var = self._assign_vars[idx]
            if var is None:
                continue
            val = var.get().strip()
            if val and val != "skip" and val.isdigit():
                save_template(val, crop_img, self.cfg.templates_dir)
                saved += 1

        if saved == 0:
            messagebox.showinfo("Nothing saved",
                                "Assign digit labels (0–9) to crops before saving.")
            return

        self._save_status.configure(text=f"Saved {saved} template(s)!", text_color="green")
        self.after(3000, lambda: self._save_status.configure(text=""))
        self.refresh_digit_grid()

    def _on_clear_all(self):
        if not messagebox.askyesno("Confirm", "Delete all digit templates and start over?"):
            return
        tdir = self.cfg.templates_dir
        for d in "0123456789":
            path = os.path.join(tdir, f"{d}.png")
            if os.path.exists(path):
                os.remove(path)
        self.refresh_digit_grid()
        messagebox.showinfo("Cleared", "All templates deleted.")
