#!/usr/bin/env python3
"""Read-only evidence extraction from frozen stages and actual FPGA collateral."""
from pathlib import Path
import datetime, hashlib, json, re
BASE=Path(__file__).resolve().parents[1]
NAME='chipyard.fpga.nm37.NM37FPGATestHarness.EmptyNM37Config'
files={}
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def text(p):
 key=str(p.relative_to(BASE))
 if key not in files:files[key]=dict(sha256=sha(p),lines=len(p.read_text().splitlines()))
 return p.read_text()
def evidence(p,pattern,minimum=1):
 lines=text(p).splitlines();rows=[dict(line=i,text=l.split('//')[0].strip()) for i,l in enumerate(lines,1) if re.search(pattern,l)]
 assert len(rows)>=minimum,(str(p),pattern,len(rows))
 return dict(file=str(p.relative_to(BASE)),sha256=sha(p),matches=rows)
def plain(p):
 t=re.sub(r'/\*.*?\*/','',text(p),flags=re.S);return re.sub(r'//[^\n]*','',t)
def assert_line(p,fragment):assert fragment in plain(p),(str(p),fragment)
def chain(p,names,first):
 ev=[]
 for n,r in zip(names,[first]+names[:-1]):
  assert re.search(r'\b'+re.escape(n)+r'\s*<=\s*'+re.escape(r)+r'\s*;',plain(p)),(p,n,r)
  ev.append(evidence(p,r'\b'+re.escape(n)+r'\s*<=\s*'+re.escape(r)+r'\s*;'))
 return ev
