import tkinter as tk
from tkinter import ttk, messagebox
import os
import cv2
import numpy as np
import threading

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = ImageTk = None

THRESH_PREVIEW_W = 600
THRESH_PREVIEW_H = 160
CROP_THUMB_W = 80
CROP_THUMB_H = 100


class CalibrationTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.cfg = app.cfg

        # State
        self._captured_frame = None          # raw numpy frame
        self._crops = []                     # list of (x, is_slash, crop_img)
        self._assign_vars = []               # tk.StringVar per crop (dropdown value)
        self._thresh_photo = None            # keep alive
        self._crop_photos = []               # keep alive

        self._built = False

    def ensure_built(self):
        if self._built:
            return
        self._built = True
        self._build_ui()
        self.refresh_digit_grid()

    # ------------------------------------------------------------------
    def _build_ui(self):
        pad = {"padx": 6, "pady": 4}

        # ---- Capture section ----
        cap_frame = ttk.LabelFrame(self, text="Step 1 — Capture Frame from NDI")
        cap_frame.pack(fill="x", padx=12, pady=(12, 4))

        self._capture_btn = ttk.Button(cap_frame, text="Capture Frame from NDI",
                                       command=self._on_capture_frame)
        self._capture_btn.grid(row=0, column=0, **pad)
        self._capture_status = ttk.Label(cap_frame, text="No frame captured yet.",
                                         foreground="gray")
        self._capture_status.grid(row=0, column=1, sticky="w", **pad)

        # Threshold image
        ttk.Label(cap_frame, text="Preprocessed Quota ROI:").grid(
            row=1, column=0, sticky="nw", **pad)
        self._thresh_canvas = tk.Canvas(cap_frame,
                                        width=THRESH_PREVIEW_W, height=THRESH_PREVIEW_H,
                                        bg="#222", relief="sunken", borderwidth=2)
        self._thresh_canvas.grid(row=1, column=1, sticky="w", **pad)
        self._thresh_canvas.create_text(
            THRESH_PREVIEW_W // 2, THRESH_PREVIEW_H // 2,
            text="Capture a frame to see the threshold image",
            fill="white", font=("Arial", 11), tags="placeholder",
        )

        # ---- Assign section ----
        assign_outer = ttk.LabelFrame(
            self, text="Step 2 — Assign Labels to Detected Character Crops")
        assign_outer.pack(fill="x", padx=12, pady=4)

        ttk.Label(assign_outer,
                  text="Each box is one detected character. Set the correct digit (0–9) or 'skip'.\n"
                       "The '/' separator is detected automatically and shown as a label.",
                  foreground="gray").pack(anchor="w", padx=6, pady=2)

        # Scrollable row of crops
        self._crops_outer = ttk.Frame(assign_outer)
        self._crops_outer.pack(fill="x", padx=6, pady=4)

        self._no_crops_label = ttk.Label(
            self._crops_outer, text="No crops detected yet.", foreground="gray")
        self._no_crops_label.pack()

        # ---- Save / Clear buttons ----
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", padx=12, pady=4)
        ttk.Button(btn_frame, text="Save Selected Templates",
                   command=self._on_save_templates).pack(side="left")
        ttk.Button(btn_frame, text="Clear All Templates",
                   command=self._on_clear_all).pack(side="left", padx=8)
        self._save_status = ttk.Label(btn_frame, text="", foreground="green")
        self._save_status.pack(side="left", padx=8)

        # ---- Digit status grid ----
        grid_outer = ttk.LabelFrame(
            self, text="Template Status  (all 10 digits required to unlock Monitor)")
        grid_outer.pack(fill="x", padx=12, pady=(4, 12))

        self._digit_status_labels = {}
        row_frame = ttk.Frame(grid_outer)
        row_frame.pack(padx=8, pady=6)
        for i, d in enumerate("0123456789"):
            col = ttk.Frame(row_frame, borderwidth=1, relief="groove", width=58, height=70)
            col.pack(side="left", padx=3)
            col.pack_propagate(False)
            ttk.Label(col, text=d, font=("Arial", 16, "bold")).pack()
            lbl = ttk.Label(col, text="✗", foreground="red", font=("Arial", 14))
            lbl.pack()
            self._digit_status_labels[d] = lbl

        self._calibration_count_var = tk.StringVar(value="0/10 digits calibrated")
        ttk.Label(grid_outer, textvariable=self._calibration_count_var,
                  font=("Arial", 11)).pack(pady=(0, 4))

    # ------------------------------------------------------------------
    def refresh_digit_grid(self):
        """Re-check templates dir and update ✓/✗ labels."""
        if not self._built:
            return
        tdir = self.cfg.templates_dir
        count = 0
        for d in "0123456789":
            path = os.path.join(tdir, f"{d}.png")
            exists = os.path.exists(path)
            lbl = self._digit_status_labels[d]
            if exists:
                lbl.configure(text="✓", foreground="green")
                count += 1
            else:
                lbl.configure(text="✗", foreground="red")
        self._calibration_count_var.set(f"{count}/10 digits calibrated")
        # Notify app so monitor tab updates its lock state
        self.app.reload_templates()

    # ------------------------------------------------------------------
    def _on_capture_frame(self):
        self._capture_btn.configure(state="disabled")
        self._capture_status.configure(text="Capturing…", foreground="orange")

        def _worker():
            # Reuse existing reader if available
            ret, frame = self.app.get_frame()
            if not ret or frame is None:
                # Open a new connection and keep it alive for all tabs to reuse
                from core import open_stream
                import time
                try:
                    reader = open_stream(
                        self.cfg["ndi_source_name"],
                        self.cfg["reconnect_delay"],
                        copy_interval=self.cfg["capture_interval"],
                    )
                    time.sleep(1)
                    ret, frame = reader.read()
                    self.app.frame_reader = reader   # keep alive — shared by all tabs
                except Exception:
                    self.after(0, lambda: self._process_captured(None))
                    return
            self.after(0, lambda: self._process_captured(frame if ret else None))

        threading.Thread(target=_worker, daemon=True).start()

    def _process_captured(self, frame):
        self._capture_btn.configure(state="normal")
        if frame is None:
            self._capture_status.configure(
                text="Failed to capture frame. Check NDI source.", foreground="red")
            return

        from core import preprocess_quota, extract_char_crops, crop as core_crop

        self._captured_frame = frame
        canvas_size = self.cfg.canvas_size
        roi_quota = self.cfg.roi_quota

        resized = cv2.resize(frame, canvas_size)
        roi_img = core_crop(resized, roi_quota)
        thresh = preprocess_quota(roi_img)
        chars = extract_char_crops(thresh)

        self._crops = chars
        self._capture_status.configure(
            text=f"Captured. Detected {len(chars)} components.", foreground="green")

        self._display_thresh(thresh)
        self._display_crops(chars)

    # ------------------------------------------------------------------
    def _display_thresh(self, thresh):
        if Image is None:
            return
        h, w = thresh.shape
        scale = min(THRESH_PREVIEW_W / w, THRESH_PREVIEW_H / h)
        disp_w = int(w * scale)
        disp_h = int(h * scale)
        resized = cv2.resize(thresh, (disp_w, disp_h))
        # thresh is binary; convert to RGB for display
        rgb = cv2.cvtColor(resized, cv2.COLOR_GRAY2RGB)
        pil_img = Image.fromarray(rgb)
        padded = Image.new("RGB", (THRESH_PREVIEW_W, THRESH_PREVIEW_H), (34, 34, 34))
        off_x = (THRESH_PREVIEW_W - disp_w) // 2
        off_y = (THRESH_PREVIEW_H - disp_h) // 2
        padded.paste(pil_img, (off_x, off_y))

        self._thresh_photo = ImageTk.PhotoImage(padded)
        self._thresh_canvas.delete("all")
        self._thresh_canvas.create_image(0, 0, anchor="nw", image=self._thresh_photo)

    def _display_crops(self, chars):
        # Clear existing crop widgets
        for w in self._crops_outer.winfo_children():
            w.destroy()
        self._assign_vars = []
        self._crop_photos = []

        if not chars:
            ttk.Label(self._crops_outer, text="No crops detected.",
                      foreground="gray").pack()
            return

        self._no_crops_label = None

        for idx, (cx, is_slash, crop_img) in enumerate(chars):
            col = ttk.Frame(self._crops_outer, borderwidth=1, relief="groove")
            col.pack(side="left", padx=4, pady=4)

            if is_slash:
                ttk.Label(col, text="/", font=("Arial", 20, "bold"),
                          foreground="orange", width=4).pack(pady=4)
                ttk.Label(col, text="separator", foreground="gray",
                          font=("Arial", 8)).pack()
                self._assign_vars.append(None)  # placeholder
            else:
                # Show crop thumbnail
                if Image is not None:
                    h_c, w_c = crop_img.shape[:2]
                    scale = min(CROP_THUMB_W / w_c, CROP_THUMB_H / h_c)
                    dw, dh = max(1, int(w_c * scale)), max(1, int(h_c * scale))
                    thumb = cv2.resize(crop_img, (dw, dh))
                    if len(thumb.shape) == 2:
                        thumb_rgb = cv2.cvtColor(thumb, cv2.COLOR_GRAY2RGB)
                    else:
                        thumb_rgb = cv2.cvtColor(thumb, cv2.COLOR_BGR2RGB)
                    pil_thumb = Image.fromarray(thumb_rgb)
                    # Pad to fixed thumb size
                    padded = Image.new("RGB", (CROP_THUMB_W, CROP_THUMB_H), (200, 200, 200))
                    padded.paste(pil_thumb, ((CROP_THUMB_W - dw) // 2,
                                             (CROP_THUMB_H - dh) // 2))
                    photo = ImageTk.PhotoImage(padded)
                    self._crop_photos.append(photo)

                    lbl = tk.Label(col, image=photo, borderwidth=0)
                    lbl.pack()

                # Dropdown
                var = tk.StringVar(value="")
                values = [""] + [str(i) for i in range(10)] + ["skip"]
                cb = ttk.Combobox(col, textvariable=var, values=values,
                                  width=5, state="readonly")
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

        self._save_status.configure(text=f"Saved {saved} template(s)!")
        self.after(3000, lambda: self._save_status.configure(text=""))
        self.refresh_digit_grid()

    def _on_clear_all(self):
        if not messagebox.askyesno(
                "Confirm", "Delete all digit templates and start over?"):
            return
        tdir = self.cfg.templates_dir
        for d in "0123456789":
            path = os.path.join(tdir, f"{d}.png")
            if os.path.exists(path):
                os.remove(path)
        self.refresh_digit_grid()
        messagebox.showinfo("Cleared", "All templates deleted.")
