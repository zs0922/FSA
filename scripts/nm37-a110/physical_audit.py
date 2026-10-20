"""Fail-closed board STA, HBM startup and routed reset evidence validation."""
import json
import hashlib
import math
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

HELPER = Path(__file__).resolve().parent


def properties(path):
    rows = [line.split('=', 1) for line in path.read_text().splitlines()]
    assert all(len(row) == 2 for row in rows), path
    assert len({row[0] for row in rows}) == len(rows), f'Duplicate properties: {path}'
    return dict(rows)


def nonnegative(value):
    value = float(value)
    return math.isfinite(value) and value >= 0


def xci_parameters(path):
    return {next((v for k, v in element.attrib.items() if k.endswith('referenceId')), ''): element.text
            for element in ET.parse(path).iter() if element.tag.endswith('configurableElementValue')}


def reset_evidence(directory):
    recheck = subprocess.run(['tclsh', str(HELPER / 'check_reset_evidence.tcl'), str(directory)],
                             text=True, capture_output=True)
    assert recheck.returncode == 0, 'Reset snapshot recheck failed: ' + recheck.stderr
    result, finals = {}, []
    for line in recheck.stdout.splitlines():
        key, value = line.split('\t', 1)
        if key == 'final':
            finals.append(value)
        else:
            assert key not in result, 'Duplicate reset summary key'
            result[key] = int(value)
    assert set(result) == {'before_count', 'replicated_count', 'routed_count'}
    assert len(finals) == result['routed_count'] and len(set(finals)) == len(finals)
    assert result['replicated_count'] > result['before_count'] > 0
    expected = {(name + '/' + pin, kind) for name in finals for pin in ('D', 'CLR') for kind in ('max', 'min')}
    observed = {}
    lines = (directory / 'postroute_timing/endpoint_coverage.txt').read_text().splitlines()
    assert lines[-1] == 'failures=', 'Reset endpoint failures recorded'
    for line in lines[:-1]:
        match = re.fullmatch(r'(\S+) (max|min) slack=(\S+)', line)
        assert match, 'Unrecognized or uncovered reset endpoint: ' + line
        pin, kind, slack = match.groups()
        assert (pin, kind) not in observed, 'Duplicate reset endpoint check'
        assert nonnegative(slack), line
        observed[pin, kind] = float(slack)
    assert set(observed) == expected, 'Missing/extra routed reset endpoint checks'
    for name in ('replication_equivalence.txt', 'postroute_equivalence.txt'):
        content = (directory / name).read_text()
        assert 'FAIL' not in content and 'PASS: identical FDCE signatures' in content
    result['covered_endpoint_checks'] = len(observed)
    result['worst_endpoint_slack_ns'] = min(observed.values())
    return result


def cdc_rows(content):
    rows = []
    for line in content.splitlines():
        if re.match(r'\s*\d+\s+CDC-\d+\s+(?:Critical|Warning|Info)', line):
            columns = re.split(r'\s{2,}', line.strip())
            assert len(columns) in (7, 8), 'Unparsed CDC detail: ' + line
            # Related clocks can have a blank exception column.
            if len(columns) == 7:
                columns.insert(5, '')
            rows.append(columns[1:])
    assert rows, 'Missing detailed CDC rows'
    return rows


