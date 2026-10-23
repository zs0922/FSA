"""Local Git fixtures for scoped initialization; no FPGA or external network."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import init_git_dependencies as init
import source_bundle as s
import test_source_bundle as fixtures


class InitGitDependenciesTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SourceBundleTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        self.root = f.root
        f.export_deps()
        f.export_main()
        self.target = self.root / 'target'
        main=json.loads((f.mainout/'main.json').read_text())
        s.clone_bundle(s.checked_bundle(f.mainout,main),self.target,f.main,main['refs'])
        self.lock=self.root/'git-lock.json'
        deps=json.loads((f.deps/'dependencies.json').read_text())
        s.save(self.lock,{'schema_version':1,'source_lock_sha256':deps['source_lock_sha256'],
            'profiles':{'A70-P3':{'commit':f.main,'root_gitlinks':s.links(self.target,f.main)}},
            'dependencies':f.pins,'excluded_dependencies':{},'out_of_scope_root_gitlinks':{}})

    def run_init(self, **kwargs):
        return init.initialize(self.target,'A70-P3',self.lock,**kwargs)

    def test_plan_no_child_writes(self):
        result=self.run_init(plan=True)
        self.assertEqual(result['count'],2)
        self.assertFalse((self.target/'lib/.git').exists())

    def test_offline_complete_repeat_and_root_clean(self):
        result=self.run_init(bundles=self.fixture.deps)
        self.assertEqual(result['restored_dependencies'],self.fixture.pins)
        self.assertEqual(s.git(self.target,'status','--porcelain'),'')
        self.assertEqual(self.run_init(bundles=self.fixture.deps)['count'],2)
        self.assertEqual(s.git(self.target/'lib','rev-parse','HEAD'),self.fixture.lib)

    def test_dirty_refused_before_updater(self):
        (self.target/'main.txt').write_text('changed')
        with self.assertRaisesRegex(ValueError,'changes'):
            self.run_init(updater=lambda *a:self.fail('must not update'))

    def test_wrong_root_and_pin_rejected(self):
        data=json.loads(self.lock.read_text())
        data['profiles']['A70-P3']['commit']='a'*40
        s.save(self.lock,data)
        with self.assertRaisesRegex(ValueError,'Wrong root'):
            self.run_init(plan=True)
        data['profiles']['A70-P3']['commit']=self.fixture.main
        data['dependencies']['lib']='b'*40
        s.save(self.lock,data)
        with self.assertRaisesRegex(ValueError,'Gitlink differs'):
            self.run_init()

    def test_online_traverses_parent_before_child(self):
        calls=[]
        deps=json.loads((self.fixture.deps/'dependencies.json').read_text())
        def updater(parent,relative):
            name=(parent/relative).relative_to(self.target).as_posix()
            calls.append(name)
            rec=deps['dependencies'][name]
            s.clone_bundle(s.checked_bundle(self.fixture.deps,rec),parent/relative,rec['commit'],rec['refs'])
        self.assertEqual(self.run_init(updater=updater)['count'],2)
        self.assertEqual(calls,['lib','lib/nested'])

    def test_corrupt_bundle_and_dirty_existing_child(self):
        deps=json.loads((self.fixture.deps/'dependencies.json').read_text())
        p=self.fixture.deps/deps['dependencies']['lib']['bundle']
        original=p.read_bytes()
        p.write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError,'SHA/size'):
            self.run_init(bundles=self.fixture.deps)
        p.write_bytes(original)
        self.run_init(bundles=self.fixture.deps)
        (self.target/'lib/lib.txt').write_text('changed')
        with self.assertRaisesRegex(ValueError,'changes'):
            self.run_init(bundles=self.fixture.deps)

    def test_online_command_is_nonrecursive_and_scoped(self):
        with patch.object(init.subprocess,'run') as run:
            init.online_update(Path('/a'),'lib')
            args=run.call_args.args[0]
            self.assertNotIn('--recursive',args)
            self.assertIn('url.https://github.com/.insteadOf=git@github.com:',args)
            self.assertEqual(args[-2:],['--','lib'])


if __name__=='__main__':
    unittest.main()
