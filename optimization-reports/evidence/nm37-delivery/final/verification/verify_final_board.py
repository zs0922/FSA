import hashlib,json,posixpath,re,subprocess,sys,tarfile
from pathlib import Path
b=Path('/home/zhangsi/tmp/FSA-m1/nm37-a70-rebuild-20261022'); d=b/'delivery-final'; review=b/'review'; commit='d589b230b975730c108426591e552c056008e82a'
receipt=json.loads((review/'final-H1b-board-archive.log').read_text()); archive=receipt['archive']; root=b/'migration-final-board-extract-v2'
def run(cmd):
 p=subprocess.run(cmd,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True)
 return json.loads(p.stdout)
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for c in iter(lambda:f.read(1048576),b''): h.update(c)
 return h.hexdigest()
extracted=run([sys.executable,str(b/'src/scripts/nm37-board/package.py'),'extract','--archive',str(d/'nm37-board-transfer.tar.gz'),'--sha256',archive['sha256'],'--out',str(root)])
verified=run([sys.executable,str(root/'board-tools/package.py'),'verify','--root',str(root)])
dry=[]
for identity in ['A70-P3','A100','A110-APB55']:
 manifest=root/(identity+'.manifest.json'); m=json.loads(manifest.read_text()); out=review/('final-H1b-'+identity+'-dryrun-v2')
 result=run([sys.executable,str(root/'board-tools/board_test.py'),'--manifest',str(manifest),'--device','xdma0','--bdf','0000:81:00.0','--loaded-bitstream-sha256',m['bitstream']['sha256'],'--seq','16','--seed','0','--out',str(out),'--dry-run'])
 assert result=={'dry_run':True,'hardware_tested':False,'files_verified':True}
 dry.append({'configuration_id':identity,'manifest_sha256':sha(manifest),'bit_sha256':m['bitstream']['sha256'],'hardware_source_commit':m['hardware_source_commit'],'identity_kind':m['source_identity_kind'],'result':result,'run_metadata_sha256':sha(out/'run.json')})
plan=json.loads((b/'delivery-plan-final/attachments-plan.json').read_text()); queue=[root/'docs/repository'/p for p in plan['report_roots']]; queue.extend((root/'board-tools').rglob('*.md')); seen=set(); links=0; absolute=[]
while queue:
 path=queue.pop().resolve()
 if path in seen:continue
 seen.add(path)
 for _,target in re.findall(r'\[([^\]]*)\]\(([^)]+)\)',path.read_text()):
  if target.startswith(('http://','https://','#')):continue
  if target.startswith('/'):
   absolute.append({'report':str(path.relative_to(root)),'historical_absolute_link':target});continue
  dest=(path.parent/target.split('#')[0]).resolve()
  assert dest.exists(),(path,target)
  links+=1
  if dest.is_file() and dest.suffix=='.md':queue.append(dest)
long_names=[]
with tarfile.open(d/'nm37-board-transfer.tar.gz','r|gz') as tar:
 for item in tar:
  assert item.isfile() and item.uid==item.gid==item.mtime==0 and item.uname==item.gname=='' and item.mode==0o644
  assert set(item.pax_headers)<= {'path'}
  if len(Path(item.name).name.encode())>100:long_names.append(item.name)
assert long_names
assert (root/'docs/repository/optimization-reports/evidence/nm37-delivery/pax-package-fix.json').exists()
result={'passed':True,'integration_commit':commit,'official_packager':True,'archive':archive,'package_sha256':receipt['package_sha256'],'extract':extracted,'verify':verified,'dry_runs':dry,'offline_device_arguments_are_fixtures':True,'hardware_tested':False,'relative_report_and_tool_links_verified':links,'historical_absolute_links_preserved':absolute,'normalized_pax_metadata_verified':True,'long_basename_files':long_names,'nm37_delivery_evidence_present':True,'extracted_root':str(root)}
(review/'final-H1b-board-validation.json').write_text(json.dumps(result,indent=2)+'\n')
(d/'board-archive-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(result,indent=2))
