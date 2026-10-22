#!/usr/bin/env python3
"""Source/constraint CDC preparation only: never accepts candidate routed reports."""
from pathlib import Path
import collections, hashlib, json, re
B=Path(__file__).resolve().parents[1];N='chipyard.fpga.nm37.NM37FPGATestHarness.EmptyNM37Config';files={}
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):
 files[str(p)]=dict(sha256=sha(p),lines=len(p.read_text().splitlines()));return p.read_text()
def ev(p,pattern,minimum=1):
 rows=[dict(line=i,text=l.strip()) for i,l in enumerate(read(p).splitlines(),1) if re.search(pattern,l)]
 assert len(rows)>=minimum,(p,pattern,len(rows))
 return dict(file=str(p),sha256=sha(p),matches=rows)
def params(p,keys):
 t=read(p);allvals=dict(re.findall(r'<spirit:configurableElementValue[^>]*referenceId="([^"]+)"[^>]*>([^<]+)',t)); vals={k:allvals[k] for k in keys}
 return dict(values=vals,evidence=ev(p,'|'.join(re.escape('referenceId="'+k+'"') for k in keys),len(keys)))
r=dict(status='SOURCE_REVIEW_COMPLETE_ROUTE_REVIEW_PENDING',postroute_accepted=False,scope='Read-only actual frozen P2/P3 shell/source/RTL/IP/constraint inspection plus historical reference reports. Candidate routed reports are deliberately not accepted by this review.',stages={})
for s,o in [('p2','final-p2'),('p3','final-p3-v2')]:
 root=B/o/'A70/fpga'/N;g=root/'gen-collateral';src=B/'stages'/s/'fpga/src/main/scala';top=g/'NM37FPGATestHarness.sv';sink=g/'ClockSinkDomain.sv';wr=g/'ResetWrangler.sv';sdc=root/(N+'.shell.sdc');xdc=root/(N+'.shell.xdc');ip=root/'NM37FPGATestHarness.srcs/sources_1/ip'
 clocks={
  'pll':params(root/'obj/ip/harnessSysPLL/harnessSysPLL.xci',['PARAM_VALUE.PRIM_IN_FREQ','PARAM_VALUE.CLKOUT1_REQUESTED_OUT_FREQ','PARAM_VALUE.NUM_OUT_CLKS']),
  'xdma':params(root/'obj/ip/nm37_xdma/nm37_xdma.xci',['PARAM_VALUE.axisten_freq','PARAM_VALUE.axi_data_width']),
  'axi_converter':params(ip/'xdma_master_axi_clock_converter_0/xdma_master_axi_clock_converter_0.xci',['PARAM_VALUE.ACLK_ASYNC','PARAM_VALUE.SYNCHRONIZATION_STAGES','PARAM_VALUE.DATA_WIDTH','PARAM_VALUE.PROTOCOL']),
  'lite_converter':params(ip/'xdma_master_lite_axi_clock_converter_0/xdma_master_lite_axi_clock_converter_0.xci',['PARAM_VALUE.ACLK_ASYNC','PARAM_VALUE.SYNCHRONIZATION_STAGES','PARAM_VALUE.DATA_WIDTH','PARAM_VALUE.PROTOCOL']),
  'hbm':params(ip/'testxilinx_hbm/testxilinx_hbm.xci',['PARAM_VALUE.USER_APB_EN','PARAM_VALUE.USER_HBM_DENSITY','PARAM_VALUE.USER_HBM_STACK','PARAM_VALUE.USER_CLK_SEL_LIST0','PARAM_VALUE.USER_HBM_REF_CLK_0','PARAM_VALUE.USER_APB_PCLK_0','PARAM_VALUE.USER_AXI_INPUT_CLK_FREQ'])}
 assert clocks['pll']['values']['PARAM_VALUE.CLKOUT1_REQUESTED_OUT_FREQ']=='70.0'
 for n in ['axi_converter','lite_converter']:
  assert clocks[n]['values']['PARAM_VALUE.ACLK_ASYNC']=='1' and clocks[n]['values']['PARAM_VALUE.SYNCHRONIZATION_STAGES']=='3'
 raw=read(sink);inst=re.search(r'\btestxilinx_hbm ram_hbm\s*\((.*?)\n\s*\);',raw,re.S);assert inst
 conn=set(re.findall(r'\.(\w+)\s*\(',inst.group(1)))
 assert {'AXI_00_ACLK','AXI_00_ARESET_N','APB_0_PCLK','APB_0_PRESET_N','HBM_REF_CLK_0'}<=conn
 unused=[f'AXI_{i:02d}_{suffix}' for i in range(1,16) for suffix in ['ACLK','ARESET_N','BREADY','RREADY']]
 assert not set(unused)&conn
 hbm_wrapper=root/'NM37FPGATestHarness.gen/sources_1/ip/testxilinx_hbm/synth/testxilinx_hbm.v';hw=read(hbm_wrapper)
 assert all(re.search(r'input wire '+n+r'\s*;',hw) for n in unused)
 r['stages'][s]=dict(
  actual_output=str(root),ip_parameters=clocks,
  expected_clock_domains=[dict(name='sys_clock',MHz=100,uses='Board input; POR; PLL input; HBM reference'),dict(name='clk_out1_harnessSysPLL',MHz=70,uses='FSA/PE/CMP/ACC/control/RAM; HBM AXI00; APB0 clock'),dict(name='_pcie_io_clocks_axi_aclk',MHz=250,uses='XDMA M_AXI and AXI-lite; source sides of the two converters'),dict(name='xdma_ref_clk/PCIe pipe/GT internal',MHz='PCIe-IP-defined',uses='PCIe ref, GT lanes and internal pipe clock; existing vendor-IP boundary')],
  actual_clock_reset_connections=ev(top,r'\.auto_(in|out)_[01]_(clock|reset)|\.auto_(master|slave)_clock_in_(clock|reset)|\.io_clocks_(sys|axi)|\.auto_ram_.*clock|\.auto_clock_in_(clock|reset)|\.clk_(in1|out1)|\.locked|\.reset\s*\(',25),
  source_clock_topology=ev(src/'nm37/TestHarness.scala',r'dutFreqMHz|FixedClockBroadcast\(Some|dutFixedClockNode :=|slaveClockNodes\(0\)|HBMRefClockNode|dutDomain.clockNode',6),
  source_cdc_topology=ev(src/'nm37/CustomOverlays.scala',r'master(Lite)?ClockConverter|axi.clock :=|axi.reset :=|sys_rst_n :=|addGroup|addAsyncPath',12),
  reset_wrangler=dict(debounce_ticks=7000,debounce_width=13,reset_catch_depth=3,evidence=ev(wr,r'causes =|13.h1B58|AsyncResetRegVec_w13|ResetCatchAndSync_d3|\.clock|\.reset|\.io_rst|assign.*clock',12),synchronizer=ev(g/'ResetCatchAndSync_d3.sv',r'AsyncResetSynchronizerShiftReg_w1_d3_i0|assign io_sync_reset',2),primitive=ev(g/'AsyncResetSynchronizerPrimitiveShiftReg_d3_i0.sv',r'reg sync_[012]|always @|sync_[012] <=',10)),
  prst_n=dict(asynchronous_external_reset=True,input_delay_specified=False,source=ev(src/'nm37/TestHarness.scala',r'prst_n|PowerOnResetFPGAOnly|addAsyncPath',8),board_pin=ev(xdc,r'prst_n',3),actual_pcie_reset_logic=ev(top,r'\.io_clocks_sys_rst_n|\.I \(prst_n\)',2)),
  constraints=dict(shell_sdc=ev(sdc,r'create_clock|set_clock_groups|harnessSysPLL/clk_out1|xdma_ref_clk|axi_aclk|set_false_path|prst_n',10),hbm_ip_xdc=ev(root/'NM37FPGATestHarness.gen/sources_1/ip/testxilinx_hbm/hdl/par/hbm_ip.xdc',r'create_clock|APB_0_PCLK',2),nominal_metadata_caveat='HBM XCI nominal USER_APB_PCLK_0 and USER_AXI_INPUT_CLK_FREQ are 100, while actual APB0/AXI00 fabric wires are DUT70. The reviewed HBM XDC creates the matching 100MHz HBM reference clock; its APB100 create_clock is commented out. Verify applied routed clock propagation instead of inferring a separate APB100/110 domain from metadata.'),
  unused_hbm_interfaces=dict(active_fabric_interfaces=['AXI_00'],omitted_named_input_ports=unused,source_facts='Actual Chisel blackbox and generated instantiation expose/connect only AXI00. Actual vendor wrapper declares AXI01..15. Those named inputs are OMITTED in ram_hbm instantiation, not explicitly tied in source. Synthesis constant resolution must be confirmed on the new netlist.',actual_instantiation=ev(sink,r'testxilinx_hbm ram_hbm|\.AXI_00_(ACLK|ARESET_N|BREADY|RREADY)|\.APB_0_|\.HBM_REF_CLK_0',8),source=ev(src/'u280/AXIHBM.scala',r'portNum > 0 && portNum <= 1|USER_APB_EN|USER_CLK_SEL_LIST0|val AXI_00_ACLK|APB_0_PCLK :=',5),vendor_wrapper_declarations=ev(hbm_wrapper,r'input wire AXI_(?:0[1-9]|1[0-5])_(ACLK|ARESET_N|BREADY|RREADY);',60)),
  changed_datapath_clocking={n:ev(g/n,r'^\s*input\s+clock\b|always @\(posedge clock\)|\.clock\s*\(clock\)',1) for n in ['MatrixEngineController.sv','Accumulator.sv','FSA.sv','FPAccUnit.sv']+(['RawFloat_MulAddExp2_1.sv','RawFloat_FMA_17.sv'] if s=='p3' else [])},
  candidate_postroute_review=dict(accepted=False,reviewed=False,expected_reports=[str(root/'obj/audit'/f) for f in ['cdc.txt','check_timing.txt','clock_interaction.txt','exceptions.txt']],reason='Await explicit routed artifact notification and full endpoint/exception comparison. Source equivalence is not route signoff.'))
