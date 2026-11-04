"""Snapshot a separate lowlight-v2 build without touching released bitstreams."""
from pathlib import Path
import filecmp
import hashlib
import json
import shutil
import stat
import argparse

root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--output',type=Path,default=root.parent/'lowlight_build_final_20261102')
build=parser.parse_args().output
build.mkdir(exist_ok=True)


def copy_source(source,target):
    target=Path(target)
    if target.exists() and filecmp.cmp(source,target,shallow=False):return target
    if target.exists():target.chmod(stat.S_IREAD|stat.S_IWRITE)
    return shutil.copy2(source,target)


for directory in ('src','ipcore'):
    shutil.copytree(root/directory,build/directory,dirs_exist_ok=True,copy_function=copy_source)
for name in ('hdmi_loop.pds','impl.tcl'):shutil.copy2(root/name,build/name)
shutil.copy2(root/'hdmi_loop.pds',build/'project_input.pds')
names=('src/hdmi_loop.v','src/hdmi_lowlight_v2.v','src/hdmi_lowlight_gain_rom.v',
       'src/hdmi_loop.fdc','src/pcie/drive_feedback_leds.v','src/pcie/pango_pcie_top.v',
       'ipcore/pcie_test/example_design/rtl/ipsl_pcie_dma_ctrl/ipsl_pcie_dma.v','hdmi_loop.pds')
(build/'source_manifest.json').write_text(json.dumps({name:hashlib.sha256((root/name).read_bytes()).hexdigest()
                                                    for name in names},indent=2),encoding='ascii')
print('LOWLIGHT_BUILD_READY',build)
