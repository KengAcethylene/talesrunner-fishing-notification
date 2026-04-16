import cv2
import numpy as np
import pytesseract
import time
import argparse
import threading
import os
import requests
from datetime import datetime

# ============================================================
# SETTINGS
# ============================================================
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

RTSP_URL = "rtsp://192.168.1.100:8554/live"  # Update to your PC's IP

CANVAS_SIZE = (1280, 720)  # All ROIs are calibrated to this size

ROI_QUOTA = (200, 480, 223, 142)   # "231/550" area

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

def _sharpen(gray):
    """Unsharp mask — restores edges blurred by H.264 compression."""
    blur = cv2.GaussianBlur(gray, (0, 0), 3)
    return cv2.addWeighted(gray, 1.5, blur, -0.5, 0)


def _preprocess_quota(scaled_bgr, thresh_val=180):
    """
    Returns a binary image (black text, white background) ready for Tesseract.

    QUOTA: grayscale + unsharp mask + fixed threshold.
           White text (~240 gray, or ~210 after H.264) on dark blue (~80).
           Sharpening boosts compressed text back above threshold 180.
    """
    gray = cv2.cvtColor(scaled_bgr, cv2.COLOR_BGR2GRAY)
    gray = _sharpen(gray)
    _, thresh = cv2.threshold(gray, thresh_val, 255, cv2.THRESH_BINARY_INV)
    return thresh


