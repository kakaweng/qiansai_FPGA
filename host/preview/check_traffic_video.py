import argparse
import csv
from pathlib import Path
import sys

from traffic_light import TrafficLightMonitor, draw_traffic_light, load_scene


def main():
    parser = argparse.ArgumentParser(description="Validate red/green color evidence in the first 30 seconds of a scene")
    parser.add_argument("video", type=Path)
    parser.add_argument("--scene", type=Path, default=Path(__file__).with_name("traffic_redgreen_scene.json"))
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--sample-fps", type=float, default=10)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--headless", action="store_true")
    arguments = parser.parse_args()
    if not 0 < arguments.seconds <= 30 or arguments.sample_fps <= 0:
        parser.error("seconds must be in (0,30]; sample-fps must be positive")
    try:
        import cv2
        import numpy
        scene = load_scene(arguments.scene)
    except (ImportError, OSError, ValueError, KeyError) as error:
        parser.exit(1, f"Traffic video setup failed: {error}\n")
    capture = cv2.VideoCapture(str(arguments.video))
    if not capture.isOpened():
        parser.exit(1, f"Cannot open video: {arguments.video}\n")
    rate = capture.get(cv2.CAP_PROP_FPS)
    if rate <= 0:
        capture.release()
        parser.exit(1, "Invalid video frame rate\n")
    monitor = TrafficLightMonitor(scene, cv2, numpy, allow_roi_fallback=True)
    arguments.output_dir.mkdir(parents=True, exist_ok=True)
    log_path = arguments.output_dir / "traffic.csv"
    frame_index, next_sample = 0, 0.0
    try:
        with log_path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.writer(output)
            writer.writerow(("video_seconds", "raw_state", "state", "action", "source", "candidates", "reason"))
            while frame_index / rate < arguments.seconds:
                success, image = capture.read()
                if not success:
                    break
                seconds = frame_index / rate
                frame_index += 1
                if seconds + 1e-9 < next_sample:
                    continue
                next_sample += 1 / min(rate, arguments.sample_fps)
                blue, green, red = (image[:, :, channel].astype(numpy.uint16) for channel in range(3))
                packed = ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)
                red, green, blue = (packed >> 11) & 31, (packed >> 5) & 63, packed & 31
                image[:, :, 2] = (red << 3) | (red >> 2)
                image[:, :, 1] = (green << 2) | (green >> 4)
                image[:, :, 0] = (blue << 3) | (blue >> 2)
                result = monitor.update(image, [], round(seconds * 1e9))
                writer.writerow((f"{seconds:.3f}", result["raw_state"], result["state"], result["action"],
                                 result["source"], len(result["candidates"]), result["reason"]))
                if result["changed"]:
                    print(f"Signal transition: video_s={seconds:.3f} state={result['state']} "
                          f"action={result['action']} source={result['source']}", flush=True)
                    cv2.imwrite(str(arguments.output_dir / f"signal_{seconds:.3f}_{result['state']}.png"),
                                draw_traffic_light(image.copy(), result, cv2))
                if not arguments.headless:
                    preview = cv2.resize(image, (960, 540))
                    draw_traffic_light(preview, result, cv2, scale=960 / image.shape[1])
                    cv2.imshow("Traffic red/green offline check", preview)
                    if cv2.waitKey(max(1, round(1000 / min(rate, arguments.sample_fps)))) & 0xFF in (27, ord("q")):
                        break
    finally:
        capture.release()
        if not arguments.headless:
            cv2.destroyAllWindows()
    print(f"Color-only scene check completed; not RKNN detection validation. Log: {log_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
