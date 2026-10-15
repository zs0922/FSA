"""Run actual fresh RTL simulator and assert bitwise equality with PyEasyFloat."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'generators/fsa/python'))
import numpy as np
import fsa as F
from main import scaled_dot_product_attention, ref_pyeasyfloat

p = argparse.ArgumentParser()
p.add_argument('--binary', required=True)
p.add_argument('--config', required=True)
p.add_argument('--out', required=True)
p.add_argument('--seq', required=True, type=int)
args = p.parse_args()
F.init(args.config)
cfg = F.get_config()
np.random.seed(0)
q, k, v = [np.random.rand(args.seq, cfg.sa_rows).astype(np.float16) for _ in range(3)]
engine = F.VerilatorSimulator(
    args.binary, output_dir=args.out, max_cycles=1000000,
    dram_sim_ini_dir=str(root / 'generators/testchipip/src/main/resources/dramsim2_ini'),
)
result = engine.execute(scaled_dot_product_attention(
    F.from_numpy(q), F.from_numpy(k), F.from_numpy(v.T), cfg.sa_rows, cfg.sa_cols))
actual = np.ascontiguousarray(F.to_numpy(result).T)
expected = np.ascontiguousarray(ref_pyeasyfloat(q, k, v, cfg.sa_rows, cfg.sa_cols, False))
np.save(Path(args.out) / 'actual.npy', actual)
np.save(Path(args.out) / 'expected.npy', expected)
equal = bool(np.array_equal(actual.view(np.uint32), expected.view(np.uint32)))
summary = dict(bitwise_equal=equal, shape=list(actual.shape),
               actual_sha256=hashlib.sha256(actual.tobytes()).hexdigest(),
               expected_sha256=hashlib.sha256(expected.tobytes()).hexdigest(),
               seed=0, seq=args.seq)
(Path(args.out) / 'result.json').write_text(json.dumps(summary, indent=2))
print(json.dumps(summary, indent=2))
if not equal:
    raise SystemExit('FAIL: FSA differs bitwise from PyEasyFloat')