def inspect(obj, result):
    props = properties(obj / 'audit/run_properties.txt')
    result['dut'] = props
    assert props['part'] == 'xcvu37p_CIV-fsvh2892-2L-e'
    assert abs(float(props['dut_period']) - 1000.0 / 110.0) < 0.001
    assert nonnegative(props['dut_setup_wns']) and nonnegative(props['dut_hold_whs'])
    timing = (obj / 'report/timing.txt').read_text()
    match = re.search(r'WNS\(ns\).*?\n\s*-+[^\n]*\n\s*([^\n]+)', timing, re.S)
    assert match, 'Missing timing summary'
    values = list(map(float, match[1].split()))
    result['timing_summary'] = values
    assert len(values) == 12 and all(math.isfinite(v) for v in values)
    assert all(values[i] >= 0 for i in (0, 4, 8)), 'Setup/hold/pulse-width failure'
    assert all(values[i] == 0 for i in (1, 2, 5, 6, 9, 10)), 'Failing global timing endpoints'
    route = (obj / 'report/route_status.txt').read_text()
    counts = [int(re.search(re.escape(k) + r'\.+\s*:\s*(\d+)', route)[1]) for k in
              ['# of routable nets', '# of fully routed nets', '# of nets with routing errors']]
    result['route'] = counts
    assert counts[0] == counts[1] > 0 and counts[2] == 0, counts
    bus = (obj / 'audit/bus_skew.txt').read_text()
    slacks = [float(v) for v in re.findall(r'Slack\s*(?:\((?:MET|VIOLATED)\))?\s*:\s*([-\d.]+)ns', bus)]
    assert slacks and all(nonnegative(v) for v in slacks), 'Missing/failing bus skew'
    result['bus_skew_worst_ns'] = min(slacks)
    drc = (obj / 'report/drc.txt').read_text()
    assert not re.search(r'\|\s*(?:Error|Critical Warning)\s*\|', drc), 'DRC error/critical'
    assert (obj / 'audit/audit_status.txt').read_text().strip() == 'failures=', 'Tcl structural audit failed'
    result['reset_replication'] = reset_evidence(obj / 'reset_replication_audit')
    hbm = properties(obj / 'hbm_audit/properties.txt')
    result['hbm'] = hbm
    assert hbm['status'] == 'PASS' and hbm['synchronizer_stages'] == '9'
    assert hbm['completion_crossing_timed'] == '1'
    for key, value in [('apb_period', 1000.0 / 55.0), ('axi_period', 1000.0 / 110.0), ('ref_period', 10.0)]:
        assert abs(float(hbm[key]) - value) < 0.001, (key, hbm[key])
    assert float(hbm['apb_high']) >= 5 and float(hbm['apb_low']) >= 5
    xcis = sorted(obj.parent.rglob('testxilinx_hbm.xci'))
    assert xcis, 'Missing generated HBM XCI'
    result['hbm_ip_configuration'] = []
    for path in xcis:
        entries = xci_parameters(path)
        frequency = entries['PARAM_VALUE.USER_APB_PCLK_0']
        period = entries['PARAM_VALUE.USER_APB_PCLK_PERIOD_0']
        assert float(frequency) == 55 and abs(float(period) - 1000.0 / 55.0) < 0.001, 'HBM configured APB clock differs from implemented clock'
        result['hbm_ip_configuration'].append({'path': str(path), 'apb_mhz': float(frequency),
            'apb_period_ns': float(period), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    plls = sorted(obj.parent.rglob('harnessSysPLL.xci'))
    assert plls, 'Missing generated harness PLL XCI'
    result['pll_configuration'] = []
    for path in plls:
        entries = xci_parameters(path)
        expected_parameters = {
            'PARAM_VALUE.NUM_OUT_CLKS': 2,
            'PARAM_VALUE.PRIM_IN_FREQ': 100,
            'PARAM_VALUE.CLKOUT1_REQUESTED_OUT_FREQ': 110,
            'PARAM_VALUE.CLKOUT2_REQUESTED_OUT_FREQ': 55,
            'MODELPARAM_VALUE.C_CLKOUT0_ACTUAL_FREQ': 110,
            'MODELPARAM_VALUE.C_CLKOUT1_ACTUAL_FREQ': 55,
        }
        for key, value in expected_parameters.items():
            assert abs(float(entries[key]) - value) < 0.0001, f'PLL parameter {key}: {entries[key]} != {value}'
        result['pll_configuration'].append({'path': str(path),
            'parameters': {key: entries[key] for key in expected_parameters},
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    lines = (obj / 'hbm_audit/endpoint_timing.tsv').read_text().splitlines()
    expected = {(name, kind) for name in ['complete_apb_to_axi'] +
                [f'{chain}_stage{stage}' for chain in ('apb_reset', 'axi_reset', 'completion_sync')
                 for stage in ('01', '12')] +
                [f'{chain}_clr{stage}' for chain in ('apb_reset', 'axi_reset', 'completion_sync')
                 for stage in (0, 1, 2)] for kind in ('max', 'min')}
    checks = {}
    for line in lines[1:]:
        name, kind, slack, start, end = line.split('\t')
        assert (name, kind) not in checks and start and end and nonnegative(slack), line
        checks[name, kind] = float(slack)
    assert set(checks) == expected, 'HBM synchronizer timing coverage incomplete'
    result['hbm_worst_timed_slack_ns'] = min(checks.values())

    # Keep pre-existing warnings visible. No new critical endpoint is silently
    # accepted, and every warning in the new startup logic requires review.
    rows = cdc_rows((obj / 'audit/cdc.txt').read_text())
    baseline = json.loads((HELPER / 'cdc-baseline.json').read_text())
    known = {tuple(row) for row in baseline['critical_rows']}
    critical = [row for row in rows if row[1] == 'Critical']
    new_critical = [row for row in critical if tuple(row) not in known]
    new_warnings = [row for row in rows if row[1] == 'Warning' and
                    any(hbm['startup_cell'] + '/' in field for field in row[-2:])]
    result['cdc'] = {'critical_count': len(critical), 'new_critical': new_critical,
                     'startup_warnings': new_warnings, 'baseline_critical_count': len(known)}
    assert not new_critical and not new_warnings, 'New CDC findings require explicit review: ' + repr(new_critical + new_warnings)
    check = (obj / 'audit/check_timing.txt').read_text()
    counts = dict((key, int(value)) for key, value in re.findall(r'^\d+\. checking (\w+) \((\d+)\)', check, re.M))
    expected_counts = dict.fromkeys(['no_clock', 'constant_clock', 'pulse_width_clock',
        'unconstrained_internal_endpoints', 'no_input_delay', 'no_output_delay', 'multiple_clock',
        'generated_clocks', 'loops', 'partial_input_delay', 'partial_output_delay', 'latch_loops'], 0)
    expected_counts.update(unconstrained_internal_endpoints=30, no_input_delay=1)
    assert counts == expected_counts, 'Changed constraint coverage: ' + repr(counts)
    unused = re.findall(r'^dutDomain/ram_hbm/\S+$', check, re.M)
    expected_unused = {f'dutDomain/ram_hbm/inst/ONE_STACK.u_hbm_top/ONE_STACK_HBM.hbm_one_stack_intf/HBM_SNGLBLI_INTF_AXI<{i}>_INST/{pin}'
                       for i in range(1, 16) for pin in ('BREADY', 'RREADY')}
    assert set(unused) == expected_unused and len(unused) == 30
    assert re.search(r'^prst_n$', check, re.M), 'Unexpected missing input delay'
    result['check_timing'] = counts
    result.update(constrained_sta_passed=True, hbm_startup_physical_passed=True,
                  board_signoff=False, cdc_review_required=True)
