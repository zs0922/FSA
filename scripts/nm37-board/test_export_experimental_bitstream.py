import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('export_experimental_bitstream.py')
TOP = 'chipyard.fpga.nm37.NM37FPGATestHarness.EmptyNM37Config'

class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.build = self.root/'build'
        obj = self.build/'A70/fpga'/TOP/'obj'
        obj.mkdir(parents=True)
        (obj/'post_route.dcp').write_bytes(b'fixture-dcp')
        (self.build/'build.json').write_text('{}')
        self.out = self.root/'experiment'
        self.exe = self.root/'vivado'
        self.exe.write_text('#!/bin/sh\nexit 1\n')
        self.exe.chmod(0o755)

    def run_export(self):
        return subprocess.run([sys.executable,str(SCRIPT),'--build-out',str(self.build),
            '--profile','A70-P3','--out',str(self.out),'--vivado',str(self.exe)],capture_output=True,text=True)

    def test_existing_output_untouched(self):
        self.out.mkdir()
        marker=self.out/'keep'; marker.write_text('original')
        self.assertNotEqual(self.run_export().returncode,0)
        self.assertEqual(marker.read_text(),'original')
        self.assertEqual(list(self.out.iterdir()),[marker])

    def test_missing_checkpoint_no_output(self):
        (self.build/'A70/fpga'/TOP/'obj/post_route.dcp').unlink()
        self.assertNotEqual(self.run_export().returncode,0)
        self.assertFalse(self.out.exists())

    def test_failure_preserves_diagnostics_no_seal(self):
        self.assertNotEqual(self.run_export().returncode,0)
        m=json.loads((self.out/'experiment.json').read_text())
        self.assertEqual(m['status'],'failed')
        self.assertFalse(m['release_approved'])
        self.assertFalse(list(self.out.rglob('*.passed.json')))
        self.assertFalse((self.out/'SHA256SUMS').exists())

    def test_success_bound_to_bit_without_release_approval(self):
        self.exe.write_text('#!'+sys.executable+'\nimport sys\nfrom pathlib import Path\nPath(sys.argv[-1]).write_bytes(b"fixture-bit")\n')
        self.assertEqual(self.run_export().returncode,0)
        m=json.loads((self.out/'experiment.json').read_text())
        self.assertEqual(m['status'],'exported')
        self.assertFalse(m['release_approved'])
        self.assertEqual(m['bitstream_sha256'],hashlib.sha256(b'fixture-bit').hexdigest())
        self.assertFalse((self.build/'bitstream.passed.json').exists())

if __name__=='__main__':unittest.main()
