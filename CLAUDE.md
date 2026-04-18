# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A passive screen-reading notification tool for TalesRunner (online game). It reads the fishing quota counter (`428/550`) from the game screen via OBS Virtual Camera, uses OpenCV template matching for OCR, and sends Telegram alerts. It does **not** automate any in-game action — human interaction is always required.

## Running the project

```bash
# Install (Windows, from repo root)
python -m venv venv
venv\Scripts\pip install -e ".[windows]"

# GUI (primary)
venv\Scripts\python app.py

# CLI (headless)
venv\Scripts\python index.py
venv\Scripts\python index.py --debug         # live ROI overlay window
venv\Scripts\python index.py --list-cameras  # enumerate DirectShow devices
venv\Scripts\python index.py --log-file monitor.log
venv\Scripts\python index.py --no-timeout    # disable auto-shutdown
```

There are no automated tests. `test_ocr.py`, `test_telegram.py`, `test_discord.py` are manual scratch scripts.

## Architecture

### Single source of truth: `core.py`

All logic lives here. No GUI imports. Key components:

- **`Config`** — loads/saves `config.json` next to the exe/script. `canvas_size` is a constant property returning `(1280, 720)` — it is NOT stored in config. `quota_limit` in config is a *fallback* only; the real limit is parsed from the screen denominator.
- **`CameraFrameReader`** — wraps `cv2.VideoCapture` in a background thread with a double-buffered frame store so `read()` never blocks.
- **`scan_cameras()`** — uses `pygrabber.dshow_graph.FilterGraph` to get DirectShow device names (same index order as cv2), then probes each with `VideoCapture.isOpened()`. Includes cameras even if the first `read()` fails (OBS Virtual Camera registers before streaming starts).
- **`run_inference(frame, templates, roi_quota, canvas_size)`** — full pipeline: resize → crop ROI → preprocess → extract chars → match digits → parse `XXX/YYY`. Returns `{"quota_raw", "quota_current", "quota_limit"}`. `quota_limit` is `None` when the denominator isn't readable.
- **`MonitorSession`** dataclass — per-session state: `start_count`, `alert_sent`, `limit_sent`, `prev_current` (for reset detection).

### OCR pipeline detail

```
preprocess_quota()     3× upscale + Otsu threshold → black-on-white binary
extract_char_crops()   dilate with (3,1) kernel to break touching digits
                       → connectedComponentsWithStats on inverted image
                       → _split_component() valley detection as fallback splitter
                       → narrowest centred blob identified as '/'
match_digit()          resize crop to (80,120), TM_CCOEFF_NORMED vs templates/
                       threshold MATCH_MIN_SCORE = 0.55
```

Templates are stored as `templates/0.png` … `templates/9.png`, always 80×120 px grayscale. All 10 digits must exist before monitoring unlocks.

### GUI: `app.py` + `gui/`

`App` (ctk.CTk) owns shared state: `cfg`, `frame_reader`, `templates`, `log_queue`. Tabs are lazy-built on first visit via `ensure_built()`. Log events travel from the monitor worker thread → `log_queue` → `_poll_log_queue()` (runs every 100 ms on main thread via `after()`) → `monitor_tab._apply_update()`.

`gui/__init__.py` exports only `labeled_frame(parent, title)` — a `CTkFrame` pair `(outer, inner)` used as a titled section box throughout all tabs.

### GUI open_camera_stream max_retries convention

- GUI tabs pass `max_retries=1` to `open_input_stream()` / `open_camera_stream()` to prevent infinite hang on misconfigured camera.
- The CLI monitor loop passes `max_retries=None` for infinite retry with reconnect delay.

### Quota limit resolution (dynamic detection)

`run_inference` parses both sides of `/`. The monitor loop uses:
```python
limit = result["quota_limit"] or cfg["quota_limit"]
```
`cfg["quota_limit"]` is a fallback used only when the on-screen denominator is unreadable.

### Config keys

`config.json` is written atomically (write to `.tmp`, then `os.replace`). Keys: `virtual_camera_index`, `virtual_camera_name`, `roi_quota` ([x,y,w,h] on 1280×720 canvas), `quota_limit` (fallback), `quota_alert_buffer`, `report_interval`, `capture_interval`, `reconnect_delay`, `no_read_timeout`, `telegram_bot_token`, `telegram_chat_id`, `templates_dir`.

## Key constraints

- **Canvas is always 1280×720.** `cfg.canvas_size` is a property returning a constant — do not add UI to change it. OBS must be configured to match.
- **Game resolution must be 1280×960** so the crop-to-canvas workflow produces the correct aspect ratio for the quota panel.
- **No NDI.** NDI was removed completely. Input is OBS Virtual Camera only (`cv2.VideoCapture` with no `CAP_DSHOW` flag).
- **No UPX.** PyInstaller was removed from the project. Run directly with Python.
- **`pygrabber`** is Windows-only (DirectShow). Do not call `scan_cameras()` on non-Windows platforms.
- **Template matching, not ML.** The game font is fixed. Do not introduce neural network dependencies.
