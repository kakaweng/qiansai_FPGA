import argparse
import ast
import hashlib
import json
import re
from pathlib import Path


EXPECTED_OUTPUT_SHAPES = [[1, 255, 80, 80], [1, 255, 40, 40], [1, 255, 20, 20]]
IMPLEMENTATION_FILES = (
    "pcie_rknn_probe.c", "rknn_stream.py", "draw_detections.py",
    "traffic_light.py", "lane_detection.py", "frame_format.py",
)


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_sources(root, manifest, label_values):
    source = (root / "pcie_rknn_probe.c").read_text(encoding="utf-8")
    label_block = re.search(r"coco_labels\[YOLO_CLASS_COUNT\]\s*=\s*\{(.*?)\};", source, re.S)
    require(label_block is not None, "cannot find C label table")
    require(re.findall(r'"([^"\n]+)"', label_block[1]) == label_values,
            "C label order differs from labels file")
    python_tree = ast.parse((root / "rknn_stream.py").read_text(encoding="utf-8"))
    python_labels = None
    for statement in python_tree.body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "COCO_LABELS"
            for target in statement.targets
        ):
            python_labels = list(ast.literal_eval(statement.value))
    require(python_labels == label_values, "preview label order differs from labels file")
    for name, value in (("YOLO_CLASS_COUNT", 80), ("YOLO_CONFIDENCE_THRESHOLD", 0.25),
                        ("YOLO_NMS_THRESHOLD", 0.45)):
        match = re.search(r"^#define\s+" + name + r"\s+([\d.]+)[Uf]?\s*$", source, re.M)
        require(match is not None and float(match[1]) == value, f"C constant differs: {name}")
    anchor_block = re.search(r"yolo_anchors\[3\]\[6\]\s*=\s*\{(.*?)\};", source, re.S)
    require(anchor_block is not None, "cannot find C anchors")
    source_anchors = [int(value) for value in re.findall(r"\d+", anchor_block[1])]
    contract_anchors = [value for head in manifest["architecture"]["anchors"]
                        for anchor in head for value in anchor]
    require(source_anchors == contract_anchors, "C anchors differ from manifest")
    hashes = manifest["implementation_files"]
    require(set(hashes) == set(IMPLEMENTATION_FILES), "implementation file list is incomplete")
    for name in IMPLEMENTATION_FILES:
        normalized_source = (root / name).read_text(encoding="utf-8")
        actual_hash = hashlib.sha256(normalized_source.encode("utf-8")).hexdigest()
        require(hashes[name] == actual_hash,
                f"implementation changed since contract review: {name}; review and version the manifest")