report=dict(passed=True,scope='Read-only structural source/actual-generated-RTL inspection, before P&R completion; not a new behavioral proof.',stages={})
for stage,output in [('p2','final-p2'),('p3','final-p3-v2')]:
 root=BASE/output/'A70/fpga'/NAME;rtl=root/'gen-collateral';src=BASE/'stages'/stage/'generators'
 ctrl=rtl/'MatrixEngineController.sv';cs=src/'fsa/src/main/scala/fsa/MatrixEngineController.scala';fsa=rtl/'FSA.sv';fs=src/'fsa/src/main/scala/fsa/FSA.scala';arith=src/'fsa/src/main/scala/fsa/arithmetic/FPArithmeticImpl.scala';exp=src/'easyfloat/src/main/scala/easyfloat/Exp2.scala';fmas=src/'easyfloat/src/main/scala/easyfloat/FMA.scala'
 cfg=json.loads(text(root/(NAME+'.FSAConfig.json')))
 assert cfg['sa_rows']==16 and cfg['sa_cols']==16 and cfg['e_type']=='fp16' and cfg['a_type']=='fp32'
 # Every delivered datapath-control field is driven directly by a single output
 # register that captures the combined FSM wavefront (payload capture is gated
 # by the corresponding valid, as prescribed by chisel.util.Pipe).
 controls={};pt=plain(ctrl)
 regnames=set(re.findall(r'^\s*reg\s+(?:\[[^\]]+\]\s+)?(\w+)\s*;',pt,re.M))
 for group in ['sp_read','acc_read','cmp_ctrl','acc_ctrl','sem_release']+[f'pe_ctrl_{i}' for i in range(16)]:
  assignments=re.findall(r'^\s*assign\s+(io_'+group+r'_(?:valid|bits_\w+))\s*=\s*(\w+)\s*;',pt,re.M)
  assert assignments and any(o.endswith('_valid') for o,r in assignments),group
  for out,r in assignments:
   assert r in regnames and re.search(r'\b'+re.escape(r)+r'\s*<=',pt),(group,r)
  controls[group]=dict(fields=len(assignments),output_registers={o:r for o,r in assignments},evidence=evidence(ctrl,r'assign io_'+group+r'_(?:valid|bits_\w+)\s*=|\b'+re.escape(next(r for o,r in assignments if o.endswith('_valid')))+r'\s*<=\s*[^1]'))
 cp=dict(cycles=1,source=evidence(cs,r'io\.(sp_read|acc_read|cmp_ctrl|acc_ctrl|sem_release|pe_ctrl) := .*Pipe',6),all_21_control_groups=controls)
 # Build the generated module graph to inspect every arithmetic descendant.
 modules={p.stem:p for p in rtl.glob('*.sv') if re.search(r'^module\s+'+re.escape(p.stem)+r'\s*\(',p.read_text(),re.M)}
 def children(n):
  return [(m,i) for m,i in re.findall(r'^\s*(\w+)\s+(\w+)\s*\(',plain(modules[n]),re.M) if m in modules]
 def closure(n):
  found=set();todo=[n]
  while todo:
   m=todo.pop()
   if m not in found:found.add(m);todo.extend(c for c,i in children(m))
  return sorted(found)
 pe_cmp={}
 for unit,start in [('PE','FPMacUnit'),('CMP','FPCmpUnit')]:
  desc=closure(start)
  for m in desc:
   assert not re.search(r'\balways\s*@\s*\(\s*(?:pos|neg)edge|\binput\b[^;\n]*\bclock\b',plain(modules[m])),(stage,unit,m)
  pe_cmp[unit]=dict(arithmetic_pipeline_cycles=0,arithmetic_root=start,arithmetic_modules=[dict(module=m,file=str(modules[m].relative_to(BASE)),sha256=sha(modules[m]),clock_ports=0,edge_triggered_blocks=0,children=children(m)) for m in desc],wrapper_evidence=evidence(rtl/(unit+'.sv'),r'\b'+start+r'\s+\w+\s*\(|always @\(posedge clock\)|assign io_out_ctrl'),source_evidence=evidence(arith,r'class FPMacUnit|Right\(pwlPieces\)|class FPCmpUnit|new FMA\(ew, mw, ew, mw\)',4))
 # ACC arithmetic path (exclude the separately stateful reciprocal controller).
 accroot=rtl/'RawFloat_MulAddExp2_1.sv'
 acc=dict(cycles=0 if stage=='p2' else 3,fpacc_instance=evidence(rtl/'FPAccUnit.sv',r'RawFloat_MulAddExp2_1 mulAddExp2'),source=evidence(arith,r'Left\(pwlConst\)|pipeline = true|def latency: Int = 3|accLatency',1))
 if stage=='p2':
  for m in closure('RawFloat_MulAddExp2_1'):
   assert not re.search(r'\balways\s*@\s*\(\s*(?:pos|neg)edge',plain(modules[m]))
  acc.update(entry_cycles=0,internal_fma_cycles=0,arithmetic_modules=closure('RawFloat_MulAddExp2_1'),fma_instance=evidence(accroot,r'RawFloat_FMA fma'))
 else:
  assert_line(arith,'pipeline = true');assert_line(arith,'def latency: Int = 3')
  acc.update(entry_cycles=1,internal_fma_cycles=2,source_entry=evidence(exp,r'pipeline: Boolean = false|nStages = if \(pipeline\) 2 else 0|val f[abc]R = Reg|ShiftRegister\(.*3\)',7),source_internal=evidence(fmas,r'nStages: Int = 0|val prodMantissa = stageReg|val adderOutAbs = stageReg|val expDiff2 = stageReg2',4),entry_registers=evidence(accroot,r'f[abc]R_(?:mantissa|exp|sign) <=',9),fma_instance=evidence(accroot,r'RawFloat_FMA_17 fma|\.io_[abc]_mantissa\s*\(f[abc]R_mantissa\)',4),internal_registers=evidence(rtl/'RawFloat_FMA_17.sv',r'prodMantissa <=|adderOutAbs <=|expDiff2 <= expDiff|prodExp2 <= prodExp',4),combinational_rounding=evidence(rtl/'FPAccUnit.sv',r'assign io_out_accType_(sign|exp|mantissa) =',3),exp2_control_chain=chain(accroot,['inExp2D_r','inExp2D_r_1','inExp2D'],'io_in_exp2'),exp2_integer_chain=chain(accroot,['outIntD_r','outIntD_r_1','outIntD'],'_split_io_outInt'))
  acc['accumulator_control_alignment']=evidence(rtl/'Accumulator.sv',r'(validD|expS1D|expS2D|setD)(?:_r(?:_1)?)? <= [^1]',12)
 # Four write address and valid registers in P3; one in P2. Both chains
 # start at the same controller read wavefront and terminate at actual SRAM.
 depth=1 if stage=='p2' else 4
 addr=['sram_accRAM_fullWrite_0_addr_REG'] if stage=='p2' else ['sram_accRAM_fullWrite_0_addr_r'+s for s in ['', '_1','_2','_3']]
 valid=['sram_accRAM_fullWrite_0_valid_REG'] if stage=='p2' else ['sram_accRAM_fullWrite_0_valid_r'+s for s in ['', '_1','_2','_3']]
 addr_ev=chain(fsa,addr,'_mxControl_io_acc_read_bits_addr')
 valid_ev=chain(fsa,valid,'_mxControl_io_acc_read_valid & _mxControl_io_acc_read_bits_rmw')
 assert depth==1+acc['cycles']
 assert_line(fsa,f'.io_fullWrite_0_valid       ({valid[-1]})');assert_line(fsa,f'.io_fullWrite_0_addr        ({addr[-1]})')
 ram=dict(read_latency_cycles=1,acc_datapath_cycles=acc['cycles'],write_valid_delay_cycles=depth,write_address_delay_cycles=depth,aligned=True,source=evidence(fs,r'accRAM.fullWrite.head.(valid|addr|data)|ev.accLatency \+ 1',3),valid_chain=valid_ev,address_chain=addr_ev,actual_ram_connection=evidence(fsa,r'\.io_fullWrite_0_(valid|addr)\s*\(',2),actual_data_connection=evidence(fsa,r'\.io_fullWrite_0_data_0\s*\(',1),actual_sram_interface=evidence(rtl/'AccumulationSRAM.sv',r'\.W0_addr \(io_fullWrite_0_addr\)|\.W0_en\s*\(io_fullWrite_0_valid',8))
 mem=rtl/(NAME+'.model.mems.v')
 ram['registered_read_evidence']=evidence(mem,r'ram_R_0_addr_pipe_0 <= R0_addr;|assign ram_R_0_addr = ram_R_0_addr_pipe_0;',8)
 report['stages'][stage]=dict(actual_output=str(root.relative_to(BASE)),source_stage=str((BASE/'stages'/stage).relative_to(BASE)),configuration=cfg,control_pipeline=cp,accumulator_pipeline=acc,accRAM_alignment=ram,pe_cmp_no_a100_pipeline=pe_cmp)
