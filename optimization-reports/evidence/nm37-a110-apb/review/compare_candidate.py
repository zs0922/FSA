#!/usr/bin/env python3
"""Read-only evidence comparison against the archived original A110 experiment.

Only output JSON files are written. Lexical RTL normalization removes exactly
CIRCT line source-location comments. No identifiers, whitespace, other comments,
string literals, synthesis directives, or executable tokens are normalized.
"""
import argparse
import ast
import hashlib
import gzip
import json
import math
from pathlib import Path, PurePosixPath
import re
import struct
import sys

COUNTERS = ('dmaActive', 'dmaInst', 'execTime', 'fence', 'mxActive', 'mxBubble', 'mxInst', 'rawInst')
CORE_PREFIXES = ('PE', 'CMP', 'FPMacUnit', 'FSA', 'InputDelayer', 'OutputDelayer',
                 'MatrixControlFSM', 'RawFloat_FMA', 'ScratchPadSRAM', 'Accumulator',
                 'AccumulationSRAM', 'SystolicArray')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def unique_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f'Duplicate JSON key in {path}: {key}')
            result[key] = value
        return result
    return json.loads(path.read_text(), object_pairs_hook=pairs)


def confined_file(base, relative):
    rel = PurePosixPath(relative)
    if rel.is_absolute() or '..' in rel.parts or not rel.parts:
        raise ValueError(f'Unsafe evidence path: {relative}')
    path = base.joinpath(*rel.parts)
    if not path.resolve().is_relative_to(base.resolve()) or not path.is_file():
        raise ValueError(f'Missing or escaped evidence file: {path}')
    return path


def require_equal(actual, expected, message):
    if actual != expected:
        raise ValueError(f'{message}: {actual!r} != {expected!r}')


def log_counters(log, name):
    counters = {}
    for key in COUNTERS:
        matches = re.findall(r'^FSA: ' + key + r' =\s*(\d+)\s*$', log, re.M)
        if len(matches) != 1:
            raise ValueError(f'{name}: expected one {key} counter, found {len(matches)}')
        counters[key] = int(matches[0])
    cycles = re.findall(r'\*\*\* PASSED \*\*\* Completed after\s+(\d+) simulation cycles', log)
    if len(cycles) != 1:
        raise ValueError(f'{name}: expected one simulation PASS/cycle count')
    return counters, int(cycles[0])


def verify_new_stage_seals(build, stages=('sim4', 'sim8', 'sim16', 'elaborate')):
    build_path = confined_file(build, 'build.json')
    identity = unique_json(build_path)
    build_hash = file_sha(build_path)
    generator = confined_file(build, 'generator.jar')
    require_equal(file_sha(generator), identity['generator_sha256'], 'Generator/build binding')
    results, manifests = {}, {}
    for stage in stages:
        path = confined_file(build, f'{stage}.passed.json')
        seal = unique_json(path)
        require_equal(seal['build_sha256'], build_hash, f'{stage}: build identity')
        files = seal['files']
        if not isinstance(files, dict) or not files:
            raise ValueError(f'{stage}: empty or invalid sealed file map')
        for relative, expected in files.items():
            artifact = confined_file(build, relative)
            require_equal(file_sha(artifact), expected, f'{stage}: sealed artifact {relative}')
        results[stage] = dict(path=str(path), sha256=file_sha(path),
                              build_sha256=build_hash, verified_file_count=len(files))
        manifests[stage] = files
    return dict(build_json=str(build_path), build_sha256=build_hash,
                root_commit=identity['root_commit'],
                generator_sha256=identity['generator_sha256'], stages=results), manifests


def require_sealed_path(build, manifests, stage, path):
    relative = path.relative_to(build).as_posix()
    if relative not in manifests[stage]:
        raise ValueError(f'{stage}: compared artifact is not in stage seal: {relative}')
    require_equal(file_sha(path), manifests[stage][relative], f'{stage}: compared artifact {relative}')


def verify_comparison_coverage(build, manifests, board, sim16):
    checked = []
    for stage, directory in [('elaborate', board), ('sim16', sim16)]:
        for path in sorted(directory.glob('*.sv')):
            require_sealed_path(build, manifests, stage, path)
            checked.append(str(path.relative_to(build)))
    for size in (4, 8, 16):
        stage = f'sim{size}'
        cfg = f'chipyard.harness.TestHarness.AXI4FSA{size}X{size}Fp16Config'
        paths = [build/stage/'simulator', build/stage/'generated-src'/cfg/f'{cfg}.FSAConfig.json']
        for seq in (size, size * 2):
            paths.extend(build/stage/f'seq{seq}'/name for name in
                         ('result.json', 'test.log', 'actual.npy', 'expected.npy', 'inst.bin', 'mem.elf'))
        for path in paths:
            require_sealed_path(build, manifests, stage, path)
            checked.append(str(path.relative_to(build)))
    return dict(verified_compared_file_count=len(checked), verified_compared_files=checked)


