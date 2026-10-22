#!/usr/bin/env python3
"""Compare P2/P3 regression artifacts without requiring identical cycle counts."""
import argparse,hashlib,json,re
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--p2',type=Path,required=True);p.add_argument('--p3',type=Path,required=True)
p.add_argument('--p2-log',type=Path,required=True);p.add_argument('--p3-log',type=Path,required=True)
p.add_argument('--out',type=Path,required=True)
a=p.parse_args()
results=[json.loads((d/'result.json').read_text()) for d in [a.p2,a.p3]]
assert all(x['bitwise_equal'] for x in results),'An individual reference regression failed'
for key in ['shape','seed','seq','array_rows','input_sha256','actual_sha256','expected_sha256']:
    assert results[0][key]==results[1][key],f'P2/P3 input or result mismatch: {key}'
assert (a.p2/'inst.bin').read_bytes()==(a.p3/'inst.bin').read_bytes(),'Different workload instructions'
counters=[]
for log in [a.p2_log,a.p3_log]:
    pairs=re.findall(r'FSA: (\w+) =\s*(\d+)',log.read_text())
    assert len(pairs)==8 and len(dict(pairs))==8,'Missing or repeated hardware counter snapshot'
    cnt={k:int(v) for k,v in pairs}
    assert cnt['execTime']>0 and cnt['fence']>0 and cnt['mxInst']>0
    counters.append(cnt)
result=dict(passed=True,source='actual hardware simulator counters',p2=counters[0],p3=counters[1],
    p3_minus_p2={k:counters[1][k]-counters[0][k] for k in counters[0]},
    compared_fields=['input arrays','instruction bytes','actual output bits','reference output bits'],
    artifacts={str(f.resolve()):hashlib.sha256(f.read_bytes()).hexdigest() for f in
      [a.p2/'result.json',a.p3/'result.json',a.p2/'inst.bin',a.p3/'inst.bin',a.p2_log,a.p3_log]})
assert not a.out.exists(),'Comparison output already exists'
a.out.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
