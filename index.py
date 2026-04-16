import cv2
import numpy as np
import pytesseract
import NDIlib as ndi
import time
import argparse
import threading
import requests
from datetime import datetime

# ============================================================
# SETTINGS
# ============================================================
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

NDI_SOURCE_NAME = ""  # Partial or full NDI source name, e.g. "OBS (Scene)" — leave empty to use first discovered source

CANVAS_SIZE = (1280, 720)  # All ROIs are calibrated to this size

ROI_QUOTA = (200, 480, 223, 142)   # "231/550" area

QUOTA_LIMIT        = 550
QUOTA_ALERT_BUFFER = 20
REPORT_INTERVAL    = 5    # Log every 5 fish
CAPTURE_INTERVAL   = 1    # Check every 1 second (matches 1 FPS stream)
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

def _preprocess_quota(scaled_bgr):
    """
    Returns a binary image (black text, white background) ready for Tesseract.

    Uses Otsu's method to automatically find the optimal threshold between
    the bright text and dark background — works regardless of the exact
    brightness of any given frame (no per-number tuning needed).
    """
    gray = cv2.cvtColor(scaled_bgr, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return thresh


def clean_and_read_quota(img_roi, psm=7):
    """Crops, pre-processes, and OCR-reads quota ROI. Returns raw text string."""
    scaled = cv2.resize(img_roi, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    thresh = _preprocess_quota(scaled)
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

    raw_quota = clean_and_read_quota(crop(resized, ROI_QUOTA), psm=7)

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
    thresh  = _preprocess_quota(scaled)

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
    Receives NDI video frames continuously in a daemon thread.
    Calling read() always returns the most recent decoded frame with no buffer lag.

    Why this matters:
      NDI recv_capture_v2() blocks until the next frame arrives.
      When the main loop sleeps for CAPTURE_INTERVAL seconds, stale frames
      would accumulate if we read synchronously — the background thread drains
      the stream non-stop so read() is instant and always fresh.
    """

    def __init__(self, source):
        recv_desc = ndi.RecvCreateV3()
        recv_desc.color_format = ndi.RECV_COLOR_FORMAT_BGRX_BGRA  # gives BGRX for progressive
        self._recv   = ndi.recv_create_v3(recv_desc)
        ndi.recv_connect(self._recv, source)
        self._frame  = None
        self._ret    = False
        self._width  = 0
        self._height = 0
        self._lock   = threading.Lock()
        self._stop   = False
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while not self._stop:
            frame_type, video, audio, metadata = ndi.recv_capture_v2(self._recv, 1000)
            if frame_type == ndi.FRAME_TYPE_VIDEO:
                # video.data is BGRX; drop the X channel to get BGR
                frame = np.copy(video.data[:, :, :3])
                h, w  = frame.shape[:2]
                ndi.recv_free_video_v2(self._recv, video)
                with self._lock:
                    self._ret    = True
                    self._frame  = frame
                    self._width  = w
                    self._height = h

    def read(self):
        with self._lock:
            if self._frame is None:
                return False, None
            return self._ret, self._frame.copy()

    def get(self, prop):
        with self._lock:
            if prop == cv2.CAP_PROP_FRAME_WIDTH:
                return self._width
            if prop == cv2.CAP_PROP_FRAME_HEIGHT:
                return self._height
        return 0

    def release(self):
        self._stop = True
        ndi.recv_destroy(self._recv)


def open_stream(source_name):
    """Blocks until an NDI source is found and delivering frames."""
    while True:
        label = f"'{source_name}'" if source_name else "(first available)"
        log(f"Looking for NDI source {label}...")

        # Keep the finder alive until AFTER recv_connect — source objects are
        # backed by the finder's internal memory and become invalid once it is
        # destroyed.
        find = ndi.find_create_v2()
        deadline = time.time() + 10
        source = None
        while time.time() < deadline:
            sources = ndi.find_get_current_sources(find)
            for src in sources:
                if not source_name or source_name.lower() in src.ndi_name.lower():
                    source = src
                    break
            if source:
                break
            time.sleep(0.5)

        if source is None:
            ndi.find_destroy(find)
            log(f"No NDI source found. Retrying in {RECONNECT_DELAY}s...", "RETRY")
            time.sleep(RECONNECT_DELAY)
            continue

        log(f"Found: {source.ndi_name}. Connecting...")
        reader = FrameReader(source)   # recv_connect called inside, finder still alive
        ndi.find_destroy(find)         # safe to destroy only after connecting

        time.sleep(2)                  # give the thread time to grab first frame
        ret, frame = reader.read()
        if ret and frame is not None:
            log("SUCCESS: NDI source connected!", "OK")
            return reader
        reader.release()
        log(f"FAILED: Reconnecting in {RECONNECT_DELAY}s...", "RETRY")
        time.sleep(RECONNECT_DELAY)


# ============================================================
# MAIN LOOP
# ============================================================
def main(debug=False):
    global last_reported_count, alert_sent, start_count

    reader = open_stream(NDI_SOURCE_NAME)
    stream_w = int(reader.get(cv2.CAP_PROP_FRAME_WIDTH))
    stream_h = int(reader.get(cv2.CAP_PROP_FRAME_HEIGHT))
    log(f"Stream resolution : {stream_w}x{stream_h}  →  canvas {CANVAS_SIZE[0]}x{CANVAS_SIZE[1]}")
    log(f"Monitoring active {'[DEBUG MODE]' if debug else ''}. Waiting for first valid OCR...")

    while True:
        ret, frame = reader.read()

        if not ret or frame is None:
            log("Lost stream. Attempting recovery...", "WARNING")
            reader.release()
            reader = open_stream(NDI_SOURCE_NAME)
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

def capture_frame(output_path="ndi_capture.jpg"):
    """
    Connects to NDI, grabs one frame, saves it, then exits.
    Use this to get a real stream frame for ROI recalibration in test_ocr.py.
    """
    reader = open_stream(NDI_SOURCE_NAME)
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
                        help="Grab one frame from NDI, save as ndi_capture.jpg, then exit")
    args = parser.parse_args()

    ndi.initialize()
    try:
        if args.capture:
            capture_frame()
        else:
            main(debug=args.debug)
    except KeyboardInterrupt:
        log("Process terminated by user.", "EXIT")
        cv2.destroyAllWindows()
    finally:
        ndi.destroy()