# Freeze and compare the actual shell artifacts, not merely a pre-patch source label.
a=B/'final-p2';b=B/'final-p3-v2';assert json.loads(read(a/'shell.json'))==json.loads(read(b/'shell.json'))
ident={}
for f in ['NM37FPGATestHarness.sv','ClockSinkDomain.sv','ResetWrangler.sv','LazyAXI4ClockConverter.sv','LazyAXI4ClockConverter_1.sv','CustomXDMA.sv']:
 x=a/'A70/fpga'/N/'gen-collateral'/f;y=b/'A70/fpga'/N/'gen-collateral'/f;ident[f]=sha(x)==sha(y);read(x);read(y)
assert all(ident.values())
r['cross_stage']=dict(shell_manifest_equal=True,key_actual_shell_rtl_byte_identical=ident,new_clock_domains_from_controlPipe_ACC=0,conclusion='controlPipe=1 and ACC entry1+internal2 add registers on the existing single DUT clock. Actual P2/P3 shell clock/reset/CDCs are byte-identical; no new clock generator, derived clock or crossing was introduced by these datapath changes.')
# Historical reports are reference evidence, never a candidate waiver.
old=Path('/home/zhangsi/tmp/FSA-m1/nm37-m1-shiftjam/A/fpga')/N/'obj/audit';a100=B/'src/optimization-reports/nm37-a100-evidence/pipeline/A100/fpga'/N/'obj/audit'
history={}
for label,d in [('historical_70MHz_A',old),('historical_A100',a100)]:
 p=d/'cdc.txt';t=read(p);counts={cid:int(count) for cid,severity,count in re.findall(r'^(CDC-\d+)\s+(Info|Warning|Critical)\s+(\d+)\s',t,re.M)}
 critical=[];sc=dc=ct=None
 for i,l in enumerate(t.splitlines(),1):
  if l.startswith('Source Clock: '):sc=l.split(': ',1)[1]
  if l.startswith('Destination Clock: '):dc=l.split(': ',1)[1]
  if l.startswith('CDC Type: '):ct=l.split(': ',1)[1]
  if re.match(r'^\s*\d+\s+CDC-\d+\s+Critical\b',l):critical.append(dict(line=i,source_clock=sc,destination_clock=dc,cdc_type=ct,row=l.strip()))
 history[label]=dict(cdc_report_file=str(p),sha256=sha(p),summary_counts=counts,critical_count=len(critical),critical_rows=critical)
 assert len(critical)==30
 check=d/'check_timing.txt';ch=read(check);endpoints=re.findall(r'^.*HBM_SNGLBLI_INTF_AXI<(\d+)>_INST/(BREADY|RREADY)\s*$',ch,re.M)
 assert set((int(n),k) for n,k in endpoints)==set((n,k) for n in range(1,16) for k in ['BREADY','RREADY']) and len(endpoints)==30
 history[label]['check_timing']=dict(file=str(check),sha256=sha(check),unused_hbm_constant_clock_endpoints=[dict(interface=int(n),pin=k) for n,k in endpoints],prst_n_no_input_delay=True,evidence=ev(check,r'checking (constant_clock|unconstrained_internal_endpoints|no_input_delay)|There are 30 pins|There are 0 pins that are not constrained|^prst_n$',7))
