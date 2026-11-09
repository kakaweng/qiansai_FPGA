import hashlib
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    report = json.loads((root/'rk3568_pie_deploy/c_export.manifest.json').read_text(encoding='utf-8'))
    if report.get('model_id') != 'pie_anticipation_v2_20261101' or report.get('no_control') is not True:
        raise ValueError('PIE C export contract mismatch')
    for name, expected in report['generated_sha256'].items():
        if hashlib.sha256((root/name).read_bytes()).hexdigest() != expected:
            raise ValueError('PIE generated file hash mismatch: '+name)
    model = root/'runs/pie_behavior/anticipation_v2_20261101'
    for name, field in [('anticipation.onnx', 'source_onnx_sha256'), ('geometry.pt', 'source_checkpoint_sha256'),
                        ('anticipation.manifest.json', 'source_manifest_sha256')]:
        if hashlib.sha256((model/name).read_bytes()).hexdigest() != report[field]:
            raise ValueError('PIE source model hash mismatch: '+name)
    print('PIE_C_EXPORT_VERIFY_PASS model='+report['model_id'])


if __name__ == '__main__':
    main()
