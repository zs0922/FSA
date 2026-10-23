"""Synthetic evidence only: verifies statistics and rejection, never touches a board."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import summarize_results as s


def save(path, value):
    path.write_text(json.dumps(value) + '\n')


class SummaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'results'
        self.root.mkdir()
        self.folders = {}
        for cfg, mhz in s.CLOCKS.items():
            folder = self.root / cfg
            folder.mkdir()
            self.folders[cfg] = folder
            array = np.zeros((16, 16), dtype=np.float16)
            np.savez(folder / 'inputs.npz', Q=array, K=array, V=array)
            reference = np.zeros((16, 16), dtype=np.float32)
            np.save(folder / 'reference.npy', reference)
            (folder / 'instructions.bin').write_bytes(bytes(8))
            manifest = dict(schema_version=2, configuration_id=cfg, clock_mhz=mhz,
                hardware_source_commit='a' * 40, build_id='synthetic-' + cfg,
                source_identity_kind='built_commit', bitstream={'sha256': 'b' * 64},
                config={'sha256': 'c' * 64}, software={'files': {'test.py': 'd' * 64},
                'easyfloat': {'files': {'test.py': 'e' * 64}}})
            inputs = {name: dict(shape=[16, 16], dtype=array.dtype.str,
                sha256_raw_c_order=s.hashlib.sha256(array.tobytes()).hexdigest()) for name in ('Q', 'K', 'V')}
            meta = dict(schema_version=1, dry_run=False, hardware_tested=True,
                configuration_id=cfg, manifest=manifest, clock_mhz_assumed=mhz,
                loaded_bitstream_sha256_declared='b' * 64, repeat=2, warmup=1, seq=16, seed=0,
                workload=dict(instruction_words=2, instructions_sha256=s.sha(folder / 'instructions.bin'),
                    reference_sha256=s.sha(folder / 'reference.npy'), inputs=inputs, runtime_versions={'numpy': 'test'}),
                hostname='fixture-not-a-board', python='fixture', host_execute_boundary='fixture', counter_scope='fixture')
            save(folder / 'run.json', meta)
            runs = []
            for i, cycles in enumerate([70000, 700, 1400]):
                sub = folder / f'run-{i:03d}'
                sub.mkdir()
                np.save(sub / 'actual.npy', reference)
                before = {name: 0 for name in s.COUNTERS}
                before.update(enq=i * 2, deq=i * 2)
                counters = dict(before, execTime=cycles, mxBubble=cycles // 10,
                                mxActive=cycles // 2, dmaActive=cycles // 4, enq=(i+1)*2, deq=(i+1)*2)
                run = dict(passed=True, bit_mismatches=0, warmup=i == 0, counters=counters,
                    counters_before=before, instruction_words_expected=2, instruction_word_deltas={'enq': 2, 'deq': 2},
                    state_before=2, monotonic_start_ns=100, monotonic_end_ns=100+cycles*1000,
                    exec_us=cycles/mhz, host_execute_ms=cycles/1000, actual_sha256=s.sha(sub/'actual.npy'))
                runs.append(run)
                save(sub / 'result.json', run)
            top = dict(passed=True, hardware_tested=True, runs=runs, measurement_count=2, warmup_count=1)
            for field in ('exec_us', 'host_execute_ms'):
                top[field] = s.stats([r[field] for r in runs[1:]])
            save(folder / 'result.json', top)

    def mutate(self, name, fn):
        path = self.folders['A100'] / name
        value = s.read(path)
        fn(value)
        save(path, value)

    def test_statistics_and_output(self):
        result = s.collect(self.root, 2)
        rows = result['rows']
        self.assertEqual(rows[0]['execTime_median'], 1050)
        self.assertEqual(rows[0]['exec_us_median'], 15)
        self.assertAlmostEqual(rows[1]['fpga_speedup'], 100/70)
        self.assertAlmostEqual(rows[2]['fpga_speedup'], 110/70)
        self.assertEqual(rows[0]['bubble_pct_median'], 10)
        self.assertEqual(len(result['raw_measurements']), 6)
        out = self.root.parent / 'summary'
        s.write_report(result, out)
        self.assertEqual(len(list(out.iterdir())), 4)
        with self.assertRaises(FileExistsError):
            s.write_report(result, out)

    def test_dryrun_and_smoke_excluded(self):
        for name, data in [('dry', {'dry_run': True}), ('smoke', {'dry_run': False, 'repeat': 1})]:
            folder = self.root / name
            folder.mkdir()
            save(folder / 'run.json', data)
        self.assertEqual(len(s.collect(self.root, 2)['excluded']), 2)

    def test_failure_and_incomplete_rejected(self):
        path = self.folders['A100'] / 'failed.json'
        save(path, {'passed': False})
        with self.assertRaisesRegex(ValueError, 'failed.json'):
            s.collect(self.root, 2)
        path.unlink()
        (self.folders['A100'] / 'result.json').unlink()
        with self.assertRaises(ValueError):
            s.collect(self.root, 2)

    def test_workload_policy_and_identity_mismatch(self):
        path = self.folders['A100'] / 'run.json'
        original = s.read(path)
        mutations = [lambda x: x.update(hostname='another-host'),
                     lambda x: x['manifest']['software']['files'].update(other='x'),
                     lambda x: x['workload']['runtime_versions'].update(numpy='other'),
                     lambda x: x.update(clock_mhz_assumed=70),
                     lambda x: x.update(loaded_bitstream_sha256_declared='wrong')]
        for fn in mutations:
            with self.subTest(fn=fn):
                changed = copy.deepcopy(original)
                fn(changed)
                save(path, changed)
                with self.assertRaises(ValueError):
                    s.collect(self.root, 2)
        save(path, original)

    def test_actual_and_input_tamper_rejected(self):
        folder = self.folders['A100']
        actual = folder / 'run-001/actual.npy'
        a = np.load(actual)
        a[0, 0] = -0.0  # Numeric equality is insufficient: signed zero differs bitwise.
        np.save(actual, a)
        top = s.read(folder / 'result.json')
        top['runs'][1]['actual_sha256'] = s.sha(actual)
        save(folder / 'run-001/result.json', top['runs'][1])
        save(folder / 'result.json', top)
        with self.assertRaisesRegex(ValueError, 'bit mismatch'):
            s.collect(self.root, 2)

    def test_input_corruption(self):
        folder = self.folders['A100']
        a = np.ones((16, 16), dtype=np.float16)
        np.savez(folder / 'inputs.npz', Q=a, K=a, V=a)
        with self.assertRaisesRegex(ValueError, 'input evidence'):
            s.collect(self.root, 2)

    def test_duplicate_and_missing(self):
        import shutil
        shutil.copytree(self.folders['A100'], self.root / 'retry')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            s.collect(self.root, 2)
        shutil.rmtree(self.root / 'retry')
        shutil.rmtree(self.folders['A100'])
        with self.assertRaisesRegex(ValueError, 'all three'):
            s.collect(self.root, 2)

    def test_counter_delta_and_timing_rejected(self):
        folder = self.folders['A100']
        original = s.read(folder / 'result.json')
        for fn in [lambda r: r['instruction_word_deltas'].update(enq=1),
                   lambda r: r.update(exec_us=123), lambda r: r.update(host_execute_ms=float('nan')),
                   lambda r: r.update(warmup=True), lambda r: r['counters'].update(execTime=2**32)]:
            top = copy.deepcopy(original)
            fn(top['runs'][1])
            save(folder / 'result.json', top)
            save(folder / 'run-001/result.json', top['runs'][1])
            with self.assertRaises(ValueError):
                s.collect(self.root, 2)

    def test_cli_and_failed_cli_no_report(self):
        import subprocess
        import sys
        out = self.root.parent / 'cli-summary'
        command = [sys.executable, str(Path(s.__file__)), '--results', str(self.root),
                   '--min-repeat', '2', '--out', str(out)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((out / 'comparison.md').is_file())
        self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
        (self.folders['A100'] / 'instructions.bin').write_bytes(b'corrupt')
        failed_out = self.root.parent / 'failed-summary'
        command[-1] = str(failed_out)
        self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
        self.assertFalse(failed_out.exists())

    def test_cumulative_counter_wrap(self):
        folder = self.folders['A100']
        top = s.read(folder / 'result.json')
        r = top['runs'][1]
        r['counters_before'].update(enq=2**32 - 1, deq=2**32 - 1)
        r['counters'].update(enq=1, deq=1)
        save(folder / 'run-001/result.json', r)
        save(folder / 'result.json', top)
        self.assertEqual(len(s.collect(self.root, 2)['rows']), 3)

    def test_zero_bubble_baseline(self):
        folder = self.folders['A70-P3']
        top = s.read(folder / 'result.json')
        for i, r in enumerate(top['runs']):
            r['counters']['mxBubble'] = 0
            save(folder / f'run-{i:03d}/result.json', r)
        save(folder / 'result.json', top)
        result = s.collect(self.root, 2)
        self.assertIsNone(result['rows'][1]['bubble_change_pct'])


if __name__ == '__main__':
    unittest.main()
