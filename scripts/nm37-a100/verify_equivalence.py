"""Unrestricted-input RTL equivalence; induction covers all post-fill cycles.

The MAC and subtraction miters compare a one-cycle candidate against a
registered legacy result. Their first, unfilled cycle is skipped, with no
initial-state or reset/operand constraints. Temporal induction then proves
equivalence forever after one transition. The max miter is combinational.
This proves arithmetic-unit equivalence, not the stateful PE scan protocol.
"""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import time

root = Path(__file__).resolve().parents[2]
out = Path(os.environ['A100_OUT']).resolve()
formal = out / 'formal'
snapshot = formal / 'proof-inputs'
yosys = os.environ.get('YOSYS', 'yowasp-yosys')
env = dict(os.environ, XDG_CACHE_HOME=str(out / 'formal-cache'))
(formal / 'yosys-version.txt').write_text(subprocess.check_output([str(yosys), '-V'], env=env, text=True))
source_hashes = json.loads((formal / 'source-hashes.json').read_text())
for name, expected in source_hashes.items():
    assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected, f'stale formal source hash: {name}'
snapshot.mkdir(exist_ok=True)
(snapshot / 'source-hashes.json').write_text(json.dumps(source_hashes, indent=2) + '\n')
for directory in ('mac', 'cmp', 'cmpmax'):
    target = snapshot / directory
    target.mkdir(exist_ok=True)
    originals = sorted((formal / directory).glob('*.sv'))
    assert originals, directory
    for original in originals:
        (target / original.name).write_bytes(original.read_bytes())
results = []
for kind, directory, top, sequential in [('cmpeq', 'cmpmax', 'PipelineCmpMaxMiter', False), ('cmpseq', 'cmp', 'PipelineCmpMiter', True), ('macseq', 'mac', 'PipelineMacMiter', True)]:
    case = formal / directory
    proof_inputs = snapshot / directory
    files = sorted(proof_inputs.glob('*.sv'))
    assert files, case
    lines = ['read_verilog -sv ' + ' '.join(p.name for p in files), f'hierarchy -check -top {top}', 'proc', 'flatten', 'opt', 'check -assert']
    command = 'sat -verify -prove io_mismatch 0 -show-inputs -show-outputs'
    if sequential:
        command += ' -seq 1 -tempinduct -maxsteps 4'
    command += ' -dump_json counterexample.json -dump_vcd counterexample.vcd'
    lines.append(command)
    (case / 'prove.ys').write_text('\n'.join(lines) + '\n')
    (proof_inputs / 'prove.ys').write_bytes((case / 'prove.ys').read_bytes())
    with (case / 'yosys.log').open('w') as log:
        start = time.monotonic()
        run = subprocess.run([str(yosys), '-s', 'prove.ys'], cwd=proof_inputs, env=env, stdout=log, stderr=subprocess.STDOUT)
    success_marker = 'Induction step proven: SUCCESS!' if sequential else 'SAT proof finished - no model found: SUCCESS!'
    success = run.returncode == 0 and success_marker in (case / 'yosys.log').read_text()
    results.append(dict(proof_type=kind, top=top, all_inputs_equivalent=success, exit_code=run.returncode,
        scope='all cycles after one pipeline transition' if sequential else 'all combinational input bit patterns',
        method='temporal induction from unconstrained initial state' if sequential else 'combinational SAT',
        input_constraints=[], initial_state_constraints=[], sat_command=command, elapsed_seconds=time.monotonic() - start,
        proof_script_sha256=hashlib.sha256((case / 'prove.ys').read_bytes()).hexdigest(),
        proof_log_sha256=hashlib.sha256((case / 'yosys.log').read_bytes()).hexdigest(),
        driver_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        proof_input_directory=str(proof_inputs.relative_to(out)),
        rtl_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}, gold='afterAdd=false', gate='afterAdd=true', source_hashes=source_hashes))
    (formal / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    print(results[-1], flush=True)
    if not success:
        raise SystemExit(f'FAIL: {kind} ({case})')
