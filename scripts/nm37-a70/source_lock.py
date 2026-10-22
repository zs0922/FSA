#!/usr/bin/env python3
"""Freeze actual build inputs, including ignored sources, resources and symlinks.

Source identity is distinct from build outputs. Dependency gitlinks are checked
recursively; only the explicitly documented unused subprojects below are omitted.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

SKIP = {'target', '.git', '.venv', '__pycache__', '.bsp', '.metals',
        'generated-src', 'test_run_dir', '.classpath_cache', '.ivy2', '.sbt',
        'node_modules', '.nm37-a70'}
SUFFIXES = {'.scala', '.sbt', '.sc', '.java', '.sv', '.v', '.vh', '.svh',
            '.c', '.cc', '.cpp', '.h', '.hpp', '.py', '.sh', '.tcl', '.xdc',
            '.mk', '.patch', '.json', '.conf', '.ini', '.yaml', '.yml',
            '.toml', '.lock', '.properties'}
PREFIXES = ('generators/', 'fpga/', 'project/', 'tools/', 'scripts/', 'sims/', 'lib/')
TOP_DEPENDENCIES = ('generators/', 'fpga/fpga-shells', 'tools/cde', 'tools/dsptools',
                    'tools/firrtl2', 'tools/fixedpoint', 'tools/rocket-dsp-utils',
                    'tools/DRAMSim2', 'sims/firesim')
# build.sbt defines these libraries explicitly using the superproject copies.
# Their upstream standalone-build dependency copies are not used by Chipyard.
UNUSED_DEPENDENCIES = {
    'tools/dsptools/fixedpoint': 'build.sbt: dsptools.dependsOn(fixedpoint), tools/fixedpoint',
    'tools/rocket-dsp-utils/tools/rocket-chip': 'build.sbt: rocket_dsp_utils.dependsOn(rocketchip), generators/rocket-chip',
    'tools/rocket-dsp-utils/tools/dsptools': 'build.sbt: rocket_dsp_utils.dependsOn(dsptools), tools/dsptools',
}
# The NM37 assembly consumes these FireSim libraries, not Golden Gate, FireSim
# platforms, or the standalone sim project and its alternative Rocket/Chisel tree.
FIRESIM_SCOPES = ('sim/firesim-lib', 'sim/midas/targetutils')

def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()

def entries(root):
    result = []
    for row in git(root, 'ls-files', '--stage', '-z').split('\0'):
        if not row:
            continue
        meta, name = row.split('\t', 1)
        mode, obj, stage = meta.split()
        assert stage == '0', f'Unmerged input: {name}'
        result.append((mode, obj, name))
    return result

def file_hash(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def fingerprint(p):
    result = {'mode': p.lstat().st_mode & 0o177777}
    if p.is_symlink():
        link = os.readlink(p)
        result.update(sha256=hashlib.sha256(os.fsencode(link)).hexdigest(), symlink=link)
        try:
            target = p.resolve(strict=True)
        except FileNotFoundError:
            result['target_missing'] = True
        except (RuntimeError, OSError) as exc:
            raise AssertionError(f'Unresolvable/cyclic source link: {p}: {exc}') from exc
        else:
            result['target_mode'] = target.stat().st_mode & 0o177777
            if target.is_file():
                result['target_sha256'] = file_hash(target)
            elif target.is_dir():
                result['target_kind'] = 'directory'
            else:
                raise AssertionError(f'Unsupported source symlink target: {p}')
    elif p.is_file():
        result['sha256'] = file_hash(p)
    else:
        raise AssertionError(f'Input must be a file or symlink: {p}')
    return result

def under(path, prefix):
    return path == prefix or path.startswith(prefix + '/')

def capture(root):
    names = set()
    dependencies = {}
    exclusions = {}
    directory_links = set()

    def add_dependency(name, expected):
        if name in UNUSED_DEPENDENCIES:
            exclusions[name] = {'gitlink': expected, 'reason': UNUSED_DEPENDENCIES[name]}
            return
        repo = root / name
        assert (repo / '.git').exists(), f'Missing dependency {name}'
        assert Path(git(repo, 'rev-parse', '--show-toplevel')).resolve() == repo.resolve(), f'Not an independent dependency {name}'
        actual = git(repo, 'rev-parse', 'HEAD')
        assert actual == expected, f'Wrong dependency {name}: expected {expected}, got {actual}'
        dependencies[name] = actual
        for mode, obj, subname in entries(repo):
            full = name + '/' + subname
            if name == 'sims/firesim' and not any(under(subname, p) for p in FIRESIM_SCOPES):
                if mode == '160000':
                    exclusions[full] = {'gitlink': obj, 'reason': 'Outside build.sbt FireSim library scopes'}
                continue
            if mode == '160000':
                add_dependency(full, obj)
            else:
                names.add(full)

    for mode, obj, name in entries(root):
        if mode == '160000':
            if name.startswith(TOP_DEPENDENCIES):
                add_dependency(name, obj)
        elif name.startswith(PREFIXES) or '/' not in name:
            names.add(name)

    # sbt also consumes newly added root-level *.sbt files; these need not
    # appear in the Git index or in a named source directory.
    for p in root.iterdir():
        if p.suffix in {'.sbt', '.scala', '.sc', '.java', '.mk'} and (p.is_file() or p.is_symlink()):
            names.add(p.name)

    def scan(scope, all_files=False, ancestors=()):
        rel = str(scope.relative_to(root))
        if any(under(rel, p) for p in UNUSED_DEPENDENCIES):
            return
        if scope.is_symlink():
            names.add(rel)
            directory_links.add(rel)
        if not scope.exists():
            return
        assert scope.is_dir(), f'Expected source directory: {scope}'
        real = scope.resolve(strict=True)
        if real in ancestors:
            raise AssertionError(f'Cyclic source directory link: {scope} -> {real}')
        ancestors = (*ancestors, real)
        # Resource contents are copied by sbt without an extension whitelist.
        all_files = all_files or scope.name in {'resources', 'lib'}
        for child in sorted(scope.iterdir()):
            if child.name in SKIP:
                continue
            if child.is_dir():
                scan(child, all_files, ancestors)
            elif all_files or child.suffix in SUFFIXES or child.name in {'Makefile', 'makefile'} or child.is_symlink():
                names.add(str(child.relative_to(root)))

    roots = ('generators', 'fpga/src', 'fpga/fpga-shells', 'project', 'scripts',
             'tools/stage', 'tools/tapeout', 'tools/cde', 'tools/dsptools',
             'tools/firrtl2', 'tools/fixedpoint', 'tools/rocket-dsp-utils',
             'tools/DRAMSim2', 'sims/verilator', 'lib',
             'sims/firesim/sim/firesim-lib', 'sims/firesim/sim/midas/targetutils')
    for name in roots:
        scan(root / name)
    # A tracked directory symlink might live outside the dynamically scanned
    # source roots. Include its target tree as well as its own link identity.
    for name in sorted(names.copy()):
        p = root / name
        if p.is_symlink() and p.is_dir() and name not in directory_links:
            scan(p, all_files=True)
    files = {}
    for name in sorted(names):
        p = root / name
        files[name] = fingerprint(p) if p.exists() or p.is_symlink() else {'deleted': True}
    return {'schema': 2, 'root_commit': git(root, 'rev-parse', 'HEAD'),
            'dependencies': dependencies, 'excluded_dependencies': exclusions,
            'input_policy': {'generated_directory_exclusions': sorted(SKIP),
                             'firesim_scopes': list(FIRESIM_SCOPES)},
            'files': files}

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('operation', choices=['capture', 'verify'])
    ap.add_argument('--source', type=Path, required=True)
    ap.add_argument('--lock', type=Path, required=True)
    args = ap.parse_args()
    actual = capture(args.source.resolve())
    if args.operation == 'capture':
        with args.lock.open('x') as f:
            json.dump(actual, f, indent=2)
            f.write('\n')
    else:
        expected = json.loads(args.lock.read_text())
        # Normalize tuples used by the policy to their JSON representation.
        actual = json.loads(json.dumps(actual))
        changed = sorted(k for k in set(expected['files']) | set(actual['files'])
                         if expected['files'].get(k) != actual['files'].get(k))
        assert actual == expected, f'Source identity changed: {changed[:30]}'
    print(f'PASS: {args.operation}: {len(actual["files"])} actual input files')

if __name__ == '__main__':
    main()
