#!/usr/bin/env python3
"""Check constrained board STA; CDC/board execution remain separate reviews."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def inspect(obj):
    assert (obj / 'audit/postroute_checkpoint.sha256').read_text().strip() == sha(obj / 'post_route.dcp'), 'Checkpoint differs from reported routed design'
    props = dict(line.split('=', 1) for line in (obj / 'audit/run_properties.txt').read_text().splitlines())
    assert props['part'] == 'xcvu37p_CIV-fsvh2892-2L-e', props
    assert abs(float(props['dut_period']) - 1000 / 70) < .001, props
    assert all(math.isfinite(float(props[k])) and float(props[k]) >= 0 for k in ('dut_setup_wns', 'dut_hold_whs')), props
    timing = (obj / 'report/timing.txt').read_text()
    match = re.search(r'WNS\(ns\).*?\n\s*-+[^\n]*\n\s*([^\n]+)', timing, re.S)
    assert match, 'Missing timing summary'
    values = list(map(float, match[1].split()))
    assert len(values) == 12 and all(math.isfinite(x) for x in values), values
    assert all(values[i] >= 0 for i in (0, 4, 8)), values
    assert all(values[i] == 0 for i in (1, 2, 5, 6, 9, 10)), values
    assert all(values[i] > 0 for i in (3, 7, 11)), 'No endpoints actually timed'
    route = (obj / 'report/route_status.txt').read_text()
    counts = []
    for key in ('# of routable nets', '# of fully routed nets', '# of nets with routing errors'):
        match = re.search(re.escape(key) + r'\.+\s*:\s*(\d+)', route)
        assert match, 'Missing route count: ' + key
        counts.append(int(match[1]))
    assert counts[0] > 0 and counts[0] == counts[1] and counts[2] == 0, counts
    bus = (obj / 'audit/bus_skew.txt').read_text()
    slacks = [float(v) for v in re.findall(r'Slack\s*(?:\((?:MET|VIOLATED)\))?\s*:\s*([-\d.]+)ns', bus)]
    assert slacks and min(slacks) >= 0, 'Missing/failing bus skew'
    drc = (obj / 'report/drc.txt').read_text()
    assert 'DRC' in drc and not re.search(r'\|\s*(?:Error|Critical Warning)\s*\|', drc), 'DRC error/critical'
    for name in ('cdc.txt', 'check_timing.txt', 'clock_interaction.txt', 'exceptions.txt'):
        assert (obj / 'audit' / name).stat().st_size > 0, name
    checks = (obj / 'audit/check_timing.txt').read_text()
    counts_by_name = {}
    for key, value in re.findall(r'checking (\w+) \((\d+)\)', checks):
        assert key not in counts_by_name or counts_by_name[key] == int(value), 'Inconsistent constraint count'
        counts_by_name[key] = int(value)
    for key in ('no_clock', 'constant_clock', 'pulse_width_clock', 'no_output_delay',
                'multiple_clock', 'generated_clocks', 'loops', 'partial_input_delay',
                'partial_output_delay', 'latch_loops'):
        assert counts_by_name.get(key) == 0, 'Missing/nonzero constraint check: ' + key
    assert counts_by_name.get('unconstrained_internal_endpoints') == 30, counts_by_name
    assert counts_by_name.get('no_input_delay') == 1, counts_by_name
    # These counts alone do not identify accepted endpoints. The separate review
    # must verify the actual unused HBM pins and asynchronous prst_n input.
    return dict(constrained_sta_passed=True, board_tested=False, cdc_review_required=True,
                constraint_review_required=True, constraint_counts=counts_by_name,
                dut=props, timing_summary=values, route=counts, bus_skew_worst_ns=min(slacks),
                checkpoint_sha256=sha(obj / 'post_route.dcp'),
                report_hashes={str(p.relative_to(obj)): sha(p) for folder in ('report', 'audit')
                               for p in sorted((obj / folder).rglob('*')) if p.is_file()})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--obj', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = inspect(args.obj)
    with args.out.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print('PASS: constrained 70 MHz board STA, route and DRC; separate CDC/board review required')
