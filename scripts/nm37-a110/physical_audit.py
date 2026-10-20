"""Fail-closed board STA, HBM startup and routed reset evidence validation."""
import json
import hashlib
import math
from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET

HELPER = Path(__file__).resolve().parent


def within(path, directory):
    try:
        Path(path).resolve().relative_to(Path(directory).resolve())
        return True
    except ValueError:
        return False


@dataclass(frozen=True)
class AuditContext:
    """Original implementation inputs and independent re-audit outputs.

    ``checkpoint`` is the original routed DCP below ``ip_root/obj``. The
    original build/IP/DCP are read-only inputs. ``review_root`` is a disjoint
    existing output directory; ``obj`` passed to inspect is its new report
    tree. No implementation pass marker is created by this module.
    """
    build_root: Path
    checkpoint: Path
    ip_root: Path
    review_root: Path

    def __post_init__(self):
        for name in ('build_root', 'checkpoint', 'ip_root', 'review_root'):
            object.__setattr__(self, name, Path(getattr(self, name)).resolve())

    def validate(self, obj):
        obj = Path(obj).resolve()
        assert self.build_root.is_dir() and (self.build_root / 'build.json').is_file(), 'Missing original build manifest'
        assert self.ip_root.is_dir() and within(self.ip_root, self.build_root), 'Original IP root must be within original build'
        assert self.checkpoint.is_file() and self.checkpoint == (self.ip_root / 'obj/post_route.dcp').resolve(), 'Expected original post_route.dcp from original IP root'
        assert within(self.checkpoint, self.build_root), 'Checkpoint escapes original build'
        assert self.review_root.is_dir(), 'Re-audit output directory must already exist'
        assert not within(self.review_root, self.build_root) and not within(self.build_root, self.review_root), 'Re-audit output and original build must be disjoint'
        assert obj.is_dir() and within(obj, self.review_root), 'New report root must be within re-audit output'
        # Reject linked report trees, including a new-looking CDC path that
        # actually resolves to the old implementation report.
        assert not any(path.is_symlink() for path in obj.rglob('*')), 'Re-audit reports must not contain symlinks'
        assert within(obj / 'audit/cdc.txt', self.review_root), 'New CDC report escapes re-audit output'
        return self

    def review_path(self, name):
        path = self.review_root / name
        assert within(path, self.review_root), 'CDC review output escapes re-audit directory'
        assert not within(path, self.build_root), 'CDC review must not write original build'
        return path

    def describe(self, obj):
        return {'mode': 'external_reaudit', 'build_root': str(self.build_root),
                'checkpoint': str(self.checkpoint), 'ip_root': str(self.ip_root),
                'report_root': str(Path(obj).resolve()), 'review_root': str(self.review_root)}


def write_review_template(path, template):
    """Replace the directory entry, never overwrite the target of a hard link."""
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.cdc-review-',
                                     suffix='.tmp', delete=False) as stream:
        stream.write(json.dumps(template, indent=2) + '\n')
        temporary = Path(stream.name)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


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


def file_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    source_clock = destination_clock = None
    for line in content.splitlines():
        if line.startswith('Source Clock: '):
            source_clock = line.partition(': ')[2]
        elif line.startswith('Destination Clock: '):
            destination_clock = line.partition(': ')[2]
        if re.match(r'\s*\d+\s+CDC-\d+\s+(?:Critical|Warning|Info)', line):
            columns = re.split(r'\s{2,}', line.strip())
            assert len(columns) in (7, 8), 'Unparsed CDC detail: ' + line
            # Related clocks can have a blank exception column.
            if len(columns) == 7:
                columns.insert(5, '')
            assert source_clock is not None and destination_clock is not None, 'CDC detail without clock pair'
            rows.append(columns[1:] + [source_clock, destination_clock])
    assert rows, 'Missing detailed CDC rows'
    return rows


def review_cdc(obj, rows, result=None, context=None):
    """Every new Warning/Critical needs explicit, report-bound review.

    The reviewer creates OUT/cdc-review.json after examining actual routed
    evidence. This data cannot change implementation constraints or waive a
    failed timing/structural check; all such checks precede this function.
    """
    obj = Path(obj)
    if context is not None:
        context.validate(obj)
    baseline = json.loads((HELPER / 'cdc-baseline.json').read_text())
    known = {tuple(row) for row in baseline['findings']}
    findings = [row for row in rows if row[1] in ('Critical', 'Warning')]
    new_findings = sorted(set(tuple(row) for row in findings) - known)
    if result is None:
        result = {}
    result.update({'critical_count': sum(row[1] == 'Critical' for row in findings),
              'warning_count': sum(row[1] == 'Warning' for row in findings),
              'baseline_critical_count': sum(row[1] == 'Critical' for row in known),
              'baseline_warning_count': sum(row[1] == 'Warning' for row in known),
              'new_findings': [list(row) for row in new_findings], 'review_required': bool(new_findings)})
    if not new_findings:
        return result
    out = context.review_root if context is not None else obj.parents[3]
    review_path = context.review_path('cdc-review.json') if context is not None else out / 'cdc-review.json'
    required_path = context.review_path('cdc-review.required.json') if context is not None else out / 'cdc-review.required.json'
    bindings = {'build_sha256': (context.build_root if context is not None else out) / 'build.json',
                'checkpoint_sha256': context.checkpoint if context is not None else obj / 'post_route.dcp',
                'cdc_report_sha256': obj / 'audit/cdc.txt'}
    template = {'schema': 1, 'reviewer': '',
                **{key: file_sha(path) for key, path in bindings.items()},
                'reviewed_findings': [{'finding': list(row), 'disposition': 'pending',
                    'classification': '', 'rationale': '', 'evidence': []} for row in new_findings]}
    if context is not None:
        template['audit_context'] = context.describe(obj)
    write_review_template(required_path, template)
    assert review_path.is_file(), 'New CDC Warning/Critical findings require ' + str(review_path) + '; see cdc-review.required.json'
    review = json.loads(review_path.read_text())
    assert review['schema'] == 1 and review['reviewer'].strip(), 'Invalid CDC review identity'
    for key, path in bindings.items():
        assert review[key] == file_sha(path), 'CDC review is stale: ' + key
    covered = set()
    for item in review['reviewed_findings']:
        finding = tuple(item['finding'])
        assert finding not in covered and finding in new_findings, 'Duplicate/foreign CDC review finding'
        assert item['disposition'] == 'accepted' and item['rationale'].strip(), 'CDC finding was not accepted with a reason'
        allowed = {'tool_recognition_limit', 'equivalent_existing_structure'}
        if finding[1] == 'Warning':
            allowed.add('timed_related_clock_crossing')
        assert item['classification'] in allowed, 'A real CDC defect cannot be waived by this review'
        assert item['evidence'], 'CDC acceptance must reference actual evidence'
        for evidence in item['evidence']:
            path = (out / evidence['path']).resolve()
            path.relative_to(out.resolve())
            assert path.is_file() and file_sha(path) == evidence['sha256'], 'Changed/missing CDC review evidence: ' + str(path)
        covered.add(finding)
    assert covered == set(new_findings), 'Incomplete CDC review'
    result.update(review_required=False, review_sha256=file_sha(review_path),
                  reviewer=review['reviewer'], reviewed_findings=review['reviewed_findings'])
    return result


