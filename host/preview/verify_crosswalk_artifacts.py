import argparse
import hashlib
import json
from pathlib import Path

from training.convert_crosswalk_rknn import MODEL_VERSION, SOURCE_SHA256, DECODER, fnv1a64


def verify(folder):
    folder = Path(folder)
    manifest = json.loads((folder / 'crosswalk.manifest.json').read_text(encoding='utf-8'))
    data = (folder / 'crosswalk.rknn').read_bytes()
    contract = (folder / 'crosswalk.contract').read_bytes()
    expected_contract = ('crosswalk_rknn_contract_v1\n'
                         f'model_version {MODEL_VERSION}\nonnx_sha256 {SOURCE_SHA256}\nepoch 11\n'
                         f'rknn_bytes {len(data)}\nrknn_fnv1a64 {fnv1a64(data):016x}\n'
                         'labels background,crosswalk_zebra\ninput rgb_u8_nhwc_1x384x640x3\n'
                         'normalization embedded_imagenet\nresize full_frame_letterbox_pil_bilinear_rgb114\n'
                         'output logits_1x2x384x640_layout_query\nprecision non_int8\n'
                         f'decoder {DECODER}\n').encode('ascii')
    if (contract != expected_contract or
            manifest.get('model_version') != MODEL_VERSION or manifest.get('source_onnx_sha256') != SOURCE_SHA256 or
            manifest.get('rknn_sha256') != hashlib.sha256(data).hexdigest() or
            manifest.get('contract_sha256') != hashlib.sha256(contract).hexdigest() or
            manifest.get('epoch') != 11 or manifest.get('labels') != ['background', 'crosswalk_zebra'] or
            manifest.get('decoder') != DECODER or manifest.get('target') != 'rk3568' or
            manifest.get('rknn_bytes') != len(data) or manifest.get('rknn_toolkit2', '').split('+')[0] != '2.3.2' or
            manifest.get('quantization') != 'none; RKNN non-INT8 FP16'):
        raise ValueError('Crosswalk artifact hash or contract mismatch')
    print('CROSSWALK_ARTIFACTS_PASS')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('folder', type=Path)
    verify(parser.parse_args().folder)
