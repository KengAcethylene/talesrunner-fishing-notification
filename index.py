import cv2
import numpy as np
import pytesseract
import time
import requests
from datetime import datetime

# ============================================================
# SETTINGS
# ============================================================
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

RTSP_URL = "rtsp://192.168.1.100:8554/live"  # Update to your PC's IP

CANVAS_SIZE = (1280, 720)  # All ROIs are calibrated to this size

ROI_QUOTA = (200, 480, 223, 142)   # "231/550" area
ROI_BAIT  = (600, 460, 367, 182)   # "1588" area
ROI_TIME  = (280, 180, 253, 163)   # "0:27:22" area

QUOTA_LIMIT        = 550
QUOTA_ALERT_BUFFER = 20
REPORT_INTERVAL    = 5    # Log every 5 fish
CAPTURE_INTERVAL   = 3    # Check every 3 seconds
RECONNECT_DELAY    = 5

LINE_TOKEN = ""  # LINE Notify token (leave empty to disable)

# Global session trackers
last_reported_count = -1
alert_sent          = False
start_count         = -1
session_start_time  = datetime.now()

# ============================================================
# LOGGING & UTILS
# ============================================================
def log(message, level="INFO"):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"[{ts}] [{level}] {message}")

def send_line(message):
    if not LINE_TOKEN:
        return
    try:
        url = "https://notify-api.line.me/api/notify"
        headers = {"Authorization": "Bearer " + LINE_TOKEN}
        requests.post(url, headers=headers, data={"message": message}, timeout=5)
    except Exception:
        log("Failed to send Line notification", "ERROR")

def crop(frame, roi):
    x, y, w, h = roi
    return frame[y:y+h, x:x+w]

def clean_and_read(img_roi, is_time=False, thresh_val=180, psm=7):
    """
    Pre-processes a cropped ROI and returns the OCR text.

    thresh_val per region:
      QUOTA (white text)         → 180  white (~240) >> all blues (≤160)
      BAIT  (large outlined text)→ 150  outline pixels are ~120-150 gray; lower threshold
                                        fills in the full glyph shape
      TIME  (yellow text)        → 150  yellow gray ≈ 200-226 after JPEG compression;
                                        150 gives a reliable margin above all blues
    psm per region:
      QUOTA / TIME → 7  (single text line)
      BAIT         → 8  (single word — better for one standalone number)
    """
    gray = cv2.cvtColor(img_roi, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    _, thresh = cv2.threshold(gray, thresh_val, 255, cv2.THRESH_BINARY_INV)
    thresh = cv2.copyMakeBorder(thresh, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=255)

    if is_time:
        config = f'--psm {psm} -c tessedit_char_whitelist=0123456789: '
    else:
        config = f'--psm {psm} -c tessedit_char_whitelist=0123456789/'

    return pytesseract.image_to_string(thresh, config=config).strip()

# ============================================================
# INFERENCE  (importable, frame-in / dict-out)
# ============================================================
def run_inference(frame):
    """
    Main inference function.

    Args:
        frame: BGR numpy array of any resolution.

    Returns dict:
        quota_raw     (str)       raw OCR text, e.g. "231/550"
        quota_current (int|None)  parsed current fish count, None on parse failure
        bait          (str)       raw OCR text, e.g. "1588"
        game_time     (str)       raw OCR text, e.g. "0:27:22"
    """
    resized = cv2.resize(frame, CANVAS_SIZE)

    raw_quota = clean_and_read(crop(resized, ROI_QUOTA), thresh_val=180, psm=7)
    raw_bait  = clean_and_read(crop(resized, ROI_BAIT),  thresh_val=150, psm=8)
    raw_time  = clean_and_read(crop(resized, ROI_TIME),  is_time=True, thresh_val=150, psm=7)

    # Parse quota string → integer current count
    quota_current = None
    clean_q = "".join(c for c in raw_quota if c.isdigit() or c == "/")
    try:
        if "/" in clean_q:
            quota_current = int(clean_q.split("/")[0])
        elif len(clean_q) >= 3:
            # Tesseract missed the slash — take the first 3 digits
            quota_current = int(clean_q[:3])
    except ValueError:
        pass

    return {
        "quota_raw":     raw_quota,
        "quota_current": quota_current,
        "bait":          raw_bait,
        "game_time":     raw_time,
    }

# ============================================================
# STREAM LOOP
# ============================================================
def connect(url):
    while True:
        log(f"Connecting to {url}...")
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if cap.isOpened():
            log("SUCCESS: Stream connected!", "OK")
            return cap
        log(f"FAILED: Reconnecting in {RECONNECT_DELAY}s...", "RETRY")
        time.sleep(RECONNECT_DELAY)

def main():
    global last_reported_count, alert_sent, start_count

    cap = connect(RTSP_URL)
    log("Monitoring active. Waiting for first valid OCR...")

    while True:
        ret, frame = cap.read()

        if not ret:
            log("Lost stream. Attempting recovery...", "WARNING")
            cap.release()
            cap = connect(RTSP_URL)
            continue

        result  = run_inference(frame)
        current = result["quota_current"]

        if current is None:
            time.sleep(CAPTURE_INTERVAL)
            continue

        # Session initialisation
        if start_count == -1:
            start_count = current
            log(f"Session Initialized. Starting count: {start_count}")

        caught_now = current - start_count
        threshold  = QUOTA_LIMIT - QUOTA_ALERT_BUFFER

        # Periodic report
        if current >= last_reported_count + REPORT_INTERVAL:
            uptime = str(datetime.now() - session_start_time).split('.')[0]
            log(f"FISH: {current}/{QUOTA_LIMIT} (+{caught_now}) | "
                f"BAIT: {result['bait']} | "
                f"GAME TIME: {result['game_time']} | "
                f"UPTIME: {uptime}")
            last_reported_count = current

        # Near-quota alert
        if current >= threshold and not alert_sent:
            msg = f"QUOTA ALMOST FULL: {current}/{QUOTA_LIMIT} | Bait Left: {result['bait']}"
            log(msg, "ALERT")
            # send_line(msg)
            alert_sent = True

        # Limit reached
        if current >= QUOTA_LIMIT:
            msg = f"LIMIT REACHED: {current}/{QUOTA_LIMIT}. AFK STOPPED."
            log(msg, "CRITICAL")
            # send_line(msg)
            time.sleep(60)

        time.sleep(CAPTURE_INTERVAL)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Process terminated by user.", "EXIT")
