#!/usr/bin/env python3
"""Execute a freshly built historical FSA and check the exact arithmetic oracle."""
import argparse,hashlib,json,sys
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--source',type=Path,required=True)
p.add_argument('--binary',type=Path,required=True)
p.add_argument('--config',type=Path,required=True)
p.add_argument('--out',type=Path,required=True)
p.add_argument('--seq',type=int,required=True)
p.add_argument('--seed',type=int,default=0)
p.add_argument('--max-cycles',type=int,default=10000000)
a=p.parse_args()
if a.seq <= 0 or a.max_cycles <= 0: p.error('--seq and --max-cycles must be positive')
if a.out.exists(): p.error('--out must be a new directory')
sys.path.insert(0,str(a.source.resolve()/'generators/fsa/python'))
import numpy as np
import fsa as F
from main import scaled_dot_product_attention,ref_pyeasyfloat
F.init(str(a.config.resolve()))
cfg=F.get_config()
if a.seq % cfg.sa_rows or a.seq % cfg.sa_cols: p.error('--seq must be divisible by array dimensions')
np.random.seed(a.seed)
q,k,v=[np.random.rand(a.seq,cfg.sa_rows).astype(np.float16) for _ in range(3)]
a.out.mkdir(parents=True,exist_ok=False)
for n,x in [('q',q),('k',k),('v',v)]:np.save(a.out/(n+'.npy'),x)
engine=F.VerilatorSimulator(str(a.binary.resolve()),output_dir=str(a.out.resolve()),max_cycles=a.max_cycles,
    dram_sim_ini_dir=str(a.source.resolve()/'generators/testchipip/src/main/resources/dramsim2_ini'))
result=engine.execute(scaled_dot_product_attention(F.from_numpy(q),F.from_numpy(k),F.from_numpy(v.T),cfg.sa_rows,cfg.sa_cols))
actual=np.ascontiguousarray(F.to_numpy(result).T)
expected=np.ascontiguousarray(ref_pyeasyfloat(q,k,v,cfg.sa_rows,cfg.sa_cols,False))
np.save(a.out/'actual.npy',actual);np.save(a.out/'expected.npy',expected)
passed=actual.shape==expected.shape and bool(np.array_equal(actual.view(np.uint32),expected.view(np.uint32)))
summary=dict(max_cycles=a.max_cycles,bitwise_equal=passed,shape=list(actual.shape),seed=a.seed,seq=a.seq,array_rows=cfg.sa_rows,
 binary_sha256=hashlib.sha256(a.binary.read_bytes()).hexdigest(),config_sha256=hashlib.sha256(a.config.read_bytes()).hexdigest(),
 actual_sha256=hashlib.sha256(actual.tobytes()).hexdigest(),expected_sha256=hashlib.sha256(expected.tobytes()).hexdigest(),
 input_sha256={n:hashlib.sha256(x.tobytes()).hexdigest() for n,x in [('q',q),('k',k),('v',v)]})
(a.out/'result.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary,indent=2))
if not passed:raise SystemExit('FAIL: FSA output differs bitwise from PyEasyFloat')
