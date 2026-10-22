#!/usr/bin/env python3
"""Prove arbitrary IEEE/MAC/EXP2 input windows with exact register expansion.

No input/reset/initial-state restrictions or cutpoint assumptions. The unmasked
3-edge data comparison is proved, and an empty initial-state cone makes this a
complete all-windows proof rather than a bounded-only result.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rtl', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True, help='Fresh output directory')
    parser.add_argument('--yosys', default=os.environ.get('YOSYS', 'yowasp-yosys'))
    args = parser.parse_args()
    rtl = args.rtl.resolve()
    out = args.out.resolve()
    files = sorted(rtl.glob('*.sv'))
    if not files:
        parser.error(f'No emitted .sv files in {rtl}')
    if not all(re.fullmatch(r'[A-Za-z0-9_.-]+', f.name) for f in files):
        parser.error('RTL filenames must contain only letters, numbers, dot, underscore, or hyphen')
    # Never overwrite or mix with an earlier proof attempt.
    out.mkdir(parents=True, exist_ok=False)
    helpers = Path(__file__).resolve().parent
    for name in ('run-proof.py', 'unroll.py', 'semantic-fixtures.py', 'proof-notes.txt'):
        shutil.copy2(helpers / name, out / name)
    for f in files:
        shutil.copy2(f, out / f.name)
    env = dict(os.environ, XDG_CACHE_HOME=str(out / 'cache'), YOSYS=args.yosys)
    start = time.monotonic()
    stage = 'version'
    passed = False
    error = None
    exit_code = 0
    audit = None
    fixture_result = None

    def run(command, logname):
        with (out / logname).open('w') as log:
            subprocess.run(command, cwd=out, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)

    try:
        run([args.yosys, '-V'], 'yosys-version.txt')
        stage = 'semantic_fixtures'
        run([sys.executable, 'semantic-fixtures.py'], 'semantic-fixtures.log')
        fixture_result = json.loads((out / 'fixtures/result.json').read_text())
        assert fixture_result['passed']
        stage = 'rtl_lowering'
        read = 'read_verilog -sv ' + ' '.join(f.name for f in files)
        structure = '\n'.join((read, 'hierarchy -check -top A70AccumulatorMiter', 'proc', 'flatten', 'opt', 'check -assert', 'write_json structure.json', 'write_verilog -noattr optimized.v', 'stat')) + '\n'
        (out / 'structure.ys').write_text(structure)
        run([args.yosys, '-s', 'structure.ys'], 'structure.log')
        stage = 'exact_temporal_expansion'
        run([sys.executable, 'unroll.py', '--work', str(out)], 'unroll.log')
        audit = json.loads((out / 'unroll-audit.json').read_text())
        assert audit['initial_state_leaves'] == []
        assert audit['free_input_groups'] == {'io_a@t0':32, 'io_b@t0':32, 'io_c@t0':32, 'io_exp2@t0':1, 'reset@t0':1, 'reset@t1':1, 'reset@t2':1}
        stage = 'combinational_sat'
        script = '\n'.join(('read_json unrolled.json', 'hierarchy -check -top A70AccumulatorMiterUnrolled', 'opt', 'check -assert', 'stat', 'write_json unrolled-optimized.json', 'write_verilog -noattr unrolled-optimized.v', 'sat -verify -prove io_mismatch 0 -prove data_mismatch 0 -show-inputs -show-outputs -dump_json counterexample.json -dump_vcd counterexample.vcd')) + '\n'
        (out / 'prove-unrolled.ys').write_text(script)
        run([args.yosys, '-s', 'prove-unrolled.ys'], 'yosys.log')
        assert 'SAT proof finished - no model found: SUCCESS!' in (out / 'yosys.log').read_text()
        passed = True
        stage = 'complete'
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
        exit_code = getattr(exc, 'returncode', 1)
    elapsed = time.monotonic() - start
    artifacts = sorted(p for p in out.rglob('*') if p.is_file() and not any(part in ('cache', '__pycache__') for part in p.relative_to(out).parts))
    result = dict(
        passed=passed, exit_code=exit_code, stage=stage, error=error,
        method='Exact three-edge temporal cone expansion and unrestricted combinational SAT',
        top='A70AccumulatorMiter', comparison_latency_cycles=3,
        complete_after_pipeline_clocks=3,
        scope='All IEEE input bit patterns and MAC/EXP2 selections, at every output after three rising clocks; unmasked gate != delayed legacy oracle proven zero. No reciprocal or scheduling claim.',
        completeness='The exact time-3 output cone has no initial-register leaves. Arbitrary start states therefore give the same zero-mismatch identity for every later three-clock window.',
        properties={'io_mismatch':0, 'data_mismatch':0},
        depends_on_valid_mask=False,
        input_constraints=[], initial_state_constraints=[], reset_constraints=[], cutpoint_assumptions=[],
        unroll_audit=audit, semantic_fixtures=fixture_result,
        rtl_sha256={f.name:sha(out/f.name) for f in files},
        driver_sha256=sha(out/'run-proof.py'), expander_sha256=sha(out/'unroll.py'),
        artifact_sha256={str(p.relative_to(out)):sha(p) for p in artifacts},
        elapsed_seconds=elapsed,
        trusted_components=['Yosys RTL lowering, optimization and SAT', 'Included auditable exact temporal expansion unroll.py'],
    )
    (out/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
