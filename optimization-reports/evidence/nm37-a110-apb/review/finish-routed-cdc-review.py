#!/usr/bin/env python3
"""Independently bind per-finding review to actual routed CDC and reset cones."""
from pathlib import Path
import json, hashlib, collections, importlib.util, datetime
B=Path(__file__).resolve().parent.parent
R=B/'review'; main=R/'build04-postroute-cdc-review'; extra=R/'build04-postroute-linkup-review04'
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def lines(p, needle):
 return [i for i,x in enumerate(p.read_text().splitlines(),1) if needle in x]
def evidence(p, needle=None):
 d={'path':str(p.relative_to(B)), 'sha256':sha(p)}
 if needle is not None:
  d['lines']=lines(p,needle); assert d['lines'],(p,needle)
 return d
manifest=json.loads((main/'manifest.json').read_text())
for d in [main,extra]:
 props=dict(x.split('=',1) for x in (d/'properties.txt').read_text().splitlines())
 assert props['report_generation']=='completed' and props['structural_mismatches']=='0', props
 assert props['checkpoint_sha256']=='44195817d21dfdc147800868637505f1ce89303fd3aa98f1c6e044f4b3ccf236'
 checks=[x for x in (d/'structure.tsv').read_text().splitlines() if x.startswith('CHECK\t')]
 assert checks and all(x.split('\t')[2]=='MATCH' for x in checks)
# Check the actual recorded reset pin polarity, including the user-reset
# output whose original helper logged rather than explicitly asserted PRE=0.
reset_polarity_evidence=[]
for line_no,line in enumerate((main/'structure.tsv').read_text().splitlines(),1):
 fields=line.split('\t')
 if fields[0]=='PIN' and fields[1].endswith(('/CLR','/PRE')):
  assert fields[3]=='IS_INVERTED=0',line
  reset_polarity_evidence.append({'pin':fields[1],'line':line_no,'is_inverted':0})
assert reset_polarity_evidence
for p,h in manifest['reports'].items(): assert sha(Path(p))==h, p
# Prior query parser changed only in later read-only validator revisions. Parse current
# reports independently and compare exact row content to immutable original manifest.
p=B/'src/scripts/nm37-a110/physical_audit.py'
spec=importlib.util.spec_from_file_location('review_parser',p); pa=importlib.util.module_from_spec(spec);spec.loader.exec_module(pa)
rows=pa.cdc_rows((main/'cdc.rpt').read_text());allrows=pa.cdc_rows((main/'cdc-all-checks.rpt').read_text())
baseline=json.loads((p.parent/'cdc-baseline.json').read_text())
known={tuple(x) for x in baseline['findings']};sig={tuple(x) for x in rows if x[1] in ('Critical','Warning')}
new=sorted(sig-known)
assert new==[tuple(x['finding']) for x in manifest['new_warning_critical_findings']]
assert not {tuple(x) for x in allrows if x[1] in ('Critical','Warning')}-sig
assert len(new)==36
reaudit_cdc=B/'reaudit04/obj/audit/cdc.txt'
reaudit_rows=pa.cdc_rows(reaudit_cdc.read_text())
reaudit_significant={tuple(x) for x in reaudit_rows if x[1] in ('Critical','Warning')}
assert reaudit_significant==sig, 'Reaudit significant CDC set changed'
assert sorted(reaudit_significant-known)==new
assert collections.Counter(tuple(x) for x in reaudit_rows)==collections.Counter(tuple(x) for x in rows), 'Full CDC rows changed'

