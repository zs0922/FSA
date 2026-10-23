"""Exercise the extra-sequence verifier with a synthetic engine, not RTL."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class WorkloadVerifierTest(unittest.TestCase):
    def test_1024_budget_and_bitwise_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';python=source/'generators/fsa/python'
            python.mkdir(parents=True)
            (python/'fsa.py').write_text('''import numpy as np
from pathlib import Path
from types import SimpleNamespace
def init(path): pass
def get_config(): return SimpleNamespace(sa_rows=16,sa_cols=16)
def from_numpy(a): return a
def to_numpy(a): return a
class VerilatorSimulator:
    def __init__(self,binary,output_dir,max_cycles,**kwargs):
        Path(output_dir,'fixture-cycle-budget.txt').write_text(str(max_cycles))
    def execute(self,kernel): return kernel
''')
            (python/'main.py').write_text('''import numpy as np
def scaled_dot_product_attention(q,k,v,*args): return np.zeros((16,q.shape[0]),dtype=np.float32)
def ref_pyeasyfloat(q,k,v,*args): return np.zeros((q.shape[0],16),dtype=np.float32)
''')
            binary=root/'fake-simulator';binary.write_bytes(b'synthetic engine fixture, not RTL')
            config=root/'config.json';config.write_text('{}')
            script=Path(__file__).with_name('verify_a70_workload.py')
            command=[sys.executable,str(script),'--source',str(source),'--binary',str(binary),'--config',str(config),
                     '--seq','1024','--max-cycles','20000000','--out',str(root/'ok')]
            r=subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)
            result=json.loads((root/'ok/result.json').read_text())
            self.assertTrue(result['bitwise_equal']);self.assertEqual(result['shape'],[1024,16])
            self.assertEqual(result['max_cycles'],20000000)
            self.assertEqual((root/'ok/fixture-cycle-budget.txt').read_text(),'20000000')
            # Change engine output sign of zero: numeric equality alone must not pass.
            p=python/'fsa.py';p.write_text(p.read_text().replace('return kernel','return -kernel'))
            command[-1]=str(root/'bad')
            r=subprocess.run(command,capture_output=True,text=True)
            self.assertNotEqual(r.returncode,0)
            self.assertFalse(json.loads((root/'bad/result.json').read_text())['bitwise_equal'])

    def test_invalid_size_before_runtime_import(self):
        script=Path(__file__).with_name('verify_a70_workload.py')
        r=subprocess.run([sys.executable,str(script),'--source','/nonexistent','--binary','/none','--config','/none',
                          '--out','/nonexistent-extra-output','--seq','0'],capture_output=True,text=True)
        self.assertEqual(r.returncode,2)
        self.assertIn('must be positive',r.stderr)


if __name__=='__main__':unittest.main()
