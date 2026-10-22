#!/usr/bin/env python3
"""Install the exact NM37 P2/P3 build-order repair into a clean historical checkout.

This is an overlay installer, not a checkout/submodule downloader. Restore the
pinned dependencies first. drycheck is read-only; install never overwrites an
existing installation and verify checks its immutable payload and patched trees.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

COMMITS = {'p2': '5acd5e8d5e61729090fc89a7e92d68bd01abcc37',
           'p3': '45d859c1c1bdbd282d0c4ac1e465c0fa0b4e296b'}
ORIGINAL_MAKEFILE_SHA = 'a8e551e68a7560391bcdcd19a8ca6771a35a01b57f23de7001fe9cea53846bae'
ORIGINAL_IGNORE_SHA = '8b7466f6bf9198f0cb0039fb0092a30f4d709eeb6f05f92e0538805900f64eae'
PINS = {'generators/fsa': 'f4051d9e6e1aac80b74fee9c9cff0096e19f7acf',
        'generators/easyfloat': '44e1c369659c2c397548d2aecfc4ded3a74a111e',
        'fpga/fpga-shells': 'd7c353201c61f1879afdb2fddf3c52283d450147'}
TESTS = ('A70AccumulatorVerification.scala', 'A70LegacyExp2.scala',
         'A70LegacyFMA.scala', 'A70LegacyReciprocal.scala')
MAKE_PREFIX = '''ifeq ($(SUB_PROJECT),nm37)
A70_STAGE := $(if $(wildcard $(base_dir)/fpga/patches/p3/fsa-p3.patch),p3,p2)
A70_PATCH_STATE := $(base_dir)/.nm37-a70/patch-state.json
.PHONY: a70-check-patched-sources apply-u280-patches apply-nm37-patches
a70-check-patched-sources:
$(A70_PATCH_STATE): a70-check-patched-sources
\tpython3 $(base_dir)/scripts/nm37-a70-prepare.py --root $(base_dir) --stage $(A70_STAGE)
apply-u280-patches apply-nm37-patches: $(A70_PATCH_STATE)
else
'''
MAKE_DEPS = '''
ifeq ($(SUB_PROJECT),nm37)
# A normal dependency invalidates any generator older than the applied patches.
# The shared prerequisite also serializes patch verification under make -j.
$(GENERATOR_CLASSPATH): $(A70_PATCH_STATE)
$(FIRRTL_FILE) $(ANNO_FILE) $(CHISEL_LOG_FILE): $(A70_PATCH_STATE)
endif
'''
IGNORE_APPEND = '\n# Frozen patch preparation state; checked before every historical NM37 build.\n.nm37-a70/\n'

def sha(data):
    return hashlib.sha256(data).hexdigest()

def require(ok, message):
    if not ok:
        raise RuntimeError(message)

def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()

def fingerprint(path):
    require(path.is_file() and not path.is_symlink(), f'Expected regular installed file: {path}')
    return {'sha256': sha(path.read_bytes()), 'mode': path.stat().st_mode & 0o7777}

def repaired_makefile(original):
    require(sha(original) == ORIGINAL_MAKEFILE_SHA, 'Unexpected original fpga/Makefile')
    text = original.decode()
    before = '.PHONY: apply-u280-patches\n'
    require(text.count(before) == 1, 'Unexpected patch target layout')
    text = text.replace(before, MAKE_PREFIX + before, 1)
    before = 'export USE_CHISEL6=1\n'
    require(text.count(before) == 1, 'Unexpected Chisel configuration layout')
    text = text.replace(before, 'endif\n\n' + before, 1)
    before = 'include $(base_dir)/common.mk\n'
    require(text.count(before) == 1, 'Unexpected common.mk include layout')
    return text.replace(before, before + MAKE_DEPS, 1).encode()

def expected_payload(root, stage):
    package = Path(__file__).resolve().parent
    original = subprocess.check_output(['git', '-C', str(root), 'show', COMMITS[stage] + ':fpga/Makefile'])
    ignore = subprocess.check_output(['git', '-C', str(root), 'show', COMMITS[stage] + ':.gitignore'])
    require(sha(ignore) == ORIGINAL_IGNORE_SHA, 'Unexpected original .gitignore')
    # A separate process completes prepare/verification BEFORE make starts and
    # evaluates its parse-time source globs. make prerequisites are a second gate.
    wrapper = '''#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
[[ $# -gt 0 ]] || { echo 'Usage: nm37-a70-build.sh COMMAND [ARG ...]' >&2; exit 2; }
python3 "$ROOT/scripts/nm37-a70-install.py" verify --root "$ROOT" --stage STAGE
cd "$ROOT"
exec "$@"
'''.replace('STAGE', stage)
    payload = {
        'fpga/Makefile': (repaired_makefile(original), 0o644),
        '.gitignore': (ignore + IGNORE_APPEND.encode(), 0o644),
        'scripts/nm37-a70-prepare.py': ((package / 'prepare_historical.py').read_bytes(), 0o755),
        'scripts/nm37-a70-source-lock.py': ((package / 'source_lock.py').read_bytes(), 0o755),
        'scripts/nm37-a70-install.py': (Path(__file__).read_bytes(), 0o755),
        'scripts/nm37-a70-build.sh': (wrapper.encode(), 0o755),
        'generators/chipyard/src/main/scala/config/A70SinglePortConfig.scala':
            ((package / 'verify/scala/A70SinglePortConfig.scala').read_bytes(), 0o644),
    }
    if stage == 'p3':
        for name in TESTS:
            payload['generators/fsa/src/test/scala/fsa/' + name] = ((package / 'verify/scala' / name).read_bytes(), 0o644)
    return payload

def state_path(root):
    return root / '.nm37-a70/install-state.json'

def check_head(root, stage):
    require(root.is_dir() and not root.is_symlink(), 'Use a real checkout directory')
    require(Path(git(root, 'rev-parse', '--show-toplevel')).resolve() == root.resolve(), 'Not the checkout root')
    require(git(root, 'rev-parse', 'HEAD') == COMMITS[stage], f'{stage} requires exact historical HEAD {COMMITS[stage]}')

def fresh_check(root, stage):
    check_head(root, stage)
    require(not (root / '.nm37-a70').exists(), 'Existing preparation/partial installation; use verify or a fresh checkout')
    require(not git(root, 'status', '--porcelain', '--untracked-files=all'), 'Historical checkout must be clean before installation')
    for relative in ('target', '.classpath_cache', 'fpga/generated-src', 'sims/verilator/generated-src',
                     'generators/fsa/target', 'generators/easyfloat/target'):
        require(not (root / relative).exists(), f'Fresh checkout required: stale build directory {relative}')
    for repo, pin in PINS.items():
        require((root / repo / '.git').exists(), f'Missing pinned dependency: {repo}')
        require(git(root / repo, 'rev-parse', 'HEAD') == pin, f'Incorrect dependency: {repo}')
        require(not git(root / repo, 'status', '--porcelain', '--untracked-files=all'), f'Dirty dependency: {repo}')
    require((root / 'fpga/patches/p3/fsa-p3.patch').exists() == (stage == 'p3'), 'P2/P3 patch stack mixed')
    require(sha((root / 'fpga/Makefile').read_bytes()) == ORIGINAL_MAKEFILE_SHA, 'Original Makefile changed')
    require(sha((root / '.gitignore').read_bytes()) == ORIGINAL_IGNORE_SHA, 'Original .gitignore changed')
    payload = expected_payload(root, stage)
    for name in payload:
        if name not in {'fpga/Makefile', '.gitignore'}:
            require(not (root / name).exists() and not (root / name).is_symlink(), f'Overlay destination exists: {name}')
    return payload

def verify(root, stage):
    check_head(root, stage)
    manifest = json.loads(state_path(root).read_text())
    require(manifest['schema'] == 1 and manifest['stage'] == stage and manifest['historical_commit'] == COMMITS[stage], 'Installation identity mismatch')
    require(manifest['installer_sha256'] == sha(Path(__file__).read_bytes()), 'Installer version differs from installed payload')
    for name, expected in manifest['files'].items():
        require(fingerprint(root / name) == expected, f'Installed payload changed: {name}')
    require(manifest['files']['scripts/nm37-a70-install.py']['sha256'] == manifest['installer_sha256'], 'Installer payload mismatch')
    require(sha((root / '.nm37-a70/patch-state.json').read_bytes()) == manifest['patch_state_sha256'], 'Patch-state identity changed')
    subprocess.run([sys.executable, str(root / 'scripts/nm37-a70-prepare.py'), '--root', str(root), '--stage', stage], check=True)
    return manifest

def install(root, stage):
    if state_path(root).exists():
        return verify(root, stage)
    payload = fresh_check(root, stage)
    # Source edits are completed before any generator is invoked. No make/sbt
    # call is made by the installer. Failure leaves a visibly partial checkout.
    for name, (data, mode) in payload.items():
        if name.startswith('generators/fsa/src/test/'):
            continue
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(mode)
    subprocess.run([sys.executable, str(root / 'scripts/nm37-a70-prepare.py'), '--root', str(root), '--stage', stage], check=True)
    # prepare insists on clean initial submodules. Add only the named independent
    # test sources AFTER the complete historical product patch stack is frozen.
    for name, (data, mode) in payload.items():
        if name.startswith('generators/fsa/src/test/'):
            target = root / name
            target.write_bytes(data)
            target.chmod(mode)
    manifest = {'schema': 1, 'stage': stage, 'historical_commit': COMMITS[stage],
                'installer_sha256': sha(Path(__file__).read_bytes()),
                'files': {name: fingerprint(root / name) for name in sorted(payload)},
                'patch_state_sha256': sha((root / '.nm37-a70/patch-state.json').read_bytes()),
                'test_scope': list(TESTS) if stage == 'p3' else [],
                'legacy_ControlSignalTest_executed': False}
    with state_path(root).open('x') as f:
        json.dump(manifest, f, indent=2)
        f.write('\n')
    return verify(root, stage)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('operation', choices=['drycheck', 'install', 'verify'])
    p.add_argument('--root', required=True, type=Path)
    p.add_argument('--stage', required=True, choices=COMMITS)
    a = p.parse_args()
    root = a.root.absolute()
    if a.operation == 'drycheck':
        if state_path(root).exists():
            verify(root, a.stage)
        else:
            fresh_check(root, a.stage)
    elif a.operation == 'install':
        install(root, a.stage)
    else:
        verify(root, a.stage)
    print(f'PASS: {a.operation} historical NM37 {a.stage}')

if __name__ == '__main__':
    main()