def clean_and_read_quota(img_roi, thresh_val=180, psm=7):
    """Crops, pre-processes, and OCR-reads quota ROI. Returns raw text string."""
    scaled = cv2.resize(img_roi, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    thresh = _preprocess_quota(scaled, thresh_val)
    thresh = cv2.copyMakeBorder(thresh, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=255)

    whitelist = '0123456789/'
    config    = f'--psm {psm} -c tessedit_char_whitelist={whitelist}'
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
    """
    resized = cv2.resize(frame, CANVAS_SIZE)

    raw_quota = clean_and_read_quota(crop(resized, ROI_QUOTA), thresh_val=180, psm=7)

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
    }

# ============================================================
# DEBUG DISPLAY
# ============================================================
def show_debug(frame, result):
    """
    Renders the live debug overlay.  Call once per OCR cycle when --debug is on.

    Main window  — live frame with colored ROI box and OCR result label.
    Region window — quota ROI shown as: original crop | threshold image side by side.

    Returns True to keep running, False if the user pressed Q / ESC.
    """
    resized = cv2.resize(frame, CANVAS_SIZE)
    overlay = resized.copy()

    x, y, w, h = ROI_QUOTA
    color = (0, 255, 0)
    cv2.rectangle(overlay, (x, y), (x + w, y + h), color, 2)
    cv2.putText(overlay, f"QUOTA: {result['quota_raw']}", (x, y - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2, cv2.LINE_AA)

    cv2.imshow("Debug - Live Stream", overlay)

    cropped = crop(resized, ROI_QUOTA)
    scaled  = cv2.resize(cropped, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    gray    = cv2.cvtColor(scaled, cv2.COLOR_BGR2GRAY)
    gray    = _sharpen(gray)
    _, thresh = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)

    thresh_bgr = cv2.cvtColor(thresh, cv2.COLOR_GRAY2BGR)
    cv2.imshow(f"QUOTA  '{result['quota_raw']}'", np.hstack([scaled, thresh_bgr]))

    key = cv2.waitKey(1) & 0xFF
    if key in (27, ord('q'), ord('Q')):     # ESC or Q
        return False
    return True

# ============================================================
# STREAM READER  (background thread — always returns the latest frame)
# ============================================================
class FrameReader:
    """
    Reads RTSP frames continuously in a daemon thread.
    Calling read() always returns the most recent decoded frame with no buffer lag.

    Why this matters:
      cap.read() on RTSP blocks until the *next* frame arrives from the network.
      When the main loop sleeps for CAPTURE_INTERVAL seconds, the internal FFMPEG
      buffer fills up with stale frames — the next cap.read() returns old data, not
      the current game state, so OCR reads the wrong numbers.
      A background thread that drains the stream non-stop ensures read() is instant
      and always fresh.
    """

    def __init__(self, url):
        # Force TCP transport — more reliable than UDP on local networks
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
        self._cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        self._frame = None
        self._ret   = False
        self._lock  = threading.Lock()
        self._stop  = False
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while not self._stop:
            ret, frame = self._cap.read()
            with self._lock:
                self._ret   = ret
                self._frame = frame

    def read(self):
        with self._lock:
            if self._frame is None:
                return False, None
            return self._ret, self._frame.copy()

    @property
    def is_opened(self):
        return self._cap.isOpened()

    def get(self, prop):
        return self._cap.get(prop)

    def release(self):
        self._stop = True
        self._cap.release()


def open_stream(url):
    """Blocks until the stream opens successfully."""
    while True:
        log(f"Connecting to {url}...")
        reader = FrameReader(url)
        time.sleep(2)                       # give the thread time to grab first frame
        ret, frame = reader.read()
        if ret and frame is not None:
            log("SUCCESS: Stream connected!", "OK")
            return reader
        reader.release()
        log(f"FAILED: Reconnecting in {RECONNECT_DELAY}s...", "RETRY")
        time.sleep(RECONNECT_DELAY)


# ============================================================
# MAIN LOOP
# ============================================================
def main(debug=False):
    global last_reported_count, alert_sent, start_count

    reader = open_stream(RTSP_URL)
    stream_w = int(reader.get(cv2.CAP_PROP_FRAME_WIDTH))
    stream_h = int(reader.get(cv2.CAP_PROP_FRAME_HEIGHT))
    log(f"Stream resolution : {stream_w}x{stream_h}  →  canvas {CANVAS_SIZE[0]}x{CANVAS_SIZE[1]}")
    log(f"Monitoring active {'[DEBUG MODE]' if debug else ''}. Waiting for first valid OCR...")

    while True:
        ret, frame = reader.read()

        if not ret or frame is None:
            log("Lost stream. Attempting recovery...", "WARNING")
            reader.release()
            reader = open_stream(RTSP_URL)
            continue

        result  = run_inference(frame)
        current = result["quota_current"]

        if debug:
            keep_going = show_debug(frame, result)
            if not keep_going:
                log("Debug window closed by user.", "EXIT")
                reader.release()
                break

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
            log(f"FISH: {current}/{QUOTA_LIMIT} (+{caught_now}) | UPTIME: {uptime}")
            last_reported_count = current

        # Near-quota alert
        if current >= threshold and not alert_sent:
            msg = f"QUOTA ALMOST FULL: {current}/{QUOTA_LIMIT}"
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

    reader.release()
    cv2.destroyAllWindows()

def capture_frame(output_path="rtsp_capture.jpg"):
    """
    Connects to RTSP, grabs one frame, saves it, then exits.
    Use this to get a real stream frame for ROI recalibration in test_ocr.py.
    """
    reader = open_stream(RTSP_URL)
    time.sleep(1)                       # let the buffer fill with a few fresh frames
    ret, frame = reader.read()
    reader.release()

    if not ret or frame is None:
        log("Failed to grab frame.", "ERROR")
        return

    h, w = frame.shape[:2]
    log(f"Raw stream resolution : {w}x{h}")
    log(f"Canvas size           : {CANVAS_SIZE[0]}x{CANVAS_SIZE[1]}")

    cv2.imwrite(output_path, frame)
    log(f"Saved → {output_path}  (run test_ocr.py to recalibrate ROIs)", "OK")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TalesRunner fish monitor")
    parser.add_argument("--debug", action="store_true",
                        help="Show live ROI boxes and threshold windows")
    parser.add_argument("--capture", action="store_true",
                        help="Grab one frame from RTSP, save as rtsp_capture.jpg, then exit")
    args = parser.parse_args()

    try:
        if args.capture:
            capture_frame()
        else:
            main(debug=args.debug)
    except KeyboardInterrupt:
        log("Process terminated by user.", "EXIT")
        cv2.destroyAllWindows()
