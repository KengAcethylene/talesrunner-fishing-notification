import tkinter as tk
from tkinter import ttk
import threading
import cv2

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = ImageTk = None

PREVIEW_W = 800
PREVIEW_H = 450


class ROITab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.cfg = app.cfg

        self.mode_var = tk.StringVar(value="quota")

        # Drag state
        self._drag_start = None
        self._drag_rect_id = None

        # Frozen frame (numpy BGR) and its display photo
        self._captured_frame = None
        self._canvas_img_id = None
        self._photo = None          # keep reference alive

        self._built = False

    def ensure_built(self):
        if self._built:
            return
        self._built = True
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self):
        pad = {"padx": 6, "pady": 4}

        # ---- Top bar ----
        top = ttk.Frame(self)
        top.pack(fill="x", padx=12, pady=(10, 0))

        ttk.Label(top, text="NDI source is taken from Settings.").pack(side="left")
        self._capture_btn = ttk.Button(top, text="Capture Frame from NDI",
                                       command=self._on_capture)
        self._capture_btn.pack(side="left", padx=8)
        self._status_var = tk.StringVar(value="No frame captured yet.")
        ttk.Label(top, textvariable=self._status_var, foreground="gray").pack(side="left")

        # ---- Mode selector ----
        mode_frame = ttk.LabelFrame(self, text="Draw Mode")
        mode_frame.pack(fill="x", padx=12, pady=(8, 0))

        ttk.Radiobutton(mode_frame, text="Quota ROI  (green)",
                        variable=self.mode_var, value="quota").pack(side="left", padx=12)
        ttk.Radiobutton(mode_frame, text="Time ROI  (blue)",
                        variable=self.mode_var, value="time").pack(side="left", padx=12)
        ttk.Label(mode_frame,
                  text="← Select mode, then drag on the image to draw a rectangle",
                  foreground="gray").pack(side="left", padx=8)

        # ---- Canvas ----
        canvas_outer = ttk.Frame(self, relief="sunken", borderwidth=2)
        canvas_outer.pack(padx=12, pady=8)
        self.canvas = tk.Canvas(canvas_outer, width=PREVIEW_W, height=PREVIEW_H,
                                bg="black", cursor="crosshair")
        self.canvas.pack()

        self.canvas.bind("<Button-1>",        self._on_mouse_press)
        self.canvas.bind("<B1-Motion>",       self._on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_mouse_release)

        self._placeholder_id = self.canvas.create_text(
            PREVIEW_W // 2, PREVIEW_H // 2,
            text="Click 'Capture Frame from NDI' to load an image",
            fill="white", font=("Arial", 13),
        )

        # ---- ROI info ----
        info_frame = ttk.LabelFrame(
            self, text="Current ROI Coordinates  (x, y, width, height in canvas-size pixels)")
        info_frame.pack(fill="x", padx=12, pady=4)

        self._quota_roi_var = tk.StringVar()
        self._time_roi_var = tk.StringVar()

        ttk.Label(info_frame, text="Quota ROI:", foreground="green").grid(
            row=0, column=0, sticky="w", padx=8, pady=2)
        ttk.Label(info_frame, textvariable=self._quota_roi_var).grid(
            row=0, column=1, sticky="w", padx=4)

        ttk.Label(info_frame, text="Time ROI:", foreground="blue").grid(
            row=1, column=0, sticky="w", padx=8, pady=2)
        ttk.Label(info_frame, textvariable=self._time_roi_var).grid(
            row=1, column=1, sticky="w", padx=4)

        self._refresh_roi_labels()

        # ---- Save ----
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", padx=12, pady=(4, 12))
        ttk.Button(btn_frame, text="Save ROI", command=self._on_save_roi).pack(side="left")
        self._save_status = ttk.Label(btn_frame, text="", foreground="green")
        self._save_status.pack(side="left", padx=8)

    # ------------------------------------------------------------------
    # Capture one frame
    # ------------------------------------------------------------------
    def _on_capture(self):
        self._capture_btn.configure(state="disabled")
        self._status_var.set("Capturing…")

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
                except Exception as e:
                    self.after(0, lambda: self._on_frame_ready(None))
                    return
            self.after(0, lambda: self._on_frame_ready(frame if ret else None))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_frame_ready(self, frame):
        self._capture_btn.configure(state="normal")
        if frame is None:
            self._status_var.set("Failed. Check NDI source in Settings.")
            return
        self._captured_frame = frame
        self._status_var.set("Frame captured. Drag rectangles to set ROIs, then Save ROI.")
        self._redraw_frame()

    # ------------------------------------------------------------------
    # Render the frozen frame with current ROI overlays
    # ------------------------------------------------------------------
    def _redraw_frame(self):
        if self._captured_frame is None or Image is None:
            return

        cfg_w, cfg_h = self.cfg.canvas_size
        resized = cv2.resize(self._captured_frame, (cfg_w, cfg_h))

        # Draw ROI rectangles
        q = self.cfg.roi_quota
        t = self.cfg.roi_time
        cv2.rectangle(resized, (q[0], q[1]), (q[0]+q[2], q[1]+q[3]), (0, 255, 0), 2)
        cv2.putText(resized, "QUOTA", (q[0], max(q[1]-4, 10)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA)

        cv2.rectangle(resized, (t[0], t[1]), (t[0]+t[2], t[1]+t[3]), (255, 0, 0), 2)
        cv2.putText(resized, "TIME", (t[0], max(t[1]-4, 10)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 1, cv2.LINE_AA)

        # Letterbox scale to canvas display size
        scale = min(PREVIEW_W / cfg_w, PREVIEW_H / cfg_h)
        disp_w = int(cfg_w * scale)
        disp_h = int(cfg_h * scale)
        display = cv2.resize(resized, (disp_w, disp_h))

        rgb = cv2.cvtColor(display, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(rgb)

        padded = Image.new("RGB", (PREVIEW_W, PREVIEW_H), (0, 0, 0))
        off_x = (PREVIEW_W - disp_w) // 2
        off_y = (PREVIEW_H - disp_h) // 2
        padded.paste(pil_img, (off_x, off_y))

        self._photo = ImageTk.PhotoImage(padded)

        if self._canvas_img_id is None:
            self.canvas.delete(self._placeholder_id)
            self._canvas_img_id = self.canvas.create_image(0, 0, anchor="nw",
                                                            image=self._photo)
        else:
            self.canvas.itemconfig(self._canvas_img_id, image=self._photo)

    # ------------------------------------------------------------------
    # Coordinate conversion
    # ------------------------------------------------------------------
    def _canvas_to_frame(self, cx, cy):
        cfg_w, cfg_h = self.cfg.canvas_size
        scale = min(PREVIEW_W / cfg_w, PREVIEW_H / cfg_h)
        off_x = (PREVIEW_W - cfg_w * scale) / 2
        off_y = (PREVIEW_H - cfg_h * scale) / 2
        fx = int((cx - off_x) / scale)
        fy = int((cy - off_y) / scale)
        return max(0, min(fx, cfg_w - 1)), max(0, min(fy, cfg_h - 1))

    # ------------------------------------------------------------------
    # Mouse handlers
    # ------------------------------------------------------------------
    def _on_mouse_press(self, event):
        if self._captured_frame is None:
            return
        self._drag_start = (event.x, event.y)
        color = "lime" if self.mode_var.get() == "quota" else "cyan"
        self._drag_rect_id = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y,
            outline=color, width=2, dash=(4, 2),
        )

    def _on_mouse_drag(self, event):
        if self._drag_rect_id is not None and self._drag_start:
            x0, y0 = self._drag_start
            self.canvas.coords(self._drag_rect_id, x0, y0, event.x, event.y)

    def _on_mouse_release(self, event):
        if self._drag_start is None:
            return

        x0c, y0c = self._drag_start
        x1c, y1c = event.x, event.y

        if self._drag_rect_id is not None:
            self.canvas.delete(self._drag_rect_id)
            self._drag_rect_id = None
        self._drag_start = None

        fx0, fy0 = self._canvas_to_frame(x0c, y0c)
        fx1, fy1 = self._canvas_to_frame(x1c, y1c)

        x = min(fx0, fx1)
        y = min(fy0, fy1)
        w = abs(fx1 - fx0)
        h = abs(fy1 - fy0)

        if w < 5 or h < 5:
            return

        if self.mode_var.get() == "quota":
            self.cfg.set("roi_quota", [x, y, w, h])
        else:
            self.cfg.set("roi_time", [x, y, w, h])

        self._refresh_roi_labels()
        self._redraw_frame()      # redraw overlays on the frozen frame

    # ------------------------------------------------------------------
    def _refresh_roi_labels(self):
        q = self.cfg.roi_quota
        t = self.cfg.roi_time
        self._quota_roi_var.set(f"x={q[0]}, y={q[1]}, w={q[2]}, h={q[3]}")
        self._time_roi_var.set(f"x={t[0]}, y={t[1]}, w={t[2]}, h={t[3]}")

    def _on_save_roi(self):
        self.cfg.save()
        self._refresh_roi_labels()
        self._save_status.configure(text="ROI saved!")
        self.after(2000, lambda: self._save_status.configure(text=""))
