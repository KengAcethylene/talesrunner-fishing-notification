import cv2
import numpy as np
import NDIlib as ndi
import time
import argparse
import threading
import requests
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Detect whether a display is available (headless Pi has none)
def _has_display():
    import platform
    if platform.system() == "Windows":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

HAS_DISPLAY = _has_display()

# ============================================================
# SETTINGS
# ============================================================
NDI_SOURCE_NAME = ""  # Partial/full NDI source name; empty = first found

CANVAS_SIZE = (1280, 720)
ROI_QUOTA   = (200, 480, 223, 142)   # "XXX/550" area
ROI_TIME    = (280, 180, 253, 163)   # "H:MM:SS" area

QUOTA_LIMIT        = 550
QUOTA_ALERT_BUFFER = 50
REPORT_INTERVAL    = 5
CAPTURE_INTERVAL   = 1
RECONNECT_DELAY    = 5
NO_READ_TIMEOUT    = 60   # exit after this many consecutive seconds with no valid read

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
DISCORD_MENTION     = os.getenv("DISCORD_MENTION", "")
REMOTE_DESKTOP_URL  = os.getenv("REMOTE_DESKTOP_URL", "")

TEMPLATES_DIR = "templates"   # one PNG per digit: 0.png … 9.png
TEMPLATE_SIZE = (80, 120)     # (w, h) — all crops normalised to this before matching

# Global session trackers
last_reported_count = -1
alert_sent          = False   # near-quota warning sent
limit_sent          = False   # quota limit reached sent
start_count         = -1
session_start_time  = datetime.now()

# ============================================================
# LOGGING & UTILS
# ============================================================
LOG_FILE           = None   # file handle, set by --log-file

def log(message, level="INFO"):
    if level == "DEBUG" and not DEBUG_MODE:
        return
    ts   = datetime.now().strftime('%H:%M:%S')
    line = f"[{ts}] [{level}] {message}"
    print(line)
    if LOG_FILE:
        LOG_FILE.write(line + "\n")
        LOG_FILE.flush()
    if DISCORD_DEBUG_MODE:
        send_discord(f"[{level}] {message}", mention=True)

def send_discord(message, mention=False):
    if not DISCORD_WEBHOOK_URL:
        return
    content = f"{DISCORD_MENTION} {message}" if (mention and DISCORD_MENTION) else message
    if mention and REMOTE_DESKTOP_URL:
        content += f"\n{REMOTE_DESKTOP_URL}"
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": content}, timeout=5)
    except Exception as e:
        # Use print directly to avoid re-entering log → send_discord recursion
        print(f"[ERROR] Failed to send Discord notification: {e}")

def crop(frame, roi):
    x, y, w, h = roi
    return frame[y:y+h, x:x+w]

# ============================================================
# TEMPLATE MATCHING
# ============================================================
def load_templates():
    """Load saved digit PNGs from TEMPLATES_DIR. Returns {digit_char: ndarray}."""
    templates = {}
    if not os.path.exists(TEMPLATES_DIR):
        return templates
    for fname in os.listdir(TEMPLATES_DIR):
        if fname.endswith('.png') and fname[0].isdigit():
            img = cv2.imread(os.path.join(TEMPLATES_DIR, fname), cv2.IMREAD_GRAYSCALE)
            if img is not None:
                templates[fname[0]] = cv2.resize(img, TEMPLATE_SIZE)
    if templates:
        log(f"Loaded templates for digits: {sorted(templates)}", "OK")
    return templates

def save_template(digit_char, crop_img):
    """Normalise and save a digit crop as a template PNG."""
    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    normalised = cv2.resize(crop_img, TEMPLATE_SIZE)
    cv2.imwrite(os.path.join(TEMPLATES_DIR, f"{digit_char}.png"), normalised)
    return normalised

MATCH_MIN_SCORE = 0.55   # below this → unknown digit ('?')

