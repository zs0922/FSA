#!/usr/bin/env python3
"""Read-only final routed CDC/constraints comparison against historical 70MHz A."""
import argparse,collections,datetime,difflib,hashlib,json,math,re
from pathlib import Path
B=Path(__file__).resolve().parents[1];N='chipyard.fpga.nm37.NM37FPGATestHarness.EmptyNM37Config'
FIELDS=['id','severity','source_clock','destination_clock','cdc_type','depth','exception','source','destination']
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
 return h.hexdigest()
def cdc(path):
 rows=[];sc=dc=ct=None
 for i,line in enumerate(path.read_text().splitlines(),1):
  if line.startswith('Source Clock: '):sc=line.split(': ',1)[1]
  elif line.startswith('Destination Clock: '):dc=line.split(': ',1)[1]
  elif line.startswith('CDC Type: '):ct=line.split(': ',1)[1]
  elif re.match(r'^\s*\d+\s+CDC-\d+\s+',line):
   m=re.match(r'^\s*\d+\s+(CDC-\d+)\s+(Info|Warning|Critical)\s+(.*?)\s{2,}(\d+)\s{2,}(.*?)\s{2,}(\S+)\s{2,}(\S+)\s*$',line)
   assert m,('Unparsed CDC row',line)
   cid,severity,description,depth,exception,source,destination=m.groups()
   assert sc is not None and dc is not None and ct is not None
   rows.append(dict(zip(FIELDS,[cid,severity,sc,dc,ct,depth,exception,source,destination]),line=i,description=description))
 assert rows
 summary={cid:int(count) for cid,severity,count in re.findall(r'^(CDC-\d+)\s+(Info|Warning|Critical)\s+(\d+)\s',path.read_text(),re.M)}
 assert collections.Counter(r['id'] for r in rows)==summary
 return rows

def sig(row):return tuple(row[k] for k in FIELDS)
def diffrows(old,new):
 a=collections.Counter(sig(r) for r in old);b=collections.Counter(sig(r) for r in new)
 def unpack(c):return [dict(zip(FIELDS,key),multiplicity=count) for key,count in sorted(c.items())]
 return dict(equal=a==b,added=unpack(b-a),removed=unpack(a-b))

def body(p):
 lines=p.read_text().splitlines()
 return [l.rstrip() for l in lines if not (l.startswith('Copyright ') or l.startswith('|') or re.match(r'^-+$',l) or not l.strip())]

def clockrows(p):
 lines=p.read_text().splitlines();header=next(l for l in lines if l.startswith('From Clock'))
 starts=[0]+[header.index(n) for n in ['To Clock','Clock Edges','WNS(ns)','TNS(ns)','Classification','Constraints']]
 result=[]
 for l in lines:
  if not l.strip() or l.startswith(('From Clock','-','|','Copyright')):continue
  src=l[:starts[1]].strip();dest=l[starts[1]:starts[2]].strip()
  if not src or not dest or ' ' in src or ' ' in dest:continue
  classification=l[starts[5]:starts[6]].strip();constraint=l[starts[6]:].strip()
  if not classification:continue
  result.append(dict(source_clock=src,destination_clock=dest,classification=classification,constraint=constraint,edges=l[starts[2]:starts[3]].strip(),wns=l[starts[3]:starts[4]].strip()))
 assert result
 return result


def inventory(p):
 rows=[m.groups() for line in p.read_text().splitlines() if (m:=re.match(r'^(\S+)\s+([\d.]+)\s+(\{[^}]+\})\s+(\S+)\s+(\{.*\})\s*$',line))]
 assert rows
 return rows