def verify_historical_archive(archive, old_rtl, old_metrics):
    inventory_path = archive / 'inventory.json'
    inventory = unique_json(inventory_path)
    candidates = [x for x in inventory['files'] if PurePosixPath(x['archived_path']).name == 'fsa-a110-rtl-comparison.json']
    if len(candidates) != 1:
        raise ValueError('Historical inventory has no unique RTL comparison entry')
    prefix = PurePosixPath(candidates[0]['archived_path']).parent
    entries, sources = {}, {}
    for item in inventory['files']:
        rel = str(PurePosixPath(item['archived_path']).relative_to(prefix))
        if rel in entries or item['source'] in sources:
            raise ValueError(f'Duplicate historical inventory record: {rel}')
        entries[rel] = item
        sources[item['source']] = rel
    checked = {}
    def read(rel):
        if rel not in entries:
            raise ValueError(f'Historical compared artifact absent from inventory: {rel}')
        entry = entries[rel]
        path = confined_file(archive, rel)
        raw = path.read_bytes()
        require_equal(len(raw), entry['bytes'], f'Historical archive size: {rel}')
        require_equal(sha(raw), entry['sha256'], f'Historical archive hash: {rel}')
        encoding = entry.get('encoding', 'identity')
        if encoding not in ('identity', 'gzip'):
            raise ValueError(f'Unknown archival encoding: {rel}: {encoding}')
        content = gzip.decompress(raw) if encoding == 'gzip' else raw
        require_equal(sha(content), entry.get('source_sha256', entry['sha256']), f'Historical source hash: {rel}')
        require_equal(len(content), entry.get('source_bytes', entry['bytes']), f'Historical source size: {rel}')
        checked[rel] = entry['sha256']
        return content
    for rel in ('fsa-a110-rtl-comparison.json', 'fsa-a110-rtl-comparison-method.txt', 'fsa-a110-metrics.json'):
        read(rel)
    old_build_bytes = read('A110/seals/build.json')
    old_build = json.loads(old_build_bytes)
    old_root = PurePosixPath(entries['A110/seals/build.json']['source']).parent
    seals = {}
    for stage in ('sim4', 'sim8', 'sim16', 'elaborate'):
        seal = json.loads(read(f'A110/seals/{stage}.passed.json'))
        require_equal(seal['build_sha256'], sha(old_build_bytes), f'Historical {stage} build binding')
        if not seal['files']:
            raise ValueError(f'Historical {stage} empty file manifest')
        seals[stage] = seal['files']
    rtl_checks = 0
    for kind, stage in [('board', 'elaborate'), ('sim16', 'sim16')]:
        directory = PurePosixPath(old_rtl[kind]['A110_directory']).relative_to(old_root)
        for record in old_rtl[kind]['files']:
            relative = str(directory / record['file'])
            require_equal(seals[stage].get(relative), record['raw_sha256']['A110'],
                          f'Historical RTL raw fingerprint/seal: {relative}')
            rtl_checks += 1
    case_checks, unavailable = [], []
    for case in old_metrics['cases']:
        size, seq = case['array_rows'], case['seq']
        stage, directory = f'sim{size}', PurePosixPath(case['case']).relative_to(old_root)
        files = seals[stage]
        log_relative = str(directory/'test.log')
        require_equal(files.get(log_relative), case['test_log_sha256'], f'Historical test log seal: {directory}')
        source_log = str(old_root/directory/'test.log')
        if source_log not in sources:
            raise ValueError(f'Historical compared counters have no archived raw log: {source_log}')
        log = read(sources[source_log]).decode('utf-8')
        require_equal(sha(log.encode('utf-8')), case['test_log_sha256'], f'Historical test log metric hash: {directory}')
        counters, cycles = log_counters(log, str(directory))
        require_equal(counters, case['counters'], f'Historical raw log counters: {directory}')
        require_equal(cycles, case['testbench_total_cycles'], f'Historical raw log cycles: {directory}')
        summary = json.loads(log[log.rfind('\n{') + 1:])
        for key, expected in [('actual_sha256', case['actual_payload_sha256']),
                              ('expected_sha256', case['expected_payload_sha256']),
                              ('bitwise_equal', case['verified_bitwise_equal']),
                              ('seed', case['seed']), ('seq', seq), ('shape', case['shape'])]:
            require_equal(summary[key], expected, f'Historical log summary {key}: {directory}')
        cfg = f'chipyard.harness.TestHarness.AXI4FSA{size}X{size}Fp16Config'
        compared = {str(directory/name): digest for name, digest in case['input_sha256'].items()}
        compared.update({str(directory/'result.json'): case['result_json_sha256'],
                         f'{stage}/simulator': case['simulator_sha256'],
                         f'{stage}/generated-src/{cfg}/{cfg}.FSAConfig.json': case['config_sha256']})
        for relative, expected in compared.items():
            require_equal(files.get(relative), expected, f'Historical metric/seal binding: {relative}')
            source = str(old_root/relative)
            if source in sources:
                require_equal(sha(read(sources[source])), expected, f'Historical available compared file: {relative}')
            else:
                unavailable.append(relative)
        case_checks.append(dict(case=str(directory), all_eight_counters_match_archived_raw_log=True,
                                output_payload_hashes_match_archived_raw_log=True,
                                input_config_result_binary_hashes_match_historical_stage_seal=True))
    return dict(inventory_sha256=file_sha(inventory_path), execution_commit=old_build['root_commit'],
                build_sha256=sha(old_build_bytes), verified_archived_files=checked,
                rtl_fingerprints_bound_to_historical_seals=rtl_checks, cases=case_checks,
                compared_files_not_archived=sorted(set(unavailable)),
                limitation='Unarchived historical bytes are not reconstructed or claimed rehashed; their recorded hashes are checked against the inventory-authenticated historical stage manifests. Historical output payload hashes and counters are independently re-parsed from the archived raw simulation logs.')


