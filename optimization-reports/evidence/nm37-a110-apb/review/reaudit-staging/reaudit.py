#!/usr/bin/env python3
"""Re-audit an immutable routed NM37 A110 build with separately identified tools.

This entry never synthesizes, places, routes, edits constraints, or writes a DCP.
The original check.py gate remains unchanged. A revised acceptance record does
not turn the original build's failed acceptance into an original pass.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import types

STAGES = ('units', 'formal', 'hbm-startup', 'sim4', 'sim8', 'sim16', 'elaborate')
LOCK_PATH = 'fpga/baselines/nm37-a110/source-lock.json'
BOARD = 'A110/fpga/chipyard.fpga.nm37.NM37FPGATestHarness.EmptyNM37Config'
REPORT_DIRS = ('report', 'audit', 'hbm_audit', 'reset_replication_audit')
AUDITOR_FILES = ('reaudit.py', 'reaudit.tcl', 'physical_audit.py', 'hbm_audit.tcl',
                 'check_reset_evidence.tcl', 'cdc-baseline.json', 'run.sh')
# An explicit list, not an entire directory/prefix waiver. Production inputs
# additionally pass the original source-lock checker, including all submodules.
ALLOWED_REUSE_CHANGES = frozenset('scripts/nm37-a110/' + p for p in (
    'hbm_audit.tcl', 'physical_audit.py', 'reaudit.py', 'reaudit.tcl', 'run.sh',
    'test_reaudit.py', 'test_physical_audit_context.py', 'test_hbm_audit.tcl'))
NONEXECUTABLE_DOCUMENTS = frozenset(('README.md', 'optimization-reports/NM37_A110_APB.md',
                                    'optimization-reports/NM37_A110_RESET_RESULTS.md'))
NONEXECUTABLE_EVIDENCE_PREFIX = 'optimization-reports/evidence/nm37-a110-apb/'
FORBIDDEN_TCL = re.compile(r'(?m)(?:^|[;\n{}])\s*(?:synth_design|opt_design|place_design|'
    r'route_design|phys_opt_design|write_checkpoint|read_xdc|read_sdc|set_property|'
    r'reset_timing|set_false_path|set_multicycle_path|set_clock_groups|'
    r'create_clock|create_generated_clock)\b')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def relative(name):
    p = PurePosixPath(name)
    require(isinstance(name, str) and name and not p.is_absolute() and
            all(part not in ('', '.', '..') for part in name.split('/')) and '\\' not in name,
            'Unsafe relative path: ' + repr(name))
    return p


def no_symlinks(path):
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        require(not item.is_symlink(), 'Symlink is not allowed: ' + str(item))
    return path


def regular(root, name):
    p = no_symlinks(Path(root).joinpath(*relative(name).parts))
    require(p.is_file(), 'Missing regular file: ' + str(p))
    return p


def json_object(path):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'Duplicate JSON key: ' + key)
            result[key] = value
        return result
    return json.loads(Path(path).read_text(), object_pairs_hook=pairs)


def write_new(path, value):
    path = no_symlinks(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as f:
        json.dump(value, f, indent=2, sort_keys=True)
        f.write('\n')


def git(source, *args):
    return subprocess.check_output(['git', '-C', str(source), *args])


def original_object(source, commit, path):
    relative(path)
    require(re.fullmatch(r'[0-9a-f]{40}', commit), 'Original commit must be full SHA-1')
    return git(source, 'show', f'{commit}:{path}')


def verify_files(root, hashes, label):
    require(isinstance(hashes, dict) and hashes, label + ': empty file map')
    for path, wanted in hashes.items():
        require(re.fullmatch(r'[0-9a-f]{64}', wanted or ''), label + ': malformed SHA')
        require(digest(regular(root, path)) == wanted, label + ': changed file ' + path)


def original_reports(build):
    result = {}
    obj = Path(build) / BOARD / 'obj'
    for directory in REPORT_DIRS:
        parent = no_symlinks(obj / directory)
        require(parent.is_dir(), 'Missing original report directory: ' + str(parent))
        for path in sorted(parent.rglob('*')):
            no_symlinks(path)
            if path.is_file():
                result[path.relative_to(build).as_posix()] = digest(path)
    require(result, 'Original report set is empty')
    for name in ('A110/implementation.log', 'physical.failed.json', 'physical.passed.json',
                 'cdc-review.json', 'cdc-review.required.json'):
        path = Path(build) / name
        if path.exists():
            result[name] = digest(regular(build, name))
    require('A110/implementation.log' in result, 'Original implementation log missing')
    return result


def validate_current_changes(source, original_commit):
    # The locked shell overlay intentionally makes its worktree dirty. Ignore
    # submodule worktree dirt here only; original sources() independently checks
    # every locked overlay byte and submodule status. Gitlink changes still show.
    changed = set(git(source, 'diff', '--ignore-submodules=dirty', '--name-only', '-z', original_commit, '--').decode().split('\0')) - {''}
    # These exact final documents/evidence are never execution inputs. This
    # permits archiving/committing the result without weakening product locks.
    allowed = {name for name in changed if name in ALLOWED_REUSE_CHANGES or
               name in NONEXECUTABLE_DOCUMENTS or name.startswith(NONEXECUTABLE_EVIDENCE_PREFIX)}
    require(changed <= allowed,
            'DCP reuse forbids non-auditor changes: ' + repr(sorted(changed - allowed)))
    for name in allowed:
        regular(source, name)
    untracked = git(source, 'ls-files', '--others', '-z', '--', 'scripts/nm37-a110').decode().split('\0')
    for name in untracked:
        if not name or '__pycache__' in PurePosixPath(name).parts:
            continue
        require(name in ALLOWED_REUSE_CHANGES, 'Untracked auditor input not permitted: ' + name)
        regular(source, name)
    return sorted(changed)


def frozen_source_check(source, build, checker_bytes):
    # Execute the exact original checker as trusted build code. Only sources()
    # is called: original driver bytes are checked separately against Git and
    # build.json, never substituted into or weakening the original gate().
    module = types.ModuleType('original_a110_source_gate')
    module.__file__ = str(source / 'scripts/nm37-a110/check.py')
    exec(compile(checker_bytes, module.__file__, 'exec'), module.__dict__)
    module.OUT = build
    module.sources()


def validate_build(source, build, check_sources=True):
    source, build = no_symlinks(source), no_symlinks(build)
    identity = json_object(regular(build, 'build.json'))
    commit = identity['root_commit']
    require(git(source, 'rev-parse', f'{commit}^{{commit}}').decode().strip() == commit,
            'Original implementation commit does not resolve exactly')
    changes = validate_current_changes(source, commit)
    drivers = {}
    require(identity.get('driver_hashes'), 'Original driver manifest missing')
    for name, expected in sorted(identity['driver_hashes'].items()):
        payload = original_object(source, commit, name)
        require(sha_bytes(payload) == expected, 'Original Git driver/build mismatch: ' + name)
        drivers[name] = payload
    lock_bytes = original_object(source, commit, LOCK_PATH)
    require(sha_bytes(lock_bytes) == identity['lock_sha256'] == digest(regular(source, LOCK_PATH)),
            'Original/current source-lock identity mismatch')
    lock = json.loads(lock_bytes)
    require(lock['configuration'] == identity['configuration'], 'Configuration identity mismatch')
    checker = drivers['scripts/nm37-a110/check.py']
    if check_sources:
        frozen_source_check(source, build, checker)
    require(digest(regular(build, 'generator.jar')) == identity['generator_sha256'], 'Generator changed')
    verify_files(build / 'shell', lock['shell_hashes'], 'Frozen shell')
    require({p.relative_to(build / 'shell').as_posix() for p in (build / 'shell').rglob('*') if p.is_file()}
            == set(lock['shell_hashes']), 'Frozen shell file set changed')
    build_sha = digest(build / 'build.json')
    seals = {}
    for stage in STAGES:
        name = stage + '.passed.json'
        path = regular(build, name)
        seal = json_object(path)
        require(seal['build_sha256'] == build_sha, 'Stage/build mismatch: ' + stage)
        verify_files(build, seal['files'], stage)
        seals[name] = digest(path)
    checkpoint_name = BOARD + '/obj/post_route.dcp'
    checkpoint = regular(build, checkpoint_name)
    checkpoint_sha = digest(checkpoint)
    require((regular(build, BOARD + '/obj/audit/postroute_checkpoint.sha256')).read_text().strip()
            == checkpoint_sha, 'Original routed checkpoint binding differs')
    reports = original_reports(build)
    environment = {name: digest(regular(build, name)) for name in ('logs/tools.txt', 'logs/environment.json')}
    ips = {}
    for path in sorted((build / BOARD).rglob('*.xci')):
        ips[path.relative_to(build).as_posix()] = digest(regular(build, path.relative_to(build).as_posix()))
    for name in ('testxilinx_hbm.xci', 'harnessSysPLL.xci'):
        require(any(PurePosixPath(path).name == name for path in ips), 'Missing generated IP configuration: ' + name)
    return dict(source=str(source), build=str(build), execution_commit=commit,
                build_sha256=build_sha, source_lock_sha256=identity['lock_sha256'],
                generator_sha256=identity['generator_sha256'], stage_seals=seals,
                driver_hashes=identity['driver_hashes'], checkpoint_path=checkpoint_name,
                checkpoint_sha256=checkpoint_sha, original_reports=reports, original_ip_files=ips,
                original_environment_files=environment,
                allowed_current_changes=changes), drivers, lock_bytes


def auditor_files(auditor):
    hashes = {}
    for name in AUDITOR_FILES:
        path = regular(auditor, name)
        if path.suffix == '.tcl':
            require(not FORBIDDEN_TCL.search(path.read_text()), 'Mutating Tcl command in auditor: ' + name)
        hashes[name] = digest(path)
    return hashes


def assert_unchanged(manifest, check_sources=True):
    original = manifest['implementation']
    current, _, _ = validate_build(Path(original['source']), Path(original['build']), check_sources)
    for key in ('execution_commit', 'build_sha256', 'source_lock_sha256', 'generator_sha256',
                'stage_seals', 'driver_hashes', 'checkpoint_path', 'checkpoint_sha256', 'original_reports',
                'original_ip_files', 'original_environment_files'):
        require(current[key] == original[key], 'Original build changed since re-audit: ' + key)
    return current


def prepare(source, build, out, auditor):
    source, build, out, auditor = map(no_symlinks, (source, build, out, auditor))
    require(not out.exists(), 'Re-audit output already exists: ' + str(out))
    require(not out.is_relative_to(build) and not build.is_relative_to(out), 'Re-audit output overlaps original build')
    require(not out.is_relative_to(source) and not source.is_relative_to(out), 'Re-audit output overlaps source checkout')
    original, drivers, lock_bytes = validate_build(source, build)
    tools = auditor_files(auditor)
    require(tools['reaudit.py'] == digest(Path(__file__)), 'Requested auditor entry differs from executing entry')
    # Shared parser/baseline must remain byte-identical to the frozen drivers.
    for name in ('check_reset_evidence.tcl', 'cdc-baseline.json'):
        require(tools[name] == original['driver_hashes']['scripts/nm37-a110/' + name],
                'Re-audit may not modify frozen helper: ' + name)
    out.mkdir(parents=True, exist_ok=False)
    write_new(out / 'INCOMPLETE.json', {'status': 'preparing'})
    for name, payload in drivers.items():
        path = out / 'implementation-drivers' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as f:
            f.write(payload)
    lock_target = out / 'implementation-drivers' / LOCK_PATH
    lock_target.parent.mkdir(parents=True, exist_ok=True)
    lock_target.write_bytes(lock_bytes)
    (out / 'auditor').mkdir()
    for name, sha in tools.items():
        shutil.copyfile(regular(auditor, name), out / 'auditor' / name)
        require(digest(out / 'auditor' / name) == sha, 'Auditor changed during copy: ' + name)
    reset = out / 'obj/reset_replication_audit'
    reset.mkdir(parents=True)
    for name in ('before.dict', 'after_replication.dict', 'replication_equivalence.txt'):
        shutil.copyfile(regular(build, BOARD + '/obj/reset_replication_audit/' + name), reset / name)
    checkout_commit = git(source, 'rev-parse', 'HEAD').decode().strip()
    tracked = set(git(source, 'ls-tree', '-r', '--name-only', checkout_commit, '--', 'scripts/nm37-a110').decode().splitlines())
    auditor_committed = all('scripts/nm37-a110/' + name in tracked and
        sha_bytes(original_object(source, checkout_commit, 'scripts/nm37-a110/' + name)) == sha
        for name, sha in tools.items())
    manifest = dict(schema=1, operation='read_only_routed_reaudit', hardware_reimplemented=False,
                    implementation=original, auditor=dict(files=tools,
                    source_checkout_commit=checkout_commit,
                    auditor_commit=checkout_commit if auditor_committed else None,
                    identity_kind='committed_auditor' if auditor_committed else 'uncommitted_file_snapshot',
                    origin=str(auditor)), output=str(out))
    write_new(out / 'inputs.json', manifest)
    assert_unchanged(manifest)
    return manifest


def load_manifest(out):
    out = no_symlinks(out)
    manifest = json_object(regular(out, 'inputs.json'))
    require(manifest['schema'] == 1 and manifest['operation'] == 'read_only_routed_reaudit'
            and manifest['hardware_reimplemented'] is False and manifest['output'] == str(out),
            'Invalid re-audit manifest identity')
    verify_files(out / 'auditor', manifest['auditor']['files'], 'Frozen revised auditor')
    require(digest(Path(__file__)) == manifest['auditor']['files']['reaudit.py'],
            'Executing entry differs from the frozen revised auditor; invoke its frozen reaudit.py')
    verify_files(out / 'implementation-drivers', manifest['implementation']['driver_hashes'], 'Original driver snapshot')
    require(digest(regular(out / 'implementation-drivers', LOCK_PATH))
            == manifest['implementation']['source_lock_sha256'], 'Snapshot source-lock changed')
    assert_unchanged(manifest)
    return manifest


def report_files(out, complete=True):
    result = {}
    for directory in REPORT_DIRS:
        root = no_symlinks(out / 'obj' / directory)
        if not root.is_dir() and not complete:
            continue
        require(root.is_dir(), 'Missing revised report directory: ' + str(root))
        for path in sorted(root.rglob('*')):
            no_symlinks(path)
            if path.is_file():
                result[path.relative_to(out).as_posix()] = digest(path)
    return result


def load_physical(out):
    path = out / 'auditor/physical_audit.py'
    module = types.ModuleType('revised_physical_audit')
    module.__file__ = str(path)
    sys.modules[module.__name__] = module
    exec(compile(path.read_bytes(), str(path), 'exec'), module.__dict__)
    return module


def validate_execution(out, manifest):
    execution = json_object(regular(out, 'vivado-result.json'))
    require(execution['exit_code'] == 0, 'Re-audit Vivado did not succeed')
    require(execution['inputs_sha256'] == digest(out / 'inputs.json'), 'Vivado execution/input mismatch')
    require(execution['log_sha256'] == digest(regular(out, 'vivado.log')), 'Vivado log changed')
    require(set(execution['tool_logs']) == {'vivado-native.log', 'vivado.jou'}, 'Incomplete native Vivado logs')
    verify_files(out, execution['tool_logs'], 'Native Vivado logs')
    verify_vivado_version(out / 'vivado-native.log', manifest)
    require(execution['report_files'] == report_files(out), 'Re-audit reports changed after Vivado execution')
    original = manifest['implementation']
    for name in ('before.dict', 'after_replication.dict', 'replication_equivalence.txt'):
        suffix = 'reset_replication_audit/' + name
        require(digest(regular(out, 'obj/' + suffix)) == original['original_reports'][BOARD + '/obj/' + suffix],
                'Copied original reset evidence changed: ' + name)


def verify_vivado_version(native_log, manifest):
    build = Path(manifest['implementation']['build'])
    environment = json_object(regular(build, 'logs/environment.json'))
    version = environment['tools']['vivado']['version']
    expected = [line.strip() for line in version.splitlines()
                if line.strip().startswith(('Vivado ', 'SW Build ', 'IP Build '))]
    require(len(expected) == 3, 'Original Vivado version/build identity is incomplete')
    actual = Path(native_log).read_text(errors='replace')
    require(all(line in actual for line in expected), 'Re-audit/export Vivado version differs from original build')


def inspect_physical(out, manifest, result):
    physical = load_physical(out)
    original = manifest['implementation']
    build = Path(original['build'])
    context = physical.AuditContext(build_root=build,
        checkpoint=build / original['checkpoint_path'], ip_root=build / BOARD, review_root=out)
    physical.inspect(out / 'obj', result, context=context)


def accept(out, attempt):
    out = no_symlinks(out)
    require(re.fullmatch(r'[a-zA-Z0-9_-]+', attempt), 'Unsafe acceptance attempt name')
    target = out / 'acceptance' / attempt
    require(not target.exists(), 'Acceptance attempt already exists')
    manifest = load_manifest(out)
    validate_execution(out, manifest)
    evidence = report_files(out)
    target.mkdir(parents=True, exist_ok=False)
    result = dict(schema=1, status='failed', operation='read_only_routed_reaudit',
                  hardware_reimplemented=False, board_signoff=False,
                  inputs_sha256=digest(out / 'inputs.json'),
                  execution_sha256=digest(out / 'vivado-result.json'),
                  implementation=manifest['implementation'], auditor=manifest['auditor'],
                  evidence=evidence, constrained_sta_passed=False)
    try:
        inspect_physical(out, manifest, result)
        assert_unchanged(manifest)
        require(report_files(out) == evidence, 'Reports changed during acceptance')
        result['review_files'] = {name: digest(regular(out, name)) for name in
            ('cdc-review.json', 'cdc-review.required.json') if (out / name).exists()}
        result['status'] = 'passed'
        write_new(target / 'reaudit.passed.json', result)
        return target / 'reaudit.passed.json'
    except Exception as error:
        result.update(status='failed', constrained_sta_passed=False, error=str(error))
        write_new(target / 'reaudit.failed.json', result)
        raise


def verify_pass(out, acceptance):
    out, acceptance = no_symlinks(out), no_symlinks(acceptance)
    require(acceptance.is_relative_to(out / 'acceptance') and acceptance.name == 'reaudit.passed.json',
            'Pass record is outside this re-audit acceptance tree')
    manifest = load_manifest(out)
    validate_execution(out, manifest)
    result = json_object(acceptance)
    require(result.get('status') == 'passed' and result.get('constrained_sta_passed') is True
            and result.get('hbm_startup_physical_passed') is True
            and result.get('hardware_reimplemented') is False and result.get('board_signoff') is False,
            'Re-audit result is not an accepted constrained STA result')
    require(result['inputs_sha256'] == digest(out / 'inputs.json') and
            result['execution_sha256'] == digest(out / 'vivado-result.json') and
            result['implementation'] == manifest['implementation'] and result['auditor'] == manifest['auditor'],
            'Pass record identity differs from re-audit inputs')
    require(result['evidence'] == report_files(out), 'Pass report set/hash changed')
    verify_files(out, result['evidence'], 'Pass evidence')
    actual_review_files = {name: digest(regular(out, name)) for name in
        ('cdc-review.json', 'cdc-review.required.json') if (out / name).exists()}
    require(result.get('review_files') == actual_review_files, 'Pass CDC review file set/hash changed')
    for name, sha in result['review_files'].items():
        require(name in ('cdc-review.json', 'cdc-review.required.json'), 'Unknown review file')
        require(digest(regular(out, name)) == sha, 'Pass CDC review changed')
    # Recompute acceptance; a hand-edited passed=True field is not a waiver.
    checked = {}
    inspect_physical(out, manifest, checked)
    require(checked.get('constrained_sta_passed') is True and checked.get('hbm_startup_physical_passed') is True,
            'Current physical evidence does not pass')
    for key, value in checked.items():
        require(key in result and result[key] == value, 'Pass physical value changed: ' + key)
    if checked.get('cdc', {}).get('new_findings'):
        require(set(result['review_files']) == {'cdc-review.json', 'cdc-review.required.json'}, 'CDC review binding incomplete')
    require(report_files(out) == result['evidence'], 'Physical recheck changed report evidence')
    for name, sha in result.get('review_files', {}).items():
        require(digest(regular(out, name)) == sha, 'Physical recheck changed CDC review')
    return manifest, result


def run_reaudit(args):
    out = no_symlinks(args.out)
    manifest = prepare(Path(args.source), Path(args.build), out, Path(args.auditor))
    original = manifest['implementation']
    cmd = [args.vivado, '-mode', 'batch', '-log', str(out / 'vivado-native.log'),
           '-journal', str(out / 'vivado.jou'), '-source', str(out / 'auditor/reaudit.tcl'),
           '-tclargs', str(Path(original['build']) / original['checkpoint_path']), str(out / 'obj'),
           str(out / 'implementation-drivers'), str(Path(original['build']) / BOARD / 'obj'),
           str(out / 'auditor')]
    with (out / 'vivado.log').open('xb') as log:
        completed = subprocess.run(cmd, cwd=out, stdout=log, stderr=subprocess.STDOUT)
    write_new(out / 'vivado-result.json', dict(command=cmd, exit_code=completed.returncode,
        log_sha256=digest(out / 'vivado.log'), inputs_sha256=digest(out / 'inputs.json'),
        tool_logs={name: digest(out / name) for name in ('vivado-native.log', 'vivado.jou') if (out / name).is_file()},
        report_files=report_files(out, complete=completed.returncode == 0)))
    assert_unchanged(manifest)
    require(completed.returncode == 0, 'Re-audit failed; original build is unchanged; see ' + str(out / 'vivado.log'))
    (out / 'INCOMPLETE.json').unlink()
    return accept(out, 'initial')


def export_bitstream(out, acceptance, destination, vivado):
    out, destination = no_symlinks(out), no_symlinks(destination)
    manifest, result = verify_pass(out, acceptance)
    build = Path(manifest['implementation']['build'])
    require(not destination.exists(), 'Bitstream output already exists')
    require(not destination.is_relative_to(build) and not build.is_relative_to(destination),
            'Bitstream output overlaps original build')
    require(not destination.is_relative_to(out) and not out.is_relative_to(destination),
            'Bitstream output overlaps immutable re-audit evidence')
    source = Path(manifest['implementation']['source'])
    require(not destination.is_relative_to(source) and not source.is_relative_to(destination),
            'Bitstream output overlaps source checkout')
    destination.mkdir(parents=True, exist_ok=False)
    write_new(destination / 'INCOMPLETE.json', {'operation': 'bitstream_from_reaudit'})
    script = regular(out / 'implementation-drivers', 'fpga/scripts/m1_a110_bitstream.tcl')
    checkpoint = build / manifest['implementation']['checkpoint_path']
    cmd = [vivado, '-mode', 'batch', '-log', str(destination / 'vivado-native.log'),
           '-journal', str(destination / 'vivado.jou'), '-source', str(script),
           '-tclargs', str(checkpoint), str(destination / 'export')]
    with (destination / 'vivado.log').open('xb') as log:
        process = subprocess.run(cmd, cwd=destination, stdout=log, stderr=subprocess.STDOUT)
    assert_unchanged(manifest)
    verify_pass(out, acceptance)
    record = dict(schema=1, operation='bitstream_from_reaudit', exit_code=process.returncode,
        command=cmd, acceptance_path=str(acceptance), acceptance_sha256=digest(acceptance),
        inputs_sha256=digest(out / 'inputs.json'), checkpoint_sha256=manifest['implementation']['checkpoint_sha256'],
        build_sha256=manifest['implementation']['build_sha256'], board_signoff=False,
        log_sha256=digest(destination / 'vivado.log'), tool_logs={name: digest(destination / name)
            for name in ('vivado-native.log', 'vivado.jou') if (destination / name).is_file()})
    bit = destination / 'export/NM37_A110.bit'
    if process.returncode == 0:
        require(set(record['tool_logs']) == {'vivado-native.log', 'vivado.jou'}, 'Missing native bitstream logs')
        verify_vivado_version(destination / 'vivado-native.log', manifest)
        require(bit.is_file() and bit.stat().st_size > 0, 'Vivado succeeded without a nonempty bitstream')
        record['bitstream'] = dict(path=str(bit), sha256=digest(bit), bytes=bit.stat().st_size)
    write_new(destination / ('export.passed.json' if process.returncode == 0 else 'export.failed.json'), record)
    require(process.returncode == 0, 'Bitstream export failed')
    (destination / 'INCOMPLETE.json').unlink()
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run')
    for name in ('source', 'build', 'out'):
        run.add_argument('--' + name, required=True, type=Path)
    run.add_argument('--auditor', type=Path, default=Path(__file__).resolve().parent)
    run.add_argument('--vivado', default='vivado')
    accept_parser = sub.add_parser('accept')
    accept_parser.add_argument('--out', type=Path, required=True)
    accept_parser.add_argument('--attempt', required=True)
    verify = sub.add_parser('verify')
    export = sub.add_parser('bitstream-from-reaudit')
    for p in (verify, export):
        p.add_argument('--out', type=Path, required=True)
        p.add_argument('--acceptance', type=Path, required=True)
    export.add_argument('--destination', type=Path, required=True)
    export.add_argument('--vivado', default='vivado')
    args = parser.parse_args()
    if args.command == 'run':
        print(run_reaudit(args))
    elif args.command == 'accept':
        print(accept(args.out, args.attempt))
    elif args.command == 'verify':
        verify_pass(args.out, args.acceptance)
        print('PASS: revised acceptance and original immutable build bindings')
    else:
        print(json.dumps(export_bitstream(args.out, args.acceptance, args.destination, args.vivado), indent=2))


if __name__ == '__main__':
    main()
