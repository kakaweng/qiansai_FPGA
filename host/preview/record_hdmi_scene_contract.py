"""Record the existing board YOLO artifact without claiming conversion provenance."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DECODER = "yolov5-coco80-road-v1"
SIGNAL_VERSION = "yolo-box-hsv-green-symbols-c-v2"
FILES = ("road_object_core.c", "road_object_core.h", "road_object_rknn.c", "road_object_rknn.h",
         "road_object_async.c", "road_object_async.h", "rknn_scheduler.c", "rknn_scheduler.h",
         "lane_style_rknn.c", "lane_style_async.c", "lane_style_core.c", "pcie_multivideo_server.c", "scene_sha256.c",
         "traffic_light.py")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def contract(data):
    value = 14695981039346656037
    for byte in data:
        value = ((value ^ byte) * 1099511628211) & ((1 << 64) - 1)
    return f"road_object_contract_v1\nbytes {len(data)}\nfnv1a64 {value:016x}\ndecoder {DECODER}\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--board-sha256")
    args = parser.parse_args()
    directory = args.directory
    data = (directory / "road_objects.rknn").read_bytes()
    if not 0 < len(data) <= 100 * 1024 * 1024:
        raise ValueError("Invalid existing RKNN artifact size")
    model_sha = sha(data)
    if args.verify:
        manifest = json.loads((directory / "road_objects.manifest.json").read_text(encoding="utf-8"))
        if (manifest["artifact"]["sha256"] != model_sha or manifest["decoder"]["version"] != DECODER or
                manifest["signal"]["version"] != SIGNAL_VERSION or
                manifest["artifact"]["contract_sha256"] != sha(contract(data).encode("ascii")) or
                (directory / "road_objects.contract").read_text(encoding="ascii") != contract(data)):
            raise ValueError("Object model identity/decoder contract mismatch")
        for name, expected in manifest["implementation_sha256"].items():
            if sha((ROOT / name).read_bytes()) != expected:
                raise ValueError(f"Source changed after model contract recording: {name}")
        print("PASS: object model SHA256, FNV contract and implementation identity")
        return
    if not args.board_sha256 or model_sha != args.board_sha256.lower():
        raise ValueError("HDC received model differs from board SHA256")
    labels_data = (ROOT / "labels" / "coco80.txt").read_bytes()
    labels = labels_data.decode("utf-8-sig").splitlines()
    expected_labels = {0: "person", 1: "bicycle", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck", 9: "traffic light"}
    if len(labels) != 80 or any(labels[index] != label for index, label in expected_labels.items()):
        raise ValueError("COCO80 label order differs from decoder")
    text = contract(data)
    (directory / "road_objects.contract").write_bytes(text.encode("ascii"))
    manifest = {
        "schema_version": "hdmi_road_objects_v1", "model_version": "existing-board-yolov5s-coco80",
        "artifact": {"filename": "road_objects.rknn", "sha256": model_sha, "bytes": len(data),
                     "source_board_path": "/data/local/tmp/rknn-yolov5-test/model/yolov5s-640-640.rknn",
                     "board_hash_method": "project scene_sha256 utility; same glibc loader as scene server",
                     "contract_sha256": sha(text.encode("ascii"))},
        "labels": labels, "labels_sha256": sha(labels_data),
        "target": "rk3568", "original_conversion_provenance": "unavailable; reused previous deployed artifact",
        "input": {"runtime_type": "UINT8", "layout": "NHWC", "shape": [1, 640, 640, 3],
                  "color": "RGB", "source": "1920x1080 RGB565LE", "resize": "nearest_center_sample 640x360",
                  "padding": [0, 140, 0, 140], "padding_value": 114, "external_normalization": "none"},
        "outputs": {"layout": "NCHW", "shapes": [[1, 255, n, n] for n in (80, 40, 20)],
                    "activation": "post_sigmoid; no second sigmoid", "quantization": "query scale/zp per tensor at startup",
                    "transfer": "native INT8/UINT8 AFFINE or requested FP32"},
        "decoder": {"version": DECODER, "confidence": 0.25, "nms_iou": 0.45,
                    "anchors": [[10,13,16,30,33,23], [30,61,62,45,59,119], [116,90,156,198,373,326]],
                    "head_mapping": "grid/stride, independent of output order", "display_classes": expected_labels},
        "signal": {"version": SIGNAL_VERSION, "source": "original frame that produced YOLO boxes",
                   "search_region": [0.15, 0.04, 0.85, 0.60], "confirmations": 3, "timeout_ms": 500,
                   "saturation_min": 110, "value_min": 160, "hue_ranges": {"red": [[0,10],[170,179]], "green": [[45,100]], "yellow": [[15,38]]},
                   "min_component_area": 6, "max_component_area": 1400, "aspect_ratio": [0.5,1.8],
                   "min_fill": 0.45, "min_dark_housing_fraction": 0.25, "roi_fallback": False,
                   "green_symbol_profile": {"min_fill": 0.25, "min_dark_housing_fraction": 0.35,
                                            "area_and_aspect": "same as compact lamps", "direction": "not classified"},
                   "scope": "color evidence only; no arrow direction, governing lane or turn permission"},
        "execution": {"server": "multivideo-hdmi-scene-v4", "inference_location": "RK3568",
                      "source": "HDMI only", "workers": "separate latest-frame queues",
                      "npu": "serialized input/run/output-get; CPU preprocessing/decoding independent",
                      "max_object_age_ms": 750, "signal_display_evidence_age_ms": 500},
        "implementation_sha256": {name: sha((ROOT / name).read_bytes()) for name in FILES},
        "validation": {"board_status": "pending_two_model_inspect_and_preview"},
    }
    (directory / "road_objects.manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"OBJECT_CONTRACT_PASS sha256={model_sha} bytes={len(data)}")


if __name__ == "__main__":
    main()