def normalize_source_locations(raw):
    """Byte-preserving stateful scanner; newlines and pre-comment spaces remain."""
    text = raw.decode('utf-8')
    out = []
    count = 0
    i = 0
    while i < len(text):
        if text.startswith('//', i):
            end = text.find('\n', i)
            if end < 0:
                end = len(text)
            comment = text[i + 2:end]
            if comment.lstrip().startswith('@['):
                count += 1
            else:
                out.append(text[i:end])
            i = end
        elif text.startswith('/*', i):
            end = text.find('*/', i + 2)
            if end < 0:
                raise ValueError('Unterminated block comment')
            end += 2
            out.append(text[i:end])
            i = end
        elif text[i] == '"':
            start = i
            i += 1
            while i < len(text):
                if text[i] == '\\':
                    i += 2
                elif text[i] == '"':
                    i += 1
                    break
                else:
                    i += 1
            else:
                raise ValueError('Unterminated string')
            out.append(text[start:i])
        elif text[i] == '\\':
            # Verilog escaped identifiers extend through the first whitespace.
            # A preprocessor line continuation is just a backslash + newline.
            start = i
            i += 1
            while i < len(text) and not text[i].isspace():
                i += 1
            out.append(text[start:i])
        else:
            out.append(text[i])
            i += 1
    return ''.join(out).encode('utf-8'), count


def scanner_selftest():
    source = (b'wire a;  // @[a.scala 1:2]\n'
              b'initial $display("// @[must retain] and \\"quoted\\"");\n'
              b'/* // @[must retain] */ wire b; // ordinary comment\n'
              b'wire \\identifier//@[keep] ;\n'
              b'`define VALUE 1 \\\n  + 2 // @[b.scala 3:4]\n')
    expected = (b'wire a;  \n'
                b'initial $display("// @[must retain] and \\"quoted\\"");\n'
                b'/* // @[must retain] */ wire b; // ordinary comment\n'
                b'wire \\identifier//@[keep] ;\n'
                b'`define VALUE 1 \\\n  + 2 \n')
    got, count = normalize_source_locations(source)
    if got != expected or count != 2:
        raise AssertionError('Lexical source-location normalization self-test failed')


def baseline_selfcheck(directory, archived, kind):
    records = []
    failures = []
    for old in archived[kind]['files']:
        path = directory / old['file']
        if not path.is_file():
            failures.append(f'Missing baseline validation file {path}')
            continue
        raw = path.read_bytes()
        normalized, removed = normalize_source_locations(raw)
        ok = (sha(raw) == old['raw_sha256']['A100'] and
              sha(normalized) == old['normalized_sha256']['A100'] and
              removed == old['removed_source_comments']['A100'])
        records.append(dict(file=str(path), raw_sha256=sha(raw), normalized_sha256=sha(normalized),
                            removed_source_comments=removed, matches_archived_A100=ok))
        if not ok:
            failures.append(f'Historical baseline scanner validation mismatch: {path.name}')
    return dict(status='pass' if not failures else 'fail', kind=kind,
                archived_file_count=len(archived[kind]['files']), files=records, failures=failures)


