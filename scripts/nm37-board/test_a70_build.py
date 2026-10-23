"""Build-frontend fixtures: no compilation, FPGA access, or real successful-build claims."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import a70_build as b


def save(p,v):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(v))


class BuildFrontendTest(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.root=Path(t.name);self.driver=self.root/'old-driver';self.driver.mkdir()
        self.source=self.root/'source';self.source.mkdir();self.out=self.root/'build';self.out.mkdir()
        self.runner=self.driver/'run.sh'
        self.runner.write_text('#!/bin/bash\nexit 0\n')
        save(self.out/'build.json',{'source':str(self.source),'stage':'p3'})
        save(self.out/'source-lock.json',{})
        save(self.out/'shell.json',{})
        self.freeze()
        self.env=patch.dict(os.environ,{'A70_SOURCE':str(self.source),'A70_OUT':str(self.out),'A70_STAGE':'p3','FSA_PYTHON':os.sys.executable})
        self.env.start();self.addCleanup(self.env.stop)

    def freeze(self):
        save(self.out/'drivers.json',{p.relative_to(self.driver).as_posix():b.sha(p) for p in self.driver.rglob('*') if p.is_file()})

    def call(self,*args):
        stdout,stderr=io.StringIO(),io.StringIO()
        with contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
            rc=b.main(['--driver',str(self.driver),*args])
        return rc,stdout.getvalue(),stderr.getvalue()

    def passed(self,phase): save(self.out/(phase+'.passed.json'),{})

    def test_bad_array_and_unknown_phase_before_build(self):
        for args in [('sim','--array-size','1024'),('unknown',),('sim','1024')]:
            with self.subTest(args=args),patch.object(b.subprocess,'run') as run,contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as e:b.main(list(args))
                self.assertEqual(e.exception.code,2);run.assert_not_called()

    def test_missing_array_explains_seq(self):
        rc,_,err=self.call('sim')
        self.assertEqual(rc,2);self.assertIn('--seq',err)

    def test_missing_assembly_before_driver(self):
        with patch.object(b.subprocess,'run') as run:
            rc,_,err=self.call('implement')
            self.assertEqual(rc,2);self.assertIn('assembly.passed.json',err);run.assert_not_called()

    def test_failed_artifact_not_deleted(self):
        p=self.out/'logs/assembly.log';p.parent.mkdir();p.write_text('compiler failure')
        with patch.object(b.subprocess,'run') as run:
            rc,_,err=self.call('assembly')
            self.assertEqual(rc,2);self.assertIn('已有产物',err);run.assert_not_called()
        self.assertEqual(p.read_text(),'compiler failure')

    def test_existing_pass_not_overwritten(self):
        self.passed('assembly');before=(self.out/'assembly.passed.json').read_bytes()
        rc,_,err=self.call('assembly')
        self.assertEqual(rc,2);self.assertIn('已有成功记录',err)
        self.assertEqual(before,(self.out/'assembly.passed.json').read_bytes())

    def test_changed_driver_status_no_silent_acceptance(self):
        self.runner.write_text('# changed\n')
        rc,stdout,err=self.call('status')
        self.assertEqual(rc,2);self.assertIn('drivers.json',err);self.assertIn('未重新验收',stdout)

    def test_old_driver_error_after_pass_is_explicit(self):
        self.runner.write_text('#!/bin/bash\necho "PASS: source check only"\nexit 7\n');self.freeze()
        rc,_,err=self.call('assembly')
        self.assertEqual(rc,7);self.assertIn('不是阶段成功',err)

    def test_zero_exit_without_seal_is_not_success(self):
        rc,_,err=self.call('assembly')
        self.assertEqual(rc,2);self.assertIn('缺少完成记录',err)

    def test_complete_only_after_seal(self):
        self.runner.write_text('#!/bin/bash\nprintf \'{}\' > "$A70_OUT/assembly.passed.json"\n');self.freeze()
        rc,stdout,_=self.call('assembly')
        self.assertEqual(rc,0);self.assertIn('COMPLETE: assembly',stdout)

    def setup_sim(self):
        self.passed('assembly')
        sim=self.out/'sim16';sim.mkdir()
        binary=sim/'simulator';binary.write_bytes(b'fixture')
        config=sim/'build/chipyard.harness.TestHarness.A70SinglePort16X16Fp16Config.FSAConfig.json'
        save(config,{})
        save(self.out/'sim16.passed.json',{'source_lock_sha256':b.sha(self.out/'source-lock.json'),
            'files':{p.relative_to(self.out).as_posix():b.sha(p) for p in (binary,config)}})
        return binary

    def test_extra_1024_forwarded_and_sealed_tree_unchanged(self):
        self.setup_sim();dest=self.root/'extra1024'
        before={p.relative_to(self.out):p.read_bytes() for p in self.out.rglob('*') if p.is_file()}
        calls=[]
        def run(argv,**kwargs):
            calls.append(argv)
            if 'verify_a70_workload.py' in ' '.join(argv):save(dest/'result.json',{'bitwise_equal':True})
            return subprocess.CompletedProcess(argv,0)
        with patch.object(b.subprocess,'run',side_effect=run):
            rc,stdout,err=self.call('sim','--array-size','16','--seq','1024','--max-cycles','20000000','--result-out',str(dest))
        self.assertEqual(rc,0,err)
        self.assertEqual(calls[-1][calls[-1].index('--seq')+1],'1024')
        self.assertEqual(calls[-1][calls[-1].index('--max-cycles')+1],'20000000')
        self.assertEqual(before,{p.relative_to(self.out):p.read_bytes() for p in self.out.rglob('*') if p.is_file()})
        self.assertTrue((dest/'provenance.json').is_file());self.assertIn('COMPLETE:',stdout)

    def test_extra_rejects_overwrite_and_changed_binary(self):
        binary=self.setup_sim()
        for dest in [self.out/'extra',self.source/'extra',self.driver/'extra']:
            rc,_,_=self.call('sim','--array-size','16','--seq','1024','--result-out',str(dest))
            self.assertEqual(rc,2);self.assertFalse(dest.exists())
        binary.write_bytes(b'changed')
        with patch.object(b.subprocess,'run',return_value=subprocess.CompletedProcess([],0)):
            rc,_,err=self.call('sim','--array-size','16','--seq','1024','--result-out',str(self.root/'extra'))
        self.assertEqual(rc,2);self.assertIn('封印不一致',err)

    def test_extra_failure_never_reports_complete(self):
        self.setup_sim();dest=self.root/'failed-extra'
        def run(argv,**kwargs):
            if 'verify_a70_workload.py' in ' '.join(argv):
                dest.mkdir();raise subprocess.CalledProcessError(1,argv)
            return subprocess.CompletedProcess(argv,0)
        with patch.object(b.subprocess,'run',side_effect=run):
            rc,stdout,err=self.call('sim','--array-size','16','--seq','1024','--result-out',str(dest))
        self.assertEqual(rc,2);self.assertNotIn('COMPLETE',stdout);self.assertIn('保留',err)
        self.assertFalse((dest/'provenance.json').exists())

    def test_direct_driver_reports_invalid_args_and_missing_stage(self):
        script=Path(__file__).resolve().parents[1]/'nm37-a70/run.sh'
        for argv,needle in [(['sim','1024'],'Sequence length'),(['bogus'],'Unknown phase'),(['implement'],'assembly.passed.json')]:
            r=subprocess.run(['bash',str(script),*argv],capture_output=True,text=True)
            self.assertEqual(r.returncode,2);self.assertIn(needle,r.stderr);self.assertNotIn('PASS:',r.stdout)


if __name__=='__main__':unittest.main()
