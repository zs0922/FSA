#!/usr/bin/env python3
"""Strict implementation additions, separate from immutable elaboration inputs.

This module checks artifact identity only. Callers must also validate their
original source lock, frozen drivers, physical and CDC review gates.
"""
import argparse
import collections
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

HERE=Path(__file__).resolve().parent
POLICY=HERE/'implementation-generated-catalog.json'
SCHEMA=1

def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
 return h.hexdigest()
def write(p,data):
 with p.open('x') as f:json.dump(data,f,indent=2);f.write('\n')
def relative(name):
 p=Path(name);assert not p.is_absolute() and '..' not in p.parts,('Unsafe artifact path',name)
 return p

def current_files(out,elaborate):
 result={}
 for name in elaborate['roots']:
  root=out/relative(name);assert root.exists(),('Missing elaboration root',name)
  for p in sorted(root.rglob('*')) if root.is_dir() else [root]:
   if root.is_dir() and (p==root/'obj' or root/'obj' in p.parents):continue
   assert not p.is_symlink(),('Symlink outside artifact model',str(p))
   if p.is_file():result[str(p.relative_to(out))]=sha(p)
 assert result
 return result

def normal(value):
 v=value.strip().lower()
 try:return str(Decimal(v).normalize())
 except InvalidOperation:return v

def audit_xci(root,ip,xci,protected):
 scripts=[p for p in root.glob('*.vivado.tcl') if p.name.endswith('.'+ip+'.vivado.tcl')]
 assert len(scripts)==1,('No unique sealed IP script',ip)
 script=scripts[0]
 # The caller already verified every original seal file, including this Tcl.
 assert str(script.resolve()) in protected,('IP Tcl not in original seal',script)
 t=script.read_text().replace('\\\n',' ')
 assert re.search(r'-module_name\s+'+re.escape(ip)+r'\b',t),ip
 name=re.search(r'create_ip\b[^\n]*?-name\s+(\w+)',t);assert name,ip
 version=re.search(r'-version\s+([\d.]+)',t)
 xml=ET.fromstring(xci.read_text());local=lambda tag:tag.rsplit('}',1)[-1]
 ref=[e for e in xml.iter() if local(e.tag)=='componentRef'];assert len(ref)==1
 attrs={local(k):v for k,v in ref[0].attrib.items()}
 assert attrs['vendor']=='xilinx.com' and attrs['library']=='ip' and attrs['name']==name[1],(ip,attrs)
 if version:assert attrs['version']==version[1],(ip,attrs)
 vals={next(v for k,v in e.attrib.items() if local(k)=='referenceId'):e.text for e in xml.iter() if local(e.tag)=='configurableElementValue'}
 configs=dict(re.findall(r'CONFIG\.([\w.]+)\s+\{([^{}]*)\}',t));assert configs,ip
 checked={}
 for key,value in configs.items():
  actual=vals.get('PARAM_VALUE.'+key)
  # AXI4-Lite has no ID channel. Vivado 2020.2 canonicalizes the Tcl's
  # ID_WIDTH=1 placeholder to zero; allow only this exact inactive-field case.
  inactive_lite_id=(key=='ID_WIDTH' and value=='1' and actual=='0' and vals.get('PARAM_VALUE.PROTOCOL')=='AXI4LITE')
  assert actual is not None and (normal(actual)==normal(value) or inactive_lite_id),('XCI differs from sealed Tcl',ip,key,value,actual)
  checked[key]=dict(tcl=value,xci=actual,inactive_lite_id_canonicalization=inactive_lite_id)
 return dict(ip=ip,vlnv=attrs,script_sha256=sha(script),xci_sha256=sha(xci),explicit_config=checked)

