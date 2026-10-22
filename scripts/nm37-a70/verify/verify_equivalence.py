#!/usr/bin/env python3
"""Prove true historical pre-P3 arithmetic equals actual P3 FPAccUnit after 3 clocks.

No operand restrictions, reset assumptions, or initial-state constraints. The miter
suppresses comparison until its three-cycle fill counter is valid. This is not a
proof of stateful accumulator scheduling, which is tested by full FSA regression.
"""
import argparse, hashlib, json, os, subprocess, time
from pathlib import Path

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
p=argparse.ArgumentParser()
p.add_argument('--rtl',type=Path,required=True)
p.add_argument('--out',type=Path,required=True)
p.add_argument('--yosys',default=os.environ.get('YOSYS','yowasp-yosys'))
a=p.parse_args()
files=sorted(a.rtl.resolve().glob('*.sv'))
assert files, 'No emitted miter RTL'
a.out.mkdir(parents=True,exist_ok=False)
for f in files: (a.out/f.name).write_bytes(f.read_bytes())
cmd='sat -verify -prove io_mismatch 0 -show-inputs -show-outputs -seq 3 -tempinduct -maxsteps 8 -dump_json counterexample.json -dump_vcd counterexample.vcd'
script='\n'.join(['read_verilog -sv '+' '.join(f.name for f in files), 'hierarchy -check -top A70AccumulatorMiter','proc','flatten','opt','check -assert',cmd])+'\n'
(a.out/'prove.ys').write_text(script)
env=dict(os.environ,XDG_CACHE_HOME=str(a.out.resolve()/'cache'))
(a.out/'yosys-version.txt').write_text(subprocess.check_output([a.yosys,'-V'],text=True,env=env))
t=time.monotonic()
with (a.out/'yosys.log').open('w') as log:
    run=subprocess.run([a.yosys,'-s','prove.ys'],cwd=a.out,env=env,stdout=log,stderr=subprocess.STDOUT)
passed=run.returncode==0 and 'Induction step proven: SUCCESS!' in (a.out/'yosys.log').read_text()
result=dict(passed=passed,exit_code=run.returncode,elapsed_seconds=time.monotonic()-t,
    top='A70AccumulatorMiter',comparison_latency_cycles=3, scope='All IEEE input bit patterns and MAC/EXP2 selections after three pipeline clocks; no scheduling or reciprocal proof',
    input_constraints=[],initial_state_constraints=[],reset_constraints=[],sat_command=cmd,
    rtl_sha256={f.name:sha(f) for f in files},driver_sha256=sha(Path(__file__)),
    proof_script_sha256=sha(a.out/'prove.ys'),proof_log_sha256=sha(a.out/'yosys.log'))
(a.out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
if not passed: raise SystemExit('Formal proof failed')
