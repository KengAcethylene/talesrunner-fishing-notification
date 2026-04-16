import cv2
import numpy as np
import signal
import sys
import argparse

from index import run_inference, clean_and_read_quota, load_templates, crop, CANVAS_SIZE, ROI_QUOTA, ROI_TIME, HAS_DISPLAY

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
parser = argparse.ArgumentParser(description="OCR test on a still image")
parser.add_argument("image", nargs="?", default="pi_vision_test.jpg",
                    help="Path to image file (default: pi_vision_test.jpg)")
args = parser.parse_args()

image_path = args.image
frame_orig = cv2.imread(image_path)

if frame_orig is None:
    print(f"[ERROR] Cannot load image: {image_path}")
    sys.exit(1)

print("=" * 50)
print(f"Image loaded  : {image_path}")
print(f"Original size : {frame_orig.shape[1]}x{frame_orig.shape[0]}")
print(f"Canvas size   : {CANVAS_SIZE[0]}x{CANVAS_SIZE[1]}")
print("=" * 50)

templates = load_templates()
result = run_inference(frame_orig, templates)  # resizing handled inside run_inference

print(f"[QUOTA]  raw='{result['quota_raw']}'  parsed={result['quota_current']}")
print(f"[TIME]   raw='{result['time_raw']}'")
print("=" * 50)

# ============================================================
# Pixel diagnostics — tells us exactly which thresholds to use
# ============================================================
print("--- Pixel Diagnostics ---")
frame_diag = cv2.resize(frame_orig, CANVAS_SIZE)
c    = crop(frame_diag, ROI_QUOTA)
gray = cv2.cvtColor(c, cv2.COLOR_BGR2GRAY)
print(f"  [QUOTA] gray  min={gray.min():3d}  max={gray.max():3d}  mean={gray.mean():.1f}")
print("=" * 50)

if not HAS_DISPLAY:
    print("[INFO] No display available — skipping visualisation windows.")
    sys.exit(0)

print("Press Q or ESC in any window to quit, or Ctrl+C in terminal.")

# ============================================================
# Debug visualisation
# ============================================================
frame = cv2.resize(frame_orig, CANVAS_SIZE)

# Main window — bounding boxes + labels on the resized frame
debug = frame.copy()

x, y, w, h = ROI_QUOTA
cv2.rectangle(debug, (x, y), (x + w, y + h), (0, 255, 0), 2)
cv2.putText(debug, f"QUOTA: {result['quota_raw']}", (x, y - 8),
            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2, cv2.LINE_AA)

x, y, w, h = ROI_TIME
cv2.rectangle(debug, (x, y), (x + w, y + h), (255, 128, 0), 2)
cv2.putText(debug, f"TIME: {result['time_raw']}", (x, y - 8),
            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 128, 0), 2, cv2.LINE_AA)

cv2.imshow("Debug - ROI Boxes", debug)

# Quota window: original crop (left) | threshold (right)
cropped = crop(frame, ROI_QUOTA)
scaled  = cv2.resize(cropped, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
gray    = cv2.cvtColor(scaled, cv2.COLOR_BGR2GRAY)
blur    = cv2.GaussianBlur(gray, (0, 0), 3)
gray    = cv2.addWeighted(gray, 1.5, blur, -0.5, 0)
_, thresh = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)
thresh_bgr = cv2.cvtColor(thresh, cv2.COLOR_GRAY2BGR)
cv2.imshow(f"QUOTA  '{result['quota_raw']}'", np.hstack([scaled, thresh_bgr]))

# Time window: original crop (left) | threshold (right)
cropped_t = crop(frame, ROI_TIME)
scaled_t  = cv2.resize(cropped_t, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
gray_t    = cv2.cvtColor(scaled_t, cv2.COLOR_BGR2GRAY)
blur_t    = cv2.GaussianBlur(gray_t, (0, 0), 3)
gray_t    = cv2.addWeighted(gray_t, 1.5, blur_t, -0.5, 0)
_, thresh_t = cv2.threshold(gray_t, 180, 255, cv2.THRESH_BINARY_INV)
thresh_t_bgr = cv2.cvtColor(thresh_t, cv2.COLOR_GRAY2BGR)
cv2.imshow(f"TIME  '{result['time_raw']}'", np.hstack([scaled_t, thresh_t_bgr]))

# ============================================================
# Event loop
# ============================================================
while True:
    key = cv2.waitKey(100) & 0xFF
    if key in (27, ord('q'), ord('Q')):  # ESC or Q
        break

cv2.destroyAllWindows()
