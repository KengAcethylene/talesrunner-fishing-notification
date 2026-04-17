# TalesRunner Fish Monitor

A passive monitoring tool that watches the fish quota counter in TalesRunner and
sends a Telegram alert when the limit is almost reached.

> **This is not a cheat or bot program.**
> It only reads numbers from the screen and sends a notification.
> The game is never automated — a human must manually stop fishing when the quota is reached.

---

## How It Works (overview)

```
TalesRunner (game)
      │  display
      ▼
OBS Studio  ──  Window Capture → crop to quota panel → Virtual Camera
      │
      ▼
TalesRunner Fish Monitor  (this app)
      │  reads frames from OBS Virtual Camera
      │  OCR on the XXX/550 counter
      ▼
Telegram alert  ──  "Quota almost full: 500/550"
```

---

## Requirements

- **Python 3.10 or newer** — [python.org](https://www.python.org/downloads/)
- **OBS Studio** — [obsproject.com](https://obsproject.com/)
- **Windows 10/11**

---

## Installation

**1. Clone or download this repository**
```
git clone https://github.com/yourname/talesrunner-fish-monitor.git
cd talesrunner-fish-monitor
```

**2. Create a virtual environment and install dependencies**
```
python -m venv venv
venv\Scripts\pip install -e ".[windows]"
```

---

## First-time Setup

### Step 1 — OBS Settings

1. Set TalesRunner's in-game resolution to **1280×960**
   (in-game Settings → Display)
2. Open OBS → **Settings → Video**
   - Canvas Resolution: `1280×720`
   - Output Resolution: `1280×720`
3. Add a source: **Sources → + → Window Capture** (or Game Capture) → select TalesRunner
4. **Crop** the source to the quota panel area (the **red square** shown in `ingame-crop-example.jpg`):
   - Hold **Alt** and drag any edge to crop
   - Hold **Shift** while resizing to stretch it to fill the full canvas
5. Once the crop looks correct, open OBS → **Settings → Output** → set Frame Rate to `1 FPS`
   (fish monitoring doesn't need more than 1 FPS)
6. Click **Start Virtual Camera** in OBS

### Step 2 — Settings tab

1. Run the app (see below), open the **Settings** tab
2. Click **Scan Cameras** → select the OBS Virtual Camera → **Save Settings**
3. Enter your Telegram Bot Token and Chat ID (see Setup tab in the app for instructions)

### Step 3 — Calibration

Digit recognition uses template matching. All 10 digits (0–9) must be captured once.

1. Start fishing in TalesRunner so the quota counter is visible
2. Open the **Calibration** tab → **Capture Frame**
3. Assign the correct digit label to each detected crop
4. Click **Save Selected Templates**
5. Repeat until all 10 digits show ✓

### Step 4 — Monitor

Open the **Monitor** tab → **Start Monitoring**

---

## Running the App

### GUI (recommended)
```
venv\Scripts\python app.py
```

### CLI (headless / background use)
```
venv\Scripts\python index.py
```

CLI options:
```
--debug           Show live ROI overlay window (requires display)
--list-cameras    List available camera devices and exit
--log-file PATH   Write all log output to a file
--no-timeout      Disable auto-shutdown when no numbers are detected
```

---

## Project Files

```
talesrunner-fish-monitor/
├── app.py                  # GUI entry point (customtkinter)
├── index.py                # CLI entry point
├── core.py                 # All shared logic (OCR, config, camera)
├── gui/
│   ├── setup_tab.py        # Setup instructions tab
│   ├── settings_tab.py     # Camera, Telegram, quota settings
│   ├── roi_tab.py          # Draw the quota ROI region
│   ├── calibration_tab.py  # Capture and label digit templates
│   └── monitor_tab.py      # Live monitoring view
├── templates/              # Digit template images (created by Calibration tab)
│   ├── 0.png … 9.png
├── ingame-crop-example.jpg # Reference image shown in Setup tab
└── pyproject.toml          # Package / dependency config
```

---

## Telegram Notifications

| Event | Message |
|---|---|
| Quota near limit | `QUOTA ALMOST FULL: 500/550` |
| Quota limit reached | `LIMIT REACHED: 550/550` |
| No numbers detected for 60 s | `No quota numbers detected — shutting down` |

See the **Setup tab** in the app for step-by-step bot creation instructions.

---

## Disclaimer

This tool does **not** interact with the game process, inject code, read game memory,
or automate any in-game action. It captures a screen region via OBS Virtual Camera
and reads numbers using image processing — the same as a human watching a second monitor.
When the quota limit is reached, a Telegram notification is sent and the **human player
decides what to do next**. No game rules are broken.