def inspect(obj, result, context=None):
    obj = Path(obj)
    if context is not None:
        context.validate(obj)
        result['audit_context'] = context.describe(obj)
    ip_root = context.ip_root if context is not None else obj.parent
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
    assert hbm['pcie_reset_monitor_clock'] == hbm['ref_clock']
    assert hbm['pcie_reset_synchronizer_stages'] == '3' and hbm['pcie_reset_counter_bits'] == '14'
    assert hbm['pcie_reset_feedback_free'] == '1'
    for key, value in [('apb_period', 1000.0 / 55.0), ('axi_period', 1000.0 / 110.0),
                       ('ref_period', 10.0), ('ref_hard_period', 10.0)]:
        assert abs(float(hbm[key]) - value) < 0.001, (key, hbm[key])
    assert float(hbm['apb_high']) >= 5 and float(hbm['apb_low']) >= 5
    xcis = sorted(ip_root.rglob('testxilinx_hbm.xci'))
    assert xcis, 'Missing generated HBM XCI'
    result['hbm_ip_configuration'] = []
    for path in xcis:
        entries = xci_parameters(path)
        frequency = entries['PARAM_VALUE.USER_APB_PCLK_0']
        period = entries['PARAM_VALUE.USER_APB_PCLK_PERIOD_0']
        assert float(frequency) == 55 and abs(float(period) - 1000.0 / 55.0) < 0.001, 'HBM configured APB clock differs from implemented clock'
        result['hbm_ip_configuration'].append({'path': str(path), 'apb_mhz': float(frequency),
            'apb_period_ns': float(period), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    plls = sorted(ip_root.rglob('harnessSysPLL.xci'))
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
        jitter_ps = {key: float(entries['PARAM_VALUE.' + key])
                     for key in ('CLKOUT1_JITTER', 'CLKOUT2_JITTER')}
        assert all(math.isfinite(value) and 0 <= value <= 230 for value in jitter_ps.values()), 'PLL output exceeds declared 230 ps peak-to-peak budget'
        jitter_input = {key: entries['PARAM_VALUE.' + key] for key in
                        ('JITTER_OPTIONS', 'CLKIN1_UI_JITTER', 'CLKIN1_JITTER_PS', 'JITTER_SEL')}
        result['pll_configuration'].append({'path': str(path),
            'parameters': {key: entries[key] for key in expected_parameters},
            'output_jitter_peak_to_peak_ps': jitter_ps, 'input_jitter_configuration': jitter_input,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    lines = (obj / 'hbm_audit/endpoint_timing.tsv').read_text().splitlines()
    expected = {(name, kind) for name in ['complete_apb_to_axi'] +
                [f'{chain}_stage{stage}' for chain in ('apb_reset', 'axi_reset', 'completion_sync')
                 for stage in ('01', '12')] +
                [f'{chain}_clr{stage}' for chain in ('apb_reset', 'axi_reset', 'completion_sync')
                 for stage in (0, 1, 2)] +
                ['pcie_reset_stage01', 'pcie_reset_stage12', 'pcie_released_clr'] +
                [f'pcie_counter_clr{bit}' for bit in range(14)] for kind in ('max', 'min')}
    checks = {}
    for line in lines[1:]:
        name, kind, slack, start, end = line.split('\t')
        assert (name, kind) not in checks and start and end and nonnegative(slack), line
        checks[name, kind] = float(slack)
    assert set(checks) == expected, 'HBM synchronizer timing coverage incomplete'
    result['hbm_worst_timed_slack_ns'] = min(checks.values())

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
    # Review every new Warning/Critical, including changed reset domains outside
    # startup. Preserve the difference even when missing/stale review fails.
    rows = cdc_rows((obj / 'audit/cdc.txt').read_text())
    result['cdc'] = {}
    review_cdc(obj, rows, result['cdc'], context=context)
    result.update(constrained_sta_passed=True, hbm_startup_physical_passed=True,
                  board_signoff=False, cdc_review_required=True)
