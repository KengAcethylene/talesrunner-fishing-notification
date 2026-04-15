import cv2
import numpy as np
import signal
import sys

from index import run_inference, clean_and_read, crop, CANVAS_SIZE, ROI_QUOTA, ROI_BAIT, ROI_TIME

# ============================================================
# Ctrl+C — closes all OpenCV windows and exits cleanly
# ============================================================
def on_exit(sig=None, frame=None):
    print("\n[EXIT] Ctrl+C detected. Closing windows...")
    cv2.destroyAllWindows()
    sys.exit(0)

signal.signal(signal.SIGINT, on_exit)

# ============================================================
# Load image and run inference
# ============================================================
image_path = "pi_vision_test.jpg"
frame_orig = cv2.imread(image_path)

if frame_orig is None:
    print(f"[ERROR] Cannot load image: {image_path}")
    sys.exit(1)

print("=" * 50)
print(f"Image loaded  : {image_path}")
print(f"Original size : {frame_orig.shape[1]}x{frame_orig.shape[0]}")
print(f"Canvas size   : {CANVAS_SIZE[0]}x{CANVAS_SIZE[1]}")
print("=" * 50)

result = run_inference(frame_orig)  # resizing handled inside run_inference

print(f"[QUOTA]  raw='{result['quota_raw']}'  parsed={result['quota_current']}")
print(f"[BAIT]   raw='{result['bait']}'")
print(f"[TIME]   raw='{result['game_time']}'")
print("=" * 50)
print("Press Q or ESC in any window to quit, or Ctrl+C in terminal.")

# ============================================================
# Debug visualisation
# ============================================================
frame = cv2.resize(frame_orig, CANVAS_SIZE)

regions = [
    ("QUOTA", ROI_QUOTA, False, result["quota_raw"]),
    ("BAIT",  ROI_BAIT,  False, result["bait"]),
    ("TIME",  ROI_TIME,  True,  result["game_time"]),
]

# Main window — bounding boxes + labels on the resized frame
debug  = frame.copy()
colors = {"QUOTA": (0, 255, 0), "BAIT": (0, 165, 255), "TIME": (0, 255, 255)}

for name, roi, _, text in regions:
    x, y, w, h = roi
    color = colors[name]
    cv2.rectangle(debug, (x, y), (x + w, y + h), color, 2)
    cv2.putText(debug, f"{name}: {text}", (x, y - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2, cv2.LINE_AA)

cv2.imshow("Debug - ROI Boxes", debug)

# Per-region window: original crop (left) | threshold (right)
for name, roi, is_time, text in regions:
    cropped    = crop(frame, roi)
    thresh_val = 150 if (is_time or name == "BAIT") else 180
    gray       = cv2.cvtColor(cropped, cv2.COLOR_BGR2GRAY)
    scaled     = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    _, thresh  = cv2.threshold(scaled, thresh_val, 255, cv2.THRESH_BINARY_INV)

    orig_bgr   = cv2.resize(cropped, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    thresh_bgr = cv2.cvtColor(thresh, cv2.COLOR_GRAY2BGR)
    cv2.imshow(f"{name}  '{text}'", np.hstack([orig_bgr, thresh_bgr]))

# ============================================================
# Event loop
# ============================================================
while True:
    key = cv2.waitKey(100) & 0xFF
    if key in (27, ord('q'), ord('Q')):  # ESC or Q
        break

cv2.destroyAllWindows()
