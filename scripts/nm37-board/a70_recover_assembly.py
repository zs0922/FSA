#!/usr/bin/env python3
"""Archive an unsealed A70 assembly attempt; never compile or manufacture a pass.

Run only after all jobs using this source/output have stopped. The default is a
read-only plan. --apply copies and verifies each file before removing its original.
Keep this tool outside the frozen scripts/nm37-a70 driver directory.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import a70_build as build


def exists(path):
    return path.exists() or path.is_symlink()


def separate(a, b):
    return not (a.is_relative_to(b) or b.is_relative_to(a))


def write(path, data):
    with path.open('x') as stream:
        json.dump(data, stream, indent=2, ensure_ascii=False)
        stream.write('\n')


def recovery_plan(args):
    for key in ('A70_SOURCE', 'A70_OUT', 'A70_STAGE'):
        build.need(bool(os.environ.get(key)), f'请设置 {key}')
    source, out = (Path(os.environ[k]) for k in ('A70_SOURCE', 'A70_OUT'))
    stage = os.environ['A70_STAGE']
    paths = [source, out, args.driver, args.archive]
    if args.new_out is not None:
        paths.append(args.new_out)
    build.need(all(p.is_absolute() and not any(c.isspace() for c in str(p)) for p in paths),
               '所有目录必须是无空白字符的绝对路径')
    source, out, driver, archive = (p.resolve() for p in paths[:4])
    new_out = args.new_out.resolve() if args.new_out is not None else None
    build.need(stage in ('p2', 'p3'), 'A70_STAGE 必须为 p2 或 p3')
    build.need(source.is_dir() and out.is_dir() and driver.is_dir(), '源码、旧输出、原驱动目录必须存在')
    build.need(not exists(args.archive), '归档目录已存在；保留之前的恢复记录，使用新目录')
    for p in (source, out, driver):
        build.need(separate(archive, p), '归档目录必须与源码、旧输出、驱动分离')
    if new_out is not None:
        build.need(not exists(args.new_out), '新输出目录已存在；不能覆盖，请选择新名字')
        build.need(all(separate(new_out, p) for p in (source, out, driver, archive)),
                   '新输出必须与源码、旧输出、驱动、归档目录分离')
    build.need(not exists(out/'assembly.passed.json'),
               'assembly.passed.json 已存在；此工具只恢复缺失 assembly 记录的构建，不改写已有封印')
    for p in build.markers(out, 'prepare'):
        build.need(p.is_file() and not p.is_symlink(), f'prepare 不完整或记录不是普通文件：{p}')
    metadata = build.read(out/'build.json')
    build.need(Path(metadata['source']).resolve() == source and metadata['stage'] == stage,
               'A70_SOURCE/A70_STAGE 与原 build.json 不匹配')
    build.driver_identity(driver, out)
    downstream = sorted({p for phase in build.PHASES[2:]
                         for p in build.markers(out, phase) + build.artifacts(out, phase) if exists(p)}
                        | {p for p in out.glob('*.passed.json') if exists(p)})
    if new_out is None:
        build.need(not downstream,
                   '发现下游产物，拒绝原地恢复：'+', '.join(map(str, downstream))+
                   '。保留旧输出，追加 --new-out /绝对/新目录；新构建重新生成下游证据。')
    patch_state = source/'.nm37-a70/patch-state.json'
    build.need(patch_state.is_file(), '缺少已有补丁状态；恢复操作不会重新安装或应用补丁')
    env = dict(os.environ, A70_SOURCE=str(source), A70_OUT=str(out), A70_STAGE=stage,
               PYTHONDONTWRITEBYTECODE='1')
    logs = {}
    # Check the source lock before executing the source's installed verifier.
    commands = [
        ('source-check', ['python3', str(driver/'artifacts.py'), 'check', '--out', str(out)]),
        ('patch-check', ['python3', str(source/'scripts/nm37-a70-prepare.py'),
                         '--root', str(source), '--stage', stage]),
    ]
    for label, command in commands:
        print('CHECK: '+shlex.join(command), flush=True)
        result = subprocess.run(command, env=env, capture_output=True, text=True)
        logs[label] = result.stdout + result.stderr
        print(logs[label], end='', flush=True)
        build.need(result.returncode == 0,
                   f'{label} 失败（退出码 {result.returncode}）；未归档任何文件。先恢复原源码/驱动身份，不能重写源锁。')
    candidates = [(source/'.classpath_cache/a70-release.jar', source, 'source-cache/a70-release.jar')]
    if new_out is None:
        candidates += [(out/'generator.jar', out, 'old-output/generator.jar'),
                       (out/'logs/assembly.log', out, 'old-output/assembly.log')]
    files = []
    for path, root, dest in candidates:
        if not exists(path):
            continue
        build.need(not path.is_symlink() and path.is_file() and path.resolve().is_relative_to(root),
                   f'拒绝自动归档符号链接/特殊文件或越界路径：{path}；需人工核查其来源')
        stat = path.stat()
        files.append(dict(original=str(path), archived=dest, sha256=build.sha(path),
                          size=stat.st_size, mode=stat.st_mode, mtime_ns=stat.st_mtime_ns))
    plan = dict(schema_version=1, source=str(source), old_out=str(out), driver=str(driver),
                stage=stage, new_out=str(new_out) if new_out else None,
                mode='new-output' if new_out else 'in-place',
                source_lock_sha256=build.sha(out/'source-lock.json'),
                drivers_sha256=build.sha(out/'drivers.json'),
                recovery_tool_sha256=build.sha(Path(__file__)),
                frontend_sha256=build.sha(Path(build.__file__)),
                downstream_preserved=[str(p) for p in downstream], files=files)
    return plan, logs, patch_state


def apply_plan(plan, logs, patch_state, archive):
    archive.mkdir(parents=True, exist_ok=False)
    write(archive/'plan.json', plan)
    evidence = archive/'evidence'
    evidence.mkdir()
    out = Path(plan['old_out'])
    records = build.markers(out, 'prepare') + [patch_state]
    installed = patch_state.with_name('install-state.json')
    if installed.is_file() and not installed.is_symlink():
        records.append(installed)
    for path in records:
        shutil.copy2(path, evidence/path.name)
    for label, log in logs.items():
        (evidence/(label+'.log')).write_text(log)
    for item in plan['files']:
        origin, dest = Path(item['original']), archive/item['archived']
        build.need(not origin.is_symlink() and build.sha(origin) == item['sha256'],
                   f'归档前文件已改变，停止：{origin}；请检查是否仍有构建进程')
        dest.parent.mkdir(parents=True, exist_ok=True)
        with origin.open('rb') as src, dest.open('xb') as dst:
            shutil.copyfileobj(src, dst)
            dst.flush()
            os.fsync(dst.fileno())
        shutil.copystat(origin, dest)
        build.need(build.sha(dest) == item['sha256'] and not origin.is_symlink()
                   and build.sha(origin) == item['sha256'],
                   f'复制后校验失败，保留原文件及归档现场：{origin}')
        origin.unlink()
    write(archive/'archived.json', dict(files=plan['files'], assembly_retried=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--driver', required=True, type=Path, help='原 prepare 使用的冻结 scripts/nm37-a70')
    parser.add_argument('--archive', required=True, type=Path, help='源码/输出/驱动之外的全新归档目录')
    parser.add_argument('--new-out', type=Path, help='保留整个旧输出，重新 prepare 的全新输出目录')
    parser.add_argument('--apply', action='store_true', help='执行已校验的归档；默认只读预览')
    args = parser.parse_args(argv)
    try:
        print('前提：同一源码/输出的 make、sbt、仿真、Vivado 作业均已停止；本工具不会停止或锁住这些作业。', flush=True)
        plan, logs, patch_state = recovery_plan(args)
        print(json.dumps(plan, indent=2, ensure_ascii=False), flush=True)
        if args.apply:
            apply_plan(plan, logs, patch_state, args.archive)
            print(f'ARCHIVED: {args.archive}/archived.json；未编译、未写入任何 .passed.json。')
        else:
            print('PLAN ONLY: 未搬移文件；检查计划后用相同参数追加 --apply。')
        selected_out = plan['new_out'] or plan['old_out']
        print('重试时设置：export A70_OUT='+shlex.quote(selected_out))
        print('使用 a70_build.py --driver '+shlex.quote(plan['driver'])+
              (' prepare，然后 assembly；新输出须重跑后续阶段。' if plan['new_out'] else ' assembly。'))
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print('ERROR: '+str(exc), file=sys.stderr)
        print('保留旧构建及可能已有的归档/plan.json；未产生新的 assembly 成功记录。不要复制或补写封印。', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
