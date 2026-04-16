# TalesRunner Fish Monitor

Monitors the fish quota counter in TalesRunner via an NDI video source.
It reads the `XXX/550` display on screen every second using image processing
and template matching, then logs progress and sends alerts when the quota
is nearly full.

---

## Project Structure

```
talesrunner-fish-monitor/
├── index.py            # Main script
├── templates/          # Digit templates (created by --calibrate)
│   ├── 0.png
│   ├── 1.png
│   └── ...
├── ndi_capture.jpg     # Last captured full frame (from --capture)
└── ndi_roi_thresh.jpg  # Last captured ROI threshold image (from --capture)
```

---

## Requirements

```
pip install opencv-python ndi-python numpy requests
```

- **NDI Runtime** — download and install from [ndi.video](https://ndi.video/download-ndi-sdk/)
- **Python 3.10**
- GPU optional (not required)

---

## Settings (top of `index.py`)

| Variable | Default | Description |
|---|---|---|
| `NDI_SOURCE_NAME` | `""` | NDI source name to connect to. Empty = first source found. |
| `CANVAS_SIZE` | `(1280, 720)` | All ROI coordinates are calibrated to this resolution. |
| `ROI_QUOTA` | `(200, 480, 223, 142)` | `(x, y, width, height)` of the quota counter on screen. |
| `QUOTA_LIMIT` | `550` | Maximum fish quota. |
| `QUOTA_ALERT_BUFFER` | `20` | Alert fires when count reaches `QUOTA_LIMIT - QUOTA_ALERT_BUFFER`. |
| `REPORT_INTERVAL` | `5` | Log a status line every N fish caught. |
| `CAPTURE_INTERVAL` | `1` | Seconds between each OCR check. |
| `LINE_TOKEN` | `""` | LINE Notify token. Leave empty to disable notifications. |

---

## How It Works

```
NDI Source
   │
   ▼
FrameReader (background thread)
   │  Continuously receives NDI video frames so read() is always fresh.
   ▼
run_inference()
   │
   ├─ Resize frame to CANVAS_SIZE
   ├─ Crop ROI_QUOTA region
   ├─ preprocess_quota()
   │     3× upscale + Otsu threshold → binary image (black text / white background)
   ├─ extract_char_crops()
   │     Connected components → individual character bounding boxes
   │     '/' identified as the narrowest component in the central image band
   ├─ match_digit()  ×N
   │     Resize crop to TEMPLATE_SIZE, compare against all saved templates
   │     using normalised cross-correlation (TM_CCOEFF_NORMED).
   │     Returns '?' if best score < MATCH_MIN_SCORE (0.55).
   └─ Parse "XXX/550" → integer quota count
```

The main loop then:
- Logs every `REPORT_INTERVAL` fish caught
- Sends an alert when count reaches `QUOTA_LIMIT - QUOTA_ALERT_BUFFER`
- Logs CRITICAL and pauses 60 s when `QUOTA_LIMIT` is reached

---

## Usage

### 1. First-time calibration (required before monitoring)

Digit recognition uses template matching against saved PNGs in `templates/`.
These must be collected once before the monitor will work.

**Step 1** — check what is currently on screen, then run:
```
py -3.10 index.py --calibrate <count>
```
Example: if the screen shows `312/550`, run:
```
py -3.10 index.py --calibrate 312
```

The script saves templates for each digit it can identify. The denominator
`550` is always labelled automatically, so `5` and `0` are saved on every run.

**Step 2** — repeat with different counts until all 10 digits are collected.
The script prints which digits are still missing after each run.

```
py -3.10 index.py --calibrate 467   # adds 4, 6, 7
py -3.10 index.py --calibrate 189   # adds 1, 8, 9
py -3.10 index.py --calibrate 23    # adds 2, 3
```

### 2. Start monitoring
```
py -3.10 index.py
```

### 3. Debug mode (shows live ROI overlay and threshold window)
```
py -3.10 index.py --debug
```

### 4. Capture a single frame for ROI recalibration
```
py -3.10 index.py --capture
```
Saves `ndi_capture.jpg` (full frame) and `ndi_roi_thresh.jpg` (quota ROI
after thresholding) for inspecting whether `ROI_QUOTA` is correctly positioned.

---

## ROI Recalibration

If the game resolution or UI layout changes, `ROI_QUOTA` must be updated.

1. Run `--capture` to save `ndi_capture.jpg`.
2. Open the image and find the pixel coordinates of the quota counter.
3. Update `ROI_QUOTA = (x, y, width, height)` in `index.py`.
4. Re-run `--calibrate` to rebuild templates at the new position.

---

## LINE Notifications

Set `LINE_TOKEN` in `index.py` to your LINE Notify token, then uncomment
the two `send_line()` calls in `main()` to enable push notifications for
the near-quota alert and the limit-reached event.
