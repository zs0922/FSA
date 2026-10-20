#!/usr/bin/env python3
"""Real archive/re-audit binding integration, using tiny isolated /tmp fixtures."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


old = load('archive_fixture', HERE / 'test_archive_candidate.py')
r = load('reaudit_archive_fixture', HERE / 'reaudit-staging/reaudit.py')
a, v = old.archive, old.verify


class ReauditArchive(unittest.TestCase):
    def setUp(self):
        self.context = old.fixture()
        self.f = self.context.__enter__()
        f = self.f
        checker = '''from pathlib import Path
import json,hashlib
ROOT=Path(__file__).resolve().parents[2]
LOCK=json.loads((ROOT/'fpga/baselines/nm37-a110/source-lock.json').read_text())
def sources():
 for name,wanted in LOCK['source_hashes'].items():
  assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==wanted
'''
        old.write(f.source / 'scripts/nm37-a110/check.py', checker)
        for name in ('check_reset_evidence.tcl', 'cdc-baseline.json', 'run.sh'):
            old.write(f.source / 'scripts/nm37-a110' / name,
                      (HERE / 'reaudit-staging' / name).read_bytes())
        old.write(f.source / 'fpga/scripts/m1_a110_bitstream.tcl', '# frozen export fixture\n')
        subprocess.run(['git', '-C', str(f.source), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(f.source), '-c', 'user.name=Fixture', '-c',
                        'user.email=fixture@example.invalid', 'commit', '-qm', 'frozen fixture'], check=True)
        identity = json.loads((f.build / 'build.json').read_text())
        identity['root_commit'] = r.git(f.source, 'rev-parse', 'HEAD').decode().strip()
        identity['driver_hashes'] = {path.relative_to(f.source).as_posix(): r.digest(path)
            for path in (f.source / 'scripts/nm37-a110').iterdir() if path.is_file()}
        identity['driver_hashes']['fpga/scripts/m1_a110_bitstream.tcl'] = r.digest(f.source / 'fpga/scripts/m1_a110_bitstream.tcl')
        old.write(f.build / 'build.json', identity)
        build_sha = r.digest(f.build / 'build.json')
        for stage in r.STAGES:
            old.rewrite(f.build / (stage + '.passed.json'), lambda data: data.update(build_sha256=build_sha))
        old.rewrite(f.review / 'candidate-comparison.json', lambda data: data.update(build_identity=dict(
            build_sha256=build_sha, stages={stage: {'sha256': r.digest(f.build / (stage + '.passed.json'))}
            for stage in ('sim4', 'sim8', 'sim16', 'elaborate')})))
        obj = f.build / r.BOARD / 'obj'
        old.write(obj / 'audit/postroute_checkpoint.sha256', r.digest(obj / 'post_route.dcp') + '\n')
        old.write(obj / 'audit/audit_status.txt', 'failures={HBM: original BUFG-only checker}\n')
        for name in ('before.dict', 'after_replication.dict', 'replication_equivalence.txt'):
            old.write(obj / 'reset_replication_audit' / name, 'original reset bytes ' + name)
        original_pass = json.loads((f.build / 'physical.passed.json').read_text())
        (f.build / 'physical.passed.json').unlink()
        old.write(f.build / 'physical.failed.json', dict(build_sha256=build_sha,
            constrained_sta_passed=False, error='original buffer primitive audit failed',
            checkpoint_sha256=original_pass['checkpoint_sha256'], evidence=original_pass['evidence']))
        self.version = 'Vivado vFIXTURE\nSW Build fixture\nIP Build fixture\n'
        old.write(f.build / 'logs/tools.txt', self.version)
        old.write(f.build / 'logs/environment.json', {'tools': {'vivado': {'version': self.version}}})
        self.auditor = f.review / 'fixture-auditor'
        self.auditor.mkdir()
        for name in r.AUDITOR_FILES:
            shutil.copyfile(HERE / 'reaudit-staging' / name, self.auditor / name)
        old.write(self.auditor / 'physical_audit.py', '''class AuditContext:
 def __init__(self,**kw): self.__dict__.update(kw)
def inspect(obj,result,context=None):
 result.update(constrained_sta_passed=True,hbm_startup_physical_passed=True,board_signoff=False,timing_summary=[0.1,0,0])
''')
        f.reaudit = f.review / 'routed-reaudit'
        r.prepare(f.source, f.build, f.reaudit, self.auditor)
        for directory in r.REPORT_DIRS:
            old.write(f.reaudit / 'obj' / directory / 'new.rpt', 'new report ' + directory)
        for name in ('vivado.log', 'vivado-native.log', 'vivado.jou'):
            old.write(f.reaudit / name, self.version + 'fixture execution ' + name)
        old.write(f.reaudit / 'vivado-result.json', dict(exit_code=0,
            log_sha256=r.digest(f.reaudit / 'vivado.log'), inputs_sha256=r.digest(f.reaudit / 'inputs.json'),
            tool_logs={name: r.digest(f.reaudit / name) for name in ('vivado-native.log', 'vivado.jou')},
            report_files=r.report_files(f.reaudit)))
        (f.reaudit / 'INCOMPLETE.json').unlink()
        self.acceptance = r.accept(f.reaudit, 'fixture')
        f.reaudit_pass = self.acceptance.relative_to(f.reaudit).as_posix()
        f.reaudit_bitstream = None

    def tearDown(self):
        self.context.__exit__(None, None, None)

    def archive(self):
        plan = a.Plan(self.f).prepare()
        plan.write()
        return v.verify(self.f.destination)

    def test_original_failure_and_revised_pass_both_preserved(self):
        self.archive()
        inventory = json.loads((self.f.destination / 'inventory.json').read_text())
        self.assertEqual(inventory['summary']['original_acceptance_status'], 'failed')
        self.assertEqual(inventory['summary']['revised_acceptance_status'], 'passed')
        self.assertFalse(inventory['summary']['hardware_reimplemented'])
        self.assertEqual(inventory['driver_snapshot'], dict(root='reaudit', prefix='implementation-drivers/'))
        self.assertTrue(any(item['source_root'] == 'build' and item['source_relative_path'] == 'physical.failed.json'
                            for item in inventory['files']))
        self.assertFalse((self.f.build / 'physical.passed.json').exists())

    def test_current_auditor_change_does_not_replace_original_driver(self):
        path = self.f.source / 'scripts/nm37-a110/check_reset_evidence.tcl'
        # This helper is not allowed to change; current hbm checker is allowed.
        hbm = self.f.source / 'scripts/nm37-a110/hbm_audit.tcl'
        old.write(hbm, '# current corrected checker\n')
        self.archive()
        inventory = json.loads((self.f.destination / 'inventory.json').read_text())
        self.assertTrue(any(item['source_root'] == 'reaudit' and
                            item['source_relative_path'] == 'implementation-drivers/scripts/nm37-a110/check.py'
                            for item in inventory['files']))
        self.assertFalse((self.f.destination / 'source/scripts/nm37-a110/check.py').exists())

    def test_reaudit_pass_requires_relative_safe_path(self):
        for path in ('/tmp/pass.json', '../escape.json'):
            self.f.reaudit_pass = path
            with self.assertRaises(ValueError):
                a.Plan(self.f)

    def test_original_report_and_xci_tamper_rejected(self):
        for path in (self.f.build / r.BOARD / 'obj/report/timing.txt',
                     self.f.build / r.BOARD / 'obj/ip/testxilinx_hbm/testxilinx_hbm.xci'):
            original = path.read_bytes(); old.write(path, 'changed')
            with self.assertRaises((RuntimeError, ValueError)):
                a.Plan(self.f).prepare()
            old.write(path, original)

    def test_revised_report_or_acceptance_tamper_rejected(self):
        path = self.f.reaudit / 'obj/report/new.rpt'
        original = path.read_bytes(); old.write(path, 'changed')
        with self.assertRaises(RuntimeError):
            a.Plan(self.f).prepare()
        old.write(path, original)
        old.rewrite(self.acceptance, lambda data: data.update(timing_summary=[999.0, 0, 0]))
        with self.assertRaisesRegex(RuntimeError, 'physical value changed'):
            a.Plan(self.f).prepare()

    def test_snapshot_or_symlink_tamper_rejected(self):
        path = self.f.reaudit / 'implementation-drivers/scripts/nm37-a110/check.py'
        original = path.read_bytes(); old.write(path, '# changed original snapshot')
        with self.assertRaises(RuntimeError):
            a.Plan(self.f).prepare()
        old.write(path, original)
        link = self.f.reaudit / 'extra.json'
        link.symlink_to(self.f.build / 'build.json')
        with self.assertRaises(ValueError):
            a.Plan(self.f).prepare()

    def test_archived_reaudit_record_cannot_be_tampered(self):
        self.archive()
        inventory = json.loads((self.f.destination / 'inventory.json').read_text())
        target = next(item for item in inventory['files'] if item['source_root'] == 'reaudit'
                      and item['source_relative_path'] == self.f.reaudit_pass)
        old.write(self.f.destination / target['archived_relative_path'], 'changed archived acceptance')
        with self.assertRaises(ValueError):
            v.verify(self.f.destination)

    def test_revised_manifest_cannot_reference_a_different_build(self):
        old.rewrite(self.f.reaudit / 'inputs.json', lambda data: data['implementation'].update(build_sha256='0' * 64))
        with self.assertRaisesRegex(ValueError, 'implementation identity'):
            a.Plan(self.f).prepare()

    def test_bitstream_export_binding_and_omission(self):
        f = self.f
        f.reaudit_bitstream = f.review / 'bitstream-export'
        real_run = subprocess.run
        def run(cmd, **kwargs):
            if cmd[0] != 'fixture-vivado':
                return real_run(cmd, **kwargs)
            old.write(Path(cmd[-1]) / 'NM37_A110.bit', b'fixture exported bytes')
            old.write(Path(cmd[-1]) / 'timing.txt', 'fixture export timing')
            old.write(Path(cmd[-1]) / 'drc.txt', 'fixture export DRC')
            old.write(Path(cmd[cmd.index('-log') + 1]), self.version + 'native fixture log')
            old.write(Path(cmd[cmd.index('-journal') + 1]), 'native fixture journal')
            return subprocess.CompletedProcess(cmd, 0)
        with mock.patch.object(r.subprocess, 'run', side_effect=run):
            r.export_bitstream(f.reaudit, self.acceptance, f.reaudit_bitstream, 'fixture-vivado')
        self.archive()
        inventory = json.loads((f.destination / 'inventory.json').read_text())
        reference = next(item for item in inventory['local_large_artifacts']
                         if item['source_root'] == 'reaudit_bitstream' and item['source_relative_path'].endswith('.bit'))
        self.assertFalse(reference['archived'])
        self.assertEqual(reference['raw_sha256'], r.digest(f.reaudit_bitstream / 'export/NM37_A110.bit'))
        self.assertFalse(list(f.destination.rglob('*.bit')))


if __name__ == '__main__':
    unittest.main(verbosity=2)