def match_digit(crop_img, templates):
    """Return (best_digit_char, score) using normalised cross-correlation."""
    if not templates:
        return '?', 0.0
    query  = cv2.resize(crop_img, TEMPLATE_SIZE).astype(np.float32)
    best, best_score = '?', -1.0
    for digit, tmpl in templates.items():
        score = cv2.matchTemplate(query, tmpl.astype(np.float32), cv2.TM_CCOEFF_NORMED)[0][0]
        if score > best_score:
            best_score, best = score, digit
    if best_score < MATCH_MIN_SCORE:
        return '?', float(best_score)
    return best, float(best_score)

# ============================================================
# IMAGE PROCESSING
# ============================================================
def preprocess_quota(img_roi):
    """3× upscale + Otsu threshold → binary (black text, white background)."""
    scaled = cv2.resize(img_roi, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    gray   = cv2.cvtColor(scaled, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return thresh

def extract_char_crops(thresh):
    """
    Find character bounding boxes via connected components.
    Returns list of (x, is_slash, crop) sorted left → right.
    '/' is identified as the narrowest component whose centre lies in the
    middle 40–60 % of the image — this prevents a narrow digit like '1'
    from being mistaken for '/'.
    """
    h, w = thresh.shape
    inv = cv2.bitwise_not(thresh)
    n, _, stats, _ = cv2.connectedComponentsWithStats(inv, connectivity=8)

    min_area = h * w * 0.005
    comps = []
    for i in range(1, n):
        cx, cy, cw, ch, area = stats[i]
        if area >= min_area:
            comps.append((cx, cw, thresh[cy:cy+ch, cx:cx+cw]))

    if not comps:
        return []

    comps.sort(key=lambda c: c[0])

    # Look for '/' only in the central band of the image
    mid_lo, mid_hi = w * 2 // 5, w * 3 // 5
    slash_idx = None
    min_cw = float('inf')
    for i, (cx, cw, _) in enumerate(comps):
        centre = cx + cw // 2
        if mid_lo <= centre <= mid_hi and cw < min_cw:
            min_cw, slash_idx = cw, i

    # Fallback: just take the globally narrowest component
    if slash_idx is None:
        slash_idx = min(range(len(comps)), key=lambda i: comps[i][1])

    return [(cx, i == slash_idx, c) for i, (cx, _, c) in enumerate(comps)]

def clean_and_read_quota(img_roi, templates):
    """
    Read the quota display using template matching.
    Returns raw text e.g. '276/550', or '' on failure.
    """
    thresh = preprocess_quota(img_roi)
    chars  = extract_char_crops(thresh)

    if not chars:
        return ''

    result = []
    for x, is_slash, char_crop in chars:
        if is_slash:
            result.append('/')
        else:
            digit, score = match_digit(char_crop, templates)
            log(f"  x={x}: '{digit}' score={score:.2f}", "DEBUG")
            result.append(digit)

    return ''.join(result)

def clean_and_read_time(img_roi, templates):
    """
    Read the in-game timer (e.g. '0:27:22') using template matching.
    Colons are detected by their small area relative to digit components —
    no colon template needed.
    Returns a time string or '' on failure.
    """
    thresh = preprocess_quota(img_roi)
    h, w   = thresh.shape
    inv    = cv2.bitwise_not(thresh)
    n, _, stats, _ = cv2.connectedComponentsWithStats(inv, connectivity=8)

    min_area = h * w * 0.003
    comps = []
    for i in range(1, n):
        cx, cy, cw, ch, area = stats[i]
        if area >= min_area:
            comps.append((cx, area, thresh[cy:cy+ch, cx:cx+cw]))

    if not comps:
        return ''

    comps.sort(key=lambda c: c[0])

    # Colons are two small dots — much smaller area than any digit
    max_area = max(c[1] for c in comps)

    result = []
    for cx, area, char_crop in comps:
        if area < max_area * 0.25:
            result.append(':')
        else:
            digit, score = match_digit(char_crop, templates)
            log(f"  time x={cx}: '{digit}' score={score:.2f}", "DEBUG")
            result.append(digit)

    return ''.join(result)

# ============================================================
# CALIBRATION
# ============================================================
def calibrate(frame, count_str, templates):
    """
    Extract and save digit templates from a frame.
    count_str is the numerator shown on screen (e.g. '483').

    The denominator is always QUOTA_LIMIT so its digits are labelled
    automatically regardless of whether the numerator count matches.
    Returns the updated templates dict.
    """
    resized = cv2.resize(frame, CANVAS_SIZE)
    roi_img = crop(resized, ROI_QUOTA)
    thresh  = preprocess_quota(roi_img)
    chars   = extract_char_crops(thresh)

    if not chars:
        log("No character components detected — check ROI position.", "ERROR")
        return templates

    log(f"Detected {len(chars)} components.", "INFO")

    # Split on the '/' separator
    slash_pos = next((i for i, (_, is_slash, _) in enumerate(chars) if is_slash), None)
    if slash_pos is None:
        log("Could not locate '/' separator.", "WARNING")
        return templates

    numerator_chars   = [(x, c) for x, is_slash, c in chars[:slash_pos]]
    denominator_chars = [(x, c) for x, is_slash, c in chars[slash_pos+1:]]

    def _save_group(char_list, digit_labels, group_name):
        if len(char_list) != len(digit_labels):
            log(f"{group_name}: {len(char_list)} components vs {len(digit_labels)} expected digits — skipping.", "WARNING")
            return
        for (_, char_crop), digit in zip(char_list, digit_labels):
            path = os.path.join(TEMPLATES_DIR, f"{digit}.png")
            if not os.path.exists(path):
                normalised = save_template(digit, char_crop)
                templates[digit] = normalised
                log(f"Saved template '{digit}' ({group_name})", "OK")
            else:
                log(f"Template '{digit}' already exists — skipped", "INFO")

    # Denominator is always the string representation of QUOTA_LIMIT
    denom_digits = list(str(QUOTA_LIMIT).zfill(3))
    _save_group(denominator_chars, denom_digits, "denominator")

    # Numerator uses the user-supplied count (no zero-padding — match actual digit count)
    numer_digits = list(count_str.lstrip('0') or '0')
    _save_group(numerator_chars, numer_digits, "numerator")

    missing = [str(d) for d in range(10) if str(d) not in templates]
    if missing:
        log(f"Still need templates for: {missing} — run --calibrate with a count containing those digits.", "INFO")
    else:
        log("All 10 digit templates collected — ready to monitor!", "OK")

# ============================================================
# INFERENCE
# ============================================================
def run_inference(frame, templates):
    resized   = cv2.resize(frame, CANVAS_SIZE)
    raw_quota = clean_and_read_quota(crop(resized, ROI_QUOTA), templates)
    raw_time  = clean_and_read_time(crop(resized, ROI_TIME), templates)

    quota_current = None
    clean_q = "".join(c for c in raw_quota if c.isdigit() or c == "/")
    try:
        if "/" in clean_q:
            quota_current = int(clean_q.split("/")[0])
        elif len(clean_q) >= 3:
            quota_current = int(clean_q[:3])
    except ValueError:
        pass

    return {
        "quota_raw":     raw_quota,
        "quota_current": quota_current,
        "time_raw":      raw_time,
    }

# ============================================================
# DEBUG DISPLAY
# ============================================================
def show_debug(frame, result):
    resized = cv2.resize(frame, CANVAS_SIZE)
    overlay = resized.copy()

    x, y, w, h = ROI_QUOTA
    color = (0, 255, 0)
    cv2.rectangle(overlay, (x, y), (x + w, y + h), color, 2)
    cv2.putText(overlay, f"QUOTA: {result['quota_raw']}", (x, y - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2, cv2.LINE_AA)
    cv2.imshow("Debug - Live Stream", overlay)

    roi_crop   = crop(resized, ROI_QUOTA)
    thresh     = preprocess_quota(roi_crop)
    thresh_bgr = cv2.cvtColor(thresh, cv2.COLOR_GRAY2BGR)
    cv2.imshow(f"QUOTA  '{result['quota_raw']}'", thresh_bgr)

    key = cv2.waitKey(1) & 0xFF
    return key not in (27, ord('q'), ord('Q'))

# ============================================================
# STREAM READER
# ============================================================
class FrameReader:
    def __init__(self, source):
        recv_desc = ndi.RecvCreateV3()
        recv_desc.color_format = ndi.RECV_COLOR_FORMAT_BGRX_BGRA
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
                frame = np.copy(video.data[:, :, :3])
                h, w  = frame.shape[:2]
                ndi.recv_free_video_v2(self._recv, video)
                with self._lock:
                    self._ret, self._frame = True, frame
                    self._width, self._height = w, h

    def read(self):
        with self._lock:
            return (False, None) if self._frame is None else (self._ret, self._frame.copy())

    def get(self, prop):
        with self._lock:
            if prop == cv2.CAP_PROP_FRAME_WIDTH:  return self._width
            if prop == cv2.CAP_PROP_FRAME_HEIGHT: return self._height
        return 0

    def release(self):
        self._stop = True
        ndi.recv_destroy(self._recv)


def open_stream(source_name):
    """Blocks until an NDI source is found and delivering frames."""
    while True:
        label = f"'{source_name}'" if source_name else "(first available)"
        log(f"Looking for NDI source {label}...")
        find     = ndi.find_create_v2()
        deadline = time.time() + 10
        source   = None
        while time.time() < deadline:
            for src in ndi.find_get_current_sources(find):
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
        reader = FrameReader(source)
        ndi.find_destroy(find)
        time.sleep(2)
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
    global last_reported_count, alert_sent, limit_sent, start_count, session_start_time

    templates = load_templates()
    if not templates:
        log("No templates found. Run:  py index.py --calibrate <current_count>", "WARNING")
        log("Example:  py index.py --calibrate 483", "WARNING")

    no_read_seconds = 0

    reader   = open_stream(NDI_SOURCE_NAME)
    stream_w = int(reader.get(cv2.CAP_PROP_FRAME_WIDTH))
    stream_h = int(reader.get(cv2.CAP_PROP_FRAME_HEIGHT))
    log(f"Stream: {stream_w}x{stream_h} → canvas {CANVAS_SIZE[0]}x{CANVAS_SIZE[1]}")
    log(f"Monitoring {'[DEBUG]' if debug else ''}. Waiting for first valid read...")

    while True:
        ret, frame = reader.read()
        if not ret or frame is None:
            log("Lost stream — reconnecting...", "WARNING")
            reader.release()
            reader = open_stream(NDI_SOURCE_NAME)
            continue

        result  = run_inference(frame, templates)
        current = result["quota_current"]

        if debug:
            if not HAS_DISPLAY:
                log("No display available — --debug ignored (headless mode).", "WARNING")
                debug = False
            elif not show_debug(frame, result):
                log("Debug window closed.", "EXIT")
                reader.release()
                break

        if current is None:
            no_read_seconds += CAPTURE_INTERVAL
            log(f"No numbers detected. ({no_read_seconds}/{NO_READ_TIMEOUT}s)", "WARNING")
            if no_read_seconds >= NO_READ_TIMEOUT:
                msg = f"No quota numbers detected for {NO_READ_TIMEOUT}s — shutting down."
                log(msg, "ERROR")
                send_discord(msg, mention=True)
                reader.release()
                break
            time.sleep(CAPTURE_INTERVAL)
            continue

        no_read_seconds = 0   # reset on successful read

        # Detect quota reset — count dropped significantly from previous read
        if start_count != -1 and current < start_count:
            log(f"Quota reset detected ({last_reported_count} → {current}). Starting new session.")
            start_count         = current
            last_reported_count = current
            alert_sent          = False
            limit_sent          = False
            session_start_time  = datetime.now()

        if start_count == -1:
            start_count = current
            log(f"Session started. Initial count: {start_count}")

        caught_now = current - start_count
        threshold  = QUOTA_LIMIT - QUOTA_ALERT_BUFFER

        if current >= last_reported_count + REPORT_INTERVAL:
            uptime = str(datetime.now() - session_start_time).split('.')[0]
            game_time = result.get("time_raw") or "--:--:--"
            log(f"FISH: {current}/{QUOTA_LIMIT} (+{caught_now}) | GAME: {game_time} | UPTIME: {uptime}")
            last_reported_count = current

        if current >= threshold and not alert_sent:
            msg = f"QUOTA ALMOST FULL: {current}/{QUOTA_LIMIT}"
            log(msg, "ALERT")
            send_discord(msg, mention=True)
            alert_sent = True

        if current >= QUOTA_LIMIT and not limit_sent:
            msg = f"LIMIT REACHED: {current}/{QUOTA_LIMIT}"
            log(msg, "CRITICAL")
            send_discord(msg, mention=True)
            limit_sent = True

        time.sleep(CAPTURE_INTERVAL)

    reader.release()
    if HAS_DISPLAY:
        cv2.destroyAllWindows()

# ============================================================
# CAPTURE / CALIBRATE
# ============================================================
def capture_frame(output_path="ndi_capture.jpg"):
    """Grab one frame, save it + threshold crop for ROI verification."""
    reader = open_stream(NDI_SOURCE_NAME)
    time.sleep(1)
    ret, frame = reader.read()
    reader.release()

    if not ret or frame is None:
        log("Failed to grab frame.", "ERROR")
        return None

    h, w = frame.shape[:2]
    log(f"Raw resolution : {w}x{h}")

    resized = cv2.resize(frame, CANVAS_SIZE)
    cv2.imwrite(output_path, resized)
    log(f"Saved frame   → {output_path}", "OK")

    thresh = preprocess_quota(crop(resized, ROI_QUOTA))
    cv2.imwrite("ndi_roi_thresh.jpg", thresh)
    log("Saved thresh  → ndi_roi_thresh.jpg", "OK")

    return frame


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TalesRunner fish monitor")
    parser.add_argument("--debug",     action="store_true",
                        help="Show live ROI and threshold windows")
    parser.add_argument("--capture",   action="store_true",
                        help="Grab one frame and save diagnostic images, then exit")
    parser.add_argument("--calibrate", metavar="COUNT",
                        help="Capture frame and save digit templates for COUNT "
                             "(e.g. --calibrate 483)")
    parser.add_argument("--discord-debug", action="store_true",
                        help="Send every log message to Discord (tests webhook)")
    parser.add_argument("--log-file", metavar="PATH",
                        help="Write all log output to a file (e.g. --log-file monitor.log)")
    parser.add_argument("--no-timeout", action="store_true",
                        help="Disable auto-shutdown when no numbers are detected")
    parser.add_argument("--list-sources", action="store_true",
                        help="Discover and list NDI sources on the network, then exit")
    args = parser.parse_args()

    DEBUG_MODE         = args.debug
    DISCORD_DEBUG_MODE = args.discord_debug

    if args.no_timeout:
        NO_READ_TIMEOUT = float('inf')

    ndi.initialize()
    try:
        if args.list_sources:
            print("Searching for NDI sources (10s)...")
            find    = ndi.find_create_v2()
            time.sleep(10)
            sources = ndi.find_get_current_sources(find)
            ndi.find_destroy(find)
            if sources:
                print(f"Found {len(sources)} source(s):")
                for s in sources:
                    print(f"  {s.ndi_name}")
            else:
                print("No NDI sources found.")
        else:
            if args.log_file:
                LOG_FILE = open(args.log_file, "a", encoding="utf-8")
                log(f"Logging to {args.log_file}")
            try:
                if args.capture:
                    capture_frame()
                elif args.calibrate:
                    frame = capture_frame()
                    if frame is not None:
                        templates = load_templates()
                        calibrate(frame, args.calibrate, templates)
                else:
                    main(debug=args.debug)
            finally:
                if args.log_file and LOG_FILE:
                    LOG_FILE.close()
    except KeyboardInterrupt:
        log("Process terminated by user.", "EXIT")
        if HAS_DISPLAY:
            cv2.destroyAllWindows()
    finally:
        ndi.destroy()
