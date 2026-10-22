import hashlib,importlib.util,json,sys,tarfile,tempfile
from pathlib import Path,PurePosixPath
sys.dont_write_bytecode=True
b=Path('/home/zhangsi/tmp/FSA-m1/nm37-a70-rebuild-20261022'); d=b/'delivery-final'; root=d/'nm37-source-transfer'; commit='d589b230b975730c108426591e552c056008e82a'
r=json.loads((d/'source-archive-receipt.json').read_text())
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for c in iter(lambda:f.read(1048576),b''):h.update(c)
 return h.hexdigest()
assert sha(d/r['archive_name'])==r['archive_sha256']
expected={line.split('  ',1)[1]:line.split('  ',1)[0] for line in (root/'SOURCE_SHA256SUMS').read_text().splitlines()}
expected['SOURCE_SHA256SUMS']=sha(root/'SOURCE_SHA256SUMS')
seen=set(); total=0
with tarfile.open(d/r['archive_name'],'r|gz') as tar:
 for item in tar:
  p=PurePosixPath(item.name)
  assert item.isfile() and not p.is_absolute() and '..' not in p.parts and item.name in expected and item.name not in seen
  assert item.uid==item.gid==item.mtime==0 and item.mode==0o644 and item.uname==item.gname==''
  seen.add(item.name);h=hashlib.sha256()
  with tar.extractfile(item) as f:
   for c in iter(lambda:f.read(1048576),b''): h.update(c);total+=len(c)
  assert h.hexdigest()==expected[item.name]
assert seen==set(expected)
spec=importlib.util.spec_from_file_location('delivery_source',root/'source_bundle.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
main=json.loads((root/'migration-main/main.json').read_text()); mod.checked_bundle(root/'migration-main',main)
assert len(main['profiles'])==9 and main['profiles']['integration']['commit']==commit
result={'passed':True,'integration_commit':commit,'archive_sha256':r['archive_sha256'],'archive_bytes':r['archive_bytes'],'all_archive_members_stream_hashed':len(seen),'payload_bytes':total,'regular_unique_relative_members_only':True,'normalized_metadata_verified':True,'final_main_bundle_verified_in_empty_object_database':True,'profiles':{k:v['commit'] for k,v in main['profiles'].items()},'main_bundle_sha256':main['sha256'],'dependency_bundle_empty_verification_in_archive_finalizer':r['all_bundles_empty_repository_verified'],'prior_full_175_dependency_restore':'review/source-bundle-full-restore-acceptance.json','full_restore_not_repeated':True}
(b/'review/final-H1b-source-validation.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
