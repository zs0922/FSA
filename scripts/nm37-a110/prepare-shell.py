"""Restore exact measured shell inputs from pinned upstream; preserve dirty trees."""
import hashlib
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[2]
shell = root / 'fpga/fpga-shells'
assets = root / 'fpga/baselines/nm37-a110'
lock = json.loads((assets / 'source-lock.json').read_text())
def matches():
    return all((shell / p).is_file() and hashlib.sha256((shell / p).read_bytes()).hexdigest() == h
               for p, h in lock['shell_hashes'].items())
assert subprocess.check_output(['git', '-C', str(shell), 'rev-parse', 'HEAD'], text=True).strip() == lock['shell_commit'], 'Wrong shell commit'
if not matches():
    assert not subprocess.check_output(['git', '-C', str(shell), 'status', '--porcelain', '--untracked-files=all']), 'Shell differs: use a fresh checkout; no files were overwritten'
    subprocess.run(['git', '-C', str(shell), 'apply', '--check', str(assets / 'fpga-shells.patch')], check=True)
    subprocess.run(['git', '-C', str(shell), 'apply', str(assets / 'fpga-shells.patch')], check=True)
assert matches(), 'Shell fingerprint mismatch'
print('PASS: 55 frozen shell files match the inherited A100 shell snapshot')
