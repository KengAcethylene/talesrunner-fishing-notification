# Architecture

## Purpose & Scope

TalesRunner Fish Monitor is a **passive screen-reading notification tool**.
It captures a video feed from OBS Virtual Camera, uses image processing to read
the fish quota counter (`XXX/550`) displayed in-game, and sends a Telegram alert
when the limit is close.

**It does not:**
- Access or modify the game process or memory
- Automate any in-game action (no mouse/keyboard injection)
- Communicate with game servers
- Modify game files

**Human interaction is required** to stop or continue fishing when the quota limit
is reached. The tool only observes and notifies.

---

## High-Level Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│  TalesRunner (game)                                          │
│  Renders quota counter: "428/550"                            │
└────────────────────┬────────────────────────────────────────┘
                     │ screen pixels
                     ▼
┌─────────────────────────────────────────────────────────────┐
│  OBS Studio                                                  │
│  Window Capture → crop to quota panel → resize to 1280×720  │
│  Output: Virtual Camera (V4L2 / DirectShow device)           │
└────────────────────┬────────────────────────────────────────┘
                     │ cv2.VideoCapture frames
                     ▼
┌─────────────────────────────────────────────────────────────┐
│  CameraFrameReader  (core.py)                                │
│  Background thread — continuously reads frames so the        │
│  main loop always gets a fresh one without blocking.         │
└────────────────────┬────────────────────────────────────────┘
                     │ numpy BGR frame (1280×720)
                     ▼
┌─────────────────────────────────────────────────────────────┐
│  run_inference()  (core.py)                                  │
│  1. Crop ROI_QUOTA region from the frame                     │
│  2. preprocess_quota() — upscale + Otsu threshold            │
│  3. extract_char_crops() — connected components + split      │
│  4. match_digit() × N — template cross-correlation           │
│  5. Parse "XXX/YYY" → integer quota_current                  │
└────────────────────┬────────────────────────────────────────┘
                     │ {"quota_current": 428, "quota_raw": "428/550"}
                     ▼
┌─────────────────────────────────────────────────────────────┐
│  MonitorSession  (core.py / monitor_tab.py / index.py)       │
│  Tracks: start count, last reported, alert/limit sent flags  │
│  Detects quota reset (new fishing session)                   │
│  Fires send_telegram() at threshold and limit events         │
└─────────────────────────────────────────────────────────────┘
                     │ HTTP POST
                     ▼
              Telegram Bot API
