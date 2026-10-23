"""Recovery fixtures verify data retention and refusal before any archive mutation."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import a70_build as build
import a70_recover_assembly as recovery


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source, self.out, self.driver, self.archive = (self.root/n for n in ('src','out','driver','archive'))
        for p in (self.source, self.out, self.driver): p.mkdir()
        (self.driver/'run.sh').write_text('# frozen fixture\n')
        self.save(self.out/'drivers.json', {'run.sh': build.sha(self.driver/'run.sh')})
        self.save(self.out/'build.json', dict(source=str(self.source), stage='p3'))
        for p in (self.out/'source-lock.json', self.out/'shell.json', self.source/'.nm37-a70/patch-state.json'):
            self.save(p, {})
        self.jar = self.out/'generator.jar'
        self.log = self.out/'logs/assembly.log'
        self.cache = self.source/'.classpath_cache/a70-release.jar'
        for p in (self.jar, self.log, self.cache):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(('failed data '+p.name).encode())
        self.env = patch.dict(os.environ, A70_SOURCE=str(self.source), A70_OUT=str(self.out), A70_STAGE='p3')
        self.env.start(); self.addCleanup(self.env.stop)
        self.checks = patch.object(recovery.subprocess, 'run', return_value=subprocess.CompletedProcess([],0,'PASS fixture\n',''))
        self.run = self.checks.start(); self.addCleanup(self.checks.stop)

    @staticmethod
    def save(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def call(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = recovery.main(['--driver',str(self.driver),'--archive',str(self.archive),*args])
        return rc, stdout.getvalue(), stderr.getvalue()

    def snapshot(self, root):
        return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}

    def test_default_is_read_only_and_runs_both_identity_checks(self):
        before = self.snapshot(self.root)
        rc, stdout, err = self.call()
        self.assertEqual(rc, 0, err)
        self.assertIn('PLAN ONLY', stdout)
        self.assertEqual(before, self.snapshot(self.root))
        self.assertEqual(self.run.call_count, 2)
        self.assertIn('artifacts.py', self.run.call_args_list[0].args[0][1])
        self.assertIn('nm37-a70-prepare.py', self.run.call_args_list[1].args[0][1])

    def test_apply_preserves_bytes_hashes_and_caches_without_passing_build(self):
        old = {p:p.read_bytes() for p in (self.jar, self.log, self.cache)}
        for name in ('.ivy2/download.jar','.sbt/cache.bin','target/classes.bin'):
            p = self.source/name; p.parent.mkdir(exist_ok=True); p.write_bytes(b'keep')
        rc, _, err = self.call('--apply')
        self.assertEqual(rc, 0, err)
        for item in build.read(self.archive/'archived.json')['files']:
            p = Path(item['original']); saved = self.archive/item['archived']
            self.assertFalse(p.exists())
            self.assertEqual(old[p], saved.read_bytes())
            self.assertEqual(build.sha(saved), item['sha256'])
        self.assertFalse((self.out/'assembly.passed.json').exists())
        self.assertTrue((self.out/'build.json').is_file())
        for name in ('.ivy2/download.jar','.sbt/cache.bin','target/classes.bin'):
            self.assertEqual((self.source/name).read_bytes(), b'keep')

    def test_downstream_artifact_or_seal_blocks_in_place(self):
        for name in ('A70','sim16','logs/elaboration.log','units.passed.json','implementation-generated.passed.json'):
            with self.subTest(name=name):
                p = self.out/name; p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'preserve')
                before = self.snapshot(self.root)
                rc, _, err = self.call('--apply')
                self.assertEqual(rc, 2)
                self.assertIn('--new-out', err)
                self.assertEqual(before, self.snapshot(self.root))
                p.unlink()

    def test_new_output_keeps_entire_old_output(self):
        self.save(self.out/'sim16.passed.json', {'untrusted':'do not reuse'})
        new = self.root/'new-out'
        before = self.snapshot(self.out)
        rc, stdout, err = self.call('--apply','--new-out',str(new))
        self.assertEqual(rc,0,err)
        self.assertEqual(before,self.snapshot(self.out))
        self.assertFalse(self.cache.exists())
        self.assertFalse(new.exists())
        self.assertIn('prepare',stdout)
        self.assertEqual(len(build.read(self.archive/'archived.json')['files']),1)

    def test_identity_failure_never_moves_anything(self):
        self.run.return_value = subprocess.CompletedProcess([],1,'','source changed')
        before = self.snapshot(self.root)
        rc, _, err = self.call('--apply')
        self.assertEqual(rc,2)
        self.assertIn('source-check 失败',err)
        self.assertEqual(before,self.snapshot(self.root))

    def test_existing_seal_or_wrong_driver_cannot_be_bypassed_by_new_output(self):
        self.save(self.out/'assembly.passed.json',{})
        rc, _, err = self.call('--new-out',str(self.root/'fresh'),'--apply')
        self.assertEqual(rc,2);self.assertIn('已存在',err)
        (self.out/'assembly.passed.json').unlink()
        (self.driver/'run.sh').write_text('changed')
        rc, _, err = self.call('--new-out',str(self.root/'fresh'),'--apply')
        self.assertEqual(rc,2);self.assertIn('drivers.json',err)
        self.assertFalse(self.archive.exists())

    def test_symlink_cache_is_not_followed_or_deleted(self):
        self.cache.unlink()
        target = self.root/'shared.jar';target.write_bytes(b'shared')
        self.cache.symlink_to(target)
        rc, _, err = self.call('--apply')
        self.assertEqual(rc,2);self.assertIn('符号链接',err)
        self.assertTrue(self.cache.is_symlink());self.assertEqual(target.read_bytes(),b'shared')
        self.assertFalse(self.archive.exists())

    def test_archive_or_new_out_inside_existing_tree_rejected(self):
        for path in (self.source/'archive', self.out/'archive', self.driver/'archive'):
            self.archive = path
            rc, _, _ = self.call('--apply')
            self.assertEqual(rc,2);self.assertFalse(path.exists())
        self.archive = self.root/'archive'
        for path in (self.source/'fresh',self.out/'fresh',self.driver/'fresh',self.archive/'fresh',self.root):
            rc, _, _ = self.call('--new-out',str(path),'--apply')
            self.assertEqual(rc,2);self.assertFalse(self.archive.exists())

    def test_copy_failure_keeps_original_and_auditable_partial_archive(self):
        before = self.cache.read_bytes()
        with patch.object(recovery.shutil, 'copyfileobj', side_effect=OSError('disk full')):
            rc, _, err = self.call('--apply')
        self.assertEqual(rc,2);self.assertIn('disk full',err)
        self.assertEqual(self.cache.read_bytes(),before)
        self.assertTrue((self.archive/'plan.json').exists())
        self.assertFalse((self.archive/'archived.json').exists())
        self.assertTrue(self.jar.exists());self.assertTrue(self.log.exists())


if __name__ == '__main__':
    unittest.main()
