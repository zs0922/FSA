"""Read header-tagged bitstreams; hashes are identity evidence, not functional proof."""
from pathlib import Path
import hashlib,json
B=Path(__file__).resolve().parents[1]
def parse(path):
 b=Path(path).read_bytes();u16=lambda p:int.from_bytes(b[p:p+2],'big')
 p=2+u16(0);assert b[2:p]==bytes.fromhex('0ff00ff00ff00ff000')
 assert u16(p)==1;p+=2;header={}
 for tag in 'abcd':
  assert b[p:p+1]==tag.encode();p+=1;n=u16(p);p+=2
  header[tag]=b[p:p+n].rstrip(b'\x00').decode('ascii');p+=n
 assert b[p:p+1]==b'e';p+=1;n=int.from_bytes(b[p:p+4],'big');p+=4
 assert p+n==len(b)
 return dict(path=str(path),bytes=len(b),sha256=hashlib.sha256(b).hexdigest(),header=header,payload_offset=p,payload_bytes=n,payload_sha256=hashlib.sha256(b[p:]).hexdigest())
old=parse(Path('/home/zhangsi/FSA/nm37-stage-results/p3/NM37FPGATestHarness-p3.bit'))
new=parse(B/'final-p3-v2/bitstream/NM37_A70.bit')
assert old['payload_sha256']=='3f2f77a21926b6257114900759f86b403baeeda79153230e1b80c92f2d65fc45'
assert old['payload_sha256']!=new['payload_sha256']
result={'historical_mislabeled_p3':old,'new_a70_p3':new,'payloads_differ':True,'meaning':'The configuration payload differs, independently of header timestamp/name changes. Optimized structure and correctness require the separate source/JAR/RTL/verification/implementation evidence chain.','board_tested':False}
p=B/'review/a70-p3-bit-identity.json';p.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
