#!/usr/bin/env python3
"""Offline validation: no FPGA/dev nodes or third-party packages required."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import board_test as b


class BoardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        (self.root / 'image.bit').write_bytes(b'test bitstream fixture, never hardware')
        cfg = dict(sa_rows=16, sa_cols=16, inst_queue_size=256, e_type='fp16', a_type='fp32',
                   mem_base=0, mem_size=268435456, mem_align=8, spad_base=0, spad_size=3072,
                   acc_base=0, acc_size=1088)
        b.save(self.root / 'config.json', cfg)
        sw = self.root / 'software/generators/fsa/python'
        (sw / 'fsa').mkdir(parents=True)
        easy = self.root / 'software/generators/easyfloat'
        (easy / 'pyeasyfloat').mkdir(parents=True)
        for name in ('pyeasyfloat/__init__.py', 'pyeasyfloat/backend.py', 'pyproject.toml', 'README.md'):
            (easy / name).write_text('# easyfloat fixture\n')
        for path in ('main.py', 'fa_ref.py', 'fsa/engine.py', 'fsa/xdma_mmio.py', 'pyproject.toml', 'uv.lock'):
            (sw / path).write_text('# test fixture\n')
        self.manifest = {'schema_version': 2, 'configuration_id': 'A100', 'clock_mhz': 100,
                         'hardware_source_commit': 'a' * 40, 'build_id': 'test-not-a-real-build', 'source_identity_kind': 'built_commit',
                         'bitstream': {'path': 'image.bit', 'sha256': b.sha(self.root / 'image.bit')},
                         'config': {'path': 'config.json', 'sha256': b.sha(self.root / 'config.json')},
                         'software': {'path': 'software/generators/fsa/python', 'files': b.software_files(sw),
                                      'easyfloat': {'path': 'software/generators/easyfloat', 'files': b.software_files(easy)}}}
        self.path = self.root / 'manifest.json'
        self.write()
        self.args = SimpleNamespace(manifest=self.path, device='xdma0', bdf='0000:81:00.0',
            loaded_bitstream_sha256=self.manifest['bitstream']['sha256'], seq=16, seed=0, repeat=1,
            warmup=0, out=self.root / 'output', dry_run=False)
        self.factory, self.probe = Mock(), Mock(return_value={'test_mock': True})

    def write(self):
        b.save(self.path, self.manifest)

    def rejected_before_device(self):
        with self.assertRaises((ValueError, FileNotFoundError)):
            b.run(self.args, self.factory, self.probe)
        self.factory.assert_not_called()
        self.probe.assert_not_called()

    def test_bitstream_corruption_never_opens_device(self):
        (self.root / 'image.bit').write_bytes(b'wrong')
        self.rejected_before_device()

    def test_config_corruption_never_opens_device(self):
        (self.root / 'config.json').write_text('{}')
        self.rejected_before_device()

    def test_unexpected_frequency_never_opens_device(self):
        for value in (0, 55, 110, True, float('nan'), '100'):
            with self.subTest(value=value):
                self.manifest['clock_mhz'] = value
                # NaN is invalid JSON but also rejected by type gate when parsed.
                self.path.write_text(json.dumps(self.manifest))
                self.rejected_before_device()

    def test_missing_provenance_never_opens_device(self):
        self.manifest['source_identity_kind'] = 'guessed'
        self.write()
        self.rejected_before_device()

    def test_missing_identity_never_opens_device(self):
        self.manifest['hardware_source_commit'] = 'ef269122'
        self.write()
        self.rejected_before_device()

    def test_misdeclared_loaded_bit_never_opens_device(self):
        self.args.loaded_bitstream_sha256 = 'f' * 64
        self.rejected_before_device()

    def test_source_mutation_never_opens_device(self):
        (self.root / 'software/generators/fsa/python/main.py').write_text('changed')
        self.rejected_before_device()

    def test_easyfloat_mutation_never_opens_device(self):
        (self.root / 'software/generators/easyfloat/pyeasyfloat/backend.py').write_text('changed')
        self.rejected_before_device()

    def test_extra_source_never_opens_device(self):
        (self.root / 'software/generators/fsa/python/extra.py').write_text('new')
        self.rejected_before_device()

    def test_unsafe_config_even_with_matching_hash(self):
        cfg = json.loads((self.root / 'config.json').read_text())
        cfg['e_type'] = '__import__("os").system("false")'
        b.save(self.root / 'config.json', cfg)
        self.manifest['config']['sha256'] = b.sha(self.root / 'config.json')
        self.write()
        self.rejected_before_device()

    def test_path_escape(self):
        self.manifest['bitstream']['path'] = '/etc/passwd'
        self.write()
        self.rejected_before_device()

    def test_missing_file(self):
        (self.root / 'image.bit').unlink()
        self.rejected_before_device()

    def test_existing_output(self):
        self.args.out.mkdir()
        self.rejected_before_device()

    def test_invalid_device_and_bdf(self):
        self.args.device = 'xdma0_user'
        self.rejected_before_device()
        self.args.device = 'xdma0'
        self.args.bdf = '81:00.0'
        self.rejected_before_device()

    def test_dry_run_does_not_probe_or_import_runtime(self):
        self.args.dry_run = True
        result = b.run(self.args, self.factory, self.probe)
        self.assertFalse(result['hardware_tested'])
        self.assertTrue(result['files_verified'])
        self.factory.assert_not_called()
        self.probe.assert_not_called()
        self.assertFalse(json.loads((self.args.out / 'run.json').read_text())['hardware_tested'])

    def test_bdf_probe_failure_does_not_open_backend(self):
        self.probe.side_effect = ValueError('BDF mismatch')
        with self.assertRaises(ValueError):
            b.run(self.args, self.factory, self.probe)
        self.factory.assert_not_called()
        self.assertTrue((self.args.out / 'failed.json').exists())

    def backend(self):
        backend = self.factory.return_value
        backend.prepare.return_value = {'inputs': {'Q': {'sha256_raw_c_order': 'b' * 64}},
                                        'instructions_sha256': 'c' * 64}
        backend.execute.side_effect = [
            {'passed': True, 'exec_us': 999, 'host_execute_ms': 999},
            {'passed': True, 'exec_us': 10, 'host_execute_ms': 20},
            {'passed': True, 'exec_us': 20, 'host_execute_ms': 30},
        ]
        return backend

    def test_warmup_excluded_from_summary_and_manifest_identity_saved(self):
        backend = self.backend()
        self.args.warmup, self.args.repeat = 1, 2
        result = b.run(self.args, self.factory, self.probe)
        self.assertEqual(result['exec_us'], {'min': 10, 'median': 15, 'max': 20})
        self.assertEqual(result['host_execute_ms']['median'], 25)
        self.assertEqual(backend.execute.call_count, 3)
        backend.open_device.assert_called_once()
        backend.close.assert_called_once()
        metadata = json.loads((self.args.out / 'run.json').read_text())
        self.assertEqual(metadata['manifest_sha256'], b.sha(self.path))
        self.assertEqual(metadata['manifest'], self.manifest)
        self.assertTrue(metadata['hardware_tested'])
        self.assertEqual(self.probe.call_count, 2)

    def test_source_changes_during_prepare_prevent_device_open(self):
        backend = self.backend()
        def mutate(out):
            (self.root / 'software/generators/fsa/python/main.py').write_text('mutated after initial verify')
            return {}
        backend.prepare.side_effect = mutate
        with self.assertRaises(ValueError):
            b.run(self.args, self.factory, self.probe)
        backend.open_device.assert_not_called()
        backend.execute.assert_not_called()
        backend.close.assert_called_once()

    def test_functional_fail_stops_repetition(self):
        backend = self.backend()
        backend.execute.side_effect = [{'passed': False, 'exec_us': 1, 'host_execute_ms': 2}]
        self.args.repeat = 3
        with self.assertRaises(RuntimeError):
            b.run(self.args, self.factory, self.probe)
        self.assertEqual(backend.execute.call_count, 1)
        self.assertFalse((self.args.out / 'result.json').exists())
        self.assertTrue((self.args.out / 'run-000/result.json').exists())

    def test_frequency_and_units(self):
        counters = {key: 0 for key in b.COUNTERS}
        counters['execTime'] = 7700
        for clock, expected in ((70, 110), (100, 77), (110, 70)):
            result = b.timing_summary(counters, clock, 1_000_000_000, 1_025_000_000)
            self.assertEqual(result['exec_us'], expected)
            self.assertEqual(result['host_execute_ms'], 25)
        with self.assertRaises(ValueError):
            b.timing_summary(counters, 110, 2, 1)
        counters.pop('enq')
        with self.assertRaises(ValueError):
            b.timing_summary(counters, 110, 0, 1)

    def test_instruction_serialization_little_endian(self):
        inst = Mock()
        inst.to_ui32_list.return_value = [0x12345678, 0x90ABCDEF]
        packed = b.instruction_bytes(SimpleNamespace(instructions=[inst]))
        self.assertEqual(packed, bytes.fromhex('78563412efcdab90'))

    def test_config_identity_different_for_historical_stage(self):
        for identity, clock in b.CLOCKS.items():
            self.manifest['configuration_id'] = identity
            self.manifest['clock_mhz'] = clock
            self.write()
            actual, _, _ = b.validate_manifest(self.path)
            self.assertEqual(actual['clock_mhz'], clock)


if __name__ == '__main__':
    unittest.main()
