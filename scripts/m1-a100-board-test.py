"""Explicit NM37 A100 board test: bitwise reference check and saved evidence.

Run with the generators/fsa/python uv environment. Device writes occur only
after arguments, frozen configuration identity and output directory checks.
Use an external timeout: the existing FPGA driver polls completion indefinitely.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'fpga/baselines/nm37-a100/FSAConfig.json'
CONFIG_SHA256 = 'd8c3330d60ee335601401365e6fa4de774b53bb099dccc0afd464048b262a6ba'

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', required=True, help='Confirmed NM37 device prefix, e.g. xdma4')
    parser.add_argument('--bitstream', type=Path, required=True, help='The bitstream you loaded; recorded for traceability, not read back from hardware')
    parser.add_argument('--seq', type=int, default=16)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--out', type=Path, required=True, help='New directory; existing directories refused')
    args = parser.parse_args()
    if not args.device.startswith('xdma') or not args.device[4:].isdigit():
        parser.error('--device must have the form xdmaN')
    if args.seq <= 0 or args.seq % 16:
        parser.error('--seq must be a positive multiple of 16')
    bitstream_digest = hashlib.sha256(args.bitstream.read_bytes()).hexdigest()
    digest = hashlib.sha256(CONFIG.read_bytes()).hexdigest()
    if digest != CONFIG_SHA256:
        raise RuntimeError('Frozen A100 configuration fingerprint mismatch')
    cfg_data = json.loads(CONFIG.read_text())
    if (cfg_data['sa_rows'], cfg_data['sa_cols'], cfg_data['mem_base']) != (16, 16, 0):
        raise RuntimeError('Unexpected A100 hardware configuration')
    for suffix in ('user', 'c2h_0', 'h2c_0'):
        if not Path(f'/dev/{args.device}_{suffix}').exists():
            raise RuntimeError(f'Missing /dev/{args.device}_{suffix}')
    args.out.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(ROOT / 'generators/fsa/python'))
    import numpy as np
    import fsa as F
    from main import scaled_dot_product_attention, ref_pyeasyfloat
    F.init(str(CONFIG))
    engine = F.FPGA(control_dev=f'/dev/{args.device}_user',
                    mem_read_dev=f'/dev/{args.device}_c2h_0',
                    mem_write_dev=f'/dev/{args.device}_h2c_0')
    rng = np.random.RandomState(args.seed)
    q, k, v = [rng.rand(args.seq, 16).astype(np.float16) for _ in range(3)]
    np.savez(args.out / 'inputs.npz', Q=q, K=k, V=v)
    metadata = dict(device=args.device, seq=args.seq, seed=args.seed,
                    config_sha256=digest, clock_assumed_mhz=100,
                    supplied_bitstream=str(args.bitstream.resolve()), bitstream_sha256=bitstream_digest,
                    note='Clock identity comes from loaded bitstream, not a hardware frequency measurement.')
    (args.out / 'run.json').write_text(json.dumps(metadata, indent=2) + '\n')
    result = engine.execute(scaled_dot_product_attention(
        F.from_numpy(q), F.from_numpy(k), F.from_numpy(v.T), 16, 16))
    actual = np.ascontiguousarray(F.to_numpy(result).T)
    np.save(args.out / 'actual.npy', actual)
    counters = {name: engine.MMIO.dev_mmio_read(getattr(engine, attr)) for name, attr in {
        'execTime': 'PERF_EXEC_TIME', 'mxBubble': 'PERF_MX_BUBBLE',
        'mxActive': 'PERF_MX_ACTIVE', 'dmaActive': 'PERF_DMA_ACTIVE',
        'rawInst': 'PERF_RAW_INST', 'mxInst': 'PERF_MX_INST',
        'dmaInst': 'PERF_DMA_INST', 'fenceInst': 'PERF_FENCE_INST',
        'enq': 'PERF_ENQ_INST', 'deq': 'PERF_DEQ_INST'}.items()}
    (args.out / 'counters.json').write_text(json.dumps(counters, indent=2) + '\n')
    reference = np.ascontiguousarray(ref_pyeasyfloat(q, k, v, 16, 16, False))
    np.save(args.out / 'reference.npy', reference)
    if actual.shape != reference.shape or actual.dtype != np.float32 or reference.dtype != np.float32:
        raise RuntimeError('Unexpected output shape or dtype')
    mismatches = int(np.count_nonzero(actual.view(np.uint32) != reference.view(np.uint32)))
    passed = mismatches == 0 and counters['enq'] == counters['deq']
    summary = dict(passed=passed, bit_mismatches=mismatches, counters=counters,
                   exec_us_at_100mhz=counters['execTime'] / 100)
    (args.out / 'result.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))
    if not passed:
        raise SystemExit(1)

if __name__ == '__main__':
    main()
