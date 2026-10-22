"""Small local Git fixtures: no remotes, tools or real FPGAs needed."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import source_bundle as s


class SourceBundleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        self.init(self.source)
        self.init(self.source / 'lib')
        self.init(self.source / 'lib/nested')
        self.nested = self.commit_file(self.source / 'lib/nested', 'nested.txt', 'nested content')
        self.link(self.source / 'lib', 'nested', self.nested)
        self.lib = self.commit_file(self.source / 'lib', 'lib.txt', 'library content')
        self.link(self.source, 'lib', self.lib)
        self.main = self.commit_file(self.source, 'main.txt', 'root content')
        self.pins = {'lib': self.lib, 'lib/nested': self.nested}
        self.lock = self.root / 'source-lock.json'
        s.save(self.lock, {'schema': 2, 'root_commit': self.main, 'dependencies': self.pins,
                          'excluded_dependencies': {}, 'files': {}})
        self.deps = self.root / 'dependencies'
        self.mainout = self.root / 'main-bundle'

    def init(self, path):
        path.mkdir(parents=True)
        s.git(None, 'init', str(path))
        s.git(path, 'config', 'user.name', 'Bundle Test')
        s.git(path, 'config', 'user.email', 'bundle-test@example.invalid')

    def commit_file(self, path, filename, content):
        (path / filename).write_text(content)
        s.git(path, 'add', filename)
        s.git(path, 'commit', '-m', filename)
        return s.git(path, 'rev-parse', 'HEAD')

    def link(self, path, name, commit):
        s.git(path, 'update-index', '--add', '--cacheinfo', '160000', commit, name)

    def export_deps(self, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return s.export_dependencies(self.source, self.lock, self.deps, **kwargs)

    def export_main(self, extra=None, source=None):
        spec = {'profiles': {'historical-p2': {'repository': str(source or self.source), 'revision': self.main}}}
        if extra:
            spec['profiles'].update(extra)
        path = self.root / 'main-spec.json'
        s.save(path, spec)
        return s.export_main(path, self.mainout)

    def test_offline_restore_after_original_source_removed(self):
        self.export_deps()
        self.export_main()
        self.source.rename(self.root / 'unavailable-original')
        restored = self.root / 'restored'
        result = s.restore(self.mainout / 'main.json', self.deps / 'dependencies.json', 'historical-p2', restored)
        self.assertEqual(result['restored_dependencies'], self.pins)
        for name, commit in {'.': self.main, **self.pins}.items():
            repo = restored / name
            self.assertEqual(s.git(repo, 'rev-parse', 'HEAD'), commit)
            self.assertEqual(s.git(repo, 'remote'), '')
            self.assertFalse((repo / '.git/objects/info/alternates').exists())
        self.assertEqual((restored / 'lib/nested/nested.txt').read_text(), 'nested content')

    def test_export_from_shared_alternates_is_self_contained(self):
        shared = self.root / 'shared'
        s.git(None, 'clone', '--shared', str(self.source), str(shared))
        self.assertTrue((shared / '.git/objects/info/alternates').exists())
        self.export_main(source=shared)
        s.git(shared, 'remote', 'remove', 'origin')
        self.source.rename(self.root / 'original-unavailable')
        shared.rename(self.root / 'shared-unavailable')
        manifest = json.loads((self.mainout / 'main.json').read_text())
        bundle = s.checked_bundle(self.mainout, manifest)
        target = self.root / 'restored'
        s.clone_bundle(bundle, target, self.main, manifest['refs'])
        self.assertEqual(s.git(target, 'rev-parse', 'HEAD'), self.main)

    def test_all_main_profiles_and_parent_history_preserved(self):
        newer = self.commit_file(self.source, 'later.txt', 'P3')
        manifest = self.export_main({'historical-p3': {'repository': str(self.source), 'revision': newer}})
        target = self.root / 'restored-main'
        s.clone_bundle(s.checked_bundle(self.mainout, manifest), target, newer, manifest['refs'])
        self.assertEqual(s.git(target, 'rev-parse', 'refs/heads/historical-p2'), self.main)
        self.assertEqual(s.git(target, 'rev-parse', 'refs/heads/historical-p3'), newer)
        s.git(target, 'merge-base', '--is-ancestor', self.main, newer)

    def test_integration_tracked_core_is_not_overwritten_by_old_submodule(self):
        self.export_deps()
        s.git(self.source, 'update-index', '--force-remove', 'lib')
        blob = s.git(self.source, 'hash-object', '-w', '--stdin', input_text='tracked integration core\n')
        s.git(self.source, 'update-index', '--add', '--cacheinfo', '100644', blob, 'lib/integration.txt')
        s.git(self.source, 'commit', '-m', 'integrate core')
        integrated = s.git(self.source, 'rev-parse', 'HEAD')
        self.export_main({'integration': {'repository': str(self.source), 'revision': integrated}})
        target = self.root / 'integration'
        result = s.restore(self.mainout / 'main.json', self.deps / 'dependencies.json', 'integration', target)
        self.assertEqual(result['restored_dependencies'], {})
        self.assertFalse((target / 'lib/.git').exists())
        self.assertEqual((target / 'lib/integration.txt').read_text(), 'tracked integration core\n')

    def test_bundle_corruption_refused(self):
        manifest = self.export_deps()
        record = manifest['dependencies']['lib']
        with (self.deps / record['bundle']).open('ab') as f:
            f.write(b'corrupt')
        with self.assertRaises(ValueError):
            s.restore_dependencies(self.deps / 'dependencies.json', self.root / 'smoke', ['lib'])

    def test_lock_gitlink_mismatch_refused(self):
        lock = json.loads(self.lock.read_text())
        lock['dependencies']['lib'] = self.nested
        s.save(self.lock, lock)
        with self.assertRaises(ValueError):
            self.export_deps()

    def test_unsafe_relative_paths_refused(self):
        for path in ('../escape', '/tmp/escape', 'lib/.git/config', 'lib//nested', '.'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                s.safe_path(path)

    def test_resume_reuses_verified_bundles(self):
        first = self.export_deps()
        before = {r['bundle']: (self.deps / r['bundle']).stat().st_mtime_ns for r in first['dependencies'].values()}
        resumed = self.export_deps(resume=True)
        self.assertTrue(resumed['complete'])
        after = {r['bundle']: (self.deps / r['bundle']).stat().st_mtime_ns for r in resumed['dependencies'].values()}
        self.assertEqual(before, after)

    def test_dependency_only_restore_selects_ancestors(self):
        self.export_deps()
        result = s.restore_dependencies(self.deps / 'dependencies.json', self.root / 'smoke', ['lib/nested'])
        self.assertEqual(result, self.pins)

    def test_existing_destination_refused(self):
        self.export_deps()
        target = self.root / 'present'
        target.mkdir()
        with self.assertRaises(ValueError):
            s.restore_dependencies(self.deps / 'dependencies.json', target, ['lib'])

    def test_incomplete_dependency_set_cannot_claim_full_restore(self):
        self.export_deps()
        manifest = json.loads((self.deps / 'dependencies.json').read_text())
        del manifest['dependencies']['lib/nested']
        s.save(self.deps / 'dependencies.json', manifest)
        self.export_main()
        with self.assertRaises(ValueError):
            s.restore(self.mainout / 'main.json', self.deps / 'dependencies.json', 'historical-p2', self.root / 'restored')

    def test_source_lock_bytes_preserve_recorded_digest(self):
        self.lock.write_text(json.dumps(json.loads(self.lock.read_text()), separators=(',', ':')))
        manifest = self.export_deps()
        self.assertEqual(s.digest(self.lock), manifest['source_lock_sha256'])
        self.assertEqual(s.digest(self.deps / 'source-lock.json'), manifest['source_lock_sha256'])

    def test_explicit_exclusion_is_recorded_and_not_restored(self):
        lock = json.loads(self.lock.read_text())
        del lock['dependencies']['lib/nested']
        lock['excluded_dependencies'] = {'lib/nested': {'gitlink': self.nested, 'reason': 'Fixture intentionally outside build scope'}}
        s.save(self.lock, lock)
        self.export_deps()
        self.export_main()
        target = self.root / 'restored'
        result = s.restore(self.mainout / 'main.json', self.deps / 'dependencies.json', 'historical-p2', target)
        self.assertEqual(result['restored_dependencies'], {'lib': self.lib})
        self.assertEqual(result['unpopulated_gitlinks']['lib/nested']['gitlink'], self.nested)
        self.assertFalse((target / 'lib/nested/.git').exists())

    def test_identical_commit_dependencies_share_one_bundle(self):
        s.git(None, 'clone', '--shared', str(self.source / 'lib'), str(self.source / 'alias'))
        self.link(self.source, 'alias', self.lib)
        self.commit_file(self.source, 'alias-note.txt', 'same commit at another path')
        lock = json.loads(self.lock.read_text())
        lock['dependencies']['alias'] = self.lib
        s.save(self.lock, lock)
        manifest = self.export_deps()
        self.assertEqual(manifest['dependencies']['alias']['bundle'], manifest['dependencies']['lib']['bundle'])
        self.assertEqual(len(list((self.deps / 'bundles').glob('*.bundle'))), 2)

    def test_manifest_contains_no_source_machine_paths(self):
        self.export_deps()
        self.export_main()
        for file in (self.deps / 'dependencies.json', self.mainout / 'main.json'):
            self.assertNotIn(str(self.root), file.read_text())


if __name__ == '__main__':
    unittest.main()
