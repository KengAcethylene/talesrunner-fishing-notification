import cv2
import numpy as np
import NDIlib as ndi
import time
import threading
import requests
import os
import sys
import json
from datetime import datetime
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# DISPLAY DETECTION
# ============================================================
def _has_display():
    import platform
    if platform.system() == "Windows":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

HAS_DISPLAY = _has_display()

# ============================================================
# CONFIG
# ============================================================
_CONFIG_DEFAULTS = {
    "ndi_source_name": "",
    "canvas_size": [1280, 720],
    "roi_quota": [200, 480, 223, 142],
    "roi_time":  [280, 180, 253, 163],
    "quota_limit": 550,
    "quota_alert_buffer": 50,
    "report_interval": 5,
    "capture_interval": 1,
    "reconnect_delay": 5,
    "no_read_timeout": 60,
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "templates_dir": "templates",
    "obs_export_resolution": "1280x720",
}


def _config_path():
    """Return path to config.json next to the executable (frozen) or script."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "config.json")


class Config:
    def __init__(self):
        self._data = dict(_CONFIG_DEFAULTS)
        path = _config_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                for k, v in loaded.items():
                    if k in self._data:
                        self._data[k] = v
            except Exception:
                pass
        # Merge .env credentials if not already in config
        if not self._data["telegram_bot_token"]:
            self._data["telegram_bot_token"] = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not self._data["telegram_chat_id"]:
            self._data["telegram_chat_id"] = os.getenv("TELEGRAM_CHAT_ID", "")

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        self._data[key] = value

    def save(self):
        path = _config_path()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)
        os.replace(tmp, path)

    # Convenience properties
    @property
    def canvas_size(self):
        v = self._data["canvas_size"]
        return (int(v[0]), int(v[1]))

    @property
    def roi_quota(self):
        return tuple(int(x) for x in self._data["roi_quota"])

    @property
    def roi_time(self):
        return tuple(int(x) for x in self._data["roi_time"])

    @property
    def templates_dir(self):
        if getattr(sys, "frozen", False):
            base = os.path.dirname(sys.executable)
        else:
            base = os.path.dirname(os.path.abspath(__file__))
        d = self._data["templates_dir"]
        if os.path.isabs(d):
            return d
        return os.path.join(base, d)


# ============================================================
# MONITOR SESSION STATE
# ============================================================
@dataclass
class MonitorSession:
    last_reported_count: int = -1
    alert_sent: bool = False
    limit_sent: bool = False
    start_count: int = -1
    session_start_time: datetime = field(default_factory=datetime.now)
    no_read_seconds: int = 0


# ============================================================
# LOGGING
# ============================================================
_log_callback = None
_log_file = None
_debug_mode = False


def set_log_callback(fn):
    global _log_callback
    _log_callback = fn


def set_log_file(fh):
    global _log_file
    _log_file = fh


def set_debug_mode(val: bool):
    global _debug_mode
    _debug_mode = val


def log(message, level="INFO"):
    if level == "DEBUG" and not _debug_mode:
        return
    ts = datetime.now().strftime('%H:%M:%S')
    line = f"[{ts}] [{level}] {message}"
    print(line)
    if _log_file:
        _log_file.write(line + "\n")
        _log_file.flush()
    if _log_callback:
        try:
            _log_callback(line)
        except Exception:
            pass


# ============================================================
# TELEGRAM
# ============================================================
def send_telegram(token: str, chat_id: str, message: str):
    if not token or not chat_id:
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": message}, timeout=5)
    except Exception as e:
        print(f"[ERROR] Failed to send Telegram notification: {e}")


# ============================================================
# UTILS
# ============================================================
def crop(frame, roi):
    x, y, w, h = roi
    return frame[y:y + h, x:x + w]


# ============================================================
# TEMPLATE MATCHING
# ============================================================
def load_templates(templates_dir: str = "templates"):
    templates = {}
    if not os.path.exists(templates_dir):
        return templates
    for fname in os.listdir(templates_dir):
        if fname.endswith('.png') and fname[0].isdigit():
            img = cv2.imread(os.path.join(templates_dir, fname), cv2.IMREAD_GRAYSCALE)
            if img is not None:
                templates[fname[0]] = cv2.resize(img, (80, 120))
    if templates:
        log(f"Loaded templates for digits: {sorted(templates)}", "OK")
    return templates


def save_template(digit_char: str, crop_img, templates_dir: str = "templates"):
    os.makedirs(templates_dir, exist_ok=True)
    normalised = cv2.resize(crop_img, (80, 120))
    cv2.imwrite(os.path.join(templates_dir, f"{digit_char}.png"), normalised)
    return normalised


MATCH_MIN_SCORE = 0.55


def match_digit(crop_img, templates):
    if not templates:
        return '?', 0.0
    query = cv2.resize(crop_img, (80, 120)).astype(np.float32)
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
    scaled = cv2.resize(img_roi, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(scaled, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return thresh


def extract_char_crops(thresh):
    h, w = thresh.shape
    inv = cv2.bitwise_not(thresh)
    n, _, stats, _ = cv2.connectedComponentsWithStats(inv, connectivity=8)

    min_area = h * w * 0.005
    comps = []
    for i in range(1, n):
        cx, cy, cw, ch, area = stats[i]
        if area >= min_area:
            comps.append((cx, cw, thresh[cy:cy + ch, cx:cx + cw]))

    if not comps:
        return []

    comps.sort(key=lambda c: c[0])

    mid_lo, mid_hi = w * 2 // 5, w * 3 // 5
    slash_idx = None
    min_cw = float('inf')
    for i, (cx, cw, _) in enumerate(comps):
        centre = cx + cw // 2
        if mid_lo <= centre <= mid_hi and cw < min_cw:
            min_cw, slash_idx = cw, i

    if slash_idx is None:
        slash_idx = min(range(len(comps)), key=lambda i: comps[i][1])

    return [(cx, i == slash_idx, c) for i, (cx, _, c) in enumerate(comps)]


def clean_and_read_quota(img_roi, templates):
    thresh = preprocess_quota(img_roi)
    chars = extract_char_crops(thresh)

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
    thresh = preprocess_quota(img_roi)
    h, w = thresh.shape
    inv = cv2.bitwise_not(thresh)
    n, _, stats, _ = cv2.connectedComponentsWithStats(inv, connectivity=8)

    min_area = h * w * 0.003
    comps = []
    for i in range(1, n):
        cx, cy, cw, ch, area = stats[i]
        if area >= min_area:
            comps.append((cx, area, thresh[cy:cy + ch, cx:cx + cw]))

    if not comps:
        return ''

    comps.sort(key=lambda c: c[0])

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
# CALIBRATE (CLI use)
# ============================================================
def calibrate(frame, count_str, templates,
              roi_quota=(200, 480, 223, 142),
              quota_limit=550,
              canvas_size=(1280, 720),
              templates_dir="templates"):
    resized = cv2.resize(frame, canvas_size)
    roi_img = crop(resized, roi_quota)
    thresh = preprocess_quota(roi_img)
    chars = extract_char_crops(thresh)

    if not chars:
        log("No character components detected — check ROI position.", "ERROR")
        return templates

    log(f"Detected {len(chars)} components.", "INFO")

    slash_pos = next((i for i, (_, is_slash, _) in enumerate(chars) if is_slash), None)
    if slash_pos is None:
        log("Could not locate '/' separator.", "WARNING")
        return templates

    numerator_chars = [(x, c) for x, is_slash, c in chars[:slash_pos]]
    denominator_chars = [(x, c) for x, is_slash, c in chars[slash_pos + 1:]]

    def _save_group(char_list, digit_labels, group_name):
        if len(char_list) != len(digit_labels):
            log(f"{group_name}: {len(char_list)} components vs {len(digit_labels)} expected digits — skipping.", "WARNING")
            return
        for (_, char_crop), digit in zip(char_list, digit_labels):
            path = os.path.join(templates_dir, f"{digit}.png")
            if not os.path.exists(path):
                normalised = save_template(digit, char_crop, templates_dir)
                templates[digit] = normalised
                log(f"Saved template '{digit}' ({group_name})", "OK")
            else:
                log(f"Template '{digit}' already exists — skipped", "INFO")

    denom_digits = list(str(quota_limit).zfill(3))
    _save_group(denominator_chars, denom_digits, "denominator")

    numer_digits = list(count_str.lstrip('0') or '0')
    _save_group(numerator_chars, numer_digits, "numerator")

    missing = [str(d) for d in range(10) if str(d) not in templates]
    if missing:
        log(f"Still need templates for: {missing} — run --calibrate with a count containing those digits.", "INFO")
    else:
        log("All 10 digit templates collected — ready to monitor!", "OK")

    return templates


# ============================================================
# INFERENCE
# ============================================================
def run_inference(frame, templates,
                  roi_quota=(200, 480, 223, 142),
                  roi_time=(280, 180, 253, 163),
                  canvas_size=(1280, 720)):
    resized = cv2.resize(frame, canvas_size)
    raw_quota = clean_and_read_quota(crop(resized, roi_quota), templates)
    raw_time = clean_and_read_time(crop(resized, roi_time), templates)

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
        "quota_raw": raw_quota,
        "quota_current": quota_current,
        "time_raw": raw_time,
    }


# ============================================================
# STREAM READER
# ============================================================
class FrameReader:
    def __init__(self, source, copy_interval: float = 1.0):
        """
        copy_interval: minimum seconds between numpy copies.
        NDI frames are received and freed at full rate to keep the sender's
        buffer healthy; the expensive np.copy happens at most once per interval.
        Default 1.0 s (1 FPS) is plenty for fish-quota monitoring and keeps
        GIL pressure negligible so the tkinter UI stays responsive.
        """
        recv_desc = ndi.RecvCreateV3()
        recv_desc.color_format = ndi.RECV_COLOR_FORMAT_BGRX_BGRA
        self._recv = ndi.recv_create_v3(recv_desc)
        ndi.recv_connect(self._recv, source)
        self._frame = None
        self._ret = False
        self._width = 0
        self._height = 0
        self._lock = threading.Lock()
        self._stop = False
        self._copy_interval = copy_interval
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        connected = False
        while not self._stop:
            # Before the first frame arrives the NDI connection is still establishing.
            # Use a long timeout (2 s) so we don't spin — recv blocks in C code and
            # releases the GIL while waiting.  Once connected, switch to a short
            # timeout (100 ms) followed by an explicit sleep; the sleep releases the
            # GIL unconditionally for copy_interval seconds so tkinter runs freely.
            timeout_ms = 100 if connected else 2000
            frame_type, video, audio, metadata = ndi.recv_capture_v2(self._recv, timeout_ms)
            if frame_type == ndi.FRAME_TYPE_VIDEO:
                frame = np.copy(video.data[:, :, :3])
                h, w = frame.shape[:2]
                ndi.recv_free_video_v2(self._recv, video)
                with self._lock:
                    self._ret, self._frame = True, frame
                    self._width, self._height = w, h
                connected = True
                time.sleep(self._copy_interval)

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


def scan_sources(timeout: int = 10) -> list:
    """Discover NDI sources on the network. Returns list of source name strings."""
    find = ndi.find_create_v2()
    time.sleep(timeout)
    sources = ndi.find_get_current_sources(find)
    names = [s.ndi_name for s in sources] if sources else []
    ndi.find_destroy(find)
    return names


def open_stream(source_name: str, reconnect_delay: int = 5,
                copy_interval: float = 1.0):
    """Blocks until an NDI source is found and delivering frames."""
    while True:
        label = f"'{source_name}'" if source_name else "(first available)"
        log(f"Looking for NDI source {label}...")
        find = ndi.find_create_v2()
        deadline = time.time() + 10
        source = None
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
            log(f"No NDI source found. Retrying in {reconnect_delay}s...", "RETRY")
            time.sleep(reconnect_delay)
            continue

        log(f"Found: {source.ndi_name}. Connecting...")
        reader = FrameReader(source, copy_interval=copy_interval)
        ndi.find_destroy(find)
        # Poll until the first frame arrives (up to 10 s).
        # A fixed sleep is unreliable because connection time varies.
        deadline = time.time() + 10
        while time.time() < deadline:
            ret, frame = reader.read()
            if ret and frame is not None:
                log("SUCCESS: NDI source connected!", "OK")
                return reader
            time.sleep(0.25)
        reader.release()
        log(f"FAILED: No frame in 10 s. Reconnecting in {reconnect_delay}s...", "RETRY")
        time.sleep(reconnect_delay)


def capture_frame(source_name: str = "",
                  canvas_size=(1280, 720),
                  roi_quota=(200, 480, 223, 142),
                  output_path: str = "ndi_capture.jpg",
                  reconnect_delay: int = 5):
    """Grab one frame; save diagnostic images. Returns (frame, thresh_img) or (None, None)."""
    reader = open_stream(source_name, reconnect_delay)
    time.sleep(1)
    ret, frame = reader.read()
    reader.release()

    if not ret or frame is None:
        log("Failed to grab frame.", "ERROR")
        return None, None

    h, w = frame.shape[:2]
    log(f"Raw resolution : {w}x{h}")

    resized = cv2.resize(frame, canvas_size)
    cv2.imwrite(output_path, resized)
    log(f"Saved frame   → {output_path}", "OK")

    thresh = preprocess_quota(crop(resized, roi_quota))
    cv2.imwrite("ndi_roi_thresh.jpg", thresh)
    log("Saved thresh  → ndi_roi_thresh.jpg", "OK")

    return frame, thresh