```

---

## Module Breakdown

### `core.py`

All shared logic. No GUI imports.

| Component | Description |
|---|---|
| `Config` | Loads/saves `config.json`. Provides typed access to all settings. Canvas size is fixed at 1280×720. |
| `CameraFrameReader` | Wraps `cv2.VideoCapture` in a background thread. Double-buffered so the latest frame is always available without blocking. |
| `scan_cameras()` | Enumerates capture devices using `pygrabber` (DirectShow) for names, cross-referenced with `cv2` index order. |
| `open_camera_stream()` | Opens a `CameraFrameReader` with optional retry limit (GUI passes `max_retries=1` to avoid hanging). |
| `preprocess_quota()` | Upscales ROI 3×, converts to grayscale, applies Otsu thresholding → black text on white background. |
| `extract_char_crops()` | Morphological dilation (3×1 kernel) breaks touching digits before connected component analysis. Valley detection splits any remaining merged blobs. Returns list of `(x, is_slash, crop_img)`. |
| `match_digit()` | Resizes crop to `TEMPLATE_SIZE`, runs `cv2.matchTemplate(TM_CCOEFF_NORMED)` against all saved templates. Returns `'?'` if best score < 0.55. |
| `run_inference()` | Orchestrates the full pipeline from frame → quota integer. |
| `MonitorSession` | Dataclass tracking per-session state (start count, alert flags, uptime). |
| `send_telegram()` | HTTP POST to Telegram Bot API. Non-blocking — called from worker thread. |

### `app.py`

GUI entry point using **customtkinter** (dark-mode tkinter wrapper).

- Creates a `CTkTabview` with 5 tabs
- Owns the shared `frame_reader`, `cfg`, `templates`, and `log_queue`
- `_poll_log_queue()` drains the queue every 100 ms on the main thread and dispatches updates to `MonitorTab`

### `gui/` tabs

| File | Tab | Role |
|---|---|---|
| `setup_tab.py` | Setup | Static instructions: OBS config, game crop, Telegram bot creation |
| `settings_tab.py` | Settings | Camera selection, Telegram credentials, quota limit — all persisted to `config.json` |
| `roi_tab.py` | ROI Setup | Live preview with drag-to-draw quota ROI rectangle. Saves coordinates to config. |
| `calibration_tab.py` | Calibration | Capture a frame, view preprocessed threshold, assign digit labels to detected crops, save PNGs to `templates/` |
| `monitor_tab.py` | Monitor | Start/stop worker thread, live log, fish count progress bar, uptime |

### `index.py`

CLI entry point. Runs the same `core.py` pipeline in a simple `while True` loop.
Useful for headless/background use or running directly without the GUI.

---

## OCR Pipeline Detail

```
ROI crop (raw pixels)
      │
      ▼  cv2.resize ×3
      │  cv2.cvtColor → GRAY
      │  cv2.threshold (Otsu)
      ▼
Binary image — black text, white background
      │
      ▼  cv2.dilate (3×1 kernel, 1 iteration)
         Erodes horizontal pixel bridges between touching digits
      │
      ▼  cv2.connectedComponentsWithStats
         One bounding box per character blob
      │
      ├─ blob too wide? → _split_component()
      │    Column-wise dark-pixel density → find valley → split at minimum
      │
      ▼
List of character crops  [(x_pos, is_slash, crop_img), ...]
      │
      ▼  For each non-slash crop:
         cv2.resize to TEMPLATE_SIZE (30×40)
         cv2.matchTemplate vs templates/0.png … 9.png
         Pick digit with highest correlation score
      │
      ▼
"428/550"  →  quota_current = 428
```

### Why template matching instead of a neural network?

- The game font is fixed — character appearance never changes
- Template matching is deterministic and fast (<1 ms per frame)
- No GPU, no model weights, no training data required
- Works offline with zero external dependencies beyond OpenCV

---

## Configuration (`config.json`)

Created automatically next to the executable (or `app.py`) on first save.

| Key | Type | Description |
|---|---|---|
| `virtual_camera_index` | int | `cv2.VideoCapture` index for OBS Virtual Camera |
| `virtual_camera_name` | str | Friendly name shown in UI |
| `roi_quota` | [x, y, w, h] | Quota counter region on the 1280×720 canvas |
| `quota_limit` | int | Maximum fish quota (default 550) |
| `quota_alert_buffer` | int | Alert fires at `quota_limit − buffer` |
| `report_interval` | int | Log every N fish caught |
| `capture_interval` | int | Seconds between OCR reads |
| `no_read_timeout` | int | Shutdown after N seconds of no detection |
| `telegram_bot_token` | str | Telegram Bot API token |
| `telegram_chat_id` | str | Telegram chat/user ID to send alerts to |

---

## Threading Model

```
Main thread (tkinter event loop)
│
├── _poll_log_queue()  — runs every 100 ms via after()
│     Drains log_queue → updates MonitorTab UI
│
└── MonitorTab._monitor_thread  (daemon)
      Runs the OCR loop
      Puts (msg_type, payload) tuples onto log_queue
      Reads frame_reader (thread-safe double buffer)
      Calls send_telegram() directly (requests is thread-safe)
```

GUI never blocks — all camera I/O and OCR happen on the worker thread.
UI updates are marshalled back to the main thread through the queue.

