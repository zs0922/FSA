import io
import json
from pathlib import Path
import tarfile
import unittest
from unittest.mock import Mock
import board_test as b
import package as pkg
import test_board_test as fixture_module


class PackageTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.BoardTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        manifest = self.fixture.manifest
        self.spec = {'schema_version': 1, 'software_root': str(self.root / 'software'),
                     'configurations': [{'configuration_id': 'A100', 'hardware_source_commit': 'a' * 40,
                          'build_id': 'fixture-only', 'source_identity_kind': 'built_commit',
                          'bitstream': {'path': str(self.root / 'image.bit'), 'sha256': manifest['bitstream']['sha256']},
                          'config': {'path': str(self.root / 'config.json'), 'sha256': manifest['config']['sha256']}}]}
        self.spec_path = self.root / 'spec.json'
        self.output = self.root / 'package'
        self.write_spec()

    def write_spec(self):
        b.save(self.spec_path, self.spec)

    def build(self):
        self.write_spec()
        return pkg.build(self.spec_path, self.output)

    def test_roundtrip_and_extracted_manifest_dryrun(self):
        result = self.build()
        self.assertEqual(result['configurations'], ['A100'])
        self.assertFalse((self.output / 'A70-P3.manifest.json').exists())
        archive = self.root / 'package.tar.gz'
        packaged = pkg.archive(self.output, archive)
        extracted = self.root / 'extracted'
        self.assertTrue(pkg.extract(archive, extracted, packaged['sha256'])['verified'])
        args = self.fixture.args
        args.manifest, args.out, args.dry_run = extracted / 'A100.manifest.json', self.root / 'dryrun', True
        factory, probe = Mock(), Mock()
        self.assertFalse(b.run(args, factory, probe)['hardware_tested'])
        factory.assert_not_called()
        probe.assert_not_called()
        for path in extracted.rglob('*'):
            if path.is_file():
                self.assertNotIn(str(self.root).encode(), path.read_bytes())

    def test_actual_source_reproduction_doc_is_bound_and_safely_extracted(self):
        original = Path(pkg.__file__).resolve().parent / 'docs/source-reproduction.md'
        self.assertTrue(original.is_file())
        self.build()
        name = 'board-tools/docs/source-reproduction.md'
        self.assertEqual((self.output / name).read_bytes(), original.read_bytes())
        info = json.loads((self.output / 'package.json').read_text())
        self.assertEqual(info['files'][name]['sha256'], b.sha(original))
        self.assertIn(b.sha(original) + '  ' + name,
                      (self.output / 'SHA256SUMS').read_text().splitlines())
        self.assertTrue(pkg.verify(self.output)['verified'])
        archive = self.root / 'with-docs.tar.gz'
        packaged = pkg.archive(self.output, archive)
        with tarfile.open(archive) as tar:
            member = tar.getmember(name)
            self.assertTrue(member.isfile())
            self.assertEqual(tar.extractfile(member).read(), original.read_bytes())
        extracted = self.root / 'extracted-with-docs'
        self.assertTrue(pkg.extract(archive, extracted, packaged['sha256'])['verified'])
        self.assertEqual((extracted / name).read_bytes(), original.read_bytes())
        (extracted / name).write_text('changed documentation')
        with self.assertRaises(ValueError):
            pkg.verify(extracted)

    def test_easyfloat_changes_are_rejected_by_both_verifiers(self):
        self.build()
        easy = self.output / 'software/generators/easyfloat/pyeasyfloat/backend.py'
        easy.write_text('corrupted')
        with self.assertRaises(ValueError):
            pkg.verify(self.output)
        with self.assertRaises(ValueError):
            b.validate_manifest(self.output / 'A100.manifest.json')

    def test_source_cache_venv_rtl_build_outputs_excluded(self):
        for name in ('software/generators/fsa/python/.venv/payload.py',
                     'software/generators/easyfloat/target/generated.py',
                     'software/generators/easyfloat/src/main/rtl.sv'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('must not be included')
        self.build()
        files = pkg.inventory(self.output)
        self.assertFalse(any('.venv' in name or 'target' in name or '.sv' in name for name in files))

    def test_duplicate_configs_rejected(self):
        self.spec['configurations'].append(dict(self.spec['configurations'][0]))
        with self.assertRaises(ValueError):
            self.build()
        self.assertFalse(self.output.exists())

    def test_wrong_bit_hash_rejected(self):
        self.spec['configurations'][0]['bitstream']['sha256'] = 'f' * 64
        with self.assertRaises(ValueError):
            self.build()
        self.assertFalse(self.output.exists())

    def test_empty_config_set_rejected(self):
        self.spec['configurations'] = []
        with self.assertRaises(ValueError):
            self.build()

    def test_archive_hash_mismatch_does_not_extract(self):
        self.build()
        archive = self.root / 'p.tar.gz'
        pkg.archive(self.output, archive)
        with self.assertRaises(ValueError):
            pkg.extract(archive, self.root / 'extract', '0' * 64)
        self.assertFalse((self.root / 'extract').exists())

    def test_archive_path_traversal_rejected(self):
        archive = self.root / 'bad.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            info = tarfile.TarInfo('../escaped')
            info.size = 1
            tar.addfile(info, io.BytesIO(b'x'))
        with self.assertRaises(ValueError):
            pkg.extract(archive, self.root / 'extract', b.sha(archive))
        self.assertFalse((self.root / 'escaped').exists())

    def test_archive_symlink_rejected(self):
        archive = self.root / 'bad.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            info = tarfile.TarInfo('link')
            info.type, info.linkname = tarfile.SYMTYPE, '/tmp'
            tar.addfile(info)
        with self.assertRaises(ValueError):
            pkg.extract(archive, self.root / 'extract', b.sha(archive))

    def test_existing_destinations_refused(self):
        self.build()
        with self.assertRaises(ValueError):
            self.build()
        archive = self.root / 'p.tar.gz'
        result = pkg.archive(self.output, archive)
        with self.assertRaises(ValueError):
            pkg.archive(self.output, archive)
        with self.assertRaises(ValueError):
            pkg.extract(archive, self.output, result['sha256'])

    def test_archive_is_reproducible(self):
        self.build()
        a = pkg.archive(self.output, self.root / 'a.tar.gz')
        c = pkg.archive(self.output, self.root / 'c.tar.gz')
        self.assertEqual(a['sha256'], c['sha256'])

    def test_attachment_escape_rejected(self):
        self.spec['attachments'] = [{'path': str(self.root / 'image.bit'),
                                     'sha256': b.sha(self.root / 'image.bit'), 'destination': '../bad'}]
        with self.assertRaises(ValueError):
            self.build()


if __name__ == '__main__':
    unittest.main()