r['historical_references']=history
r['historical_interpretation']=dict(critical_boundaries={'CDC-7':'12 asynchronous-reset paths at external prst_n/POR to PCIe reset state and PCIe/POR to wrangler deglitch. Must compare complete fields, not just count.','CDC-10':'2 POR logic-before-synchronizer paths to PCIe user_lnk_up/user_reset.','CDC-11':'16 pipe_clk -> xdma_ref_clk lane rate/CDR and receiver-detect paths inside existing PCIe IP.'},A100_difference='A100 has CDC-15=172 versus 70MHz reference170 because the reset debounce extends from 13bits/7000ticks to14bits/10000ticks. Current actual P2/P3 stay13bits/7000ticks. Do not use the A100 count blindly.',boundary_status='Recorded historical classifications remain existing review/signoff boundaries, not newly accepted CDC correctness or board-level safety claims.')
r['routed_acceptance_checklist']=[
 dict(check='Clock inventory and frequency propagation',expected='DUT PLL70, sys/HBMref100, XDMA AXI250; no newly unconstrained FSA/ACC clocks. Inspect HBM nominal100 metadata versus actual70 propagated clock.',status='PENDING'),
 dict(check='AXI converter structure and reset crossings',expected='Full and lite converters remain async with3 synchronizer stages; no bypass/reconvergence or new unsynchronized FSA crossing. Review ASYNC_REG depths and exceptions on actual instances.',status='PENDING'),
 dict(check='Reset external and wrangler/PCIe boundary',expected='Compare CDC-7/10/11 full source/destination/clock/type/depth/exception rows to70MHz baseline; re-evaluate new paths, do not waive unchanged Criticals automatically.',status='PENDING'),
 dict(check='Unused HBM30 internal endpoints',expected='Exactly interfaces1..15 BREADY and RREADY, each demonstrably unused/constant-clock on new implemented netlist; active interface0 must not appear. Source named-port omissions alone are not final tieoff evidence.',status='PENDING'),
 dict(check='prst_n no_input_delay and asynchronous exceptions',expected='Confirm BF5 external active-low reset, through false path reach and actual recovery/removal/reset synchronization boundary. Historical no_input_delay1 persists despite source false path; inspect applied constraint reach instead of declaring constrained.',status='PENDING'),
 dict(check='check_timing and clock interaction exceptions',expected='0 max-delay-unconstrained internal endpoints excluding the individually reviewed constant-clock30. Compare no_clock/multiple_clock/pulse_width and missing-delay categories; inspect actual clock groups/false paths for accidental coverage of timed FSA datapaths.',status='PENDING'),
 dict(check='Actual routed provenance',expected='Bind CDC/check_timing/clock_interaction/exceptions/netlist reports to final P2/P3 source/RTL/IP hashes and routed checkpoint. This source review grants no route acceptance.',status='PENDING')]
r['files']=files;r['inspector_sha256']=sha(Path(__file__))
(B/'review/a70-cdc-source-review.json').write_text(json.dumps(r,indent=2)+'\n')
print(r['status']);print('files_hashed',len(files));print('P2/P3 shell identical, new domains0, final routed acceptance PENDING')
