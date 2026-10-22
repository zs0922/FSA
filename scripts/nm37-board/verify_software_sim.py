#!/usr/bin/env python3
"""Exercise the common board Runtime/software on a separately built RTL simulator.

This never opens an FPGA device. The simulator-only 0x80000000 DRAM mapping is
explicit, and production board manifest/config validation remains unchanged.
"""
import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n')


def module_origins(software, easyfloat):
    origins = {}
    for name, module in sorted(sys.modules.items()):
        expected = None
        if name in ('main', 'fa_ref') or name == 'fsa' or name.startswith('fsa.'):
            expected = software
        elif name == 'pyeasyfloat' or name.startswith('pyeasyfloat.'):
            expected = easyfloat
        if expected is not None:
            filename = getattr(module, '__file__', None)
            if filename is None or not Path(filename).resolve().is_relative_to(expected):
                raise RuntimeError(f'{name} imported outside the selected software tree: {filename}')
            path = Path(filename).resolve()
            origins[name] = {'path': str(path), 'sha256': sha(path)}
    for name in ('fsa', 'main', 'pyeasyfloat'):
        if name not in origins:
            raise RuntimeError(f'Required software module not imported: {name}')
    return origins


@contextmanager
def simulator_log(path):
    # VerilatorSimulator invokes subprocess.run without capture_output. Redirect
    # the real file descriptors so both Python messages and child output persist.
    sys.stdout.flush()
    sys.stderr.flush()
    old = (os.dup(1), os.dup(2))
    try:
        with Path(path).open('x') as stream:
            os.dup2(stream.fileno(), 1)
            os.dup2(stream.fileno(), 2)
            yield
            sys.stdout.flush()
            sys.stderr.flush()
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(old[0], 1)
        os.dup2(old[1], 2)
        os.close(old[0])
        os.close(old[1])


