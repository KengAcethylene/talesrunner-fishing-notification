import cv2
import numpy as np
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
    "virtual_camera_index": 0,      # cv2.VideoCapture index for OBS Virtual Camera
    "virtual_camera_name": "",      # friendly name displayed in UI
    "roi_quota": [200, 480, 223, 142],
    "quota_limit": 550,
    "quota_alert_buffer": 50,
    "report_interval": 5,
    "capture_interval": 1,
    "reconnect_delay": 5,
    "no_read_timeout": 60,
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "templates_dir": "templates",
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
        return (1280, 720)

    @property
    def roi_quota(self):
        return tuple(int(x) for x in self._data["roi_quota"])

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
    prev_current: int = -1   # used to detect quota reset


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


def _split_component(crop, gap_fraction: float = 0.10):
    """Split a connected-component crop into individual characters.

    Always runs vertical valley detection — no aspect-ratio guard, because
    tall narrow fonts (aspect < 1) still produce multi-digit blobs.

    Returns list of (x_offset_in_crop, sub_crop).
    gap_fraction: columns whose dark-pixel density is below this fraction of
                  the column-wise peak are treated as inter-character gaps.
    """
    ch, cw = crop.shape[:2]

    # Count dark (ink) pixels per column
    col_dark = np.sum(crop == 0, axis=0).astype(float)

    if col_dark.max() == 0:
        return [(0, crop)]

    # Smooth over ~5 % of width to suppress intra-character valleys
    k = max(1, cw // 20)
    col_smooth = np.convolve(col_dark, np.ones(k) / k, mode='same')

    in_char = col_smooth >= col_smooth.max() * gap_fraction

    # Collect contiguous character regions
    regions = []
    start = None
    for i, active in enumerate(in_char):
        if active and start is None:
            start = i
        elif not active and start is not None:
            regions.append((start, i))
            start = None
    if start is not None:
        regions.append((start, cw))

    # Discard slivers narrower than 15 % of the crop height (noise)
    min_w = max(2, int(ch * 0.15))
    regions = [(s, e) for s, e in regions if (e - s) >= min_w]

    if len(regions) <= 1:
        return [(0, crop)]

    return [(s, crop[:, s:e]) for s, e in regions]


def extract_char_crops(thresh):
    h, w = thresh.shape

    # Dilate the white background with a horizontal kernel to break thin
    # pixel connections between touching characters before component analysis.
    # Original thresh is kept for the actual crops (better shape for matching).
    sep_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 1))
    sep = cv2.dilate(thresh, sep_kernel, iterations=1)

    inv = cv2.bitwise_not(sep)
    n, _, stats, _ = cv2.connectedComponentsWithStats(inv, connectivity=8)

    min_area = h * w * 0.005
    comps = []
    for i in range(1, n):
        cx, cy, cw, ch, area = stats[i]
        if area >= min_area:
            # Crop from original thresh (unmodified) for template quality
            blob = thresh[cy:cy + ch, cx:cx + cw]
            # _split_component as a secondary fallback for any still-merged blobs
            for x_off, sub in _split_component(blob):
                comps.append((cx + x_off, sub.shape[1], sub))

    if not comps:
        return []

    comps.sort(key=lambda c: c[0])

    mid_lo, mid_hi = w * 1 // 5, w * 4 // 5
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
                  canvas_size=(1280, 720)):
    resized = cv2.resize(frame, canvas_size)
    raw_quota = clean_and_read_quota(crop(resized, roi_quota), templates)

    quota_current = None
    quota_limit   = None
    clean_q = "".join(c for c in raw_quota if c.isdigit() or c == "/")
    try:
        if "/" in clean_q:
            left, _, right = clean_q.partition("/")
            if left:
                quota_current = int(left)
            if right:
                quota_limit = int(right)
        elif len(clean_q) >= 3:
            quota_current = int(clean_q[:3])
    except ValueError:
        pass

    return {
        "quota_raw":     raw_quota,
        "quota_current": quota_current,
        "quota_limit":   quota_limit,   # None when denominator not visible
    }


