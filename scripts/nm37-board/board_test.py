#!/usr/bin/env python3
"""Manifest-bound NM37 test. Never programs hardware; use an external timeout."""
import argparse
import fcntl
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import socket
import stat
import statistics
import struct
import sys
import time

CLOCKS = {'A70-P2': 70, 'A70-P3': 70, 'A100': 100, 'A110-APB55': 110}
COUNTERS = {
    'execTime': 'PERF_EXEC_TIME', 'mxBubble': 'PERF_MX_BUBBLE',
    'mxActive': 'PERF_MX_ACTIVE', 'dmaActive': 'PERF_DMA_ACTIVE',
    'rawInst': 'PERF_RAW_INST', 'mxInst': 'PERF_MX_INST',
    'dmaInst': 'PERF_DMA_INST', 'fenceInst': 'PERF_FENCE_INST',
    'enq': 'PERF_ENQ_INST', 'deq': 'PERF_DEQ_INST',
}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n')


def digest_ok(value, length=64):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{%d}' % length, value) is not None


def inside(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError('Artifact paths must be nonempty relative paths')
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError('Artifact path escapes manifest directory')
    return path


def software_files(root):
    """Source inventory; excludes installed environments, bytecode and VCS metadata."""
    files = {}
    for directory, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in ('.venv', '__pycache__', '.git', 'target', 'build', 'dist', '.pytest_cache', '.mypy_cache', '.ruff_cache') and not d.endswith('.egg-info'))
        for name in sorted(names):
            if name.endswith('.py') or name in ('uv.lock', 'pyproject.toml', 'README.md'):
                path = Path(directory) / name
                if path.is_symlink():
                    raise ValueError('Software source symlinks are not permitted')
                files[path.relative_to(root).as_posix()] = sha(path)
    return files


def validate_manifest(path):
    path = Path(path).resolve(strict=True)
    manifest = json.loads(path.read_text())
    required = {'schema_version', 'configuration_id', 'clock_mhz', 'hardware_source_commit',
                'build_id', 'source_identity_kind', 'bitstream', 'config', 'software'}
    if set(manifest) != required or type(manifest['schema_version']) is not int or manifest['schema_version'] != 2:
        raise ValueError('Manifest schema keys/version mismatch')
    identity, clock = manifest['configuration_id'], manifest['clock_mhz']
    if identity not in CLOCKS or type(clock) is not int or CLOCKS[identity] != clock:
        raise ValueError('Configuration/frequency mismatch')
    if manifest['source_identity_kind'] not in ('built_commit', 'reconstructed_source_snapshot'):
        raise ValueError('Explicit source identity provenance required')
    if not digest_ok(manifest['hardware_source_commit'], 40):
        raise ValueError('A full hardware source commit is required')
    if not isinstance(manifest['build_id'], str) or not manifest['build_id'].strip():
        raise ValueError('build_id is required')
    resolved = {}
    for key in ('bitstream', 'config'):
        item = manifest[key]
        if set(item) != {'path', 'sha256'} or not digest_ok(item['sha256']):
            raise ValueError(f'Invalid {key} record')
        resolved[key] = inside(path.parent, item['path'])
        if not resolved[key].is_file() or resolved[key].stat().st_size == 0 or sha(resolved[key]) != item['sha256']:
            raise ValueError(f'{key} SHA-256 mismatch or empty file')
    software = manifest['software']
    if set(software) != {'path', 'files', 'easyfloat'} or not isinstance(software['files'], dict):
        raise ValueError('Invalid software inventory')
    resolved['software'] = inside(path.parent, software['path'])
    actual_files = software_files(resolved['software'])
    if not actual_files or actual_files != software['files']:
        raise ValueError('Software inventory mismatch')
    for needed in ('main.py', 'fa_ref.py', 'fsa/engine.py', 'fsa/xdma_mmio.py', 'pyproject.toml', 'uv.lock'):
        if needed not in actual_files:
            raise ValueError(f'Software inventory missing {needed}')
    easy = software['easyfloat']
    if set(easy) != {'path', 'files'} or not isinstance(easy['files'], dict):
        raise ValueError('Invalid easyfloat inventory')
    resolved['easyfloat'] = inside(path.parent, easy['path'])
    if resolved['easyfloat'] != (resolved['software'] / '../../easyfloat').resolve():
        raise ValueError('easyfloat must preserve the locked ../../easyfloat source path')
    if software_files(resolved['easyfloat']) != easy['files'] or not easy['files']:
        raise ValueError('easyfloat source inventory mismatch')
    for needed in ('pyeasyfloat/__init__.py', 'pyeasyfloat/backend.py', 'pyproject.toml', 'README.md'):
        if needed not in easy['files']:
            raise ValueError(f'easyfloat inventory missing {needed}')
    config = json.loads(resolved['config'].read_text())
    expected = {'sa_rows', 'sa_cols', 'inst_queue_size', 'e_type', 'a_type', 'mem_base',
                'mem_size', 'mem_align', 'spad_base', 'spad_size', 'acc_base', 'acc_size'}
    if set(config) != expected:
        raise ValueError('Unsupported hardware config keys')
    if (config['sa_rows'], config['sa_cols'], config['e_type'], config['a_type']) != (16, 16, 'fp16', 'fp32'):
        raise ValueError('This workload requires 16x16 fp16/fp32')
    for key in expected - {'e_type', 'a_type'}:
        if type(config[key]) is not int or config[key] < 0:
            raise ValueError(f'Invalid config value: {key}')
    for key in ('inst_queue_size', 'mem_size', 'mem_align', 'spad_size', 'acc_size'):
        if config[key] <= 0:
            raise ValueError(f'Invalid config value: {key}')
    if config['mem_base'] != 0 or config['spad_base'] != 0 or config['acc_base'] != 0:
        raise ValueError('This NM37 test supports zero-based hardware memories only')
    return manifest, resolved, config


