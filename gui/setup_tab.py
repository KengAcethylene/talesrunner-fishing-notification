import tkinter as tk
import customtkinter as ctk
import os
import sys

from gui import labeled_frame


def _asset_path(filename):
    """Resolve a bundled asset path (works frozen and unfrozen)."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, filename)


class SetupTab(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        self._example_photo = None
        self._built = False

    def ensure_built(self):
        if self._built:
            return
        self._built = True
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self):
        # Make the tab scrollable so nothing gets cut off on small windows
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        # ---- Game Resolution & OBS Setup ----
        obs_outer, obs_frame = labeled_frame(scroll, "Step 1 — OBS Settings")
        obs_outer.pack(fill="x", padx=12, pady=(12, 4))
        ctk.CTkLabel(
            obs_frame,
            text=(
                "1. Set TalesRunner resolution to 1280×960\n"
                "       (in-game Settings → Display)\n\n"
                "2. In OBS → Settings → Video\n"
                "       • Set Canvas Resolution to 1280×720\n"
                "       • Set Output Resolution to 1280×720\n\n"
                "3. In OBS → Settings → Output → set Frame Rate to 1 FPS\n"
                "       (fish monitoring doesn't need more than 1 FPS)\n\n"
                "4. Start Virtual Camera:  OBS → Start Virtual Camera"
            ),
            text_color="gray",
            justify="left",
            anchor="nw",
        ).pack(anchor="w", padx=8, pady=8)

        # ---- Game Capture Setup ----
        cap_outer, cap_frame = labeled_frame(scroll, "Step 2 — Capture & Crop the Quota Area")
        cap_outer.pack(fill="x", padx=12, pady=4)

        # Left: example image
        img_path = _asset_path("ingame-crop-example.jpg")
        try:
            from PIL import Image, ImageTk
            img = Image.open(img_path)
            img.thumbnail((420, 280), Image.LANCZOS)
            self._example_photo = ImageTk.PhotoImage(img)
            tk.Label(cap_frame, image=self._example_photo,
                     borderwidth=1, relief="solid", bg="#1a1a1a").grid(
                row=0, column=0, padx=(6, 14), pady=6, sticky="nw")
        except Exception:
            ctk.CTkLabel(cap_frame, text="[example image not found]",
                         text_color="gray").grid(row=0, column=0, padx=6, pady=6)

        # Right: instructions
        ctk.CTkLabel(
            cap_frame,
            text=(
                "1. In OBS, add a source:\n"
                "       Sources → + → Window Capture (or Game Capture)\n"
                "       Select the TalesRunner window\n\n"
                "2. Crop the source to the quota panel\n"
                "       (the red box shown in the example image):\n"
                "       • Hold  Alt  and drag any edge of the source to crop\n\n"
                "3. Resize the cropped area to fill the full OBS canvas:\n"
                "       • Hold  Shift  while dragging to stretch it to\n"
                "         fill the full 1280×720 canvas\n\n"
                "4. Go to  Settings tab  → Scan Cameras → select the\n"
                "       OBS Virtual Camera → Save Settings"
            ),
            text_color="gray",
            justify="left",
            anchor="nw",
        ).grid(row=0, column=1, sticky="nw", padx=(0, 8), pady=6)

        cap_frame.grid_columnconfigure(1, weight=1)

        # ---- Telegram Bot Setup ----
        tg_outer, tg_frame = labeled_frame(scroll, "Step 3 — Create a Telegram Bot (optional)")
        tg_outer.pack(fill="x", padx=12, pady=(4, 12))
        ctk.CTkLabel(
            tg_frame,
            text=(
                "1. Open Telegram and search for  @BotFather\n\n"
                "2. Send  /newbot  — follow the prompts to name your bot\n\n"
                "3. Copy the Bot Token BotFather gives you\n"
                "       → paste it into  Bot Token  in the Settings tab\n\n"
                "4. To find your Chat ID:\n"
                "       • Send any message to your new bot\n"
                '       • Open:  https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates\n'
                '       • Copy the  "id"  value inside  "chat"\n'
                "       → paste it into  Chat ID  in the Settings tab\n\n"
                "5. Click  Test Telegram  in the Settings tab to verify."
            ),
            text_color="gray",
            justify="left",
            anchor="nw",
        ).pack(anchor="w", padx=8, pady=8)
