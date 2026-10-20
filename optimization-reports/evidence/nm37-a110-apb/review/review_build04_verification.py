#!/usr/bin/env python3
"""Independent, read-only acceptance of build04 functional verification evidence.

Writes only this review's JSON under review/. Does not invoke a simulator,
compiler, formal solver, repository driver, or implementation tool.
"""
import ast
import hashlib
import json
import math
from pathlib import Path
import re
import struct

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / 'build04'
ROOT = BASE / 'src'
BASELINE = Path('/home/zhangsi/tmp/FSA-m1/optimization-reports/evidence/nm37-a100-reproduction')
COMMIT = 'eeb1a1d0ac6f47497eb2806e412d9df9ad4b6b67'
STAGES = ('hbm-startup', 'units', 'formal', 'sim4', 'sim8', 'sim16', 'elaborate')
COUNTERS = {'dmaActive', 'dmaInst', 'execTime', 'fence', 'mxActive', 'mxBubble', 'mxInst', 'rawInst'}


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def load(path):
    return json.loads(path.read_text())


def evidence(path):
    return {'path': str(path), 'sha256': sha(path), 'bytes': path.stat().st_size}


def npy(path):
    raw = path.read_bytes()
    assert raw[:6] == b'\x93NUMPY', path
    major = raw[6]
    assert major in (1, 2), (path, major)
    size = 2 if major == 1 else 4
    length = struct.unpack('<H' if size == 2 else '<I', raw[8:8+size])[0]
    offset = 8 + size + length
    header = ast.literal_eval(raw[8+size:offset].decode('latin1').strip())
    assert header['descr'] == '<f4' and not header['fortran_order'], (path, header)
    payload = raw[offset:]
    assert len(payload) == math.prod(header['shape']) * 4, path
    return header, payload


def metrics(log):
    pairs = re.findall(r'^FSA:\s+(\w+)\s*=\s*(\d+)\s*$', log, re.M)
    assert len(pairs) == 8 and {k for k, _ in pairs} == COUNTERS, pairs
    total = re.findall(r'\*\*\* PASSED \*\*\* Completed after\s+(\d+) simulation cycles', log)
    assert len(total) == 1, total
    return {key: int(value) for key, value in pairs}, int(total[0])


