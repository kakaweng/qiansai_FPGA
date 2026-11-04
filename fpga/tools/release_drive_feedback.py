"""Publish only the completed isolated build, with source and timing provenance."""
from pathlib import Path
import hashlib
import json
import shutil

root=Path(__file__).resolve().parents[1]
build=root.parent/'drive_feedback_build_20261102'
manifest=json.loads((build/'source_manifest.json').read_text(encoding='utf-8'))
for name,expected in manifest.items():
    for location in (root,build):
        if hashlib.sha256((location/name).read_bytes()).hexdigest()!=expected:
            raise SystemExit('Source changed since isolated build: '+str(location/name))
log_bytes=(build/'build_run.log').read_bytes()
# Windows PowerShell redirects native output as UTF-16; direct PDS logs use UTF-8.
log_encoding='utf-16' if log_bytes.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
log=log_bytes.decode(log_encoding,errors='replace')
if 'Generating Programming File done' not in log or 'Generate Bitstream" done' not in log:
    raise SystemExit('PDS has not completed bitstream generation')
source=build/'generate_bitstream/hdmi_loop.sbit'
data=source.read_bytes()
if len(data)<1000000:raise SystemExit('Bitstream unexpectedly small')
destination=root/'hdmi_loop.drive_feedback_v1.sbit'
shutil.copy2(source,destination)
report=build/'report_timing/hdmi_loop.rtr'
timing=report.read_text(encoding='utf-8',errors='replace')
timing_closed='Some Constraints Violated' not in timing and 'All Constraints Met' in timing
result=dict(version='drive-feedback-v1',file=str(destination),bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),source_manifest=manifest,
            timing_closed=timing_closed,board_verified=False,
            timing_report=str(report),build_log=str(build/'build_run.log'),
            preserved_keys='KEY1..4 legacy; KEY5/6/7 left/straight/right',
            pins={'left':'A2','straight':'B3','right':'C5'},register='BAR1 0x180; BAR0 0x180..0x18c')
(build/'release_manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
host=root.parent/'09_pcie_test/pcie_host/rk3568_fpga_feedback_deploy'
(host/'fpga_manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print('FPGA_FEEDBACK_BITSTREAM_PASS',json.dumps(result,ensure_ascii=False))