def inspect(stage):
 obj=B/('final-p3-v2' if stage=='p3' else 'final-p2')/'A70/fpga'/N/'obj';audit=obj/'audit'
 baseline=Path('/home/zhangsi/tmp/FSA-m1/nm37-m1-shiftjam/A/fpga')/N/'obj/audit'
 needed=[audit/n for n in ['cdc.txt','check_timing.txt','clock_interaction.txt','exceptions.txt','run_properties.txt']]+[obj/'post_route.dcp',obj/'report/timing.txt',obj/'report/route_status.txt',obj/'report/clocks.txt']
 assert all(p.is_file() and p.stat().st_size>0 for p in needed),'Final routed artifact set incomplete'
 checkpoint=sha(obj/'post_route.dcp')
 marker=audit/'postroute_checkpoint.sha256'
 marker_match=marker.is_file() and marker.read_text().strip()==checkpoint
 assert marker_match or (stage=='p2' and not marker.exists()),'Final DCP hash marker missing or mismatched'
 # A timing-failing P2 can exit in report.tcl before the marker is written.
 # Its collected reports are still compared, with incomplete binding explicit.
 orig=cdc(baseline/'cdc.txt');candidate=cdc(audit/'cdc.txt');delta=diffrows(orig,candidate)
 critical=diffrows([r for r in orig if r['severity']=='Critical'],[r for r in candidate if r['severity']=='Critical'])
 text=(audit/'check_timing.txt').read_text();counts={k:int(v) for k,v in re.findall(r'checking (\w+) \((\d+)\)',text)}
 endpoint_lines=[l.strip() for l in text.splitlines() if 'HBM_SNGLBLI_INTF_AXI<' in l]
 expected={f'dutDomain/ram_hbm/inst/ONE_STACK.u_hbm_top/ONE_STACK_HBM.hbm_one_stack_intf/HBM_SNGLBLI_INTF_AXI<{i}>_INST/{pin}' for i in range(1,16) for pin in ['BREADY','RREADY']}
 actual=set(endpoint_lines)
 valid_counts=all(counts.get(k)==0 for k in ['no_clock','constant_clock','pulse_width_clock','no_output_delay','multiple_clock','generated_clocks','loops','partial_input_delay','partial_output_delay','latch_loops']) and counts.get('unconstrained_internal_endpoints')==30 and counts.get('no_input_delay')==1
 max_unconstrained=re.search(r'There are (\d+) pins that are not constrained for maximum delay\.',text)
 constant_unconstrained=re.search(r'There are (\d+) pins that are not constrained for maximum delay due to constant clock',text)
 prst=bool(re.search(r'^prst_n\s*$',text,re.M))
 endpoint_ok=actual==expected and len(endpoint_lines)==30 and max_unconstrained and max_unconstrained[1]=='0' and constant_unconstrained and constant_unconstrained[1]=='30'
 old_clock=clockrows(baseline/'clock_interaction.txt');new_clock=clockrows(audit/'clock_interaction.txt')
 def clocksig(rows):return {(r['source_clock'],r['destination_clock'],r['classification'],r['constraint']) for r in rows}
 ca=clocksig(old_clock);cb=clocksig(new_clock)
 clock_delta=dict(equal=ca==cb,added=[list(v) for v in sorted(cb-ca)],removed=[list(v) for v in sorted(ca-cb)],actual=new_clock)
 exold=body(baseline/'exceptions.txt');exnew=body(audit/'exceptions.txt');exc_equal=exold==exnew
 diffpath=B/f'review/a70-{stage}-exceptions.diff';diffpath.write_text('\n'.join(difflib.unified_diff(exold,exnew,fromfile='historical70/exceptions.txt',tofile=f'{stage}/exceptions.txt',lineterm=''))+'\n')
 props=dict(l.split('=',1) for l in (audit/'run_properties.txt').read_text().splitlines())
 timing=(obj/'report/timing.txt').read_text();m=re.search(r'WNS\(ns\).*?\n\s*-+[^\n]*\n\s*([^\n]+)',timing,re.S);assert m
 values=list(map(float,m[1].split()));assert len(values)==12
 timing_ok=all(math.isfinite(x) for x in values) and all(values[i]>=0 for i in [0,4,8]) and all(values[i]==0 for i in [1,2,5,6,9,10]) and all(values[i]>0 for i in [3,7,11]) and all(float(props[k])>=0 for k in ['dut_setup_wns','dut_hold_whs'])
 route=(obj/'report/route_status.txt').read_text();routecounts={k:int(re.search(re.escape(k)+r'\.+\s*:\s*(\d+)',route)[1]) for k in ['# of routable nets','# of fully routed nets','# of nets with routing errors']}
 route_ok=routecounts['# of routable nets']==routecounts['# of fully routed nets'] and routecounts['# of nets with routing errors']==0
 old_inv=inventory(baseline.parent/'report/clocks.txt');new_inv=inventory(obj/'report/clocks.txt')
 inv_equal=set(old_inv)==set(new_inv)
 constrained_compare=bool(inv_equal and delta['equal'] and critical['equal'] and endpoint_ok and prst and valid_counts and clock_delta['equal'] and exc_equal)
 physical_driver=B/'src/scripts/nm37-a70/physical.py'
 ns={'__name__':'read_only_physical_check'}
 exec(compile(physical_driver.read_text(),str(physical_driver),'exec'),ns)
 try:
  physical_result=ns['inspect'](obj);physical_ok=True
 except Exception as exc:
  physical_result={'error':f'{type(exc).__name__}: {exc}'};physical_ok=False
 artifacts={str(p.relative_to(obj)):sha(p) for directory in ['audit','report'] for p in sorted((obj/directory).rglob('*')) if p.is_file()}
 result=dict(stage=stage,review_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),status='ROUTED_REPORTS_REVIEWED',scope='Read-only routed CDC/constraint endpoint comparison, not board execution or blanket CDC safety waiver.',source_review_sha256=sha(B/'review/a70-cdc-source-review.json'),structure_review_sha256=sha(B/'review/a70-generated-structure.json'),checkpoint_sha256=checkpoint,checkpoint_marker_matches=marker_match,checkpoint_binding_note=('Final script-written checksum marker matches hashed DCP.' if marker_match else 'P2 has no final checksum marker: legacy report.tcl exits on setup violation before marker creation. DCP and reports are independently SHA-bound as collected files, but script-written DCP/report provenance is incomplete; no board-test candidacy.'),artifact_sha256=artifacts,inspector_sha256=sha(Path(__file__)),implementation_log_sha256=sha(obj.parents[3]/'logs/implementation.log'),baseline_sha256={n:sha(baseline/n) for n in ['cdc.txt','check_timing.txt','clock_interaction.txt','exceptions.txt']},cdc=dict(comparison_fields=FIELDS,baseline_rows=len(orig),candidate_rows=len(candidate),baseline_counts=dict(collections.Counter(r['id'] for r in orig)),candidate_counts=dict(collections.Counter(r['id'] for r in candidate)),full_fields_difference=delta,critical_full_fields_difference=critical,critical_rows=[r for r in candidate if r['severity']=='Critical'],known_critical_boundaries='Existing12 CDC7 asynchronous-reset,2 CDC10 POR-before-synchronizer,16 CDC11 PCIe pipe/GT paths remain explicit shell/vendor review boundaries. Matching their fields is regression evidence, not proof of CDC/board safety.'),constraints=dict(counts=counts,all_expected_counts=valid_counts,hbm30_actual_endpoints=sorted(actual),hbm30_expected_match=bool(endpoint_ok),additional_hbm_or_wrong_endpoints=sorted(actual-expected),missing_hbm_endpoints=sorted(expected-actual),max_delay_unconstrained_nonconstant=int(max_unconstrained[1]) if max_unconstrained else None,prst_n_no_input_delay=prst,check_timing_body_equal=body(baseline/'check_timing.txt')==body(audit/'check_timing.txt'),clock_interaction=clock_delta,clock_inventory=dict(equal=inv_equal,baseline_file_sha256=sha(baseline.parent/'report/clocks.txt'),baseline_count=len(old_inv),candidate_count=len(new_inv),added=[list(v) for v in sorted(set(new_inv)-set(old_inv))],removed=[list(v) for v in sorted(set(old_inv)-set(new_inv))],actual=[dict(zip(['name','period_ns','waveform','attributes','sources'],v)) for v in new_inv]),exceptions_body_equal=exc_equal,exceptions_diff=dict(file=str(diffpath),sha256=sha(diffpath))),timing=dict(passed=timing_ok,global_summary=values,dut=props),route=dict(passed=route_ok,counts=routecounts),cdc_constraint_regression_review_passed=constrained_compare,physical_checks=dict(passed=physical_ok,result=physical_result,driver_sha256=sha(physical_driver)),board_test_candidate=bool(constrained_compare and timing_ok and route_ok and physical_ok),board_test_candidate_scope='Candidate for controlled board testing only when physical_checks and exact CDC/constraints regression comparison pass; existing shell reset/PCIe Critical classifications remain documented boundaries. Parent release gate decides any export.',board_tested=False,bitstream_exported_by_this_review=False,limitations=['Existing Critical CDC classifications are not resolved or waived by equivalence of recorded paths.','The30 HBM pins are identified from actual routed check_timing report and correlated to source-omitted interfaces1..15; this report does not independently query their constant drivers from Vivado.','prst_n source false path and applied no-input-delay warning remain an asynchronous-reset board boundary.','Full endpoint/exception changes that differ from baseline require explanation before release approval.','Timing is assessed separately; P2 cannot be called timing-passed solely because CDC matches.'])
 out=B/f'review/a70-{stage}-routed-cdc-review.json';out.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:result[k] for k in ['stage','cdc_constraint_regression_review_passed','board_test_candidate','checkpoint_sha256']},indent=2));return result
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--stage',choices=['p2','p3'],default='p3');a=p.parse_args();inspect(a.stage)