def rtl_compare(directory, archived, kind):
    expected = {x['file']: x for x in archived[kind]['files']}
    current = {x.name: x for x in directory.glob('*.sv')}
    if not current:
        raise ValueError(f'No SV files in {directory}')
    required_core = set(archived[kind]['core_module_files'])
    required_core.update(n for n in expected if n.endswith('.sv') and
                         any(n[:-3] == p or n.startswith(p + '_') for p in CORE_PREFIXES))
    records, failures = [], []
    for name in sorted(expected.keys() | current.keys()):
        old, path = expected.get(name), current.get(name)
        row = dict(file=name, core_module=name in required_core)
        if path:
            raw = path.read_bytes()
            normalized, count = normalize_source_locations(raw)
            row.update(candidate_path=str(path), candidate_raw_sha256=sha(raw),
                       candidate_normalized_sha256=sha(normalized),
                       candidate_removed_source_comments=count)
        if old:
            row.update(baseline_raw_sha256=old['raw_sha256']['A110'],
                       baseline_normalized_sha256=old['normalized_sha256']['A110'],
                       baseline_removed_source_comments=old['removed_source_comments']['A110'])
        row['status'] = ('added' if old is None else 'removed' if path is None else
                         'identical' if row['candidate_normalized_sha256'] == row['baseline_normalized_sha256']
                         else 'changed')
        if row['core_module'] and row['status'] != 'identical':
            failures.append(f'{kind} core module {name}: {row["status"]}')
        if kind == 'sim16' and row['status'] != 'identical':
            failures.append(f'sim16 file {name}: {row["status"]}')
        records.append(row)
    return dict(kind=kind, directory=str(directory), baseline_sv_count=len(expected),
                candidate_sv_count=len(current), core_module_files=sorted(required_core),
                core_modules_identical=not any(x['core_module'] and x['status'] != 'identical' for x in records),
                all_files_identical=all(x['status'] == 'identical' for x in records),
                differences=[x for x in records if x['status'] != 'identical'],
                files=records, failures=failures)


def npy_float32(path):
    raw = path.read_bytes()
    if raw[:6] != b'\x93NUMPY':
        raise ValueError(f'Not NPY: {path}')
    version = tuple(raw[6:8])
    if version == (1, 0):
        length, offset = struct.unpack('<H', raw[8:10])[0], 10
    elif version in ((2, 0), (3, 0)):
        length, offset = struct.unpack('<I', raw[8:12])[0], 12
    else:
        raise ValueError(f'Unsupported NPY version: {path}')
    header = ast.literal_eval(raw[offset:offset + length].decode('utf-8'))
    if header['descr'] != '<f4' or header['fortran_order']:
        raise ValueError(f'Expected C-contiguous little-endian fp32 NPY: {path}: {header}')
    payload = raw[offset + length:]
    if len(payload) != math.prod(header['shape']) * 4:
        raise ValueError(f'NPY size mismatch: {path}')
    return list(header['shape']), payload