vendor=B/'build04/A110/fpga/chipyard.fpga.nm37.NM37FPGATestHarness.EmptyNM37Config/obj/ip/nm37_xdma/ip_0/source/nm37_xdma_pcie4c_ip_pcie4c_uscale_core_top.v'
entries=[]
for n,f in enumerate(new,1):
 dest=f[6]; e={'index':n,'finding':list(f),'classification':'equivalent_existing_structure','recommendation':'accept_differential_review_with_stated_scope','evidence':[evidence(main/'cdc.rpt',dest)]}
 if dest.startswith('wrangler/'):
  mapped=list(f);mapped[6]=mapped[6].replace('nodeOut_reset_catcher_2/','nodeOut_reset_catcher_1/')
  for i in (7,8):mapped[i]=mapped[i].replace('clk_out2_harnessSysPLL','clk_out1_harnessSysPLL')
  assert tuple(mapped) in known
  e['baseline_exact_after_declared_mapping']=mapped
  e['rationale']='Existing ResetWrangler asynchronous reset/debounce structure. Only documented slow clock changes 110 to 55 MHz and PCIe catcher hierarchy index 1 to 2. Actual routed FDCE/FDPE types, initialization, clocks, reset-cone truth and final three-stage PCIe release chain match the generated architecture. Counter is 13 bits at 5500 cycles rather than 14 bits at 11000 cycles, retaining nominal 100 us. This is endpoint correspondence, not a claim that every asynchronous register has its own synchronizer.'
  e['evidence'] += [evidence(main/'structure.tsv',dest.rsplit('/',1)[0]),evidence(R/'build04-postopt-cdc-review-preparation.json')]
 else:
  matched=[list(x) for x in sorted(known) if x[0]==f[0] and x[6]==dest and x[8]==f[8]]
  assert matched
  e['baseline_same_rule_destination_clock']=matched
  e['rationale']='The newly selected launch clock comes from the board-clock reset guard. Destination is an existing vendor PCIe fundamental-reset consumer. Supported asynchronous assertion is preserved; the guard adds qualified delayed release, and the installed vendor reset logic remains intact. This must not be generalized into permission for arbitrary unsynchronized custom crossings.'
  if 'user_lnk_up' in dest:
   e['rationale']+=' Actual routed effective link-up clear equals POR OR NOT qualified OR NOT released OR NOT link_status[0] OR NOT link_status[1]. The two XPM stages have CLR IS_INVERTED=1 and the complementary positive input truth; the output register has CLR IS_INVERTED=0 and the negative input truth. All 32 input combinations and these exact pin polarities are verified together. Two ASYNC_REG FDCE stages with vendor-defined INIT=1 followed by the FDCE output register with INIT=0, constant CE and direct D links, implement synchronous assertion of link-up after asynchronous deassertion, matching installed vendor HDL.'
   e['evidence'] += [evidence(extra/'structure.tsv',dest.rsplit('/',1)[0]),evidence(vendor,'user_lnk_up_cdc')]
  elif 'user_reset_cdc' in dest:
   e['rationale']+=' Actual routed user-reset chain is two ASYNC_REG FDPE stages followed by the output FDPE with direct D links; the checked 64-row reset truth includes fundamental reset, hot reset and link status. This is the supported vendor asynchronous-assert/synchronous-release reset path.'
   e['evidence'] += [evidence(main/'structure.tsv',dest.rsplit('/',1)[0]),evidence(vendor,'user_reset_cdc')]
  else:
   e['rationale']+=' This destination is directly asynchronously reset by vendor sys_or_hot_rst, not a per-endpoint synchronizer. Its actual 16-row reset truth equals hot_reset OR POR OR NOT qualified OR NOT released and its primitive type/INIT/destination clock are checked.'
   e['evidence'] += [evidence(main/'structure.tsv',dest.rsplit('/',1)[0]),evidence(vendor,'sys_or_hot_rst')]
 entries.append(e)
result={'schema_version':1,'reviewer':'/root/cdc_finish','reviewed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'candidate_physical_acceptance':False,'checkpoint_sha256':manifest['checkpoint_sha256'],'build_manifest_sha256':manifest['build_manifest_sha256'],'review_script':evidence(Path(__file__)),'main_reset_pin_polarities_independently_checked':reset_polarity_evidence,'ordinary_counts':dict(collections.Counter(x[1] for x in rows)),'reaudit_report_binding':evidence(reaudit_cdc),'reaudit_full_row_multiset_identical':True,'new_count':len(new),'new_rule_counts':dict(collections.Counter(x[0] for x in new)),'all_checks_additional_significant':[],'entries':entries,'evidence':[evidence(x) for d in (main,extra) for x in sorted(d.iterdir()) if x.is_file()],'official_sources':json.loads((R/'build04-postopt-cdc-review-preparation.json').read_text())['official_sources'],'supplement_audit_correction':{'preserved_failed_attempts':['review/build04-postroute-linkup-review02','review/build04-postroute-linkup-review03'],'reason':'Original supplemental query incorrectly required INIT=0 for all three link-up registers; installed XPM active-low reset implementation specifies INIT=1 for the two synchronizer stages, while the output register is INIT=0. Attempt03 confirmed exact initialization but exposed another incorrect query assumption: XPM CLR pins are inverted while the output CLR is not. Attempt04 requires exact initialization and polarity, and checks complementary input truth tables, rather than permitting arbitrary initialization or polarity.','xpm_source':'/opt/Xilinx_2020.2/Vivado/2020.2/data/ip/xpm/xpm_cdc/hdl/xpm_cdc.sv','xpm_source_sha256':sha(Path('/opt/Xilinx_2020.2/Vivado/2020.2/data/ip/xpm/xpm_cdc/hdl/xpm_cdc.sv')),'xpm_source_lines':[1222,1223,1226]},'limits':['Recommendations cover the 36 actual routed new Critical/Warning records only; baseline still contains asynchronous CDC structures and is not a blanket clean-CDC signoff.','Final acceptance also requires bound reaudit report identity and all timing/DRC/route/HBM structural gates. No timing failure can be waived by this review.','The old A110 checkpoint was unavailable: correspondence uses archived exact CDC rows plus installed vendor HDL and current routed netlist, not a reopened old checkpoint.','External PCIe PERST/refclk contract remains required. The board 100 MHz guard does not prove PCIe refclk stability.','No hardware board test, analog metastability test, MTBF measurement, or complete end-to-end AXI/HBM transaction test is claimed.','Newer official manuals describe reset-interface semantics; installed Vivado 2020.2 HDL and actual routed primitives establish implementation-specific evidence.']}
out=R/'build04-routed-cdc-final-review.json'
assert not out.exists()
out.write_text(json.dumps(result,indent=2)+'\n')
print(out,sha(out))
