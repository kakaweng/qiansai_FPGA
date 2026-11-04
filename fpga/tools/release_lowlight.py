"""Publish a completed lowlight-v2 build with truthful source/timing provenance."""
from pathlib import Path
import hashlib
import json
import shutil
import re

root=Path(__file__).resolve().parents[1]
build=root.parent/'lowlight_build_final_20261102'
source_manifest=json.loads((build/'source_manifest.json').read_text(encoding='ascii'))
for name,expected in source_manifest.items():
    for place in (root,build):
        checked=place/'project_input.pds' if place==build and name=='hdmi_loop.pds' else place/name
        if hashlib.sha256(checked.read_bytes()).hexdigest()!=expected:
            raise SystemExit('Source differs from compiled snapshot: '+str(place/name))
# PDS rewrites its timestamps, build states and output sections. Setup must stay identical.
def project_setup(path):
    data=path.read_text(encoding='utf-8').split('(_task tsk_compile',1)[0]
    return re.sub(r'\s+',' ',re.sub(r'\(_(?:timespec|comment) "[^"]*"\)','',data)).strip()
if project_setup(build/'project_input.pds')!=project_setup(build/'hdmi_loop.pds'):
    raise SystemExit('PDS changed source/device/project setup')
log_bytes=(build/'build_run.log').read_bytes()
log=log_bytes.decode('utf-16' if log_bytes.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig',errors='replace')
if 'Generating Programming File done' not in log or 'Process "Generate Bitstream" done' not in log:
    raise SystemExit('Bitstream generation has not succeeded')
verification=json.loads((build/'verification.json').read_text(encoding='utf-8'))
if not verification['checks']['monotonic_curves'] or any(case['returncode'] for case in verification['rtl']):
    raise SystemExit('Lowlight verification missing/failed')
source=build/'generate_bitstream/hdmi_loop.sbit'
data=source.read_bytes()
if len(data)!=2101696:raise SystemExit('Unexpected PGL50H bitstream size')
timing_report=build/'report_timing/hdmi_loop.rtr'
timing=timing_report.read_text(encoding='utf-8',errors='replace')
for section in timing.split('Startpoint  :')[1:]:
    heading=section.split('Location',1)[0]
    if 'u_hdmi_lowlight/' in heading and re.search(r'Slack \(VIOLATED\)\s+-',section):
        raise SystemExit('Reported new lowlight timing path still violates constraints')
destination=root/'hdmi_loop.lowlight_v2.sbit'
shutil.copy2(source,destination)
result=dict(version='lowlight-v2',file=str(destination),bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),
            source_manifest=source_manifest,board_verified=False,
            timing_closed='All Constraints Met' in timing and 'Some Constraints Violated' not in timing,
            timing_report=str(timing_report),build_log=str(build/'build_run.log'),
            preserves='KEY1..4 legacy modes; mode5 KEY2/3 strength; KEY5..7 routes; LF01 LEDs; three inputs',
            scope='FPGA HDMI output and existing RGB UDP branch; PCIe inference input remains original RGB565',
            latency_clocks=10,strengths=['LOW 1 weak','LOW 2 medium default','LOW 3 strong'])
(build/'release_manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print('LOWLIGHT_BITSTREAM_PASS',json.dumps(result,ensure_ascii=False))