def metrics_compare(build, archived):
    records, failures = [], []
    expected_keys = {(4, 4), (4, 8), (8, 8), (8, 16), (16, 16), (16, 32)}
    if {(x['array_rows'], x['seq']) for x in archived['cases']} != expected_keys:
        raise ValueError('Unexpected baseline case set')
    for old in sorted(archived['cases'], key=lambda x: (x['array_rows'], x['seq'])):
        size, seq = old['array_rows'], old['seq']
        case = build / f'sim{size}' / f'seq{seq}'
        name = f'sim{size}/seq{seq}'
        result_file, log_file = case / 'result.json', case / 'test.log'
        cfg_name = f'chipyard.harness.TestHarness.AXI4FSA{size}X{size}Fp16Config'
        config_file = build / f'sim{size}' / 'generated-src' / cfg_name / f'{cfg_name}.FSAConfig.json'
        result, config = json.loads(result_file.read_text()), json.loads(config_file.read_text())
        log = log_file.read_text()
        counters = {}
        for key in COUNTERS:
            matches = re.findall(r'^FSA: ' + key + r' =\s*(\d+)\s*$', log, re.M)
            if len(matches) != 1:
                raise ValueError(f'{name}: expected one {key} counter, found {len(matches)}')
            counters[key] = int(matches[0])
        cycles = re.findall(r'\*\*\* PASSED \*\*\* Completed after\s+(\d+) simulation cycles', log)
        if len(cycles) != 1:
            raise ValueError(f'{name}: expected one simulation PASS/cycle count')
        actual_shape, actual = npy_float32(case / 'actual.npy')
        expected_shape, expected = npy_float32(case / 'expected.npy')
        input_hashes = {name: file_sha(case / name) for name in ('inst.bin', 'mem.elf')}
        checks = dict(
            same_workload=(size == old['array_cols'] and result['seq'] == seq and
                           result['seed'] == old['seed'] and actual_shape == old['shape'] and
                           expected_shape == old['shape'] and result['shape'] == old['shape']),
            same_software_config=config == old['config'],
            same_software_config_bytes=file_sha(config_file) == old['config_sha256'],
            same_input_bytes=input_hashes == old['input_sha256'],
            both_verified_bitwise_equal=(old['verified_bitwise_equal'] is True and
                                         result['bitwise_equal'] is True and actual == expected),
            result_hashes_match_payload=(result['actual_sha256'] == sha(actual) and
                                        result['expected_sha256'] == sha(expected)),
            same_actual_output_bits=sha(actual) == old['actual_payload_sha256'],
            same_expected_output_bits=sha(expected) == old['expected_payload_sha256'],
            same_all_eight_counters=counters == old['counters'],
            same_testbench_cycles=int(cycles[0]) == old['testbench_total_cycles'])
        for check, passed in checks.items():
            if not passed:
                failures.append(f'{name}: {check} failed')
        records.append(dict(case=name, checks=checks, counters=counters,
                            counter_delta={k: counters[k] - old['counters'][k] for k in COUNTERS},
                            testbench_total_cycles=int(cycles[0]),
                            testbench_total_cycle_delta=int(cycles[0]) - old['testbench_total_cycles'],
                            actual_payload_sha256=sha(actual), expected_payload_sha256=sha(expected),
                            input_sha256=input_hashes, config=config,
                            artifacts={str(p): file_sha(p) for p in
                                       (result_file, log_file, config_file, case/'actual.npy', case/'expected.npy')},
                            conditional_dut_active_time_us_at_110mhz=counters['execTime'] / 110))
    return dict(cases=records, failures=failures,
                limitations=archived['caveats'] + [
                    'System simulations use the FSA test harness and do not model the board HBM startup latency.',
                    'These are cycle and output comparisons, not measured board throughput or HBM calibration tests.'])


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--archive', type=Path, required=True)
    p.add_argument('--build', type=Path)
    p.add_argument('--validate-old-board', type=Path)
    p.add_argument('--validate-old-sim16', type=Path)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    report = dict(schema_version=2, script_sha256=file_sha(Path(__file__).resolve()),
                  method=__doc__, failures=[])
    try:
        scanner_selftest()
        report['lexical_selftest'] = 'pass'
        rtl_path = args.archive / 'fsa-a110-rtl-comparison.json'
        metric_path = args.archive / 'fsa-a110-metrics.json'
        old_rtl, old_metrics = unique_json(rtl_path), unique_json(metric_path)
        report['baseline_evidence_sha256'] = {str(p): file_sha(p) for p in (rtl_path, metric_path)}
        report['historical_archive_verification'] = verify_historical_archive(args.archive, old_rtl, old_metrics)
        for kind, directory in [('board', args.validate_old_board), ('sim16', args.validate_old_sim16)]:
            if directory:
                result = baseline_selfcheck(directory, old_rtl, kind)
                report[f'baseline_scanner_validation_{kind}'] = result
                report['failures'].extend(result['failures'])
        if args.build:
            report['build_identity'], manifests = verify_new_stage_seals(args.build)
            board = args.build / 'A110/fpga/chipyard.fpga.nm37.NM37FPGATestHarness.EmptyNM37Config/gen-collateral'
            sim16 = args.build / 'sim16/generated-src/chipyard.harness.TestHarness.AXI4FSA16X16Fp16Config/gen-collateral'
            report['compared_artifact_seal_coverage'] = verify_comparison_coverage(args.build, manifests, board, sim16)
            for kind, directory in [('board', board), ('sim16', sim16)]:
                result = rtl_compare(directory, old_rtl, kind)
                report[kind] = result
                report['failures'].extend(result['failures'])
            report['metrics'] = metrics_compare(args.build, old_metrics)
            report['failures'].extend(report['metrics']['failures'])
    except Exception as error:
        report['failures'].append(f'{type(error).__name__}: {error}')
    report['status'] = 'pass' if not report['failures'] else 'fail'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'output': str(args.output), 'failures': report['failures']}))
    return 0 if report['status'] == 'pass' else 1


if __name__ == '__main__':
    sys.exit(main())
