"""One-time reviewed transition for the original, unchanged P3 phase seals."""
import hashlib,json,os,subprocess,sys
from pathlib import Path
import implementation_generated
out=Path(os.environ['A70_OUT']).resolve();helper=Path(__file__).resolve().parent
method=json.loads((out/'bitstream-method-update.json').read_text())
def sha(p):return hashlib.file_digest(p.open('rb'),'sha256').hexdigest()
def inventory(root):return {str(p.relative_to(root)):sha(p) for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}
assert method['accepted_by_parent'] is True and method['hardware_changed'] is False
assert method['new_export_driver_files']==inventory(helper),'Reviewed export driver changed'
old=Path(method['original_frozen_driver_directory'])
assert method['original_driver_manifest_sha256']==sha(out/'drivers.json')
assert json.loads((out/'drivers.json').read_text())==inventory(old),'Original frozen helpers changed'
assert sha(out/'implementation-generated.passed.json')==method['implementation_generated_seal_sha256']
assert sha(out/'source-lock.json')==method['source_lock_sha256']
subprocess.run(['python3',str(old/'artifacts.py'),'check','--out',str(out)],check=True)
newfiles=implementation_generated.check(out)['generated']
for phase in ['assembly','units','formal','sim4','sim16','elaborate','implement']:
 p=out/(phase+'.passed.json');assert sha(p)==method['original_phase_seals_sha256'][phase]
 seal=json.loads(p.read_text());assert seal['source_lock_sha256']==method['source_lock_sha256']
 actual={}
 for name in seal['roots']:
  root=out/name;assert root.exists()
  for f in sorted(root.rglob('*')) if root.is_dir() else [root]:
   if phase=='elaborate' and root.is_dir() and (f==root/'obj' or root/'obj' in f.parents):continue
   if f.is_file() and '__pycache__' not in f.parts:actual[str(f.relative_to(out))]=sha(f)
 expected=dict(seal['files']);assert expected
 if phase=='elaborate':
  assert not set(expected)&set(newfiles)
  expected.update(newfiles)
 assert expected==actual,'Phase artifact identity differs: '+phase
reviewpath=out/'cdc-constraint-review.json';assert sha(reviewpath)==method['cdc_constraint_review_sha256']
r=json.loads(reviewpath.read_text());assert r['accepted_for_board_test'] and r['board_tested'] is False
assert r['source_lock_sha256']==method['source_lock_sha256'] and r['analysis'] and r['known_boundaries']
obj,=(out/'A70/fpga').glob('*/obj')
required=['post_route.dcp','audit/cdc.txt','audit/check_timing.txt','audit/clock_interaction.txt','audit/exceptions.txt']
assert r['evidence_sha256']=={n:sha(obj/n) for n in required}
subprocess.run(['python3',str(old/'physical.py'),'--obj',str(obj),'--out',str(out/'physical-export-recheck.json')],check=True)
print('PASS: unchanged original source/drivers/phase seals, explicitly reviewed implementation additions, CDC and full physical checks')
