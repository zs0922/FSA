from pathlib import Path
import json,hashlib,gzip,shutil,re
B=Path(__file__).resolve().parents[1]
E=B/'src/optimization-reports/evidence/nm37-a70-rebuild'
N='chipyard.fpga.nm37.NM37FPGATestHarness.EmptyNM37Config'
def sha(p):return hashlib.file_digest(p.open('rb'),'sha256').hexdigest()
def copy(p,d):
 d.parent.mkdir(parents=True,exist_ok=True)
 if p.stat().st_size>150000 or p.suffix=='.log':
  d=Path(str(d)+'.gz')
  with p.open('rb') as i,d.open('wb') as o,gzip.GzipFile(fileobj=o,mode='wb',mtime=0) as z:shutil.copyfileobj(i,z)
 else:shutil.copyfile(p,d)
 return {'archive':str(d.relative_to(E)),'original_sha256':sha(p),'original_bytes':p.stat().st_size,'archive_sha256':sha(d)}
records={};stages={}
for stage,dirname in [('p2','final-p2'),('p3','final-p3-v2')]:
 out=B/dirname;obj=out/'A70/fpga'/N/'obj'
 review=json.loads((B/f'review/a70-{stage}-routed-cdc-review.json').read_text())
 assert review['timing']['passed']==(stage=='p3')
 for folder in ['audit','report']:
  for p in sorted((obj/folder).rglob('*')):
   if p.is_file():records[f'{stage}/{folder}/{p.relative_to(obj/folder)}']=copy(p,E/stage/folder/p.relative_to(obj/folder))
 for p in [out/'logs/implementation.log',out/'physical.passed.json',out/'implement.passed.json',out/'cdc-constraint-review.json',out/'physical-export-recheck.json',out/'bitstream.passed.json',out/'logs/bitstream.log',out/'logs/reviewed-export-gate.log',out/'implementation-generated.passed.json',out/'bitstream-method-update.json',out/'bitstream-attempt01-blocked.json']:
  if p.is_file():records[f'{stage}/{p.relative_to(out)}']=copy(p,E/stage/p.relative_to(out))
 if (out/'bitstream').is_dir():
  for p in sorted((out/'bitstream').iterdir()):
   if p.is_file() and p.suffix in ['.txt','.json']:records[f'{stage}/bitstream/{p.name}']=copy(p,E/stage/'bitstream'/p.name)
 for name in [f'a70-{stage}-routed-cdc-review.json']:
  records[name]=copy(B/'review'/name,E/name)
 util=(obj/'report/utilization.txt').read_text()
 row=next(l for l in util.splitlines() if re.match(r'\| NM37FPGATestHarness\s+\|',l))
 nums=[int(v.strip()) for v in row.split('|')[3:-1]]
 keys=['lut','logic_lut','lutram','srl','ff','ramb36','ramb18','uram','dsp']
 resources=dict(zip(keys,nums));assert len(nums)==len(keys)
 resources['utilization_percent']={k:round(resources[k]/n*100,4) for k,n in [('lut',1303680),('ff',2607360),('ramb36',2016),('dsp',9024)]}
 timing=review['timing'];path=(obj/'audit/dut_setup_top100.txt').read_text()
 first=path[path.index('Slack '):];first=first[:first.index('\n\n')]
 stages[stage]={'resources':resources,'timing':timing,'worst_dut_path':first,'checkpoint_sha256':sha(obj/'post_route.dcp'),'bitstream_exported':(out/'bitstream/NM37_A70.bit').is_file(),'board_tested':False}
 if stages[stage]['bitstream_exported']:
  bit=out/'bitstream/NM37_A70.bit';stages[stage]['bitstream']={'path':str(bit),'sha256':sha(bit),'bytes':bit.stat().st_size}
for name in ['new-a70-pcie-id.json','review-routed-cdc.py']:
 records[name]=copy(B/'review'/name,E/name)
(E/'routed-results.json').write_text(json.dumps(stages,indent=2)+'\n')
(E/'routed-archive-index.json').write_text(json.dumps(records,indent=2)+'\n')
print(json.dumps(stages,indent=2))
