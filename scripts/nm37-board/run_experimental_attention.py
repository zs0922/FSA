#!/usr/bin/env python3
"""Run the original FSA/Torch/PyEasyFloat error display on a selected NM37 board."""
import argparse
import contextlib
import json
import os
from pathlib import Path
import sys
from board_test import probe_device, sha

class Tee:
    def __init__(self, stream, log): self.stream, self.log = stream, log
    def write(self, text):
        self.stream.write(text); self.log.write(text); self.flush()
        return len(text)
    def flush(self): self.stream.flush(); self.log.flush()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True,help='Matching FSA repository root')
    p.add_argument('--bitstream',type=Path,required=True,help='Operator-declared programmed .bit; not hardware readback')
    p.add_argument('--config',type=Path,required=True,help='Exact generated FSAConfig.json')
    p.add_argument('--device',required=True,help='Confirmed xdmaN')
    p.add_argument('--bdf',required=True,help='Confirmed full PCI BDF')
    p.add_argument('--seq',type=int,default=16)
    p.add_argument('--out',type=Path,required=True,help='New output directory')
    p.add_argument('--dry-run',action='store_true',help='Validate inputs only; no hardware access')
    a=p.parse_args()
    source=a.source.resolve(); config=a.config.resolve()
    software=source/'generators/fsa/python';easy=source/'generators/easyfloat'
    if not (software/'main.py').is_file() or not easy.is_dir():p.error('Missing FSA/easyfloat source')
    if not a.bitstream.is_file() or a.bitstream.stat().st_size==0:p.error('Missing/empty bitstream')
    cfg=json.loads(config.read_text())
    if (cfg.get('sa_rows'),cfg.get('sa_cols'))!=(16,16):p.error('Expected 16x16 hardware config')
    if a.seq<=0 or a.seq%16:p.error('--seq must be a positive multiple of 16')
    import re
    if not re.fullmatch(r'xdma\d+',a.device) or not re.fullmatch(r'[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]',a.bdf):p.error('Invalid device/BDF syntax')
    if a.out.exists():p.error('Output exists; select a new --out')
    if a.dry_run:
        print('INPUTS OK; no hardware access or numerical test performed');return
    mapping=probe_device(a.device,a.bdf)
    a.out.mkdir(parents=True)
    metadata={'bitstream':str(a.bitstream.resolve()),'bitstream_sha256':sha(a.bitstream),'bit_identity_kind':'operator_declaration_not_readback','source':str(source),'config':str(config),'config_sha256':sha(config),'seq':a.seq,
              'device':mapping,'status':'started','purpose':'experimental-error-display'}
    def save(): (a.out/'run.json').write_text(json.dumps(metadata,indent=2)+'\n')
    save()
    sys.path.insert(0,str(software));sys.path.insert(0,str(easy))
    engine=None
    with (a.out/'console.log').open('w') as log, contextlib.redirect_stdout(Tee(sys.stdout,log)), contextlib.redirect_stderr(Tee(sys.stderr,log)):
        try:
            import fsa as F
            import main as workload
            import pyeasyfloat
            if not Path(pyeasyfloat.__file__).resolve().is_relative_to(easy):raise RuntimeError('Wrong easyfloat import')
            F.init(str(config))
            engine=F.FPGA(control_dev=f'/dev/{a.device}_user',mem_read_dev=f'/dev/{a.device}_c2h_0',mem_write_dev=f'/dev/{a.device}_h2c_0')
            state=int(engine.MMIO.dev_mmio_read(engine.STATE_OFFSET))
            if state not in (0,2):raise RuntimeError(f'Device not idle/done: {state}; reset/reload before retry')
            if engine.MMIO.dev_mmio_read(engine.PERF_ENQ_INST)!=engine.MMIO.dev_mmio_read(engine.PERF_DEQ_INST):raise RuntimeError('Outstanding instructions; reset/reload before retry')
            print(f'CONFIG={config}\nDEVICE={a.device} BDF={a.bdf} SEQ={a.seq} ARRAY=16x16')
            workload.main(a.seq,a.seq,d=16,br=16,bc=16,engine=engine,diff_easyfloat=True)
            metadata['status']='completed_metrics_printed'
            print('Completed: inspect error metrics; completion is not a numerical PASS threshold.')
        except BaseException as exc:
            metadata.update(status='failed_or_interrupted',error=str(exc));raise
        finally:
            save()
            if engine is not None:engine.MMIO.close()

if __name__=='__main__': main()
