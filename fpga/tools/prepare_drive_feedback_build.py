"""Create an isolated PDS build, preserving existing released bitstreams."""
from pathlib import Path
import hashlib
import json
import shutil
import filecmp
import stat

root = Path(__file__).resolve().parents[1]
destination = root.parent / 'drive_feedback_build_20261102'
destination.mkdir(exist_ok=True)
def copy_source(source, target):
    if Path(target).exists() and filecmp.cmp(source, target, shallow=False):
        return target
    if Path(target).exists():
        Path(target).chmod(stat.S_IREAD | stat.S_IWRITE)
    return shutil.copy2(source, target)

for directory in ('src', 'ipcore'):
    shutil.copytree(root / directory, destination / directory, dirs_exist_ok=True, copy_function=copy_source)
for filename in ('hdmi_loop.pds', 'impl.tcl'):
    shutil.copy2(root / filename, destination / filename)
tracked = ('src/hdmi_loop.v', 'src/hdmi_loop.fdc', 'src/pcie/pango_pcie_top.v',
           'src/pcie/drive_feedback_leds.v',
           'ipcore/pcie_test/example_design/rtl/ipsl_pcie_dma_ctrl/ipsl_pcie_dma.v')
manifest = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in tracked}
(destination / 'source_manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
print('FEEDBACK_ISOLATED_BUILD_READY', destination)