# The same control implementation survives both stages byte-for-byte.
r2=BASE/'final-p2/A70/fpga'/NAME/'gen-collateral';r3=BASE/'final-p3-v2/A70/fpga'/NAME/'gen-collateral'
assert sha(r2/'MatrixEngineController.sv')==sha(r3/'MatrixEngineController.sv')
report['cross_stage']=dict(control_rtl_byte_identical=True,pe_cmp_wrapper_logic_identical={n:re.sub(r'\s+','',plain(r2/(n+'.sv')))==re.sub(r'\s+','',plain(r3/(n+'.sv'))) for n in ['PE','CMP','FPMacUnit','FPCmpUnit']})
assert all(report['cross_stage']['pe_cmp_wrapper_logic_identical'].values())
report['files']=files
report['inspector_sha256']=sha(Path(__file__))
report['conclusion']='PASS: P2/P3 controlPipe=1; P2 ACC arithmetic=0, P3 ACC=1 entry+2 internal=3; accRAM valid/address alignment P2=1/P3=4=read1+ACC latency; both PE/CMP arithmetic subtrees remain combinational (no A100 arithmetic pipeline).'
(BASE/'review/a70-generated-structure.json').write_text(json.dumps(report,indent=2)+'\n')
print(report['conclusion']);print('hashed_files',len(files))