def probe_device(device, bdf):
    """Read sysfs/stat only, require all three actual character devices at the BDF."""
    result = {'bdf': bdf, 'device': device, 'nodes': {}}
    pci = Path('/sys/bus/pci/devices') / bdf
    result['vendor'] = (pci / 'vendor').read_text().strip()
    result['pci_device'] = (pci / 'device').read_text().strip()
    if result['vendor'] != '0x10ee' or result['pci_device'] != '0x9038':
        raise ValueError('Expected NM37 FSA Xilinx XDMA 10ee:9038; verify loaded image and BDF')
    for suffix in ('user', 'h2c_0', 'c2h_0'):
        node = Path(f'/dev/{device}_{suffix}')
        info = node.stat()
        if not stat.S_ISCHR(info.st_mode):
            raise ValueError(f'Not a character device: {node}')
        target = (Path('/sys/dev/char') / f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}').resolve(strict=True)
        if bdf not in target.parts or target.name != node.name:
            raise ValueError(f'Device/BDF mismatch: {node} -> {target}')
        result['nodes'][suffix] = {'path': str(node), 'sysfs': str(target)}
    driver = (pci / 'driver').resolve(strict=True)
    if driver.name != 'xdma':
        raise ValueError('PCI device is not bound to the xdma driver')
    result['driver'] = str(driver)
    version = Path('/sys/module/xdma/version')
    result['driver_version'] = version.read_text().strip() if version.exists() else 'not exposed by module'
    return result


def instruction_bytes(kernel):
    words = [int(word) for inst in kernel.instructions for word in inst.to_ui32_list()]
    return struct.pack(f'<{len(words)}I', *words)


def timing_summary(counters, clock_mhz, start_ns, end_ns):
    if type(clock_mhz) is not int or clock_mhz not in (70, 100, 110) or end_ns < start_ns:
        raise ValueError('Invalid frequency/monotonic time')
    if set(counters) != set(COUNTERS) or any(type(v) is not int or not 0 <= v < 2**32 for v in counters.values()):
        raise ValueError('Expected all ten uint32 hardware counters')
    return {'exec_us': counters['execTime'] / clock_mhz,
            'host_execute_ms': (end_ns - start_ns) / 1e6}


