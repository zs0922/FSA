#!/usr/bin/env python3
"""Read-only NM37 board evidence validation and A70/P3-based comparison. Needs numpy."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys

import numpy as np

CLOCKS = {'A70-P3': 70, 'A100': 100, 'A110-APB55': 110}
COUNTERS = ('execTime', 'mxBubble', 'mxActive', 'dmaActive', 'rawInst',
            'mxInst', 'dmaInst', 'fenceInst', 'enq', 'deq')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_text())


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def uint(value, positive=False):
    return type(value) is int and (1 if positive else 0) <= value < 2**32


def near(a, b):
    return type(a) in (int, float) and math.isfinite(a) and math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)


def stats(values):
    return {'min': min(values), 'median': statistics.median(values), 'max': max(values)}


def validate(folder, meta):
    require(not (folder / 'failed.json').exists(), 'failed.json exists')
    top = read(folder / 'result.json')
    require(meta['dry_run'] is False and meta['hardware_tested'] is True and
            top['passed'] is True and top['hardware_tested'] is True, 'not a completed hardware PASS')
    require(meta['schema_version'] == 1, 'unsupported run schema')
    cfg, manifest = meta['configuration_id'], meta['manifest']
    require(cfg in CLOCKS, 'unsupported configuration (P2 is not accepted)')
    mhz = CLOCKS[cfg]
    require(manifest['schema_version'] == 2 and manifest['configuration_id'] == cfg and
            type(manifest['clock_mhz']) is int and manifest['clock_mhz'] == mhz and
            meta['clock_mhz_assumed'] == mhz and
            meta['loaded_bitstream_sha256_declared'] == manifest['bitstream']['sha256'], 'hardware identity/frequency mismatch')
    repeat, warmup = meta['repeat'], meta['warmup']
    require(uint(repeat, True) and uint(warmup) and uint(meta['seq'], True) and
            meta['seq'] % 16 == 0 and uint(meta['seed']), 'invalid measurement parameters')
    runs = top['runs']
    require(len(runs) == repeat + warmup and top['measurement_count'] == repeat and
            top['warmup_count'] == warmup, 'measurement count mismatch')
    workload = meta['workload']
    words = workload['instruction_words']
    require(uint(words, True) and (folder / 'instructions.bin').stat().st_size == 4 * words and
            sha(folder / 'instructions.bin') == workload['instructions_sha256'], 'instruction evidence mismatch')
    require(sha(folder / 'reference.npy') == workload['reference_sha256'], 'reference hash mismatch')
    reference = np.load(folder / 'reference.npy', allow_pickle=False)
    require(reference.dtype == np.float32 and reference.shape == (meta['seq'], 16), 'reference shape/type mismatch')
    with np.load(folder / 'inputs.npz', allow_pickle=False) as inputs:
        require(set(inputs.files) == {'Q', 'K', 'V'} and set(workload['inputs']) == {'Q', 'K', 'V'}, 'input names mismatch')
        for name in ('Q', 'K', 'V'):
            array = inputs[name]
            expected = workload['inputs'][name]
            require(array.shape == (meta['seq'], 16) and array.dtype == np.float16 and
                    list(array.shape) == expected['shape'] and array.dtype.str == expected['dtype'] and
                    hashlib.sha256(array.tobytes(order='C')).hexdigest() == expected['sha256_raw_c_order'], 'input evidence mismatch: ' + name)
    measured = []
    for index, run in enumerate(runs):
        sub = folder / f'run-{index:03d}'
        require(read(sub / 'result.json') == run, 'root/individual result mismatch')
        require(run['passed'] is True and run['bit_mismatches'] == 0 and
                run['warmup'] is (index < warmup), 'failed run or wrong warmup marker')
        require(sha(sub / 'actual.npy') == run['actual_sha256'], 'actual output hash mismatch')
        actual = np.load(sub / 'actual.npy', allow_pickle=False)
        require(actual.shape == reference.shape and actual.dtype == np.float32 and
                np.array_equal(actual.view(np.uint32), reference.view(np.uint32)), 'actual/reference bit mismatch')
        counters, before = run['counters'], run['counters_before']
        require(set(counters) == set(COUNTERS) and set(before) == set(COUNTERS) and
                all(uint(v) for v in [*counters.values(), *before.values()]), 'invalid uint32 counters')
        require(counters['execTime'] > 0 and run['state_before'] in (0, 2) and
                before['enq'] == before['deq'] and counters['enq'] == counters['deq'] and
                run['instruction_words_expected'] == words, 'counter/state invariant failed')
        for name in ('enq', 'deq'):
            require(run['instruction_word_deltas'][name] == words ==
                    (counters[name] - before[name]) % 2**32, 'instruction delta mismatch')
        start, end = run['monotonic_start_ns'], run['monotonic_end_ns']
        require(type(start) is int and type(end) is int and end > start >= 0, 'invalid monotonic timestamps')
        require(near(run['exec_us'], counters['execTime'] / mhz) and
                near(run['host_execute_ms'], (end - start) / 1e6), 'incorrect timing conversion')
        if index >= warmup:
            measured.append(dict(run_index=index, **{n: counters[n] for n in COUNTERS},
                                 exec_us=counters['execTime'] / mhz, host_execute_ms=(end - start) / 1e6,
                                 bubble_pct=100 * counters['mxBubble'] / counters['execTime']))
    aggregates = {name: stats([r[name] for r in measured]) for name in
                  (*COUNTERS, 'exec_us', 'host_execute_ms', 'bubble_pct')}
    for name in ('exec_us', 'host_execute_ms'):
        require(all(near(top[name][k], v) for k, v in aggregates[name].items()), 'root timing summary mismatch')
    # Paths and hardware commits differ by design. Compare actual software content/config/workload instead.
    comparison = dict(seq=meta['seq'], seed=meta['seed'], repeat=repeat, warmup=warmup,
                      workload=workload, software=manifest['software']['files'],
                      easyfloat=manifest['software']['easyfloat']['files'], config=manifest['config']['sha256'],
                      hostname=meta['hostname'], python=meta['python'],
                      host_execute_boundary=meta['host_execute_boundary'], counter_scope=meta['counter_scope'])
    return dict(configuration=cfg, seq=meta['seq'], seed=meta['seed'], clock_mhz=mhz,
                repeat=repeat, warmup=warmup, source_directory=str(folder.resolve()),
                run_json_sha256=sha(folder / 'run.json'), result_json_sha256=sha(folder / 'result.json'),
                hardware_identity={k: manifest[k] for k in ('hardware_source_commit', 'build_id', 'source_identity_kind', 'bitstream')},
                comparison=comparison, statistics=aggregates, measurements=measured)


def collect(root, min_repeat):
    groups, excluded, identities = {}, [], {}
    paths = sorted(root.rglob('run.json'))
    require(bool(paths), 'No run.json found')
    for path in paths:
        meta = read(path)
        if meta.get('dry_run') is True:
            excluded.append(dict(directory=str(path.parent), reason='dry-run; no hardware data'))
            continue
        require(uint(meta.get('repeat'), True), f'{path}: invalid repeat')
        if meta['repeat'] < min_repeat:
            excluded.append(dict(directory=str(path.parent), reason=f'repeat < {min_repeat}; not selected'))
            continue
        try:
            item = validate(path.parent, meta)
            cfg = item['configuration']
            require(cfg not in identities or identities[cfg] == item['hardware_identity'], 'mixed hardware builds for one configuration')
            identities[cfg] = item['hardware_identity']
            group = groups.setdefault((item['seq'], item['seed']), {})
            require(cfg not in group, 'duplicate configuration/seq/seed; select one campaign, do not pool retries/smoke runs')
            group[cfg] = item
        except (ValueError, KeyError, OSError, TypeError) as exc:
            raise ValueError(f'{path.parent}: {exc}') from exc
    require(bool(groups), 'No selected hardware measurements')
    rows, raw, details = [], [], []
    for key, group in sorted(groups.items()):
        require(set(group) == set(CLOCKS), f'seq/seed {key}: need all three configurations; found {sorted(group)}')
        baseline = group['A70-P3']
        for cfg in CLOCKS:
            item = group[cfg]
            require(item['comparison'] == baseline['comparison'], f'seq/seed {key}, {cfg}: workload/software/environment/test policy differs from A70-P3')
            s, base = item['statistics'], baseline['statistics']
            row = dict(configuration=cfg, seq=key[0], seed=key[1], repeat=item['repeat'], clock_mhz=item['clock_mhz'])
            for metric in ('execTime', 'exec_us', 'host_execute_ms', 'mxBubble', 'bubble_pct', 'mxActive', 'dmaActive'):
                for stat in ('min', 'median', 'max'):
                    row[f'{metric}_{stat}'] = s[metric][stat]
            row.update(fpga_speedup=base['exec_us']['median'] / s['exec_us']['median'],
                       host_speedup=base['host_execute_ms']['median'] / s['host_execute_ms']['median'],
                       bubble_change_pct=(100 * (s['mxBubble']['median'] / base['mxBubble']['median'] - 1)
                                          if base['mxBubble']['median'] else None))
            rows.append(row)
            raw.extend(dict(configuration=cfg, seq=key[0], seed=key[1], **r) for r in item['measurements'])
            details.append(item)
    return dict(schema_version=1, baseline='A70-P3', excluded=excluded, rows=rows, raw_measurements=raw, evidence=details)


def write_report(result, out):
    out.mkdir(parents=True, exist_ok=False)
    (out / 'summary.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    for filename, rows in [('comparison.csv', result['rows']), ('measurements.csv', result['raw_measurements'])]:
        with (out / filename).open('w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    lines = ['# NM37 板测性能对比', '',
             '仅汇总通过证据核验的实际板测，预热已排除；以下数值为每组测量中位数。', '',
             '| 配置 | seq | seed | 次数 | exec周期 | FPGA µs | FPGA加速比 | 主机 ms | 主机加速比 | mxBubble周期 | bubble占比 % | bubble变化 % | mxActive | dmaActive |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    fields = ['configuration', 'seq', 'seed', 'repeat', 'execTime_median', 'exec_us_median', 'fpga_speedup',
              'host_execute_ms_median', 'host_speedup', 'mxBubble_median', 'bubble_pct_median', 'bubble_change_pct', 'mxActive_median', 'dmaActive_median']
    for row in result['rows']:
        values = [row[k] for k in fields]
        lines.append('| ' + ' | '.join('N/A' if v is None else f'{v:.4f}' if isinstance(v, float) else str(v) for v in values) + ' |')
    lines += ['', '加速比 = A70-P3 时间中位数 / 当前配置时间中位数；大于 1 表示更快。',
              'FPGA 时间 = execTime / 70、100、110 MHz；频率来自 manifest 与操作员加载声明，未实测。',
              '主机时间包含输入写入、指令提交、轮询、调试输出和结果读回；不包含 reference 计算。',
              'bubble 占比为逐次 100 × mxBubble / execTime 的中位数，表示矩阵指令接口 ready && !valid，不是 PE 总空闲率。',
              'bubble 变化 = (当前 mxBubble 中位数 / A70-P3 中位数 − 1) × 100%；基线为零时为 N/A。',
              'ready=0 的依赖等待无法仅凭这些计数器拆分；计数器不能简单相加。八项性能计数器溢出无法由保存值可靠检测。',
              'A70 是历史 P3，跨配置差异同时包含结构和频率变化。真实上板/加载身份仍依赖原始设备及烧录记录。',
              'CSV 提供 min/median/max；measurements.csv 保留非预热的十计数器；summary.json 保留输入、构建身份、来源和排除清单。', '',
              f'排除 {len(result["excluded"])} 个 dry-run 或低于 --min-repeat 的目录（排除不代表通过）：']
    lines += [f'- `{x["directory"]}`：{x["reason"]}' for x in result['excluded']]
    (out / 'comparison.md').write_text('\n'.join(lines) + '\n')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--results', type=Path, required=True, help='One campaign directory containing all three configurations')
    p.add_argument('--out', type=Path, required=True, help='New output directory, preferably outside results')
    p.add_argument('--min-repeat', type=int, default=20, help='Exclude smoke runs; default 20')
    args = p.parse_args()
    try:
        require(args.min_repeat >= 1, '--min-repeat must be positive')
        require(args.results.is_dir(), '--results must exist')
        require(not args.out.exists(), '--out must not exist')
        result = collect(args.results, args.min_repeat)
        write_report(result, args.out)
    except (ValueError, KeyError, OSError, TypeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    print(f'Validated {len(result["rows"])} configuration/workload groups: {args.out / "comparison.md"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
