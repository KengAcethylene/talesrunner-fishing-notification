import cv2
import time
import argparse
import os
from datetime import datetime

from core import (
    Config, MonitorSession,
    log, set_log_file, set_debug_mode, set_log_callback,
    send_telegram,
    load_templates, save_template,
    match_digit, preprocess_quota, extract_char_crops,
    clean_and_read_quota, clean_and_read_time,
    calibrate, run_inference,
    FrameReader, open_stream, capture_frame, scan_sources,
    HAS_DISPLAY, crop,
)
import NDIlib as ndi

# ============================================================
# DEBUG DISPLAY (CLI only)
# ============================================================
def show_debug(frame, result, canvas_size, roi_quota):
    resized = cv2.resize(frame, canvas_size)
    overlay = resized.copy()

    x, y, w, h = roi_quota
    color = (0, 255, 0)
    cv2.rectangle(overlay, (x, y), (x + w, y + h), color, 2)
    cv2.putText(overlay, f"QUOTA: {result['quota_raw']}", (x, y - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2, cv2.LINE_AA)
    cv2.imshow("Debug - Live Stream", overlay)

    roi_crop = crop(resized, roi_quota)
    thresh = preprocess_quota(roi_crop)
    thresh_bgr = cv2.cvtColor(thresh, cv2.COLOR_GRAY2BGR)
    cv2.imshow(f"QUOTA  '{result['quota_raw']}'", thresh_bgr)

    key = cv2.waitKey(1) & 0xFF
    return key not in (27, ord('q'), ord('Q'))


# ============================================================
# MAIN LOOP
# ============================================================
def main(cfg: Config, debug: bool = False):
    templates = load_templates(cfg.templates_dir)
    if not templates:
        log("No templates found. Run:  py index.py --calibrate <current_count>", "WARNING")
        log("Example:  py index.py --calibrate 483", "WARNING")

    session = MonitorSession()

    reader = open_stream(cfg["ndi_source_name"], cfg["reconnect_delay"])
    stream_w = int(reader.get(cv2.CAP_PROP_FRAME_WIDTH))
    stream_h = int(reader.get(cv2.CAP_PROP_FRAME_HEIGHT))
    log(f"Stream: {stream_w}x{stream_h} → canvas {cfg.canvas_size[0]}x{cfg.canvas_size[1]}")
    log(f"Monitoring {'[DEBUG]' if debug else ''}. Waiting for first valid read...")

    while True:
        ret, frame = reader.read()
        if not ret or frame is None:
            log("Lost stream — reconnecting...", "WARNING")
            reader.release()
            reader = open_stream(cfg["ndi_source_name"], cfg["reconnect_delay"])
            continue

        result = run_inference(frame, templates, cfg.roi_quota, cfg.roi_time, cfg.canvas_size)
        current = result["quota_current"]

        if debug:
            if not HAS_DISPLAY:
                log("No display available — --debug ignored (headless mode).", "WARNING")
                debug = False
            elif not show_debug(frame, result, cfg.canvas_size, cfg.roi_quota):
                log("Debug window closed.", "EXIT")
                reader.release()
                break

        if current is None:
            session.no_read_seconds += cfg["capture_interval"]
            log(f"No numbers detected. ({session.no_read_seconds}/{cfg['no_read_timeout']}s)", "WARNING")
            if session.no_read_seconds >= cfg["no_read_timeout"]:
                msg = f"No quota numbers detected for {cfg['no_read_timeout']}s — shutting down."
                log(msg, "ERROR")
                send_telegram(cfg["telegram_bot_token"], cfg["telegram_chat_id"], msg)
                reader.release()
                break
            time.sleep(cfg["capture_interval"])
            continue

        session.no_read_seconds = 0

        if session.start_count != -1 and current < session.start_count:
            log(f"Quota reset detected ({session.last_reported_count} → {current}). Starting new session.")
            session.start_count = current
            session.last_reported_count = current
            session.alert_sent = False
            session.limit_sent = False
            session.session_start_time = datetime.now()

        if session.start_count == -1:
            session.start_count = current
            log(f"Session started. Initial count: {session.start_count}")

        caught_now = current - session.start_count
        threshold = cfg["quota_limit"] - cfg["quota_alert_buffer"]

        if current >= session.last_reported_count + cfg["report_interval"]:
            uptime = str(datetime.now() - session.session_start_time).split('.')[0]
            game_time = result.get("time_raw") or "--:--:--"
            log(f"FISH: {current}/{cfg['quota_limit']} (+{caught_now}) | GAME: {game_time} | UPTIME: {uptime}")
            session.last_reported_count = current

        if current >= threshold and not session.alert_sent:
            msg = f"QUOTA ALMOST FULL: {current}/{cfg['quota_limit']}"
            log(msg, "ALERT")
            send_telegram(cfg["telegram_bot_token"], cfg["telegram_chat_id"], msg)
            session.alert_sent = True

        if current >= cfg["quota_limit"] and not session.limit_sent:
            msg = f"LIMIT REACHED: {current}/{cfg['quota_limit']}"
            log(msg, "CRITICAL")
            send_telegram(cfg["telegram_bot_token"], cfg["telegram_chat_id"], msg)
            session.limit_sent = True

        time.sleep(cfg["capture_interval"])

    reader.release()
    if HAS_DISPLAY:
        cv2.destroyAllWindows()


# ============================================================
# ENTRY POINT
# ============================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TalesRunner fish monitor")
    parser.add_argument("--debug", action="store_true",
                        help="Show live ROI and threshold windows")
    parser.add_argument("--capture", action="store_true",
                        help="Grab one frame and save diagnostic images, then exit")
    parser.add_argument("--calibrate", metavar="COUNT",
                        help="Capture frame and save digit templates for COUNT "
                             "(e.g. --calibrate 483)")
    parser.add_argument("--telegram-debug", action="store_true",
                        help="Send every log message to Telegram (tests bot)")
    parser.add_argument("--log-file", metavar="PATH",
                        help="Write all log output to a file (e.g. --log-file monitor.log)")
    parser.add_argument("--no-timeout", action="store_true",
                        help="Disable auto-shutdown when no numbers are detected")
    parser.add_argument("--list-sources", action="store_true",
                        help="Discover and list NDI sources on the network, then exit")
    args = parser.parse_args()

    cfg = Config()

    set_debug_mode(args.debug)

    if args.no_timeout:
        cfg.set("no_read_timeout", float('inf'))

    if args.telegram_debug:
        def _tg_relay(line):
            send_telegram(cfg["telegram_bot_token"], cfg["telegram_chat_id"], line)
        set_log_callback(_tg_relay)

    ndi.initialize()
    try:
        if args.list_sources:
            print("Searching for NDI sources (10s)...")
            sources = scan_sources(10)
            if sources:
                print(f"Found {len(sources)} source(s):")
                for s in sources:
                    print(f"  {s}")
            else:
                print("No NDI sources found.")
        else:
            log_fh = None
            if args.log_file:
                log_fh = open(args.log_file, "a", encoding="utf-8")
                set_log_file(log_fh)
                log(f"Logging to {args.log_file}")
            try:
                if args.capture:
                    capture_frame(
                        source_name=cfg["ndi_source_name"],
                        canvas_size=cfg.canvas_size,
                        roi_quota=cfg.roi_quota,
                        reconnect_delay=cfg["reconnect_delay"],
                    )
                elif args.calibrate:
                    frame, _ = capture_frame(
                        source_name=cfg["ndi_source_name"],
                        canvas_size=cfg.canvas_size,
                        roi_quota=cfg.roi_quota,
                        reconnect_delay=cfg["reconnect_delay"],
                    )
                    if frame is not None:
                        templates = load_templates(cfg.templates_dir)
                        calibrate(frame, args.calibrate, templates,
                                  roi_quota=cfg.roi_quota,
                                  quota_limit=cfg["quota_limit"],
                                  canvas_size=cfg.canvas_size,
                                  templates_dir=cfg.templates_dir)
                else:
                    main(cfg, debug=args.debug)
            finally:
                if log_fh:
                    log_fh.close()
    except KeyboardInterrupt:
        log("Process terminated by user.", "EXIT")
        if HAS_DISPLAY:
            cv2.destroyAllWindows()
    finally:
        ndi.destroy()
