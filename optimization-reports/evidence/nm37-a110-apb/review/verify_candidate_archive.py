#!/usr/bin/env python3
"""Read-only verification of selected evidence, optionally against the Git index.

The packaged file bytes are verified offline. Omitted large local artifacts are
not claimed to be present elsewhere, and this command does not re-run design
validation or turn a partial evidence archive into a complete build backup.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

STAGES = ('units', 'formal', 'hbm-startup', 'sim4', 'sim8', 'sim16', 'elaborate')
SHA = re.compile('[0-9a-f]{64}\\Z')
CHUNK = 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique_json_bytes(data, label):
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, f'Duplicate JSON key in {label}: {key}')
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs)


def relative(value):
    require(isinstance(value, str) and value and '\\' not in value and '\x00' not in value,
            f'Invalid relative path: {value!r}')
    path = PurePosixPath(value)
    require(not path.is_absolute() and '..' not in path.parts and str(path) == value and value != '.',
            f'Unsafe relative path: {value!r}')
    return path


def safe_file(root, value):
    path = root.joinpath(*relative(value).parts)
    for ancestor in (path, *path.parents):
        require(not ancestor.is_symlink(), f'Symlink in archive path: {ancestor}')
    require(path.is_file() and path.resolve().is_relative_to(root), f'Missing/escaped file: {path}')
    return path


def sha_file(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(CHUNK), b''):
            result.update(block)
    return result.hexdigest()


def check_index(root, repository, files):
    require(repository is not None, '--require-index needs --repository')
    repository = repository.resolve(strict=True)
    top = subprocess.run(['git', '-C', str(repository), 'rev-parse', '--show-toplevel'],
                         check=True, capture_output=True, text=True).stdout.strip()
    require(Path(top).resolve() == repository, 'Repository must be the Git checkout root')
    require(root.is_relative_to(repository), 'Archive is outside the requested repository')
    prefix = root.relative_to(repository)
    output = subprocess.run(['git', '-C', str(repository), 'ls-files', '--stage', '-z', '--', str(prefix)],
                            check=True, capture_output=True).stdout
    indexed = {}
    for item in output.split(b'\0'):
        if not item:
            continue
        metadata, name = item.split(b'\t', 1)
        mode, digest, stage = metadata.decode('ascii').split()
        name = os.fsdecode(name)
        require(stage == '0' and name not in indexed, f'Unmerged/duplicate index entry: {name}')
        indexed[name] = (mode, digest)
    algorithm = subprocess.run(['git', '-C', str(repository), 'rev-parse', '--show-object-format'],
                               check=True, capture_output=True, text=True).stdout.strip()
    require(algorithm in ('sha1', 'sha256'), f'Unsupported Git object format: {algorithm}')
    expected = {str(prefix / name) for name in files}
    require(set(indexed) == expected,
            f'Archive/index file set differs: missing={sorted(expected - set(indexed))[:20]}, extra={sorted(set(indexed) - expected)[:20]}')
    for name in files:
        path = safe_file(root, name)
        mode, expected_hash = indexed[str(prefix / name)]
        require(mode in ('100644', '100755'), f'Nonregular archived index object: {name}')
        digest = hashlib.new(algorithm)
        digest.update(b'blob ' + str(path.stat().st_size).encode('ascii') + b'\0')
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(CHUNK), b''):
                digest.update(block)
        require(digest.hexdigest() == expected_hash, f'Index bytes differ from verified archive: {name}')
    return dict(checked=True, repository=str(repository), indexed_file_count=len(expected),
                git_object_format=algorithm, all_verified_bytes_in_index=True)


def verify(root, repository=None, require_index=False):
    require(root.is_absolute(), 'Archive path must be absolute')
    for ancestor in (root, *root.parents):
        require(not ancestor.is_symlink(), f'Symlink in archive root: {ancestor}')
    root = root.resolve(strict=True)
    require(not (root / 'ARCHIVE_INCOMPLETE.json').exists(), 'Archive is marked incomplete')
    inventory_file = safe_file(root, 'inventory.json')
    inventory_bytes = inventory_file.read_bytes()
    inventory_hash = hashlib.sha256(inventory_bytes).hexdigest()
    require(safe_file(root, 'inventory.sha256').read_text() == inventory_hash + '  inventory.json\n',
            'Inventory checksum differs')
    inventory = unique_json_bytes(inventory_bytes, 'inventory.json')
    require(inventory.get('archive_status') == 'complete_selected_evidence' and
            inventory.get('full_build_backup') is False, 'Wrong archive status/scope')
    entries, sources, observed = {}, {}, {}
    for item in inventory['files']:
        name = str(relative(item['archived_relative_path']))
        source_key = (item['source_root'], str(relative(item['source_relative_path'])))
        require(name not in entries and source_key not in sources, f'Duplicate archive entry: {name}')
        require(item['source_root'] in ('build', 'source', 'review', 'reaudit', 'reaudit_bitstream'), 'Unknown source root')
        require(SHA.fullmatch(item['raw_sha256']) is not None and SHA.fullmatch(item['archived_sha256']) is not None,
                f'Invalid artifact digest: {name}')
        path = safe_file(root, name)
        require(path.stat().st_size == item['archived_bytes'] and sha_file(path) == item['archived_sha256'],
                f'Archived bytes/hash differ: {name}')
        require(item['encoding'] in ('identity', 'gzip'), f'Unknown encoding: {name}')
        digest = hashlib.sha256()
        count = 0
        opener = gzip.open if item['encoding'] == 'gzip' else open
        with opener(path, 'rb') as stream:
            for block in iter(lambda: stream.read(CHUNK), b''):
                count += len(block)
                digest.update(block)
        require(count == item['raw_bytes'] and digest.hexdigest() == item['raw_sha256'],
                f'Decompressed source bytes/hash differ: {name}')
        entries[name], sources[source_key], observed[source_key] = item, name, item['raw_sha256']
    def read_json(root_name, path):
        key = (root_name, path)
        require(key in sources, f'Required archived record missing: {key}')
        item = entries[sources[key]]
        file = safe_file(root, sources[key])
        data = gzip.decompress(file.read_bytes()) if item['encoding'] == 'gzip' else file.read_bytes()
        return unique_json_bytes(data, str(key))
    build = read_json('build', 'build.json')
    build_hash = observed[('build', 'build.json')]
    require(build_hash == inventory['summary']['build_sha256'], 'Archive/build identity differs')
    lock_name = 'fpga/baselines/nm37-a110/source-lock.json'
    lock = read_json('source', lock_name)
    require(observed[('source', lock_name)] == build['lock_sha256'], 'Source-lock/build hash differs')
    driver_snapshot = inventory.get('driver_snapshot', dict(root='source', prefix=''))
    require(driver_snapshot in (dict(root='source', prefix=''), dict(root='reaudit', prefix='implementation-drivers/')),
            'Unrecognized original driver snapshot location')
    require(bool(inventory.get('revised_audit')) == (driver_snapshot['root'] == 'reaudit'),
            'Driver snapshot and revised audit identity disagree')
    for path, digest in build['driver_hashes'].items():
        require(observed.get((driver_snapshot['root'], driver_snapshot['prefix'] + path)) == digest,
                f'Frozen driver bytes absent/changed: {path}')
    for path, digest in lock['shell_hashes'].items():
        require(observed.get(('build', 'shell/' + path)) == digest, f'Shell snapshot bytes absent/changed: {path}')
    for (root_name, path), digest in observed.items():
        if root_name == 'source' and path in lock['source_hashes']:
            require(digest == lock['source_hashes'][path], f'Source snapshot differs from lock: {path}')
    omitted = {}
    for item in inventory['unpackaged_stage_files']:
        path = str(relative(item['source_relative_path']))
        require(item['source_root'] == 'build' and item['archived'] is False and path not in omitted,
                f'Invalid omitted-file record: {path}')
        omitted[path] = item
    expected_omitted = {}
    for stage in STAGES:
        path = f'{stage}.passed.json'
        seal = read_json('build', path)
        require(seal['build_sha256'] == build_hash and seal['files'], f'Wrong/empty stage binding: {stage}')
        require(observed[('build', path)] == inventory['stage_verification'][stage]['raw_sha256'],
                f'Stage inventory digest differs: {stage}')
        for path, digest in seal['files'].items():
            relative(path)
            if ('build', path) in observed:
                require(observed[('build', path)] == digest, f'Archived file differs from {stage} seal: {path}')
            else:
                require(path in omitted and omitted[path]['raw_sha256'] == digest,
                        f'Sealed file neither archived nor explicitly omitted: {path}')
                expected_omitted.setdefault(path, set()).add(stage)
    require(set(omitted) == set(expected_omitted), 'Unexpected/unused omitted-file record')
    for path, stages in expected_omitted.items():
        require(set(omitted[path]['sealed_by']) == stages, f'Omitted file stage list differs: {path}')
    expected_cases = {(size, seq) for size in (4, 8, 16) for seq in (size, size * 2)}
    require({(item['array_size'], item['seq']) for item in inventory['six_simulation_cases']} == expected_cases,
            'Six case inventory is incomplete')
    require(len(inventory['six_simulation_cases']) == 6, 'Duplicate simulation cases')
    for case in inventory['six_simulation_cases']:
        for name in ('inst.bin', 'mem.elf', 'actual.npy', 'expected.npy', 'result.json', 'test.log'):
            require(('build', case['directory'] + '/' + name) in observed, f'Missing actual case artifact: {case}/{name}')
        require(('build', case['configuration']) in observed, 'Missing actual simulation config')
    local_artifacts = {}
    for item in inventory['local_large_artifacts']:
        key = (item['source_root'], str(relative(item['source_relative_path'])))
        require(key not in local_artifacts and item['archived'] is False and
                SHA.fullmatch(item['raw_sha256']) is not None and item['raw_bytes'] > 0,
                f'Invalid local artifact reference: {key}')
        require(key not in observed, f'Large artifact unexpectedly copied: {key}')
        local_artifacts[key] = item
    require(local_artifacts.get(('build', 'generator.jar'), {}).get('raw_sha256') == build['generator_sha256'],
            'Omitted generator reference differs from build identity')
    for size in (4, 8, 16):
        path = f'sim{size}/simulator'
        require(local_artifacts.get(('build', path), {}).get('raw_sha256') == omitted[path]['raw_sha256'],
                f'Omitted simulator reference differs from sealed hash: {path}')
    hbm_result = read_json('build', 'hbm-startup/results.json')
    require(local_artifacts.get(('build', 'hbm-startup/obj/VHBMStartupVerificationHarness'), {}).get('raw_sha256') ==
            hbm_result['binary_sha256'], 'Omitted HBM simulator differs from verification result')
    for filename in inventory['physical_record_files']:
        result = read_json('build', filename)
        if 'build_sha256' in result:
            require(result['build_sha256'] == build_hash, f'Physical record/build identity differs: {filename}')
        for path, digest in result.get('stage_seals', {}).items():
            require(observed.get(('build', path)) == digest, f'Physical record/stage hash differs: {path}')
        for path, digest in result.get('evidence', {}).items():
            require(observed.get(('build', path)) == digest, f'Physical raw evidence missing/changed: {path}')
        if 'checkpoint_sha256' in result:
            checkpoint = [(key, item) for key, item in local_artifacts.items()
                          if key[0] == 'build' and key[1].endswith('/obj/post_route.dcp')]
            require(len(checkpoint) == 1 and checkpoint[0][1]['raw_sha256'] == result['checkpoint_sha256'],
                    'Omitted routed checkpoint differs from physical record')
    revised = inventory.get('revised_audit')
    if revised:
        inputs = read_json('reaudit', revised['inputs'])
        acceptance = read_json('reaudit', revised['acceptance'])
        require(revised['inputs'] == 'inputs.json' and revised['hardware_reimplemented'] is False
                and revised['original_acceptance_rewritten'] is False and revised['board_signoff'] is False,
                'Invalid revised audit scope')
        require(observed[('reaudit', revised['inputs'])] == revised['inputs_sha256'] == acceptance['inputs_sha256'],
                'Revised audit input identity differs')
        require(observed[('reaudit', revised['acceptance'])] == revised['acceptance_sha256'],
                'Revised acceptance identity differs')
        require(inputs['schema'] == 1 and inputs['operation'] == 'read_only_routed_reaudit'
                and inputs['hardware_reimplemented'] is False and acceptance['status'] == 'passed'
                and acceptance['constrained_sta_passed'] is True and acceptance['hbm_startup_physical_passed'] is True
                and acceptance['hardware_reimplemented'] is False and acceptance['board_signoff'] is False,
                'Invalid revised physical acceptance')
        original = inputs['implementation']
        require(acceptance['implementation'] == original and acceptance['auditor'] == inputs['auditor'],
                'Revised result changes input/auditor identity')
        require(original['build_sha256'] == build_hash == revised['build_sha256']
                and original['execution_commit'] == build['root_commit'] == revised['execution_commit']
                and original['source_lock_sha256'] == build['lock_sha256']
                and original['generator_sha256'] == build['generator_sha256']
                and original['driver_hashes'] == build['driver_hashes'], 'Re-audit original build identity differs')
        require(original['stage_seals'] == {stage + '.passed.json': observed[('build', stage + '.passed.json')]
                                           for stage in STAGES}, 'Re-audit stage bindings differ')
        for path, digest in {**original['original_reports'], **original['original_ip_files'],
                             **original['original_environment_files']}.items():
            require(observed.get(('build', path)) == digest, 'Re-audit original input missing/changed: ' + path)
        require(local_artifacts.get(('build', original['checkpoint_path']), {}).get('raw_sha256')
                == original['checkpoint_sha256'] == revised['checkpoint_sha256'], 'Re-audit DCP binding differs')
        tools = inputs['auditor']['files']
        require(set(tools) == {'reaudit.py', 'reaudit.tcl', 'physical_audit.py', 'hbm_audit.tcl',
                               'check_reset_evidence.tcl', 'cdc-baseline.json', 'run.sh'}, 'Incomplete revised auditor snapshot')
        for path, digest in tools.items():
            require(observed.get(('reaudit', 'auditor/' + str(relative(path)))) == digest,
                    'Revised auditor missing/changed: ' + path)
        for name in ('check_reset_evidence.tcl', 'cdc-baseline.json'):
            require(tools[name] == build['driver_hashes']['scripts/nm37-a110/' + name],
                    'Revised audit changed frozen baseline/parser')
        execution = read_json('reaudit', 'vivado-result.json')
        require(observed[('reaudit', 'vivado-result.json')] == acceptance['execution_sha256']
                and execution['inputs_sha256'] == revised['inputs_sha256'] and execution['exit_code'] == 0,
                'Re-audit Vivado execution binding differs')
        require(observed.get(('reaudit', 'vivado.log')) == execution['log_sha256'], 'Re-audit stdout log differs')
        require(set(execution['tool_logs']) == {'vivado-native.log', 'vivado.jou'}, 'Re-audit native log set differs')
        for path, digest in execution['tool_logs'].items():
            require(observed.get(('reaudit', path)) == digest, 'Re-audit native log changed')
        reports = {path: digest for (root_name, path), digest in observed.items() if root_name == 'reaudit'
                   and any(path.startswith('obj/' + name + '/') for name in
                           ('report', 'audit', 'hbm_audit', 'reset_replication_audit'))}
        require(reports == execution['report_files'] == acceptance['evidence'], 'Revised report set/hash differs')
        review_files = {name: observed[('reaudit', name)] for name in ('cdc-review.json', 'cdc-review.required.json')
                        if ('reaudit', name) in observed}
        require(acceptance['review_files'] == review_files, 'Revised CDC review file set/hash differs')
        require(inventory['summary']['acceptance_basis'] == 'revised_read_only_audit'
                and inventory['summary']['revised_acceptance_status'] == 'passed'
                and inventory['summary']['hardware_reimplemented'] is False,
                'Archive summary misrepresents revised acceptance')
        export = revised.get('bitstream_export')
        if export:
            record = read_json('reaudit_bitstream', export['record'])
            require(observed[('reaudit_bitstream', export['record'])] == export['record_sha256']
                    and record['operation'] == 'bitstream_from_reaudit' and record['exit_code'] == 0
                    and record['board_signoff'] is False, 'Re-audit bitstream export identity differs')
            for key in ('checkpoint_sha256', 'build_sha256', 'acceptance_sha256', 'inputs_sha256'):
                require(record[key] == revised[key], 'Re-audit bitstream binding differs: ' + key)
            bit = local_artifacts.get(('reaudit_bitstream', 'export/NM37_A110.bit'), {})
            require(bit.get('raw_sha256') == record['bitstream']['sha256'] == export['bitstream_sha256']
                    and bit.get('raw_bytes') == record['bitstream']['bytes'], 'Re-audit bitstream artifact reference differs')
            require(observed.get(('reaudit_bitstream', 'vivado.log')) == record['log_sha256'], 'Export stdout differs')
            require(set(record['tool_logs']) == {'vivado-native.log', 'vivado.jou'}, 'Export native logs incomplete')
            for path, digest in record['tool_logs'].items():
                require(observed.get(('reaudit_bitstream', path)) == digest, 'Export native log differs')
    comparison = read_json('review', 'candidate-comparison.json')
    require(comparison['build_identity']['build_sha256'] == build_hash, 'Comparison/build identity differs')
    require(comparison['script_sha256'] == observed[('review', 'compare_candidate.py')],
            'Comparison script/result identity differs')
    expected_files = set(entries) | {'inventory.json', 'inventory.sha256', 'README.md'}
    disk_files = set()
    for current, directories, filenames in os.walk(root, followlinks=False):
        for name in directories + filenames:
            require(not (Path(current)/name).is_symlink(), f'Symlink in archive tree: {Path(current)/name}')
        for name in filenames:
            path = Path(current)/name
            require(path.is_file(), f'Nonregular file in archive: {path}')
            disk_files.add(path.relative_to(root).as_posix())
    require(disk_files == expected_files,
            f'Archive file set differs: missing={sorted(expected_files - disk_files)}, extra={sorted(disk_files - expected_files)}')
    result = dict(status='pass', archive=str(root), inventory_sha256=inventory_hash,
                  build_sha256=build_hash, packaged_file_count=len(entries), total_archive_file_count=len(expected_files),
                  verified_seals=len(STAGES), actual_simulation_cases=6,
                  explicitly_omitted_sealed_files=len(omitted), full_build_backup=False,
                  local_omitted_artifacts_rehashed=False)
    result['git_index'] = check_index(root, repository, sorted(expected_files)) if require_index else dict(checked=False)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--repository', type=Path)
    parser.add_argument('--require-index', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.archive, args.repository, args.require_index), indent=2))
        return 0
    except Exception as error:
        print(json.dumps(dict(status='fail', error=f'{type(error).__name__}: {error}')), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
