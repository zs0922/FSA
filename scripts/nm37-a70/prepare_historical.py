#!/usr/bin/env python3
"""Apply the complete historical patch stack before starting a separate build process.

Only clean pinned submodules are accepted initially. Repeated calls verify the
entire recorded patched trees, rather than treating one grep marker as proof.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

PINS = {'generators/fsa': 'f4051d9e6e1aac80b74fee9c9cff0096e19f7acf',
        'generators/easyfloat': '44e1c369659c2c397548d2aecfc4ded3a74a111e',
        'fpga/fpga-shells': 'd7c353201c61f1879afdb2fddf3c52283d450147'}

def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()

def digest(path):
    value = os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
    return hashlib.sha256(value).hexdigest()

def plan(stage):
    result = [('fpga/fpga-shells', 'fpga/patches/p0p1/fpga-shells-p0p1.patch'),
              ('fpga/fpga-shells', 'fpga/patches/nm37/fpga-shells-nm37.patch'),
              ('generators/fsa', 'fpga/patches/u280/fsa-u280.patch'),
              ('generators/fsa', 'fpga/patches/p2/fsa-p2.patch')]
    if stage == 'p3':
        result += [('generators/fsa', 'fpga/patches/p3/fsa-p3.patch'),
                   ('generators/easyfloat', 'fpga/patches/p3/easyfloat-p3.patch')]
    return result + [('generators/fsa', 'fpga/patches/nm37/fsa-nm37.patch')]

def snapshot(root, steps):
    names = set()
    for repo in PINS:
        names.update(repo + '/' + p for p in git(root / repo, 'ls-files', '-z').split('\0') if p)
    for repo, patch in steps:
        for line in (root / patch).read_text().splitlines():
            if line.startswith('+++ b/'):
                names.add(repo + '/' + line[6:])
    return {name: {'sha256': digest(root / name),
                   'mode': (root / name).lstat().st_mode & 0o177777}
            for name in sorted(names) if (root / name).exists() or (root / name).is_symlink()}

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--stage', choices=['p2', 'p3'], required=True)
    args = ap.parse_args()
    root = args.root.resolve()
    steps = plan(args.stage)
    identity = {'stage': args.stage, 'pins': PINS,
                'patches': {p: digest(root / p) for _, p in steps}}
    state = root / '.nm37-a70' / 'patch-state.json'
    for repo, pin in PINS.items():
        assert git(root / repo, 'rev-parse', 'HEAD') == pin, f'Wrong gitlink: {repo}'
    if state.exists():
        recorded = json.loads(state.read_text())
        assert recorded['identity'] == identity, 'Patch identities changed'
        assert recorded['files'] == snapshot(root, steps), 'Patched source changed'
        print('PASS: exact historical patched trees unchanged')
        return
    for repo in PINS:
        assert not git(root / repo, 'status', '--porcelain', '--untracked-files=all'), f'Use a clean submodule: {repo}'
    # Each command is fail-closed. Partial failure requires a fresh checkout;
    # no marker can excuse a failed patch in a different repository.
    for repo, patch in steps:
        subprocess.run(['git', '-C', str(root / repo), 'apply', '--check', str(root / patch)], check=True)
        subprocess.run(['git', '-C', str(root / repo), 'apply', str(root / patch)], check=True)
    state.parent.mkdir(exist_ok=True)
    with state.open('x') as f:
        json.dump({'schema': 1, 'identity': identity, 'files': snapshot(root, steps)}, f, indent=2)
        f.write('\n')
    print('PASS: complete historical patches applied before generator compilation')

if __name__ == '__main__':
    main()
