#!/usr/bin/env python3
"""Compare exact expansion to Yosys native sequential SAT on positive/negative fixtures."""
import copy, json, os, subprocess
from pathlib import Path
from unroll import expand
P=Path(__file__).resolve().parent
F=P/'fixtures';F.mkdir(exist_ok=True)
Y=os.environ.get('YOSYS','yowasp-yosys')
ENV=dict(os.environ,XDG_CACHE_HOME=str(P/'cache'))
class Design:
 def __init__(self):
  self.n=2;self.m=dict(attributes={},ports={},cells={},netnames={});self.clk=self.input('clock',1)
 def wire(self,w):
  v=list(range(self.n,self.n+w));self.n+=w;return v
 def input(self,n,w):
  b=self.wire(w);self.m['ports'][n]=dict(direction='input',bits=b);return b
 def cell(self,n,t,inputs,widths,params):
  con=dict(inputs);dirs={p:'input' for p in inputs}
  for p,w in widths.items():con[p]=self.wire(w);dirs[p]='output'
  self.m['cells'][n]=dict(hide_name=0,type=t,parameters={k:(str(v) if isinstance(v,str) else format(v,'b')) for k,v in params.items()},attributes={},port_directions=dirs,connections=con)
  return {p:con[p] for p in widths}
 def comb(self,n,t,inputs,w,params=None):
  return self.cell(n,t,inputs,{'Y':w},params or {'WIDTH':w})['Y']
 def dff(self,n,d):return self.cell(n,'$dff',{'CLK':self.clk,'D':d},{'Q':len(d)},{'WIDTH':len(d),'CLK_POLARITY':1})['Q']
 def pipe(self,n,d,depth):
  for t in range(depth):d=self.dff(f'{n}{t}',d)
  return d
 def neq(self,n,a,b):
  return self.comb(n,'$ne',{'A':a,'B':b},1,{'A_SIGNED':0,'B_SIGNED':0,'A_WIDTH':len(a),'B_WIDTH':len(b),'Y_WIDTH':1})
 def finish(self,e):
  self.m['ports']['mismatch']=dict(direction='output',bits=e)
  for n,p in self.m['ports'].items():self.m['netnames'][n]=dict(hide_name=0,bits=p['bits'],attributes={})
  return self.m

def pipeline(wrong=None):
 d=Design();x=d.input('data',3);g=d.pipe('gold',x,3);v=d.pipe('gate',x,2 if wrong=='latency' else 3)
 if wrong=='value':v=d.comb('invert','$not',{'A':v},3,{'A_SIGNED':0,'A_WIDTH':3,'Y_WIDTH':3})
 return d.finish(d.neq('neq',g,v)),3

def sdff(polarity,wrong=False):
 d=Design();x=d.input('data',3);r=d.input('reset',1);val=list('101');
 mux=d.comb('mux','$mux',{'A':x if polarity else val,'B':val if polarity else x,'S':r},3)
 g=d.dff('gold',mux)
 v=d.cell('gate','$sdff',{'CLK':d.clk,'D':x,'SRST':r},{'Q':3},{'WIDTH':3,'CLK_POLARITY':1,'SRST_POLARITY':1-polarity if wrong else polarity,'SRST_VALUE':'101'})['Q']
 return d.finish(d.neq('neq',g,v)),1

def multioutput():
 d=Design();a=d.input('a',3);b=d.input('b',3)
 alu=d.cell('alu','$alu',{'A':a,'B':b,'CI':['0'],'BI':['0']},{'X':3,'Y':3,'CO':3},{'A_SIGNED':0,'B_SIGNED':0,'A_WIDTH':3,'B_WIDTH':3,'Y_WIDTH':3})
 sm=d.comb('sum','$add',{'A':a,'B':b},4,{'A_SIGNED':0,'B_SIGNED':0,'A_WIDTH':3,'B_WIDTH':3,'Y_WIDTH':4})
 xr=d.comb('xor','$xor',{'A':a,'B':b},3,{'A_SIGNED':0,'B_SIGNED':0,'A_WIDTH':3,'B_WIDTH':3,'Y_WIDTH':3})
 errors=d.neq('neq_y',alu['Y'],sm[:3])+d.neq('neq_co',alu['CO'][2:],sm[3:])+d.neq('neq_x',alu['X'],xr)
 out=d.comb('error','$reduce_or',{'A':errors},1,{'A_SIGNED':0,'A_WIDTH':3,'Y_WIDTH':1})
 return d.finish(out),0

cases=[('dff3',*pipeline(),True),('wrong_latency',*pipeline('latency'),False),('wrong_value',*pipeline('value'),False),('sdff_high',*sdff(1),True),('sdff_low',*sdff(0),True),('sdff_wrong_polarity',*sdff(1,True),False),('multioutput_shared',*multioutput(),True)]
results=[]
for name,original,depth,expected in cases:
 case=F/name;case.mkdir(exist_ok=True)
 unrolled,audit=expand(original,{'mismatch':original['ports']['mismatch']['bits']},depth)
 outcome={}
 for kind,m in [('native',original),('expanded',unrolled)]:
  (case/f'{kind}.json').write_text(json.dumps({'modules':{'fixture':m}},indent=2)+'\n')
  seq=f'-seq {depth+1} -prove-skip {depth}' if kind=='native' else ''
  script=f'read_json {kind}.json\nhierarchy -check -top fixture\ntechmap\nopt\ncheck -assert\nsat -verify {seq} -prove mismatch 0 -show-inputs -show-outputs\n'
  (case/f'{kind}.ys').write_text(script)
  with (case/f'{kind}.log').open('w') as log:
   run=subprocess.run([Y,'-s',f'{kind}.ys'],cwd=case,env=ENV,stdout=log,stderr=subprocess.STDOUT)
  text=(case/f'{kind}.log').read_text()
  success=run.returncode==0 and 'SAT proof finished - no model found: SUCCESS!' in text
  failure=run.returncode!=0 and 'proof did fail' in text
  assert success if expected else failure,(name,kind,run.returncode,text[-2000:])
  outcome[kind]='PASS' if success else 'EXPECTED_COUNTEREXAMPLE'
 results.append(dict(name=name,expected_equivalent=expected,**outcome))
# Fail closed on unsupported temporal or combinational semantics.
base,_=pipeline()
for name,mutate in [
 ('other_clock',lambda m:m['cells']['gate0']['connections'].update(CLK=m['ports']['data']['bits'][:1])),
 ('negative_edge',lambda m:m['cells']['gate0']['parameters'].update(CLK_POLARITY='0')),
 ('unknown_cell',lambda m:m['cells']['gate0'].update(type='$unknown')),
 ('asynchronous_register',lambda m:m['cells']['gate0'].update(type='$adff')),
]:
 m=copy.deepcopy(base);mutate(m)
 try:expand(m,{'mismatch':m['ports']['mismatch']['bits']},3)
 except AssertionError:results.append(dict(name=name,rejected=True))
 else:raise AssertionError(f'{name} unexpectedly accepted')
try:expand(base,{'mismatch':base['ports']['mismatch']['bits']},2)
except AssertionError:results.append(dict(name='initial_state_dependency',rejected=True))
else:raise AssertionError('initial-state dependency unexpectedly accepted')
(F/'result.json').write_text(json.dumps(dict(passed=True,cases=results),indent=2)+'\n')
print(json.dumps(dict(passed=True,cases=results),indent=2))