class Runtime:
    def __init__(self, paths, args):
        if sys.byteorder != 'little':
            raise ValueError('Existing FPGA engine serializes native-endian instructions; little-endian host required')
        sys.path.insert(0, str(paths['software']))
        sys.path.insert(0, str(paths['easyfloat']))
        import numpy as np
        import fsa as F
        import pyeasyfloat
        if not Path(pyeasyfloat.__file__).resolve().is_relative_to(paths['easyfloat'].resolve()):
            raise ValueError('pyeasyfloat was imported from outside the verified source tree')
        from main import scaled_dot_product_attention, ref_pyeasyfloat
        self.np, self.F, self.args = np, F, args
        self.attention, self.reference = scaled_dot_product_attention, ref_pyeasyfloat
        F.init(str(paths['config']))
        self.engine = None

    def prepare(self, out):
        np, F = self.np, self.F
        rng = np.random.RandomState(self.args.seed)
        self.q, self.k, self.v = [rng.rand(self.args.seq, 16).astype(np.float16) for _ in range(3)]
        np.savez(out / 'inputs.npz', Q=self.q, K=self.k, V=self.v)
        self.kernel = self.attention(F.from_numpy(self.q), F.from_numpy(self.k), F.from_numpy(self.v.T), 16, 16)
        self.original_data = [tensor.data for tensor in self.kernel.input]
        packed = instruction_bytes(self.kernel)
        self.instruction_word_count = len(packed) // 4
        if not 0 < self.instruction_word_count < 2**32:
            raise ValueError('Instruction count outside supported 32-bit single-run range')
        (out / 'instructions.bin').write_bytes(packed)
        # Reference occurs before opening any MMIO and is excluded from host execution timing.
        self.ref = np.ascontiguousarray(self.reference(self.q, self.k, self.v, 16, 16, False))
        np.save(out / 'reference.npy', self.ref)
        inputs = {name: {'shape': list(array.shape), 'dtype': array.dtype.str,
                         'sha256_raw_c_order': hashlib.sha256(array.tobytes(order='C')).hexdigest()}
                  for name, array in [('Q', self.q), ('K', self.k), ('V', self.v)]}
        return {'inputs': inputs, 'runtime_versions': {name: importlib.metadata.version(name) for name in ('numpy', 'torch', 'pyeasyfloat')},
                'instructions_sha256': hashlib.sha256(packed).hexdigest(),
                'instruction_words': len(packed) // 4, 'reference_sha256': sha(out / 'reference.npy')}

    def open_device(self):
        dev = self.args.device
        self.engine = self.F.FPGA(control_dev=f'/dev/{dev}_user', mem_read_dev=f'/dev/{dev}_c2h_0',
                                  mem_write_dev=f'/dev/{dev}_h2c_0')

    def execute(self, out, clock_mhz):
        engine, np = self.engine, self.np
        # Unlike the existing engine (which checks after writes), refuse active/error BEFORE any input write.
        state = int(engine.MMIO.dev_mmio_read(engine.STATE_OFFSET))
        if state not in (0, 2):
            raise RuntimeError(f'Before input writes: expected idle/done, got state {state}; reset/reload before retry')
        before = {name: int(engine.MMIO.dev_mmio_read(getattr(engine, attr))) for name, attr in COUNTERS.items()}
        if before['enq'] != before['deq']:
            raise RuntimeError('Outstanding instruction words before input writes; reset/reload before retry')
        # Previous output is present in kernel.input; never turn it into an additional input on repeats.
        for tensor, data in zip(self.kernel.input, self.original_data):
            tensor.data = data
        start = time.monotonic_ns()
        result = engine.execute(self.kernel)
        end = time.monotonic_ns()
        actual = np.ascontiguousarray(self.F.to_numpy(result).T)
        np.save(out / 'actual.npy', actual)
        counters = {name: int(engine.MMIO.dev_mmio_read(getattr(engine, attr))) for name, attr in COUNTERS.items()}
        if int(engine.MMIO.dev_mmio_read(engine.STATE_OFFSET)) != 2:
            raise RuntimeError('State changed while collecting counters; discard run')
        if actual.shape != self.ref.shape or actual.dtype != np.float32 or self.ref.dtype != np.float32:
            raise RuntimeError('Unexpected output shape/type')
        mismatches = int(np.count_nonzero(actual.view(np.uint32) != self.ref.view(np.uint32)))
        word_deltas = {name: (counters[name] - before[name]) % 2**32 for name in ('enq', 'deq')}
        words_ok = all(value == self.instruction_word_count for value in word_deltas.values())
        result = {'passed': mismatches == 0 and counters['enq'] == counters['deq'] and words_ok,
                  'instruction_word_deltas': word_deltas, 'instruction_words_expected': self.instruction_word_count,
                  'bit_mismatches': mismatches, 'counters': counters, 'state_before': state,
                  'counters_before': before, 'monotonic_start_ns': start, 'monotonic_end_ns': end,
                  'actual_sha256': sha(out / 'actual.npy'), **timing_summary(counters, clock_mhz, start, end)}
        save(out / 'result.json', result)
        return result

    def close(self):
        if self.engine is not None:
            self.engine.MMIO.close()


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--device', required=True, help='Confirmed xdmaN, never auto-selected')
    p.add_argument('--bdf', required=True, help='Confirmed full PCI BDF, e.g. 0000:81:00.0')
    p.add_argument('--loaded-bitstream-sha256', required=True, help='Operator declaration, NOT hardware readback')
    p.add_argument('--seq', type=int, default=16)
    p.add_argument('--seed', type=int, default=0)
    p.add_argument('--repeat', type=int, default=1)
    p.add_argument('--warmup', type=int, default=0)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--dry-run', action='store_true', help='Verify files and arguments only; no device/sysfs access or FSA imports')
    return p


def run(args, backend_factory=Runtime, device_probe=probe_device):
    manifest, paths, _ = validate_manifest(args.manifest)
    if not re.fullmatch(r'xdma[0-9]+', args.device) or not re.fullmatch(r'[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]', args.bdf):
        raise ValueError('Explicit xdmaN and lowercase full PCI BDF required')
    if args.loaded_bitstream_sha256 != manifest['bitstream']['sha256']:
        raise ValueError('Declared loaded bitstream does not match manifest')
    if args.seq <= 0 or args.seq % 16 or not 0 <= args.seed < 2**32 or args.repeat < 1 or args.warmup < 0:
        raise ValueError('Invalid seq/seed/repeat/warmup')
    if args.out.exists() or args.out.is_symlink():
        raise ValueError('Output directory must not exist')
    metadata = {'schema_version': 1, 'manifest': manifest, 'manifest_sha256': sha(args.manifest),
                'configuration_id': manifest['configuration_id'], 'clock_mhz_assumed': manifest['clock_mhz'],
                'device': args.device, 'bdf': args.bdf, 'seq': args.seq, 'seed': args.seed,
                'warmup': args.warmup, 'repeat': args.repeat, 'hostname': socket.gethostname(),
                'python': sys.version, 'dry_run': args.dry_run, 'hardware_tested': False,
                'loaded_bitstream_identity': 'operator declaration; no hardware readback/frequency measurement',
                'loaded_bitstream_sha256_declared': args.loaded_bitstream_sha256,
                'host_execute_boundary': 'entry/return of FPGA.execute: serialization + input HBM writes + /tmp debug writes + SET_ACTIVE + queue submission + polling/printed counters + output HBM reads; excludes kernel compilation, reference, file summaries',
                'counter_scope': 'SET_ACTIVE done->active clears eight perf counters, not cumulative enq/deq. Preserve all ten raw uint32 values; enq/deq per-run modulo 2^32 deltas must equal submitted word count (<2^32). No active-cycle overflow correction; mxBubble is ready&&!valid, not total array idle.',
                'external_timeout_required': True}
    args.out.mkdir(parents=True, exist_ok=False)
    save(args.out / 'run.json', metadata)
    if args.dry_run:
        return {'dry_run': True, 'hardware_tested': False, 'files_verified': True}
    backend = None
    try:
        # Lock is cooperative; operators must exclude all other tools accessing this PCI device.
        with open(f'/tmp/fsa-nm37-board-{args.bdf}.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            metadata['device_probe'] = device_probe(args.device, args.bdf)
            backend = backend_factory(paths, args)
            metadata['workload'] = backend.prepare(args.out)
            # Recheck immutable input files after reference preparation and before device opening.
            again, _, _ = validate_manifest(args.manifest)
            if again != manifest:
                raise ValueError('Manifest changed during preparation')
            metadata['device_probe'] = device_probe(args.device, args.bdf)
            save(args.out / 'run.json', metadata)
            backend.open_device()
            results = []
            for index in range(args.warmup + args.repeat):
                sub = args.out / f'run-{index:03d}'
                sub.mkdir()
                result = backend.execute(sub, manifest['clock_mhz'])
                result['warmup'] = index < args.warmup
                results.append(result)
                save(sub / 'result.json', result)
                if not result['passed']:
                    raise RuntimeError(f'Functional/counter check failed in run {index}')
            measured = results[args.warmup:]
            summary = {'passed': True, 'hardware_tested': True, 'runs': results,
                       'measurement_count': len(measured), 'warmup_count': args.warmup}
            for field in ('exec_us', 'host_execute_ms'):
                values = [r[field] for r in measured]
                summary[field] = {'min': min(values), 'median': statistics.median(values), 'max': max(values)}
            save(args.out / 'result.json', summary)
            metadata['hardware_tested'] = True
            save(args.out / 'run.json', metadata)
            return summary
    except Exception as exc:
        save(args.out / 'failed.json', {'passed': False, 'error': str(exc), 'type': type(exc).__name__})
        raise
    finally:
        if backend is not None:
            backend.close()


if __name__ == '__main__':
    print(json.dumps(run(parser().parse_args()), indent=2, allow_nan=False))
