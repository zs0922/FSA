#!/usr/bin/env python3
"""Isolated provenance/entry tests. Never opens a real checkpoint or Vivado."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('reaudit', Path(__file__).with_name('reaudit.py'))
r = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(r)


def put(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())


def js(path, value):
    put(path, json.dumps(value))


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nm37-reaudit-test-')
        self.root = Path(self.temp.name)
        self.source, self.build = self.root / 'source', self.root / 'build'
        self.auditor, self.out = self.root / 'auditor', self.root / 'reaudit'
        self.source.mkdir(); self.build.mkdir(); self.auditor.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.source)], check=True)
        self.checker = b'''from pathlib import Path
import json, hashlib
ROOT=Path(__file__).resolve().parents[2]
LOCK=json.loads((ROOT/'fpga/baselines/nm37-a110/source-lock.json').read_text())
def sources():
 for p,h in LOCK['source_hashes'].items():
  assert hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h, 'production source changed'
'''
        self.drivers = {
            'scripts/nm37-a110/check.py': self.checker,
            'scripts/nm37-a110/check_reset_evidence.tcl': b'# fixed parser\n',
            'scripts/nm37-a110/cdc-baseline.json': b'{"findings": []}\n',
            'scripts/nm37-a110/reset_audit.tcl': b'# original reset checker\n',
            'scripts/nm37-a110/check_timing.tcl': b'# original strict timing\n',
            'scripts/nm37-a110/hbm_audit.tcl': b'# original hbm audit\n',
            'scripts/nm37-a110/run.sh': b'# original entry\n',
            'fpga/scripts/m1_a110_bitstream.tcl': b'# original export\n',
        }
        for name, payload in self.drivers.items():
            put(self.source / name, payload)
        put(self.source / 'fpga/product.scala', 'locked product\n')
        put(self.build / 'shell/common.tcl', '# frozen shell\n')
        lock = dict(configuration={'f': 110}, source_hashes={
            'fpga/product.scala': r.digest(self.source / 'fpga/product.scala')},
            shell_hashes={'common.tcl': r.digest(self.build / 'shell/common.tcl')})
        js(self.source / r.LOCK_PATH, lock)
        subprocess.run(['git', '-C', str(self.source), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.source), '-c', 'user.name=Fixture',
            '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture'], check=True)
        commit = r.git(self.source, 'rev-parse', 'HEAD').decode().strip()
        put(self.build / 'generator.jar', 'generator fixture')
        js(self.build / 'build.json', dict(root_commit=commit, configuration=lock['configuration'],
            lock_sha256=r.digest(self.source / r.LOCK_PATH), generator_sha256=r.digest(self.build / 'generator.jar'),
            driver_hashes={name: r.sha_bytes(payload) for name, payload in self.drivers.items()}))
        for stage in r.STAGES:
            name = stage + '/payload'
            put(self.build / name, stage)
            js(self.build / (stage + '.passed.json'), dict(build_sha256=r.digest(self.build / 'build.json'),
                files={name: r.digest(self.build / name)}))
        obj = self.build / r.BOARD / 'obj'
        put(obj / 'post_route.dcp', 'unchanged routed fixture')
        for directory in r.REPORT_DIRS:
            put(obj / directory / 'original.txt', 'original report ' + directory)
        put(obj / 'audit/postroute_checkpoint.sha256', r.digest(obj / 'post_route.dcp') + '\n')
        put(obj / 'audit/audit_status.txt', 'failures={HBM: original buffer check}\n')
        for name in ('before.dict', 'after_replication.dict', 'replication_equivalence.txt'):
            put(obj / 'reset_replication_audit' / name, 'original reset evidence ' + name)
        put(self.build / 'A110/implementation.log', 'original audit failure\n')
        self.version = 'Vivado vFIXTURE\nSW Build fixture\nIP Build fixture\n'
        put(self.build / 'logs/tools.txt', self.version)
        js(self.build / 'logs/environment.json', {'tools': {'vivado': {'version': self.version}}})
        for name in ('testxilinx_hbm.xci', 'harnessSysPLL.xci'):
            put(self.build / r.BOARD / 'ip' / name, 'original IP ' + name)
        for name in r.AUDITOR_FILES:
            original = self.drivers.get('scripts/nm37-a110/' + name)
            put(self.auditor / name, Path(r.__file__).read_bytes() if name == 'reaudit.py'
                else original or ('# revised fixture ' + name + '\n'))

    def tearDown(self):
        self.temp.cleanup()

    def prepare(self):
        return r.prepare(self.source, self.build, self.out, self.auditor)

    def reports(self):
        for directory in r.REPORT_DIRS:
            put(self.out / 'obj' / directory / 'new.rpt', 'new report ' + directory)
        put(self.out / 'vivado.log', 'fixture successful report process\n')
        for name in ('vivado-native.log', 'vivado.jou'):
            put(self.out / name, self.version + 'native fixture ' + name)
        js(self.out / 'vivado-result.json', dict(exit_code=0,
            log_sha256=r.digest(self.out / 'vivado.log'), inputs_sha256=r.digest(self.out / 'inputs.json'),
            tool_logs={name: r.digest(self.out / name) for name in ('vivado-native.log', 'vivado.jou')},
            report_files=r.report_files(self.out)))

    def fake_inspect(self, out, manifest, result):
        result.update(constrained_sta_passed=True, hbm_startup_physical_passed=True, board_signoff=False,
                      timing_summary=[0.1, 0, 0])

    def passing(self):
        self.prepare(); self.reports()
        with mock.patch.object(r, 'inspect_physical', side_effect=self.fake_inspect):
            return r.accept(self.out, 'attempt1')

    def test_complete_original_bindings_and_immutable_original(self):
        before = r.original_reports(self.build)
        manifest = self.prepare()
        self.assertEqual(set(manifest['implementation']['stage_seals']), {s + '.passed.json' for s in r.STAGES})
        self.assertEqual(before, r.original_reports(self.build))
        self.assertEqual((self.out / 'implementation-drivers/scripts/nm37-a110/check.py').read_bytes(), self.checker)
        r.load_manifest(self.out)

    def test_entry_keeps_original_physical_gate_and_separate_reaudit(self):
        entry = Path(__file__).with_name('run.sh').read_text()
        self.assertIn('  physical)\n    python3 "$helper/check.py" gate\n    python3 "$helper/check.py" physical\n    ;;', entry)
        self.assertIn('  bitstream)\n    python3 "$helper/check.py" gate\n    python3 "$helper/check.py" physical', entry)
        for name in ('reaudit)', 'reaudit-accept)', 'reaudit-verify)', 'bitstream-from-reaudit)'):
            self.assertIn('  ' + name, entry)
        self.assertNotIn('check.py" record', entry.split('  reaudit)')[1].split('  prepare)')[0])

    def test_physical_entry_rejects_stale_driver_without_rewriting_failure(self):
        helper = self.root / 'entry-source/scripts/nm37-a110'
        put(helper / 'run.sh', Path(__file__).with_name('run.sh').read_bytes())
        put(helper / 'check.py', '''import os,sys
from pathlib import Path
if sys.argv[1]=='gate': sys.exit(7)
(Path(os.environ['A110_OUT'])/'physical.failed.json').write_text('overwritten')
''')
        marker = self.build / 'physical.failed.json'
        put(marker, 'original failure must remain')
        process = subprocess.run(['bash', str(helper / 'run.sh'), 'physical'],
            env={**os.environ, 'A110_OUT': str(self.build)}, capture_output=True, text=True)
        self.assertEqual(process.returncode, 7)
        self.assertEqual(marker.read_text(), 'original failure must remain')

    def test_product_change_rejected(self):
        put(self.source / 'fpga/product.scala', 'changed product')
        with self.assertRaisesRegex(RuntimeError, 'non-auditor'):
            self.prepare()

    def test_original_source_checker_executes(self):
        with mock.patch.object(r, 'validate_current_changes', return_value=[]):
            put(self.source / 'fpga/product.scala', 'changed product')
            with self.assertRaisesRegex(AssertionError, 'production source'):
                self.prepare()

    def test_allowed_auditor_change_uses_original_git_bytes(self):
        put(self.source / 'scripts/nm37-a110/hbm_audit.tcl', '# revised current auditor')
        self.prepare()
        self.assertEqual((self.out / 'implementation-drivers/scripts/nm37-a110/hbm_audit.tcl').read_bytes(),
                         self.drivers['scripts/nm37-a110/hbm_audit.tcl'])

    def test_reset_implementation_change_rejected(self):
        put(self.source / 'scripts/nm37-a110/reset_opt.tcl', '# mutation')
        with self.assertRaisesRegex(RuntimeError, 'Untracked auditor'):
            self.prepare()

    def test_only_exact_final_documents_and_evidence_are_allowed(self):
        base = r.git(self.source, 'rev-parse', 'HEAD').decode().strip()
        paths = list(r.NONEXECUTABLE_DOCUMENTS) + [r.NONEXECUTABLE_EVIDENCE_PREFIX + 'build/original.log']
        for path in paths:
            put(self.source / path, 'nonexecuted report bytes')
        subprocess.run(['git', '-C', str(self.source), 'add', '--', *paths], check=True)
        self.assertEqual(set(r.validate_current_changes(self.source, base)), set(paths))
        forbidden = 'optimization-reports/other-report.md'
        put(self.source / forbidden, 'not allowlisted')
        subprocess.run(['git', '-C', str(self.source), 'add', '--', forbidden], check=True)
        with self.assertRaisesRegex(RuntimeError, 'non-auditor'):
            r.validate_current_changes(self.source, base)

    def test_allowlisted_document_must_not_be_symlink(self):
        base = r.git(self.source, 'rev-parse', 'HEAD').decode().strip()
        path = self.source / 'README.md'
        path.symlink_to('fpga/product.scala')
        subprocess.run(['git', '-C', str(self.source), 'add', '--', 'README.md'], check=True)
        with self.assertRaisesRegex(RuntimeError, 'Symlink'):
            r.validate_current_changes(self.source, base)

    def test_dirty_submodule_worktree_is_separate_from_gitlink_change(self):
        sub = self.source / 'fpga/fpga-shells'
        sub.mkdir(parents=True)
        subprocess.run(['git', 'init', '-q', str(sub)], check=True)
        put(sub / 'input.tcl', '# original')
        subprocess.run(['git', '-C', str(sub), 'add', '.'], check=True)
        commit = ['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', '-C']
        subprocess.run(commit + [str(sub), 'commit', '-qm', 'submodule fixture'], check=True)
        first = r.git(sub, 'rev-parse', 'HEAD').decode().strip()
        subprocess.run(['git', '-C', str(self.source), 'update-index', '--add', '--cacheinfo',
                        '160000,' + first + ',fpga/fpga-shells'], check=True)
        subprocess.run(commit + [str(self.source), 'commit', '-qm', 'add gitlink'], check=True)
        base = r.git(self.source, 'rev-parse', 'HEAD').decode().strip()
        put(sub / 'input.tcl', '# source-locked dirty overlay')
        self.assertEqual(r.validate_current_changes(self.source, base), [])
        subprocess.run(['git', '-C', str(sub), 'add', '.'], check=True)
        subprocess.run(commit + [str(sub), 'commit', '-qm', 'different gitlink target'], check=True)
        with self.assertRaisesRegex(RuntimeError, 'non-auditor'):
            r.validate_current_changes(self.source, base)

    def test_git_driver_snapshot_must_match_manifest(self):
        record = r.json_object(self.build / 'build.json')
        record['driver_hashes']['scripts/nm37-a110/check.py'] = '0' * 64
        js(self.build / 'build.json', record)
        with self.assertRaisesRegex(RuntimeError, 'Git driver/build mismatch'):
            self.prepare()

    def test_all_seven_payloads_checked(self):
        for stage in r.STAGES:
            path = self.build / stage / 'payload'
            saved = path.read_bytes()
            put(path, b'changed')
            with self.assertRaisesRegex(RuntimeError, 'changed file'):
                r.validate_build(self.source, self.build)
            put(path, saved)

    def test_stage_wrong_build_rejected(self):
        path = self.build / 'sim16.passed.json'
        record = r.json_object(path); record['build_sha256'] = '0' * 64; js(path, record)
        with self.assertRaisesRegex(RuntimeError, 'Stage/build mismatch'):
            self.prepare()

    def test_stage_path_escapes_and_symlink_rejected(self):
        for malicious in ('/tmp/escape', '../escape', 'sim4/../../escape'):
            path = self.build / 'sim4.passed.json'
            saved = path.read_bytes(); data = r.json_object(path)
            data['files'] = {malicious: '0' * 64}; js(path, data)
            with self.assertRaisesRegex(RuntimeError, 'Unsafe relative'):
                self.prepare()
            put(path, saved)
        payload = self.build / 'sim4/payload'; payload.unlink()
        payload.symlink_to(self.build / 'sim8/payload')
        with self.assertRaisesRegex(RuntimeError, 'Symlink'):
            self.prepare()

    def test_output_refuses_existing_and_overlap(self):
        self.out.mkdir()
        with self.assertRaisesRegex(RuntimeError, 'already exists'):
            self.prepare()
        for destination in (self.build / 'new', self.source / 'new'):
            with self.assertRaisesRegex(RuntimeError, 'overlaps'):
                r.prepare(self.source, self.build, destination, self.auditor)

    def test_output_symlink_parent_rejected(self):
        link = self.root / 'link'; link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'Symlink'):
            r.prepare(self.source, self.build, link / 'new', self.auditor)

    def test_dcp_and_original_reports_immutable_after_prepare(self):
        manifest = self.prepare()
        for path in (self.build / r.BOARD / 'obj/post_route.dcp',
                     self.build / r.BOARD / 'obj/report/original.txt',
                     self.build / 'A110/implementation.log'):
            saved = path.read_bytes(); put(path, 'changed')
            with self.assertRaises(RuntimeError):
                r.assert_unchanged(manifest)
            put(path, saved)

    def test_generated_ip_content_and_set_are_bound(self):
        manifest = self.prepare()
        path = self.build / r.BOARD / 'ip/testxilinx_hbm.xci'
        original = path.read_bytes(); put(path, 'changed clock')
        with self.assertRaisesRegex(RuntimeError, 'original_ip_files'):
            r.assert_unchanged(manifest)
        put(path, original)
        put(self.build / r.BOARD / 'other/harnessSysPLL.xci', 'unexpected additional PLL')
        with self.assertRaisesRegex(RuntimeError, 'original_ip_files'):
            r.assert_unchanged(manifest)

    def test_revised_tool_and_original_driver_tampering(self):
        self.prepare()
        for path in (self.out / 'auditor/hbm_audit.tcl', self.out / 'implementation-drivers/scripts/nm37-a110/check.py'):
            saved = path.read_bytes(); put(path, 'changed')
            with self.assertRaisesRegex(RuntimeError, 'changed file'):
                r.load_manifest(self.out)
            put(path, saved)

    def test_mutation_commands_and_changed_baseline_rejected(self):
        for command in ('set_property X Y Z', 'route_design', 'set_false_path -from X', 'write_checkpoint x'):
            put(self.auditor / 'hbm_audit.tcl', command)
            with self.assertRaisesRegex(RuntimeError, 'Mutating Tcl'):
                self.prepare()
        put(self.auditor / 'hbm_audit.tcl', '# readonly')
        put(self.auditor / 'cdc-baseline.json', '{}')
        with self.assertRaisesRegex(RuntimeError, 'may not modify frozen helper'):
            self.prepare()

    def test_pass_is_separate_and_recomputed(self):
        accepted = self.passing()
        with mock.patch.object(r, 'inspect_physical', side_effect=self.fake_inspect) as inspect:
            r.verify_pass(self.out, accepted)
            self.assertEqual(inspect.call_count, 1)
        self.assertFalse((self.build / 'physical.passed.json').exists())
        self.assertIn('original buffer check', (self.build / r.BOARD / 'obj/audit/audit_status.txt').read_text())

    def test_fake_pass_cannot_override_real_physical_failure(self):
        accepted = self.passing()
        with mock.patch.object(r, 'inspect_physical', side_effect=RuntimeError('real slack failure')):
            with self.assertRaisesRegex(RuntimeError, 'real slack failure'):
                r.verify_pass(self.out, accepted)

    def test_pass_numbers_and_deleted_review_map_rejected(self):
        accepted = self.passing(); original = accepted.read_bytes()
        data = r.json_object(accepted); data['timing_summary'] = [99.0, 0, 0]; js(accepted, data)
        with mock.patch.object(r, 'inspect_physical', side_effect=self.fake_inspect):
            with self.assertRaisesRegex(RuntimeError, 'physical value changed'):
                r.verify_pass(self.out, accepted)
        put(accepted, original)
        data = r.json_object(accepted); del data['review_files']; js(accepted, data)
        with self.assertRaisesRegex(RuntimeError, 'review file set/hash'):
            r.verify_pass(self.out, accepted)

    def test_acceptance_attempt_cannot_overwrite(self):
        self.passing()
        with self.assertRaisesRegex(RuntimeError, 'already exists'):
            r.accept(self.out, 'attempt1')
        with self.assertRaisesRegex(RuntimeError, 'Unsafe acceptance'):
            r.accept(self.out, '../escape')

    def test_failure_is_retained_without_original_mutation(self):
        self.prepare(); self.reports()
        before = r.original_reports(self.build)
        with mock.patch.object(r, 'inspect_physical', side_effect=RuntimeError('CDC review required')):
            with self.assertRaisesRegex(RuntimeError, 'CDC review required'):
                r.accept(self.out, 'pending')
        self.assertEqual(r.json_object(self.out / 'acceptance/pending/reaudit.failed.json')['status'], 'failed')
        self.assertEqual(r.original_reports(self.build), before)

    def test_execution_report_log_and_cdc_bindings(self):
        accepted = self.passing()
        for path in (self.out / 'obj/report/new.rpt', self.out / 'vivado.log', self.out / 'vivado-result.json'):
            saved = path.read_bytes(); put(path, '{}' if path.suffix == '.json' else 'changed')
            with self.assertRaises((RuntimeError, KeyError)):
                r.verify_pass(self.out, accepted)
            put(path, saved)
        data = r.json_object(accepted)
        put(self.out / 'cdc-review.json', '{"fixture": true}')
        data['review_files'] = {'cdc-review.json': r.digest(self.out / 'cdc-review.json')}
        js(accepted, data); put(self.out / 'cdc-review.json', '{}')
        with self.assertRaisesRegex(RuntimeError, 'CDC review file set/hash changed'):
            r.verify_pass(self.out, accepted)

    def test_vivado_version_cannot_change_with_rehashed_native_log(self):
        manifest = self.prepare(); self.reports()
        put(self.out / 'vivado-native.log', 'Vivado vOTHER\nSW Build fixture\nIP Build fixture\n')
        execution = r.json_object(self.out / 'vivado-result.json')
        execution['tool_logs']['vivado-native.log'] = r.digest(self.out / 'vivado-native.log')
        js(self.out / 'vivado-result.json', execution)
        with self.assertRaisesRegex(RuntimeError, 'Vivado version differs'):
            r.validate_execution(self.out, manifest)

    def test_failed_process_can_preserve_partial_report_manifest(self):
        self.prepare()
        reports = r.report_files(self.out, complete=False)
        self.assertIn('obj/reset_replication_audit/before.dict', reports)
        with self.assertRaisesRegex(RuntimeError, 'Missing revised report directory'):
            r.report_files(self.out)

    def test_export_requires_verified_pass_and_new_destination(self):
        accepted = self.passing()
        with mock.patch.object(r, 'inspect_physical', side_effect=self.fake_inspect):
            for destination in (self.build / 'new', self.out / 'new', self.source / 'new'):
                with self.assertRaisesRegex(RuntimeError, 'overlaps'):
                    r.export_bitstream(self.out, accepted, destination, 'not-called')
            existing = self.root / 'existing'; existing.mkdir()
            with self.assertRaisesRegex(RuntimeError, 'already exists'):
                r.export_bitstream(self.out, accepted, existing, 'not-called')

    def test_export_calls_only_original_tcl_same_dcp(self):
        accepted = self.passing(); destination = self.root / 'bitstream'
        def subprocess_stub(cmd, **kwargs):
            self.assertEqual(cmd[cmd.index('-source') + 1], str(self.out / 'implementation-drivers/fpga/scripts/m1_a110_bitstream.tcl'))
            self.assertEqual(cmd[-2], str(self.build / r.BOARD / 'obj/post_route.dcp'))
            put(Path(cmd[-1]) / 'NM37_A110.bit', b'fixture-bitstream')
            put(Path(cmd[cmd.index('-log') + 1]), self.version + 'native export log')
            put(Path(cmd[cmd.index('-journal') + 1]), 'native export journal')
            return subprocess.CompletedProcess(cmd, 0)
        # Patching the shared subprocess module also affects check_output/git;
        # preserve Git process execution and intercept only the fake Vivado.
        real_run = subprocess.run
        def dispatch(cmd, **kwargs):
            return subprocess_stub(cmd, **kwargs) if cmd[0] == 'fake-vivado' else real_run(cmd, **kwargs)
        with mock.patch.object(r, 'inspect_physical', side_effect=self.fake_inspect), mock.patch.object(r.subprocess, 'run', side_effect=dispatch):
            record = r.export_bitstream(self.out, accepted, destination, 'fake-vivado')
        self.assertEqual(record['checkpoint_sha256'], r.digest(self.build / r.BOARD / 'obj/post_route.dcp'))
        self.assertFalse((destination / 'INCOMPLETE.json').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