# ============================================================
# CAMERA READER (OBS Virtual Camera / webcam)
# ============================================================
class CameraFrameReader:
    """Reads frames from a cv2.VideoCapture device (e.g. OBS Virtual Camera).
    Exposes the same read() / release() interface as FrameReader.
    """
    def __init__(self, index: int, copy_interval: float = 1.0):
        self._cap = cv2.VideoCapture(index)
        self._frame = None
        self._ret   = False
        self._lock  = threading.Lock()
        self._stop  = False
        self._copy_interval = copy_interval
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while not self._stop:
            try:
                ret, frame = self._cap.read()
            except Exception:
                time.sleep(self._copy_interval)
                continue
            if ret and frame is not None:
                with self._lock:
                    self._ret   = True
                    self._frame = frame
            time.sleep(self._copy_interval)

    def read(self):
        with self._lock:
            if self._frame is None:
                return False, None
            return self._ret, self._frame.copy()

    def get(self, prop):
        return self._cap.get(prop)

    def release(self):
        self._stop = True
        self._cap.release()


def _get_camera_names_windows() -> list:
    """Return camera device names via DirectShow (same order as cv2.CAP_DSHOW indices)."""
    try:
        from pygrabber.dshow_graph import FilterGraph
        return FilterGraph().get_input_devices()
    except Exception:
        return []


def scan_cameras() -> list:
    """Return list of (index, name) for all available camera devices.

    Uses pygrabber to get the full DirectShow device list — same enumeration
    order as cv2.CAP_DSHOW, so index N in the list == VideoCapture(N).
    Includes devices that are registered but not yet streaming (e.g. OBS
    Virtual Camera when OBS is open but virtual camera not started).
    """
    pnp_names = _get_camera_names_windows()
    # Scan at least as many indices as pygrabber found, with a floor of 9
    limit = max(9, len(pnp_names) - 1)
    found  = []
    name_i = 0

    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    saved_stderr = os.dup(2)
    os.dup2(devnull_fd, 2)
    os.close(devnull_fd)
    try:
        for i in range(limit + 1):
            cap = cv2.VideoCapture(i)
            opened = cap.isOpened()
            cap.release()

            if opened:
                name = pnp_names[name_i] if name_i < len(pnp_names) else f"Camera {i}"
                name_i += 1
                found.append((i, name))
    finally:
        os.dup2(saved_stderr, 2)
        os.close(saved_stderr)

    return found


def open_camera_stream(index: int, copy_interval: float = 1.0,
                       max_retries: int = None) -> CameraFrameReader:
    """Open a camera by index and wait for the first frame (up to 5 s).

    max_retries: None = infinite (CLI), 1 = fail fast (GUI).
    """
    attempt = 0
    while True:
        if max_retries is not None and attempt >= max_retries:
            raise RuntimeError(
                f"Could not open camera index {index} after {max_retries} attempt(s).")
        attempt += 1
        log(f"Opening camera index {index}...")
        reader = CameraFrameReader(index, copy_interval=copy_interval)
        deadline = time.time() + 5
        while time.time() < deadline:
            ret, frame = reader.read()
            if ret and frame is not None:
                log(f"Camera {index} ready.", "OK")
                return reader
            time.sleep(0.2)
        reader.release()
        log(f"No frame from camera {index}.", "RETRY")


def get_source_label(cfg) -> str:
    """Return a human-readable description of the configured camera."""
    cam_name = cfg.get("virtual_camera_name", "").strip()
    idx = cfg.get("virtual_camera_index", 0)
    if cam_name:
        return f"{cam_name}  (index {idx})"
    return f"Camera index {idx}"


def open_input_stream(cfg, max_retries: int = None) -> CameraFrameReader:
    """Open the configured camera.  Pass max_retries=1 from GUI workers."""
    return open_camera_stream(
        int(cfg["virtual_camera_index"]),
        copy_interval=cfg["capture_interval"],
        max_retries=max_retries,
    )