def main():
    missing = [stage for stage in STAGES if not (OUT / (stage + '.passed.json')).is_file()]
    assert not missing, 'build04 stages not ready: ' + ', '.join(missing)
    build_path = OUT / 'build.json'
    build = load(build_path)
    assert build['root_commit'] == COMMIT, build['root_commit']
    assert sha(OUT / 'generator.jar') == build['generator_sha256']
    lock_path = ROOT / 'fpga/baselines/nm37-a110/source-lock.json'
    assert sha(lock_path) == build['lock_sha256']
    lock = load(lock_path)
    for name, expected in build['driver_hashes'].items():
        assert sha(ROOT / name) == expected, 'Changed build04 driver: ' + name

    stages = {}
    for stage in STAGES:
        path = OUT / (stage + '.passed.json')
        seal = load(path)
        assert seal['build_sha256'] == sha(build_path), stage
        assert seal['files'], stage
        for name, expected in seal['files'].items():
            actual = (OUT / name).resolve()
            assert actual.is_relative_to(OUT), name
            assert sha(actual) == expected, 'Changed stage artifact: ' + name
        stages[stage] = {**evidence(path), 'verified_files': len(seal['files'])}

    hbm_dir = OUT / 'hbm-startup'
    hbm_path = hbm_dir / 'results.json'
    hbm = load(hbm_path)
    counts = {'clock_scenarios': 8, 'gate_vectors': 32768,
              'short_complete_faults': 8, 'partial_initialization_resets': 8,
              'pll_scenarios': 8, 'pll_stable_cycles': 7, 'pll_release_events': 32}
    assert hbm['passed'] and hbm['checks'] > 100000
    assert all(hbm[key] == expected for key, expected in counts.items())
    assert hbm['rtl_firtool_versions'] == ['1.75.0']
    assert 'Verilator 5.022' in hbm['verilator']
    for name, expected in hbm['inputs_sha256'].items():
        path = Path(name)
        assert path.is_relative_to(ROOT) or path.is_relative_to(hbm_dir), name
        assert sha(path) == expected, name
    for key, filename in [('binary_sha256', 'obj/VHBMStartupVerificationHarness'),
                          ('simulation_log_sha256', 'simulation.log'),
                          ('verilator_build_log_sha256', 'verilator-build.log')]:
        assert sha(hbm_dir / filename) == hbm[key], filename
    actual_counts = load_json_line((hbm_dir / 'simulation.log').read_text())
    assert all(actual_counts[key] == hbm[key] for key in [*counts, 'checks', 'passed'])
    inline = (hbm_dir / 'rtl/HBMAsyncLevelSynchronizer.sv').read_text()
    assert 'ASYNC_REG = "TRUE"' in inline and 'SHREG_EXTRACT = "NO"' in inline
    assert 'always @(posedge clock or posedge reset)' in inline

    unit_log_path = OUT / 'logs/unit.log'
    unit_log = unit_log_path.read_text()
    for text in ['FPMac delay equivalence 14394 cases', 'CMP max/diff equivalence 20169 cases',
                 'PE EXP2 scans 90 cases', 'Tests: succeeded 5, failed 0', 'All tests passed.',
                 'schedules (4,4), (4,8), (8,4), (8,8), (16,16)']:
        assert text in unit_log, text
    score_path = OUT / 'score-events.json'
    assert load(score_path) == load(BASELINE / 'score-events.json'), 'Changed schedule events'

    proof_results_path = OUT / 'formal/results.json'
    proofs = load(proof_results_path)
    assert len(proofs) == 3 and {p['proof_type'] for p in proofs} == {'cmpeq', 'cmpseq', 'macseq'}
    proof_summaries = []
    for proof in proofs:
        assert proof['all_inputs_equivalent'] and proof['exit_code'] == 0
        assert proof['input_constraints'] == [] and proof['initial_state_constraints'] == []
        directory = {'cmpeq': 'cmpmax', 'cmpseq': 'cmp', 'macseq': 'mac'}[proof['proof_type']]
        case = OUT / 'formal' / directory
        snapshot = OUT / proof['proof_input_directory']
        assert snapshot == OUT / 'formal/proof-inputs' / directory
        log = (case / 'yosys.log').read_text()
        marker = ('SAT proof finished - no model found: SUCCESS!' if directory == 'cmpmax'
                  else 'Induction step proven: SUCCESS!')
        assert marker in log
        assert sha(case / 'yosys.log') == proof['proof_log_sha256']
        assert sha(case / 'prove.ys') == proof['proof_script_sha256']
        assert (case / 'prove.ys').read_bytes() == (snapshot / 'prove.ys').read_bytes()
        assert sha(ROOT / 'scripts/nm37-a110/verify_equivalence.py') == proof['driver_sha256']
        assert set(proof['rtl_sha256']) == {p.name for p in snapshot.glob('*.sv')}
        for name, expected in proof['rtl_sha256'].items():
            assert sha(snapshot / name) == expected
            assert (snapshot / name).read_bytes() == (case / name).read_bytes()
            assert 'Generated by CIRCT firtool-1.75.0' in (snapshot / name).read_text()
        for name, expected in proof['source_hashes'].items():
            assert lock['source_hashes'][name] == expected and sha(ROOT / name) == expected, name
        proof_summaries.append({key: proof[key] for key in
            ('proof_type', 'top', 'scope', 'method', 'input_constraints', 'initial_state_constraints',
             'proof_script_sha256', 'proof_log_sha256', 'rtl_sha256')})

    cases = []
    for size in (4, 8, 16):
        for sequence in (size, size * 2):
            case = OUT / f'sim{size}/seq{sequence}'
            baseline_case = BASELINE / f'sim{size}/seq{sequence}'
            result = load(case / 'result.json')
            baseline_result = load(baseline_case / 'result.json')
            assert result['bitwise_equal'] and result['seed'] == 0 and result['seq'] == sequence
            actual_header, actual = npy(case / 'actual.npy')
            expected_header, expected = npy(case / 'expected.npy')
            baseline_header, baseline_actual = npy(baseline_case / 'actual.npy')
            assert actual_header == expected_header == baseline_header
            assert actual == expected == baseline_actual
            assert hashlib.sha256(actual).hexdigest() == result['actual_sha256'] == result['expected_sha256']
            assert result == baseline_result
            counter, total = metrics((case / 'test.log').read_text())
            old_counter, old_total = metrics((baseline_case / 'test.log').read_text())
            assert counter == old_counter and total == old_total
            for name in ('inst.bin', 'mem.elf'):
                assert (case / name).read_bytes() == (baseline_case / name).read_bytes(), (case, name)
            configs = list((OUT / f'sim{size}/generated-src').glob('*/*.FSAConfig.json'))
            assert len(configs) == 1
            config = load(configs[0])
            assert config['sa_rows'] == size and config['sa_cols'] == size
            assert config['e_type'] == 'fp16' and config['a_type'] == 'fp32'
            cases.append({'array_rows': size, 'array_cols': size, 'seq': sequence,
                'shape': result['shape'], 'seed': 0, 'bitwise_equal': True,
                'same_A100_inputs_outputs_and_counters': True,
                'actual_payload_sha256': result['actual_sha256'], 'counters': counter,
                'testbench_total_cycles': total,
                'conditional_110MHz_active_time_us': counter['execTime'] / 110,
                'conditional_100MHz_active_time_us': counter['execTime'] / 100,
                'evidence': [evidence(case / name) for name in
                    ('result.json', 'test.log', 'actual.npy', 'expected.npy', 'inst.bin', 'mem.elf')],
                'baseline_evidence': [evidence(baseline_case / name) for name in
                    ('result.json', 'test.log', 'actual.npy', 'inst.bin', 'mem.elf')],
                'config': evidence(configs[0])})

    board_configs = list((OUT / 'A110/fpga').glob('*/*.FSAConfig.json'))
    assert len(board_configs) == 1
    assert load(board_configs[0]) == load(ROOT / 'fpga/baselines/nm37-a110/FSAConfig.json')
    result = {
        'accepted_functional_verification': True,
        'build_directory': str(OUT), 'executed_commit': COMMIT,
        'build': evidence(build_path), 'source_lock': evidence(lock_path),
        'generator_sha256_verified': build['generator_sha256'],
        'stage_seals': stages,
        'hbm_startup': {'result': evidence(hbm_path), 'counts': {**counts, 'checks': hbm['checks']},
            'simulation_log': evidence(hbm_dir / 'simulation.log'),
            'rtl_firtool_versions': hbm['rtl_firtool_versions'],
            'verilator': hbm['verilator'], 'production_PLL_stable_cycles': 10000,
            'coverage': ['APB/AXI asynchronous assertion and three-edge local release',
                'APB and AXI stopped-clock startup; short asynchronous cold-reset pulses',
                'initialization wait, one-APB-cycle completion loss, sticky fault until common reset',
                'reset interruption after one/two destination synchronizer stages',
                'all ten valid/ready inputs, all five AXI channels, full packed payloads, request/response backpressure',
                'eight PLL monitor-clock cases; exact 3+7 edge qualification, lock-loss/PERST short pulses',
                'monitor stopped clock, qualification restart, interleaved causes, no HBM-ready feedback dependence']},
        'units': {'log': evidence(unit_log_path), 'score_events': evidence(score_path),
            'FPMac_cases': 14394, 'CMP_cases': 20169, 'PE_EXP2_cases': 90,
            'control_tests': 5, 'schedule_dimensions': [[4,4], [4,8], [8,4], [8,8], [16,16]],
            'same_A100_score_events': True},
        'formal': {'results': evidence(proof_results_path), 'proofs': proof_summaries},
        'system_cases': cases, 'board_config': evidence(board_configs[0]),
        'boundaries': [
            'This review covers only build04 eeb1a1d0. No build02/build03 stage was reused.',
            'HBM/PLL startup is digital RTL protocol verification, not analog metastability, HBM PHY or board testing.',
            'PLL qualification tests use stableCycles=7. Production uses 10000 at the board 100MHz clock; emitted board topology and counter require separate structural/physical review.',
            'Gate verification is combinational control/payload and directed backpressure checking, not a complete transaction-level AXI scoreboard.',
            'Initialization loss is fail-stop. In-flight requests and memory contents are not preserved across a common cold reset.',
            'Arithmetic equivalence does not prove whole FSA, HBM startup, PE resident-state protocol or post-layout netlist equivalence.',
            'FSA simulations use their existing simulated memory system, not the NM37 HBM hard macro or PCIe hardware.',
            'Frequency-derived active times are conditional estimates from unchanged DUT counters, not measured board throughput.',
            '4x4 and 8x8 cases are functional scaling coverage; the routed board target is 16x16.',
            'Physical STA, pulse width, CDC, reset replication and board signoff remain separate requirements.'
        ],
        'review_script': evidence(Path(__file__).resolve()),
    }
    destination = BASE / 'review/build04-verification-review.json'
    destination.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'accepted': True, 'output': str(destination), 'stages': len(stages),
                      'system_cases': len(cases), 'hbm_checks': hbm['checks']}))


def load_json_line(text):
    return json.loads(next(line for line in reversed(text.splitlines()) if line.startswith('{')))


if __name__ == '__main__':
    main()