def validate(manifest_path, model_path=None, require_artifact=False):
    root = manifest_path.resolve().parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    required = ("schema_version", "manifest_id", "model_version", "artifact", "target",
                "architecture", "labels", "input", "outputs", "quantization", "decoder",
                "semantic_bindings", "implementation_files")
    for field in required:
        require(field in manifest, f"missing manifest field: {field}")
    require(manifest["schema_version"] == "1.0", "unsupported schema version")
    require(manifest["manifest_id"] and manifest["model_version"], "model identity must be present")
    architecture = manifest["architecture"]
    labels = manifest["labels"]
    input_spec = manifest["input"]
    outputs = manifest["outputs"]
    decoder = manifest["decoder"]
    require(architecture["family"] == "yolov5" and architecture["class_count"] == 80,
            "this validator supports the current YOLOv5 80-class decoder only")
    require(architecture["strides"] == [8, 16, 32], "baseline strides must be 8,16,32")
    label_path = root / labels["path"]
    label_values = label_path.read_text(encoding="utf-8").splitlines()
    require(len(label_values) == 80 and all(label.strip() == label and label for label in label_values),
            "labels must contain exactly 80 nonempty, unpadded lines")
    require(len(set(label_values)) == 80, "duplicate labels")
    label_hash = sha256_file(label_path)
    require(labels["sha256"] == label_hash, "label file sha256 does not match manifest")
    require(input_spec["layout"] == "NHWC" and input_spec["shape"] == [1, 640, 640, 3]
            and input_spec["dtype"] == "uint8" and input_spec["color_order"] == "RGB"
            and input_spec["source_format"] == "RGB565LE", "baseline input contract differs")
    preprocess = input_spec["preprocess"]
    expected_preprocess = {
        "source_size": [1920, 1080], "resize_mode": "letterbox",
        "resize_interpolation": "nearest_center_sample", "resized_size": [640, 360],
        "padding": [0, 140, 0, 140], "padding_value": 114,
        "channel_expand_rule": "R/B=(value5<<3)|(value5>>2); G=(value6<<2)|(value6>>4)",
        "normalization": "none_in_C; model_internal_or_toolkit_normalization_unverified",
        "toolkit_mean_values": None, "toolkit_std_values": None,
    }
    for field, expected in expected_preprocess.items():
        require(preprocess[field] == expected, f"baseline preprocessing differs: {field}")
    require(outputs["layout"] == "NCHW" and outputs["head_count"] == 3
            and outputs["values_per_anchor"] == 85 and outputs["activation"] == "post_sigmoid",
            "baseline requires three NCHW post-sigmoid heads")
    require([tensor["shape"] for tensor in outputs["tensors"]] == EXPECTED_OUTPUT_SHAPES
            and [tensor["index"] for tensor in outputs["tensors"]] == [0, 1, 2]
            and [tensor["stride"] for tensor in outputs["tensors"]] == [8, 16, 32],
            "baseline output tensor order or shape differs")
    expected_decoder = {
        "type": "yolov5_anchor_grid", "apply_sigmoid": False,
        "objectness": "post_sigmoid_from_model", "class_probability": "post_sigmoid_from_model",
        "box_center": "(xy*2-0.5+grid)*stride", "box_size": "(wh*2)^2*anchor",
        "score": "objectness*class_probability", "confidence_threshold": 0.25,
        "nms_threshold": 0.45, "implementation": "pcie_rknn_probe.c",
    }
    for field, expected in expected_decoder.items():
        require(decoder[field] == expected, f"baseline decoder differs: {field}")
    require(manifest["target"]["chip"] == "rk3568", "target chip must be rk3568")
    require(manifest["quantization"]["weights"] == "int8"
            and manifest["quantization"]["activations"] == "int8", "baseline quantization differs")
    semantic = manifest["semantic_bindings"]
    require(semantic["traffic_light_class_id"] == 9 and label_values[9] == "traffic light"
            and semantic["occluder_class_ids"] == [0, 1, 2, 3, 5, 7], "baseline semantic mapping differs")
    validate_sources(root, manifest, label_values)
    artifact = manifest["artifact"]
    if model_path is None and artifact.get("project_path"):
        model_path = root / artifact["project_path"]
    if require_artifact:
        require(model_path is not None and artifact.get("sha256"),
                "release check requires both the actual RKNN file and its recorded sha256")
    if model_path is not None:
        require(bool(artifact.get("sha256")), "record artifact.sha256 before verifying a model")
        require(sha256_file(model_path) == artifact["sha256"], "RKNN sha256 differs from manifest")
    return label_hash, label_values, model_path is not None


def main():
    parser = argparse.ArgumentParser(description="Check current baseline against C/Python implementations")
    parser.add_argument("manifest", nargs="?", type=Path,
                        default=Path(__file__).with_name("model_manifest.json"))
    parser.add_argument("--model", type=Path, help="Actual RKNN file to verify against artifact.sha256")
    parser.add_argument("--require-artifact", action="store_true", help="Fail if the RKNN binary is unavailable")
    arguments = parser.parse_args()
    try:
        label_hash, labels, artifact_verified = validate(
            arguments.manifest, arguments.model, arguments.require_artifact)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"Manifest validation failed: {error}\n")
    print(f"Manifest/source contract OK: {arguments.manifest}")
    print(f"Labels: {len(labels)}; sha256={label_hash}")
    print("Decoder: YOLOv5 / 80 classes / 3 post-sigmoid NCHW heads / INT8 baseline")
    print("RKNN artifact: verified" if artifact_verified else
          "RKNN artifact: NOT verified; reference contract only, not a board deployment check")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
