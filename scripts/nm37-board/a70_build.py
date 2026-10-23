#!/usr/bin/env python3
"""Diagnostic frontend for frozen A70 drivers. Never rewrites seals or retries over artifacts."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

TOP = 'chipyard.fpga.nm37.NM37FPGATestHarness.EmptyNM37Config'
PHASES = ('prepare', 'assembly', 'units', 'formal', 'sim4', 'sim16', 'elaborate', 'implement', 'bitstream')


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()


def need(ok, message):
    if not ok: raise ValueError(message)


def read(path):
    try: return json.loads(path.read_text())
    except (OSError, ValueError) as exc: raise ValueError(f'无法读取 {path}: {exc}') from exc


def artifacts(out, phase):
    build = out / 'A70/fpga' / TOP
    return {
        'prepare': [out],
        'assembly': [out/'generator.jar', out/'logs/assembly.log'],
        'units': [out/'logs/units.log', out/'formal-rtl'],
        'formal': [out/'formal', out/'logs/formal.log'],
        'sim4': [out/'sim4'], 'sim16': [out/'sim16'],
        'elaborate': [out/'A70', out/'logs/elaboration.log'],
        'implement': [build/'obj', out/'logs/implementation.log', out/'physical.passed.json'],
        'bitstream': [out/'bitstream', out/'logs/bitstream.log', out/'physical-export-recheck.json'],
    }[phase]


def dependencies(phase, stage):
    if phase == 'prepare': return []
    if phase == 'assembly': return ['prepare']
    if phase == 'formal': return ['prepare', 'assembly', 'units']
    if phase in ('units', 'sim4', 'sim16', 'elaborate'): return ['prepare', 'assembly']
    result = ['prepare', 'assembly', 'sim4', 'sim16', 'elaborate']
    if stage == 'p3': result += ['units', 'formal']
    if phase == 'bitstream': result += ['implement']
    return result


def markers(out, phase):
    if phase == 'prepare':
        return [out/name for name in ('build.json', 'source-lock.json', 'drivers.json', 'shell.json')]
    return [out/(phase+'.passed.json')]


def state(out, phase):
    paths = markers(out, phase)
    if all(p.is_file() for p in paths):
        try:
            for p in paths: read(p)
            return '记录存在（未重新验收）'
        except ValueError: return '记录损坏'
    if any(p.exists() for p in paths + artifacts(out, phase)):
        return '不完整/已有产物，不能视为通过'
    return '未开始'


def driver_identity(driver, out):
    recorded = read(out/'drivers.json')
    actual = {p.relative_to(driver).as_posix(): sha(p) for p in sorted(driver.rglob('*'))
              if p.is_file() and '__pycache__' not in p.parts}
    need(recorded == actual,
         '驱动与 prepare 时的 drivers.json 不一致。不要修改旧封印；保留原工具目录，'
         '或独立检出原工具 commit，并用 --driver 指向完全匹配的 scripts/nm37-a70。'
         '新工具仅运行本前端；如要使用新驱动构建，请选择新源码和新 A70_OUT。')


def preflight(driver, source, out, stage, phase):
    need((driver/'run.sh').is_file(), '找不到驱动 run.sh: '+str(driver))
    need(stage in ('p2','p3'), 'A70_STAGE 必须为 p2 或 p3')
    need(stage == 'p3' or phase not in ('units','formal'), 'units/formal 仅适用于 P3；P2 使用系统回归。')
    need(all(p.is_absolute() and not any(c.isspace() for c in str(p)) for p in (driver,source,out)),
         'driver、A70_SOURCE、A70_OUT 必须是无空白字符的绝对路径。')
    if phase != 'prepare':
        missing = [(p, f) for p in dependencies(phase,stage) for f in markers(out,p) if not f.is_file()]
        need(not missing, '前置阶段未完成，缺少：'+', '.join(str(f) for _,f in missing)+
             '。先检查对应日志；未开始的阶段可执行，有失败产物的阶段需保留现场后处理；不能手工生成 .passed.json。')
        build = read(out/'build.json')
        need(Path(build['source']).resolve() == source.resolve() and build['stage'] == stage,
             '输出目录属于不同的源码目录或阶段；请恢复原 A70_SOURCE/A70_STAGE，不要混用。')
        driver_identity(driver,out)
    existing_markers = markers(out,phase)
    if phase != 'prepare' and any(p.exists() for p in existing_markers):
        raise ValueError(f'{phase} 已有成功记录或记录残留：{existing_markers[0]}。不要重跑覆盖；'
                         '先用 status 查看，记录存在不等于已重新验收；若要复验使用新构建。')
    occupied = [p for p in artifacts(out,phase) if p.exists() or p.is_symlink()]
    need(not occupied, f'{phase} 已有产物：'+', '.join(map(str,occupied))+
         '。可能是之前失败/中断或已完成的阶段。先查看日志并保留目录；不要删除日志以绕过门禁，'
         '也不要复制封印。如需重新构建，使用独立源码和新的 A70_OUT；原成功结果保留。')
    if phase == 'bitstream':
        review = out/'cdc-constraint-review.json'
        need(review.is_file(), f'缺少本次 CDC/约束审查 {review}。按复现文档审查实际 routed 报告后填写，不能沿用旧 DCP 审查。')


def failure_help(out, phase, rc):
    print(f'ERROR: {phase} 失败，退出码 {rc}。此前 source verify 的 PASS 不是阶段成功。', file=sys.stderr)
    paths = list((out/'logs').glob('*.log')) if (out/'logs').exists() else []
    if phase.startswith('sim'): paths += list((out/phase).glob('*.log'))
    if paths:
        print('检查日志（不删除、不覆盖）：\n'+ '\n'.join(str(p) for p in paths),file=sys.stderr)
    log_names = {'assembly':'assembly.log','units':'units.log','formal':'formal.log',
                 'elaborate':'elaboration.log','implement':'implementation.log','bitstream':'bitstream.log'}
    relevant = ([out/'logs'/log_names[phase]] if phase in log_names else
                sorted((out/phase).glob('*.log'), key=lambda p:p.stat().st_mtime) if phase.startswith('sim') else [])
    if relevant and relevant[-1].is_file():
        log = relevant[-1]
        with log.open('rb') as stream:
            stream.seek(max(0, log.stat().st_size - 16384))
            tail = stream.read().decode(errors='replace').splitlines()[-40:]
        print(f'最近日志末尾（最多40行）：{log}\n'+'\n'.join(tail),file=sys.stderr)
    print('保留本次结果。修复环境后仅继续尚未开始的阶段；已有失败产物须先诊断，'
          '或用独立源码/新输出重新构建。不要手工补写成功标记。',file=sys.stderr)


def extra_workload(args, driver, source, out, stage):
    need(args.seq > 0 and args.seq % args.array_size == 0, '--seq 必须为正数且是阵列尺寸的整数倍')
    need(args.max_cycles > 0, '--max-cycles 必须为正整数')
    need(bool(args.result_out), '额外序列测试必须给出 --result-out /绝对/新目录，不修改已封印 sim 目录')
    dest = args.result_out
    need(dest.is_absolute() and not dest.exists() and not dest.is_symlink(), '--result-out 必须是尚不存在的绝对路径')
    need(not any(dest.resolve().is_relative_to(p.resolve()) or p.resolve().is_relative_to(dest.resolve())
                 for p in (out,source,driver)), '--result-out 必须与构建、源码、驱动目录分离')
    for phase in ('prepare','assembly',f'sim{args.array_size}'):
        need(all(p.is_file() for p in markers(out,phase)), f'先完成 {phase}，再复用其仿真器测试 seq={args.seq}')
    driver_identity(driver,out)
    env=dict(os.environ,A70_SOURCE=str(source),A70_OUT=str(out),A70_STAGE=stage)
    subprocess.run(['python3',str(driver/'artifacts.py'),'check','--out',str(out)],env=env,check=True)
    size=args.array_size
    cfg='AXI4FSA4X4Fp16Config' if size==4 else 'A70SinglePort16X16Fp16Config'
    binary=out/f'sim{size}/simulator'
    config=out/f'sim{size}/build'/f'chipyard.harness.TestHarness.{cfg}.FSAConfig.json'
    seal=read(out/f'sim{size}.passed.json')
    need(seal['source_lock_sha256']==sha(out/'source-lock.json'), '仿真器封印不属于当前 source-lock')
    for p in (binary,config):
        need(p.is_file() and seal['files'].get(p.relative_to(out).as_posix())==sha(p),'仿真器/config 与封印不一致: '+str(p))
    python=os.environ.get('FSA_PYTHON')
    need(bool(python), '请设置 FSA_PYTHON 为该源码的已安装 Python 环境')
    env['PYTHONPATH']=str(source/'generators/easyfloat')+':'+str(source/'generators/fsa/python')
    runner=Path(__file__).with_name('verify_a70_workload.py')
    subprocess.run([python,str(runner),'--source',str(source),'--binary',str(binary),'--config',str(config),
                    '--out',str(dest),'--seq',str(args.seq),'--max-cycles',str(args.max_cycles)],env=env,check=True)
    result=read(dest/'result.json')
    need(result['bitwise_equal'] is True, '额外仿真未逐位通过')
    provenance={'source_lock_sha256':sha(out/'source-lock.json'),'sim_seal_sha256':sha(out/f'sim{size}.passed.json'),
                'verifier_sha256':sha(runner),'frontend_sha256':sha(Path(__file__)),
                'drivers_seal_sha256':sha(out/'drivers.json'),'seq':args.seq,'array_size':size,
                'max_cycles':args.max_cycles,'result_sha256':sha(dest/'result.json')}
    with (dest/'provenance.json').open('x') as f: json.dump(provenance,f,indent=2);f.write('\n')
    print(f'COMPLETE: sim array={size}, seq={args.seq}; {dest}/result.json。原阶段封印未修改。')


def parser():
    p=argparse.ArgumentParser(description=__doc__,epilog='sim --array-size 16 运行原基线回归；sim --array-size 16 --seq 1024 --result-out /new/path 复用已封印仿真器。')
    p.add_argument('--driver',type=Path,default=Path(__file__).resolve().parents[1]/'nm37-a70',help='已有构建请指向原冻结工具目录')
    p.add_argument('phase',choices=('status','prepare','assembly','units','formal','sim','elaborate','implement','bitstream'))
    p.add_argument('--array-size',type=int,choices=(4,16))
    p.add_argument('--seq',type=int)
    p.add_argument('--max-cycles',type=int,default=10000000)
    p.add_argument('--result-out',type=Path)
    return p


def main(argv=None):
    p=parser();args=p.parse_args(argv)
    phase=args.phase
    try:
        need(phase=='sim' or (args.array_size is None and args.seq is None and args.result_out is None and args.max_cycles==10000000),
             '--array-size/--seq/--result-out/--max-cycles 只用于 sim')
        if phase=='sim':
            need(args.array_size in (4,16),'sim 必须明确 --array-size 4 或 16；1024 是 --seq，不是阵列尺寸')
            need(args.seq is not None or (args.result_out is None and args.max_cycles==10000000), '--result-out/--max-cycles 仅用于显式 --seq 的额外测试')
            phase=f'sim{args.array_size}'
        for key in ('A70_SOURCE','A70_OUT','A70_STAGE'): need(bool(os.environ.get(key)),f'请设置 {key}')
        source,out=Path(os.environ['A70_SOURCE']),Path(os.environ['A70_OUT']);stage=os.environ['A70_STAGE']
        driver=args.driver.resolve()
        if args.phase=='status':
            print(f'A70_SOURCE={source}\nA70_OUT={out}\nA70_STAGE={stage}\nDRIVER={driver}')
            for name in PHASES:
                if stage=='p2' and name in ('units','formal'): continue
                print(f'{name}: {state(out,name)}')
            if (out/'drivers.json').exists(): driver_identity(driver,out)
            print('status 仅读取记录/文件并核对驱动身份，不等于功能、完整源锁或物理验收。')
            return 0
        if args.seq is not None:
            extra_workload(args,driver,source,out,stage)
            return 0
        preflight(driver,source,out,stage,phase)
        command=['bash',str(driver/'run.sh'),args.phase]
        if args.phase=='sim': command.append(str(args.array_size))
        print(f'START: {phase}，源校验 PASS 不代表此阶段完成。',flush=True)
        rc=subprocess.run(command).returncode
        if rc:
            failure_help(out,phase,rc)
            return rc if rc>0 else 128-rc
        need(all(p.is_file() for p in markers(out,phase)),f'{phase} 返回0但缺少完成记录，不能视为成功')
        for marker in markers(out,phase): read(marker)
        print(f'COMPLETE: {phase}; '+', '.join(str(p) for p in markers(out,phase)))
        return 0
    except (ValueError,OSError,KeyError,subprocess.CalledProcessError) as exc:
        print('ERROR: '+str(exc),file=sys.stderr)
        if isinstance(exc,subprocess.CalledProcessError) and args.result_out:
            print(f'额外仿真失败；保留 {args.result_out} 和终端日志，新尝试使用新目录；未改写原 sim 封印。',file=sys.stderr)
        return 2


if __name__=='__main__': sys.exit(main())
