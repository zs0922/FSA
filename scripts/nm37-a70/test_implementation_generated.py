#!/usr/bin/env python3
"""Real-layout positive and mutation negatives; works only on copied fixtures."""
import argparse,copy,json,os,shutil,subprocess,sys,tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
sys.dont_write_bytecode=True
import implementation_generated as m
import artifacts

def main():
 p=argparse.ArgumentParser();p.add_argument('--reference',type=Path,required=True);p.add_argument('--result',type=Path,required=True);a=p.parse_args();reference=a.reference.resolve();results=[]
 def passed(name):results.append(name)
 original=m.classify(reference);assert len(original['generated'])==141;passed('real_P3_141_classification')
 with tempfile.TemporaryDirectory(prefix='a70-generated-seal-') as tmp:
  out=Path(tmp);seal=json.loads((reference/'elaborate.passed.json').read_text());shell=json.loads((reference/'shell.json').read_text())
  names=set(seal['files'])|set(original['generated'])|set(original['additional_ip_dependencies'])|set(shell)|{'elaborate.passed.json','source-lock.json','shell.json','build.json'}
  for name in names:
   src=reference/name;dst=out/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
  # Relocate external source references in the NEW project metadata only.
  fixture_root=next(out/n for n in seal['roots'] if (out/n/'gen-collateral').is_dir())
  reference_root=reference/fixture_root.relative_to(out)
  project=fixture_root/'NM37FPGATestHarness.xpr';tree=ET.parse(project)
  for e in tree.iter('File'):
   value=e.attrib['Path'];resolved=Path(value.replace('$PPRDIR',str(reference_root)).replace('$PSRCDIR',str(reference_root/'NM37FPGATestHarness.srcs'))).resolve()
   if not resolved.is_relative_to(reference):e.attrib['Path']='$PPRDIR/'+os.path.relpath(resolved,fixture_root)
  tree.write(project,encoding='utf-8',xml_declaration=True)
  assert set(m.classify(out)['generated'])==set(original['generated']);passed('copied_real_layout_positive')
  old_seal_bytes=(out/'elaborate.passed.json').read_bytes();m.record(out);m.check(out);passed('record_and_check_positive')
  def rejected(name,action,types=(AssertionError,)):
   try:action()
   except types:passed(name)
   else:raise AssertionError(name+' was not rejected')
  rejected('record_is_create_only',lambda:m.record(out),(FileExistsError,))
  def mutate_file(name,action,update=lambda b:b+b'\n// changed\n'):
   f=out/name;old=f.read_bytes()
   try:f.write_bytes(update(old));action()
   finally:f.write_bytes(old)
  protected_rtl=next(n for n in seal['files'] if n.endswith('/gen-collateral/FSA.sv'))
  protected_tcl=next(n for n in seal['files'] if n.endswith('.testxilinx_hbm.vivado.tcl'))
  generated_rtl=next(n for n in original['generated'] if '/synth/testxilinx_hbm.v' in n)
  xci=next(n for n in original['generated'] if n.endswith('/xdma_master_axi_clock_converter_0.xci'))
  rejected('original_RTL_mutation',lambda:mutate_file(protected_rtl,lambda:m.classify(out)))
  rejected('original_Tcl_mutation',lambda:mutate_file(protected_tcl,lambda:m.classify(out)))
  rejected('generated_RTL_mutation_after_seal',lambda:mutate_file(generated_rtl,lambda:m.check(out)))
  rejected('XCI_explicit_config_mismatch',lambda:mutate_file(xci,lambda:m.classify(out),lambda b:b.replace(b'referenceId="PARAM_VALUE.DATA_WIDTH">256<',b'referenceId="PARAM_VALUE.DATA_WIDTH">128<')))
  # A harmless XML comment must still be caught by the exact post-seal SHA.
  rejected('XCI_raw_mutation_after_seal',lambda:mutate_file(xci,lambda:m.check(out),lambda b:b+b'\n<!-- changed -->\n'))
  dep=next(iter(original['additional_ip_dependencies']))
  rejected('obj_IP_XCI_mutation_after_seal',lambda:mutate_file(dep,lambda:m.check(out),lambda b:b+b'\n<!-- changed -->\n'))
  f=out/protected_rtl;old=f.read_bytes();f.unlink()
  try:rejected('original_file_removed',lambda:m.classify(out))
  finally:f.write_bytes(old)
  f=out/generated_rtl;old=f.read_bytes();f.unlink()
  try:rejected('generated_file_removed',lambda:m.classify(out))
  finally:f.write_bytes(old)
  root=next(out/n for n in seal['roots'] if (out/n/'gen-collateral').is_dir());rogue=root/'NM37FPGATestHarness.gen/sources_1/ip/testxilinx_hbm/hdl/rogue.sv';rogue.write_text('module rogue; endmodule\n')
  try:rejected('unlisted_new_RTL_inside_gen',lambda:m.classify(out))
  finally:rogue.unlink()
  rogue=root/'rogue.tcl';rogue.write_text('set_false_path -from [all_clocks]\n')
  try:rejected('unlisted_new_Tcl',lambda:m.classify(out))
  finally:rogue.unlink()
  xpr=next(n for n in original['generated'] if n.endswith('.xpr'))
  rejected('project_external_source_injection',lambda:mutate_file(xpr,lambda:m.classify(out),lambda b:b.replace(b'</Project>',b'<File Path="$PPRDIR/rogue.sv"/></Project>')))
  f=out/generated_rtl;old=f.read_bytes();f.unlink();f.symlink_to(reference/generated_rtl)
  try:rejected('generated_symlink_rejected',lambda:m.classify(out))
  finally:f.unlink();f.write_bytes(old)
  side=out/'implementation-generated.passed.json';old=side.read_bytes();v=json.loads(old);v['catalog_sha256']='0'*64;side.write_text(json.dumps(v))
  try:rejected('sidecar_policy_fingerprint_tamper',lambda:m.check(out))
  finally:side.write_bytes(old)
  assert (out/'elaborate.passed.json').read_bytes()==old_seal_bytes;passed('original_elaboration_seal_never_changed')
  # Exercise actual artifacts.py seal/gate integration on isolated evidence.
  manifest=json.loads((out/'build.json').read_text());env=dict(os.environ,A70_SOURCE=manifest['source'],A70_STAGE=manifest['stage'],PYTHONDONTWRITEBYTECODE='1')
  for phase in ['assembly','sim4','sim16','units','formal']:
   f=out/f'phase-evidence/{phase}.txt';f.parent.mkdir(exist_ok=True);f.write_text('fixture proof artifact\n')
   (out/f'{phase}.passed.json').write_text(json.dumps(dict(source_lock_sha256=m.sha(out/'source-lock.json'),roots=[str(f.relative_to(out))],files={str(f.relative_to(out)):m.sha(f)})))
  (out/'drivers.json').write_text(json.dumps(artifacts.inventory([m.HERE],m.HERE)))
  obj=root/'obj';evidence=['post_route.dcp','audit/cdc.txt','audit/check_timing.txt','audit/clock_interaction.txt','audit/exceptions.txt']
  for name in evidence:
   f=obj/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_text('isolated gate fixture '+name+'\n')
  (out/'cdc-constraint-review.json').write_text(json.dumps(dict(accepted_for_board_test=True,board_tested=False,source_lock_sha256=m.sha(out/'source-lock.json'),analysis='Isolated artifact gate fixture only',known_boundaries=['fixture'],evidence_sha256={n:m.sha(obj/n) for n in evidence})))
  f=out/'phase-evidence/implement.txt';f.write_text('fixture implementation\n')
  def cli(*args,expect=True):
   run=subprocess.run([sys.executable,str(m.HERE/'artifacts.py'),*args,'--out',str(out)],env=env,capture_output=True,text=True)
   assert (run.returncode==0)==expect,(args,run.stdout[-1000:],run.stderr[-2000:])
  cli('seal','--phase','implement',str(f));passed('artifacts_implement_seal_binds_sidecar')
  cli('gate','--phase','bitstream');passed('artifacts_bitstream_gate_positive')
  cli('gate',expect=False);passed('pre_implementation_gate_stays_exact')
  mutate_file(generated_rtl,lambda:cli('gate','--phase','bitstream',expect=False));passed('integrated_gate_rejects_generated_RTL_change')
  mutate_file(protected_tcl,lambda:cli('gate','--phase','bitstream',expect=False));passed('integrated_gate_rejects_original_Tcl_change')
  f=out/'implement.passed.json';v=json.loads(f.read_text());v['implementation_generated_seal_sha256']='0'*64;f.write_text(json.dumps(v))
  cli('gate','--phase','bitstream',expect=False);passed('integrated_gate_rejects_unbound_sidecar')
 a.result.parent.mkdir(parents=True,exist_ok=True);m.write(a.result,dict(passed=True,cases=results,count=len(results)));print(json.dumps(dict(passed=True,cases=results,count=len(results)),indent=2))
if __name__=='__main__':main()