def run(args):
    software_root = args.software_root.resolve(strict=True)
    hardware = args.hardware_source.resolve(strict=True)
    software = software_root / 'generators/fsa/python'
    easyfloat = software_root / 'generators/easyfloat'
    binary = args.binary.resolve(strict=True)
    config = args.config.resolve(strict=True)
    out = args.out.absolute()
    if out.exists() or out.is_symlink():
        raise ValueError('Output must be a new directory')
    if args.seq <= 0 or args.seq % 16 or not 0 <= args.seed < 2**32 or args.max_cycles <= 0:
        raise ValueError('Require positive seq divisible by 16, uint32 seed and positive max-cycles')
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError('An executable simulator binary is required')
    cfg = json.loads(config.read_text())
    if (cfg['sa_rows'], cfg['sa_cols'], cfg['e_type'], cfg['a_type'], cfg['mem_base']) != (16, 16, 'fp16', 'fp32', 0x80000000):
        raise ValueError('This verification requires the 16x16 fp16/fp32 simulator config at 0x80000000')
    dram = hardware / 'generators/testchipip/src/main/resources/dramsim2_ini'
    if not dram.is_dir():
        raise ValueError('Missing hardware-source DRAMSim ini directory')
    board_path = software_root / 'scripts/nm37-board/board_test.py'
    spec = importlib.util.spec_from_file_location('nm37_common_board_runtime', board_path)
    board = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(board)
    software_inventory = board.software_files(software)
    easy_inventory = board.software_files(easyfloat)
    if not software_inventory or not easy_inventory:
        raise ValueError('Missing common software source trees')
    identity = {
        'software_root': str(software_root), 'hardware_source': str(hardware),
        'hardware_source_commit': subprocess.check_output(['git', '-C', str(hardware), 'rev-parse', 'HEAD'], text=True).strip(),
        'binary': {'path': str(binary), 'sha256': sha(binary)},
        'config': {'path': str(config), 'sha256': sha(config), 'contents': cfg},
        'board_runtime': {'path': str(board_path), 'sha256': sha(board_path)},
        'verifier_sha256': sha(Path(__file__)),
        'software_files': software_inventory, 'easyfloat_files': easy_inventory,
        'dram_sim_ini': {'path': str(dram), 'files': {str(p.relative_to(dram)): sha(p) for p in sorted(dram.rglob('*')) if p.is_file()}},
        'seq': args.seq, 'seed': args.seed, 'max_cycles': args.max_cycles,
        'hardware_tested': False,
        'workload': 'Direct call to common board Runtime.__init__/prepare; FPGA.open_device/execute are never called.',
        'memory_mapping': 'Simulator mem_base=0x80000000; board mem_base=0. Absolute DRAM addresses in instructions therefore differ from board runs, while generation/serialization logic is exactly shared.',
    }
    out.mkdir(parents=True, exist_ok=False)
    save(out / 'identity.json', identity)
    try:
        runtime = board.Runtime({'software': software, 'easyfloat': easyfloat, 'config': config}, args)
        origins = module_origins(software, easyfloat)
        workload = runtime.prepare(out)
        origins.update(module_origins(software, easyfloat))
        save(out / 'workload.json', workload)
        engine = runtime.F.VerilatorSimulator(str(binary), output_dir=str(out), max_cycles=args.max_cycles,
                                             dram_sim_ini_dir=str(dram))
        start = time.monotonic_ns()
        with simulator_log(out / 'simulator.log'):
            returned = engine.execute(runtime.kernel)
        elapsed = time.monotonic_ns() - start
        np = runtime.np
        actual = np.ascontiguousarray(runtime.F.to_numpy(returned).T)
        np.save(out / 'actual.npy', actual)
        if actual.shape != runtime.ref.shape or actual.dtype != np.float32 or runtime.ref.dtype != np.float32:
            raise RuntimeError('Unexpected output shape/type')
        mismatches = int(np.count_nonzero(actual.view(np.uint32) != runtime.ref.view(np.uint32)))
        if (out / 'inst.bin').read_bytes() != (out / 'instructions.bin').read_bytes():
            raise RuntimeError('Simulator instruction stream differs from common board Runtime serialization')
        pairs = re.findall(r'FSA: (\w+) =\s*(\d+)', (out / 'simulator.log').read_text())
        counters = {k: int(v) for k, v in pairs}
        if len(pairs) != 8 or len(counters) != 8 or counters.get('execTime', 0) <= 0:
            raise RuntimeError('Missing/repeated simulator hardware counter snapshot')
        for path, expected in [(binary, identity['binary']['sha256']), (config, identity['config']['sha256']),
                               (board_path, identity['board_runtime']['sha256'])]:
            if sha(path) != expected:
                raise RuntimeError(f'Input changed during verification: {path}')
        if board.software_files(software) != software_inventory or board.software_files(easyfloat) != easy_inventory:
            raise RuntimeError('Common software sources changed during verification')
        origins.update(module_origins(software, easyfloat))
        result = {
            'passed': mismatches == 0, 'bitwise_equal': mismatches == 0, 'bit_mismatches': mismatches,
            'hardware_tested': False, 'seq': args.seq, 'seed': args.seed, 'shape': list(actual.shape),
            'counters': counters, 'simulator_host_elapsed_ns': elapsed,
            'board_host_timing_measured': False, 'identity_sha256': sha(out / 'identity.json'),
            'actual_sha256_raw': hashlib.sha256(actual.tobytes()).hexdigest(),
            'expected_sha256_raw': hashlib.sha256(runtime.ref.tobytes()).hexdigest(),
            'actual_file_sha256': sha(out / 'actual.npy'), 'expected_file_sha256': sha(out / 'reference.npy'),
            'instructions_sha256': sha(out / 'instructions.bin'), 'instruction_words': workload['instruction_words'],
            'inputs': workload['inputs'], 'module_origins': origins,
            'scope': 'Common host kernel/instruction/software-reference compatibility with this RTL simulator; not XDMA/MMIO, PCIe, HBM initialization, board frequency, or physical board validation.',
        }
        save(out / 'result.json', result)
        if mismatches:
            raise RuntimeError(f'Output differs from common software reference at {mismatches} fp32 words')
        return result
    except Exception as exc:
        save(out / 'failed.json', {'passed': False, 'hardware_tested': False, 'error': str(exc), 'type': type(exc).__name__})
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for flag in ('software-root', 'hardware-source', 'binary', 'config', 'out'):
        p.add_argument('--' + flag, type=Path, required=True)
    p.add_argument('--seq', type=int, required=True)
    p.add_argument('--seed', type=int, default=0)
    p.add_argument('--max-cycles', type=int, default=1000000)
    result = run(p.parse_args())
    print(json.dumps({k: result[k] for k in ('passed', 'seq', 'seed', 'bit_mismatches', 'counters', 'instructions_sha256')}, indent=2))


if __name__ == '__main__':
    main()