def classify(out,elaborate=None):
 """Validate old inputs; classify exact additions. Does not write a seal."""
 out=Path(out).resolve();sealpath=out/'elaborate.passed.json'
 elaborate=elaborate or json.loads(sealpath.read_text())
 assert elaborate['source_lock_sha256']==sha(out/'source-lock.json'),'Source lock identity differs'
 old=elaborate['files'];assert old
 now=current_files(out,elaborate)
 removed=sorted(set(old)-set(now));changed=sorted(n for n in set(old)&set(now) if old[n]!=now[n])
 assert not removed and not changed,('Original elaboration inputs removed/changed',removed,changed)
 dirs=[out/relative(n) for n in elaborate['roots'] if (out/relative(n)/'gen-collateral').is_dir()]
 assert len(dirs)==1,'Expected exactly one A70 generated build root'
 root=dirs[0];policy=json.loads(POLICY.read_text());top=policy['top']
 assert (root/'gen-collateral'/f'{top}.sv').is_file(),'Unsupported top'
 allowed={str((root/p.format(top=top)).relative_to(out)):cat for p,cat in policy['paths'].items()}
 additions={n:now[n] for n in sorted(set(now)-set(old))}
 unknown=sorted(set(additions)-set(allowed));assert not unknown,('Unknown implementation additions',unknown)
 expected=set(allowed)-set(old);assert set(additions)==expected,('Incomplete implementation layout',sorted(expected-set(additions)))
 for n in ['vivado.log','vivado.jou']:assert policy['vivado'] in (root/n).read_text(errors='replace')[:512],('Unsupported Vivado version',n)
 protected={str((out/relative(n)).resolve()) for n in old}
 deps={};ip_audits=[]
 for ip in ['testxilinx_hbm','xdma_master_axi_clock_converter_0','xdma_master_lite_axi_clock_converter_0']:
  p=root/f'{top}.srcs/sources_1/ip/{ip}/{ip}.xci'
  ip_audits.append(audit_xci(root,ip,p,protected))
 # These two original IPs are created in obj by the sealed scripts. Their XCI
 # files are also fixed by the separate seal despite obj's elaboration exclusion.
 for ip in ['harnessSysPLL','nm37_xdma']:
  p=root/f'obj/ip/{ip}/{ip}.xci';assert p.is_file()
  deps[str(p.relative_to(out))]=sha(p)
  ip_audits.append(audit_xci(root,ip,p,protected))
 # Reject an unexpected project reference, including sources outside this build.
 allowed_refs={str((out/relative(n)).resolve()) for n in old}|{str((out/relative(n)).resolve()) for n in additions}|{str((out/relative(n)).resolve()) for n in deps}
 shell=json.loads((out/'shell.json').read_text())
 allowed_refs|={str((out/relative(n)).resolve()) for n in shell}
 for p in root.glob('*.vsrcs.f'):
  assert str(p.resolve()) in protected
  allowed_refs|={str(Path(line.strip()).resolve()) for line in p.read_text().splitlines() if line.strip()}
 project=ET.fromstring((root/f'{top}.xpr').read_text());refs=[]
 for elem in project.iter('File'):
  value=elem.attrib['Path']
  assert value.startswith(('$PPRDIR/','$PSRCDIR/')),('Unexpected XPR path form',value)
  resolved=Path(value.replace('$PPRDIR',str(root)).replace('$PSRCDIR',str(root/f'{top}.srcs'))).resolve()
  assert str(resolved) in allowed_refs and resolved.is_file(),('Unexpected/missing XPR source/constraint',value)
  refs.append(value)
 assert refs
 return dict(schema=SCHEMA,source_lock_sha256=sha(out/'source-lock.json'),elaboration_seal_sha256=sha(sealpath),classifier_sha256=sha(Path(__file__)),catalog_sha256=sha(POLICY),catalog_scope=dict(top=top,vivado=policy['vivado']),original_files_checked=len(old),original_removed=[],original_changed=[],generated=additions,categories={n:allowed[n] for n in additions},category_counts=dict(collections.Counter(allowed[n] for n in additions)),additional_ip_dependencies=deps,ip_configuration_audits=ip_audits,project_file_references=refs)

def record(out,seal=None):
 """Write a NEW sidecar; never rewrites the elaboration or implementation seal."""
 out=Path(out).resolve();data=classify(out);write(Path(seal) if seal else out/'implementation-generated.passed.json',data);return data

def check(out,seal=None):
 """Standalone recovery check; caller separately verifies old drivers/source."""
 out=Path(out).resolve();path=Path(seal) if seal else out/'implementation-generated.passed.json'
 expected=json.loads(path.read_text());actual=classify(out)
 assert expected==actual,'Implementation-generated seal changed or differs from current artifacts'
 return actual

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['classify','record','check']);p.add_argument('--out',type=Path,required=True);p.add_argument('--seal',type=Path);p.add_argument('--report',type=Path);a=p.parse_args()
 result=classify(a.out) if a.action=='classify' else record(a.out,a.seal) if a.action=='record' else check(a.out,a.seal)
 if a.report:write(a.report,result)
 print(json.dumps(dict(passed=True,action=a.action,original_files_checked=result['original_files_checked'],generated_count=len(result['generated']),category_counts=result['category_counts']),indent=2))
if __name__=='__main__':main()
