#!/usr/bin/env python3
"""Read original NM37 physical reports; never invoke Vivado or alter a build.

Every report datum carries a source path, on-disk SHA256, and line/JSON pointer.
Compressed archives additionally carry the decoded SHA256. Missing evidence is
not a zero or a pass. Supplementary reports require a checkpoint-bound manifest:
{"checkpoint_sha256": "...", "reports": {"/absolute/report.rpt": "sha256"}}.
Only --out is written; without --allow-incomplete missing candidate evidence
returns exit 2 after writing a partial summary for diagnosis.
"""
import argparse
import datetime
import gzip
import hashlib
import json
import math
import re
from pathlib import Path
import xml.etree.ElementTree as ET

BASE = Path('/home/zhangsi/tmp/FSA-m1/nm37-m1-pipeline/apb110-20261020')
ARCHIVE = Path('/home/zhangsi/tmp/FSA-m1/optimization-reports/evidence')
NUM = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)'
SUMMARY_NAMES = ('wns_ns', 'tns_ns', 'setup_failing_endpoints', 'setup_total_endpoints',
                 'whs_ns', 'ths_ns', 'hold_failing_endpoints', 'hold_total_endpoints',
                 'wpws_ns', 'tpws_ns', 'pulse_failing_endpoints', 'pulse_total_endpoints')
BOARD = 'A110/fpga/chipyard.fpga.nm37.NM37FPGATestHarness.EmptyNM37Config'


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def missing(reason, **extra):
    return dict(status='missing', reason=reason, **extra)


class Report:
    def __init__(self, path):
        self.path = Path(path).resolve()
        raw = self.path.read_bytes()
        decoded = gzip.decompress(raw) if self.path.suffix == '.gz' else raw
        self.text = decoded.decode('utf-8')
        self.lines = self.text.splitlines()
        self.source = {'path': str(self.path), 'sha256': hashlib.sha256(raw).hexdigest()}
        if self.path.suffix == '.gz':
            self.source.update(compression='gzip', decoded_sha256=hashlib.sha256(decoded).hexdigest())

    def ref(self, line=None, pointer=None):
        result = dict(self.source)
        if line is not None:
            result.update(line=line, raw_line=self.lines[line - 1])
        if pointer is not None:
            result['json_pointer'] = pointer
        return result

    def at(self, offset):
        return self.text.count('\n', 0, offset) + 1


def choose(*paths):
    for path in paths:
        if path is None:
            continue
        path = Path(path)
        for candidate in (path, Path(str(path) + '.gz')):
            if candidate.is_file():
                return Report(candidate)
    return None


def value(number, report, line=None, pointer=None, **extra):
    return dict(value=number, source=report.ref(line, pointer), **extra)


def field(report, key):
    match = re.search(r'^\|\s*' + re.escape(key) + r'\s*:\s*(.+)$', report.text, re.M)
    return value(match[1].strip(), report, report.at(match.start())) if match else missing(key)


def guarded(callback, *args):
    try:
        return callback(*args)
    except (ValueError, KeyError, AssertionError, IndexError, TypeError, OSError) as exc:
        return dict(status='parse_error', reason=str(exc))


def global_summary(report):
    if report is None:
        return missing('No global timing report')
    match = re.search(r'^\s*WNS\(ns\)[^\n]*\n\s*-+[^\n]*\n\s*([^\n]+)', report.text, re.M)
    assert match, 'Global summary header absent'
    row = match[1].split()
    assert len(row) == 12, f'Expected 12 summary fields, found {len(row)}'
    data = [float(item) for item in row]
    assert all(math.isfinite(item) for item in data), 'Nonfinite timing summary'
    result = dict(status='available')
    for i, name in enumerate(SUMMARY_NAMES):
        if name.endswith('endpoints'):
            assert data[i] == int(data[i]), 'Nonintegral endpoint count'
            data[i] = int(data[i])
        result[name] = value(data[i], report, report.at(match.start(1)))
    result['constraints_met'] = {
        'value': all(data[i] >= 0 for i in (0, 4, 8)) and all(data[i] == 0 for i in (1, 2, 5, 6, 9, 10)),
        'source': report.ref(report.at(match.start(1))),
        'method': 'All setup/hold/pulse worst slacks nonnegative; negative slack totals and failing counts zero'}
    return result


def properties(report):
    if report is None:
        return missing('No properties report')
    result = {'status': 'available'}
    for i, line in enumerate(report.lines, 1):
        if '=' in line:
            key, text = line.split('=', 1)
            name = 'reported_status' if key.strip() == 'status' else key.strip()
            result[name] = value(text.strip(), report, i)
    return result


def critical_paths(report, limit=3):
    if report is None:
        return missing('No DUT path report')
    slack = list(re.finditer(r'^Slack\s*(?:\((?:MET|VIOLATED)\))?\s*:\s*(' + NUM + r')ns', report.text, re.M))
    assert slack, 'No actual timing paths'
    records = []
    for index, start in enumerate(slack[:limit]):
        stop = slack[index + 1].start() if index + 1 < len(slack) else len(report.text)
        block = report.text[start.start():stop]
        record = {'rank_in_report': index + 1, 'slack_ns': value(float(start[1]), report, report.at(start.start()))}
        for name, label, pattern, transform in (
                ('source', 'Source', r'([^\n]+)', str.strip),
                ('destination', 'Destination', r'([^\n]+)', str.strip),
                ('path_group', 'Path Group', r'([^\n]+)', str.strip),
                ('path_type', 'Path Type', r'([^\n]+)', str.strip),
                ('requirement_ns', 'Requirement', '(' + NUM + ')ns', float),
                ('logic_levels', 'Logic Levels', r'(\d+)', int)):
            match = re.search(r'^\s*' + label + r':\s*' + pattern, block, re.M)
            assert match, f'Missing {label} in path {index + 1}'
            record[name] = value(transform(match[1]), report, report.at(start.start() + match.start()))
        delay = re.search(r'Data Path Delay:\s*(' + NUM + r')ns\s*\(logic\s+(' + NUM +
                          r')ns\s*\((' + NUM + r')%\)\s*route\s+(' + NUM + r')ns\s*\((' + NUM + r')%\)\)', block)
        assert delay, f'Missing logic/route delay split in path {index + 1}'
        for name, token in zip(('data_delay_ns', 'logic_delay_ns', 'logic_percent', 'route_delay_ns', 'route_percent'), delay.groups()):
            record[name] = value(float(token), report, report.at(start.start() + delay.start()))
        assert abs(record['data_delay_ns']['value'] - record['logic_delay_ns']['value'] - record['route_delay_ns']['value']) < 0.0021
        records.append(record)
    assert len(records) == limit, f'Only {len(records)} DUT paths, expected {limit}'
    worst = min(float(item[1]) for item in slack)
    assert abs(records[0]['slack_ns']['value'] - worst) < 0.0001, 'Report not ordered by worst slack'
    return {'status': 'available', 'reported_paths': value(len(slack), report),
            'worst_slack_ns': value(worst, report, report.at(slack[0].start())), 'paths': records,
            'scope': 'First actual DUT-domain paths in Vivado report order; source preserves Vivado launch-clock pin notation'}


def apb_checks(reports):
    records = []
    # Use the explicit pulse-width report when it contains all primitive checks.
    for report in reports:
        if report is None:
            continue
        current = []
        for line_num, line in enumerate(report.lines, 1):
            if 'HBM_SNGLBLI_INTF_APB' not in line or '/PCLK' not in line:
                continue
            match = re.match(r'\s*(Min Period|Low Pulse Width|High Pulse Width)\s+(\S+)\s+(\S+)\s+(\S+)\s+(' + NUM + r')\s+(' + NUM + r')\s+(' + NUM + r')\s+(?:(\S+)\s+)?(\S+)\s*$', line)
            if not match:
                continue
            kind, corner, primitive, lib_pin, required, actual, slack, site, endpoint = match.groups()
            current.append({'kind': kind, 'corner': corner, 'primitive_pin': primitive,
                            'library_pin': lib_pin, 'required_ns': float(required), 'actual_ns': float(actual),
                            'slack_ns': float(slack), 'site': site, 'endpoint': endpoint, 'source': report.ref(line_num)})
        if current:
            records = current
            if {r['kind'] for r in current} == {'Min Period', 'Low Pulse Width', 'High Pulse Width'}:
                break
    required_kinds = {'Min Period', 'Low Pulse Width', 'High Pulse Width'}
    if not records:
        return missing('No original HBM APB PCLK primitive period/pulse-width check rows; waveform arithmetic is not a substitute')
    available = {r['kind'] for r in records}
    result = {'status': 'available' if available == required_kinds else 'missing', 'raw_checks': records,
              'missing_check_types': sorted(required_kinds - available),
              'minimum_slack_ns': min(r['slack_ns'] for r in records),
              'all_reported_checks_nonnegative': all(r['slack_ns'] >= 0 for r in records),
              'aggregation_sources': [r['source'] for r in records]}
    for row in records:
        assert abs(row['actual_ns'] - row['required_ns'] - row['slack_ns']) <= 0.0021, 'APB row arithmetic mismatch'
    return result


def flat_rows(report):
    if report is None:
        return {}
    result = {}
    for number, line in enumerate(report.lines, 1):
        columns = [part.strip() for part in line.split('|')[1:-1]]
        if len(columns) != 5 or not re.fullmatch(NUM, columns[1]) or not re.fullmatch(NUM, columns[3]):
            continue
        result.setdefault(columns[0].rstrip('*'), dict(used=float(columns[1]), available=float(columns[3]),
                          reported_percent=float(columns[4]) if columns[4] else None, source=report.ref(number)))
    return result


def resources(hierarchy, capacity, own_flat=None):
    if hierarchy is None:
        return missing('No original hierarchy utilization report')
    header = None
    top = None
    for number, line in enumerate(hierarchy.lines, 1):
        cols = [item.strip() for item in line.split('|')[1:-1]]
        if cols and cols[0] == 'Instance' and 'Total LUTs' in cols:
            header = cols
        elif header and len(cols) == len(header) and cols[1] == '(top)' and not cols[0].startswith('('):
            top = dict(zip(header, cols))
            break
    assert top, 'Missing complete top-level hierarchy resource row'
    capacity = own_flat or capacity
    flat = flat_rows(capacity)
    own = flat_rows(own_flat)
    device = field(hierarchy, 'Device')
    capacity_device = field(capacity, 'Device') if capacity else missing('No capacity report')
    if capacity:
        norm = lambda text: re.sub(r'[^a-zA-Z0-9]', '', text).lower()
        assert norm(device['value']) == norm(capacity_device['value']), 'Capacity report device differs from candidate'
    mapping = {'Total LUTs': 'CLB LUTs', 'Logic LUTs': 'LUT as Logic',
               'LUTRAMs': 'LUT as Memory', 'SRLs': 'LUT as Memory',
               'FFs': 'CLB Registers', 'RAMB36': 'RAMB36/FIFO', 'RAMB18': 'RAMB18',
               'URAM': 'URAM', 'DSP Blocks': 'DSPs'}
    result = {'status': 'available', 'device': device, 'capacity_device': capacity_device, 'resources': {}}
    for key, cap_key in mapping.items():
        count = int(top[key])
        item = {'used': value(count, hierarchy, number)}
        if cap_key in flat:
            denominator = flat[cap_key]['available']
            item['available'] = {'value': denominator, 'source': flat[cap_key]['source'], 'capacity_label': cap_key}
            item['percent_of_device_capacity'] = {
                'value': count / denominator * 100, 'method': '100 * used / available',
                'sources': [item['used']['source'], item['available']['source']]}
            if key in ('LUTRAMs', 'SRLs'):
                item['capacity_note'] = 'Shared memory-capable LUT pool; LUTRAM and SRL have no separate capacity in this flat report'
        else:
            item['available'] = missing('No original Available capacity row for ' + cap_key)
            item['percent_of_device_capacity'] = missing('Capacity unavailable')
            result['status'] = 'missing'
            result['reason'] = 'At least one required device capacity row is missing'
        if cap_key in own and key not in ('LUTRAMs', 'SRLs'):
            assert count == own[cap_key]['used'], 'Flat/hierarchy utilization mismatch: ' + key
            item['reported_percent'] = {'value': own[cap_key]['reported_percent'], 'source': own[cap_key]['source']}
        result['resources'][key] = item
    assert int(top['Total LUTs']) == sum(int(top[k]) for k in ('Logic LUTs', 'LUTRAMs', 'SRLs')), 'LUT components do not sum'
    return result


def route(report):
    if report is None:
        return missing('No route status report')
    result = {'status': 'available', 'counts': {}}
    for number, line in enumerate(report.lines, 1):
        match = re.search(r'# of ([^.]+)\.+\s*:\s*(\d+)', line)
        if match:
            result['counts'][match[1].strip()] = value(int(match[2]), report, number)
    for required in ('routable nets', 'fully routed nets', 'nets with routing errors'):
        assert required in result['counts'], 'Missing route count: ' + required
    return result


def drc(report):
    if report is None:
        return missing('No DRC report')
    total = re.search(r'Violations found:\s*(\d+)', report.text)
    assert total, 'Missing DRC total'
    details = re.search(r'\n2\. REPORT DETAILS\n-+', report.text)
    body = report.text[:details.start()] if details else report.text
    rows = []
    for match in re.finditer(r'^\|\s*(\S+)\s*\|\s*(Error|Critical Warning|Warning|Advisory|Info)\s*\|\s*([^|]+)\|\s*(\d+)\s*\|', body, re.M):
        rows.append(dict(rule=match[1], severity=match[2], description=match[3].strip(),
                         count=int(match[4]), source=report.ref(report.at(match.start()))))
    assert sum(row['count'] for row in rows) == int(total[1]), 'DRC rule counts do not sum to original total'
    counts = {kind: sum(row['count'] for row in rows if row['severity'] == kind)
              for kind in ('Error', 'Critical Warning', 'Warning', 'Advisory', 'Info')}
    return {'status': 'available', 'total': value(int(total[1]), report, report.at(total.start())),
            'severity_counts': {key: value(count, report, method='Sum complete DRC summary table') for key, count in counts.items()},
            'rules': rows}


def cdc(report):
    if report is None:
        return missing('No CDC report')
    # Summary IDs begin at column zero; detailed rows start with a row number.
    rows = []
    for match in re.finditer(r'^(CDC-\d+)\s+(Info|Warning|Critical)\s+(\d+)\s+([^\n]+)', report.text, re.M):
        rows.append(dict(rule=match[1], severity=match[2], count=int(match[3]), description=match[4].strip(),
                         source=report.ref(report.at(match.start()))))
    assert rows, 'No original CDC summary rows'
    return {'status': 'available', 'rules': rows,
            'total': value(sum(row['count'] for row in rows), report, method='Sum CDC summary Count; not a CDC signoff'),
            'severity_counts': {kind: value(sum(row['count'] for row in rows if row['severity'] == kind), report,
                                           method='Sum CDC summary Count') for kind in ('Critical', 'Warning', 'Info')}}


def bus_skew(report):
    if report is None:
        return missing('No bus skew report')
    matches = list(re.finditer(r'Slack\s*(?:\((?:MET|VIOLATED)\))?\s*:\s*(' + NUM + ')ns', report.text))
    assert matches, 'No actual bus skew path slack rows'
    worst = min(matches, key=lambda item: float(item[1]))
    return {'status': 'available', 'reported_path_checks': value(len(matches), report),
            'violated_reported_path_checks': value(sum(float(item[1]) < 0 for item in matches), report),
            'worst_slack_ns': value(float(worst[1]), report, report.at(worst.start())),
            'scope': 'Count is printed path checks, not logical bus bits or number of constraints'}


def json_record(report, keys=None):
    if report is None:
        return missing('No JSON evidence')
    obj = json.loads(report.text)
    return {'status': 'available', **{('reported_status' if key == 'status' else key): value(obj[key], report, pointer='/' + key)
                                    for key in (keys or obj.keys()) if key in obj}}


def ip_configuration(ip_root):
    """Read original generated XCI, never an IP file copied into re-audit output."""
    names = {'testxilinx_hbm.xci': ('PARAM_VALUE.USER_APB_PCLK_0', 'PARAM_VALUE.USER_APB_PCLK_PERIOD_0'),
             'harnessSysPLL.xci': ('PARAM_VALUE.NUM_OUT_CLKS', 'PARAM_VALUE.PRIM_IN_FREQ',
                 'PARAM_VALUE.CLKOUT1_REQUESTED_OUT_FREQ', 'PARAM_VALUE.CLKOUT2_REQUESTED_OUT_FREQ',
                 'MODELPARAM_VALUE.C_CLKOUT0_ACTUAL_FREQ', 'MODELPARAM_VALUE.C_CLKOUT1_ACTUAL_FREQ',
                 'PARAM_VALUE.CLKOUT1_JITTER', 'PARAM_VALUE.CLKOUT2_JITTER')}
    records = []
    missing_names = []
    for name, keys in names.items():
        paths = sorted(ip_root.rglob(name))
        if not paths:
            missing_names.append(name)
        for path in paths:
            report = Report(path)
            values = {}
            for element in ET.fromstring(report.text).iter():
                if not element.tag.endswith('configurableElementValue'):
                    continue
                reference = next((v for k, v in element.attrib.items() if k.endswith('referenceId')), '')
                if reference in keys:
                    assert reference not in values, 'Duplicate XCI parameter: ' + reference
                    match = re.search(r'referenceId=[\"\']' + re.escape(reference) + r'[\"\']', report.text)
                    values[reference] = value(element.text, report, report.at(match.start()) if match else None,
                                               xml_reference_id=reference)
            assert set(values) == set(keys), 'Missing original IP parameters: ' + str(path)
            records.append({'name': name, 'source': report.ref(), 'parameters': values})
    return {'status': 'missing' if missing_names else 'available', 'original_ip_root': str(ip_root.resolve()),
            'missing_files': missing_names, 'files': records}


def extract(label, obj, physical, capacity, build=None, flat=None, pulse=None):
    timing = choose(obj / 'report/timing.txt')
    setup = guarded(critical_paths, choose(obj / 'audit/dut_setup_top100.txt'))
    hold = guarded(critical_paths, choose(obj / 'audit/dut_hold_top100.txt'))
    props = properties(choose(obj / 'audit/run_properties.txt'))
    checks = []
    for key, paths in (('dut_setup_wns', setup), ('dut_hold_whs', hold)):
        if key in props and paths.get('status') == 'available':
            checks.append({'check': key + ' matches worst actual path',
                           'passed': abs(float(props[key]['value']) - paths['worst_slack_ns']['value']) < 0.0001,
                           'sources': [props[key]['source'], paths['worst_slack_ns']['source']]})
    return {'label': label, 'original_obj_or_archive': str(obj.resolve()),
            'build': guarded(json_record, choose(build) if build else None, ['root_commit', 'configuration', 'lock_sha256', 'generator_sha256']),
            'physical_decision': guarded(json_record, choose(physical) if physical else None,
                ['constrained_sta_passed', 'board_signoff', 'whole_board_passed', 'status', 'error', 'failure',
                 'operation', 'hardware_reimplemented', 'inputs_sha256', 'execution_sha256',
                 'hbm_startup_physical_passed', 'build_sha256', 'checkpoint_sha256', 'stage_seals']),
            'timing_summary': guarded(global_summary, timing), 'dut_properties': props,
            'dut_setup': setup, 'dut_hold': hold, 'internal_consistency_checks': checks,
            'apb_primitive': guarded(apb_checks, [choose(pulse) if pulse else None, timing]),
            'resources': guarded(resources, choose(obj / 'report/utilization.txt', obj / 'reset_replication_audit/utilization_hierarchical.rpt'),
                                 choose(capacity), choose(flat) if flat else None),
            'bus_skew': guarded(bus_skew, choose(obj / 'audit/bus_skew.txt')),
            'route': guarded(route, choose(obj / 'report/route_status.txt')),
            'drc': guarded(drc, choose(obj / 'report/drc.txt')),
            'cdc': guarded(cdc, choose(obj / 'audit/cdc.txt')),
            'hbm_properties': properties(choose(obj / 'hbm_audit/properties.txt'))}


def differences(candidate, baseline):
    output = {}
    pairs = [('timing_summary/' + key, ['timing_summary', key]) for key in SUMMARY_NAMES]
    pairs += [(name + '/worst_slack_ns', [name, 'worst_slack_ns']) for name in ('dut_setup', 'dut_hold', 'bus_skew')]
    pairs += [('resources/' + key, ['resources', 'resources', key, 'used'])
              for key in ('Total LUTs', 'Logic LUTs', 'LUTRAMs', 'SRLs', 'FFs', 'RAMB36', 'RAMB18', 'URAM', 'DSP Blocks')]
    pairs += [(name + '/' + key, [name, 'severity_counts', key])
              for name, keys in (('drc', ('Error', 'Critical Warning', 'Warning', 'Advisory', 'Info')),
                                  ('cdc', ('Critical', 'Warning', 'Info'))) for key in keys]
    pairs += [('route/' + key, ['route', 'counts', key])
              for key in ('routable nets', 'fully routed nets', 'nets with routing errors')]
    for name, path in pairs:
        a, b = candidate, baseline
        try:
            for part in path:
                a, b = a[part], b[part]
            output[name] = {'candidate': a['value'], 'baseline': b['value'],
                            'delta': a['value'] - b['value'], 'sources': [a['source'], b['source']]}
            if name.startswith('resources/'):
                output[name]['relative_change_percent'] = (100 * (a['value'] - b['value']) / b['value']) if b['value'] else None
                output[name]['relative_change_method'] = '100 * (candidate - baseline) / baseline; null if baseline is zero'
        except KeyError:
            output[name] = missing('Candidate or baseline original datum unavailable')
    for kind in ('Min Period', 'Low Pulse Width', 'High Pulse Width'):
        a = [r for r in candidate['apb_primitive'].get('raw_checks', []) if r['kind'] == kind]
        b = [r for r in baseline['apb_primitive'].get('raw_checks', []) if r['kind'] == kind]
        key = 'apb_primitive/' + kind
        if a and b:
            first = min(a, key=lambda r: r['slack_ns'])
            second = min(b, key=lambda r: r['slack_ns'])
            output[key] = {'candidate_worst_slack_ns': first['slack_ns'], 'baseline_worst_slack_ns': second['slack_ns'],
                           'delta_ns': first['slack_ns'] - second['slack_ns'], 'sources': [first['source'], second['source']]}
        else:
            output[key] = missing('Candidate or baseline original primitive check unavailable')
    return output


def verify_bindings(build, physical, obj, extra_paths, manifest):
    """A passed JSON filename alone is insufficient: rehash its recorded evidence."""
    if not physical or not Path(physical).is_file():
        return missing('No candidate physical decision JSON; build may still be running')
    report = Report(physical)
    data = json.loads(report.text)
    result = {'status': 'available', 'decision_source': report.ref(), 'checks': []}
    def check(name, actual, expected, source):
        result['checks'].append(dict(check=name, passed=actual == expected, actual=actual, expected=expected, source=source))
    build_report = choose(build / 'build.json')
    if 'build_sha256' in data and build_report:
        check('build.json SHA256', build_report.source['sha256'], data['build_sha256'], report.ref(pointer='/build_sha256'))
    else:
        result['checks'].append({'check': 'build.json SHA256', 'passed': False, 'reason': 'Missing original binding'})
    checkpoint = obj / 'post_route.dcp'
    checkpoint_sha = sha(checkpoint) if checkpoint.is_file() else None
    check('post_route.dcp SHA256', checkpoint_sha, data.get('checkpoint_sha256'), report.ref(pointer='/checkpoint_sha256'))
    if checkpoint_sha is None:
        result['checks'][-1]['passed'] = False
    evidence = data.get('evidence', {})
    stages = data.get('stage_seals', {})
    for group, records in (('evidence', evidence), ('stage_seals', stages)):
        mismatches = []
        for relative, expected in records.items():
            path = build / relative
            if not path.is_file() or sha(path) != expected:
                mismatches.append(relative)
        result['checks'].append({'check': group + ' recorded file hashes', 'passed': bool(records) and not mismatches,
                                 'checked_files': len(records), 'mismatches': mismatches, 'source': report.ref(pointer='/' + group)})
    if extra_paths:
        if manifest is None or not Path(manifest).is_file():
            result['checks'].append({'check': 'supplemental reports', 'passed': False, 'reason': 'Missing supplemental checkpoint manifest'})
        else:
            extra = Report(manifest)
            meta = json.loads(extra.text)
            check('supplemental report generation completed', meta.get('report_generation_completed'), True,
                  extra.ref(pointer='/report_generation_completed'))
            check('supplemental checkpoint SHA256', meta.get('checkpoint_sha256'), checkpoint_sha, extra.ref(pointer='/checkpoint_sha256'))
            check('supplemental build manifest SHA256', meta.get('build_manifest_sha256'),
                  build_report.source['sha256'] if build_report else None, extra.ref(pointer='/build_manifest_sha256'))
            for path in extra_paths:
                resolved = str(Path(path).resolve())
                check('supplemental report ' + resolved, sha(path), meta.get('reports', {}).get(resolved), extra.ref(pointer='/reports'))
    result['all_bindings_verified'] = all(item['passed'] for item in result['checks'])
    return result


def verify_reaudit_bindings(build, physical, obj, reaudit):
    """Independently bind revised reports to their immutable original inputs.

    This function reads/hashes data only. It does not import or execute the
    revised auditor, rerun acceptance, or create/modify a CDC review template.
    """
    build, obj, reaudit = (Path(p).resolve() for p in (build, obj, reaudit))
    physical = Path(physical).resolve()
    physical.relative_to(reaudit / 'acceptance')
    assert physical.name == 'reaudit.passed.json', 'Expected explicit revised acceptance record'
    assert obj == reaudit / 'obj', 'Re-audit mode must read only its new obj report directory'
    decision, inputs, execution = Report(physical), Report(reaudit / 'inputs.json'), Report(reaudit / 'vivado-result.json')
    data, meta, run = (json.loads(r.text) for r in (decision, inputs, execution))
    original = meta['implementation']
    result = {'status': 'available', 'mode': 'read_only_routed_reaudit', 'decision_source': decision.ref(),
              'inputs_source': inputs.ref(), 'checks': []}

    def check(name, actual, expected, source):
        result['checks'].append({'check': name, 'passed': actual == expected, 'actual': actual,
                                 'expected': expected, 'source': source})

    def hashes(name, root, files, source, require_nonempty=True):
        assert isinstance(files, dict), name + ' is not a file map'
        bad = []
        for relative, expected in files.items():
            path = (root / relative).resolve()
            path.relative_to(root.resolve())
            if not path.is_file() or sha(path) != expected:
                bad.append(relative)
        result['checks'].append({'check': name, 'passed': not bad and (bool(files) or not require_nonempty),
                                 'checked_files': len(files), 'mismatches': bad, 'source': source})

    check('original build root', original['build'], str(build), inputs.ref(pointer='/implementation/build'))
    for key in ('schema', 'operation', 'hardware_reimplemented', 'output'):
        expected = {'schema': 1, 'operation': 'read_only_routed_reaudit', 'hardware_reimplemented': False, 'output': str(reaudit)}[key]
        check('inputs ' + key, meta.get(key), expected, inputs.ref(pointer='/' + key))
    for key, expected in (('status', 'passed'), ('constrained_sta_passed', True),
                          ('hbm_startup_physical_passed', True), ('hardware_reimplemented', False), ('board_signoff', False)):
        check('decision ' + key, data.get(key), expected, decision.ref(pointer='/' + key))
    for key in ('implementation', 'auditor'):
        check('decision ' + key + ' identity', data.get(key), meta[key], decision.ref(pointer='/' + key))
    check('inputs.json SHA256', sha(reaudit / 'inputs.json'), data['inputs_sha256'], decision.ref(pointer='/inputs_sha256'))
    check('vivado-result.json SHA256', sha(reaudit / 'vivado-result.json'), data['execution_sha256'], decision.ref(pointer='/execution_sha256'))
    check('Vivado input binding', run['inputs_sha256'], sha(reaudit / 'inputs.json'), execution.ref(pointer='/inputs_sha256'))
    check('Vivado exit code', run['exit_code'], 0, execution.ref(pointer='/exit_code'))
    check('Vivado stdout SHA256', sha(reaudit / 'vivado.log'), run['log_sha256'], execution.ref(pointer='/log_sha256'))
    check('native Vivado log file set', sorted(run['tool_logs']), ['vivado-native.log', 'vivado.jou'], execution.ref(pointer='/tool_logs'))
    hashes('native Vivado logs', reaudit, run['tool_logs'], execution.ref(pointer='/tool_logs'))
    build_report = Report(build / 'build.json')
    build_data = json.loads(build_report.text)
    check('original build.json SHA256', build_report.source['sha256'], original['build_sha256'], inputs.ref(pointer='/implementation/build_sha256'))
    check('original execution commit', build_data['root_commit'], original['execution_commit'], inputs.ref(pointer='/implementation/execution_commit'))
    check('original source-lock identity', build_data['lock_sha256'], original['source_lock_sha256'], inputs.ref(pointer='/implementation/source_lock_sha256'))
    check('original source-lock snapshot', sha(reaudit / 'implementation-drivers/fpga/baselines/nm37-a110/source-lock.json'),
          original['source_lock_sha256'], inputs.ref(pointer='/implementation/source_lock_sha256'))
    check('original generator SHA256', sha(build / 'generator.jar'), original['generator_sha256'], inputs.ref(pointer='/implementation/generator_sha256'))
    checkpoint = (build / original['checkpoint_path']).resolve()
    checkpoint.relative_to(build)
    check('original checkpoint location', str(checkpoint), str(build / BOARD / 'obj/post_route.dcp'), inputs.ref(pointer='/implementation/checkpoint_path'))
    checkpoint_sha = sha(checkpoint)
    check('same routed DCP SHA256', checkpoint_sha, original['checkpoint_sha256'], inputs.ref(pointer='/implementation/checkpoint_sha256'))
    hashes('original stage seals', build, original['stage_seals'], inputs.ref(pointer='/implementation/stage_seals'))
    check('all original stage seals present', sorted(original['stage_seals']),
          sorted(stage + '.passed.json' for stage in ('units', 'formal', 'hbm-startup', 'sim4', 'sim8', 'sim16', 'elaborate')),
          inputs.ref(pointer='/implementation/stage_seals'))
    for name in original['stage_seals']:
        stage_report = Report(build / name)
        stage = json.loads(stage_report.text)
        check('stage/build identity: ' + name, stage['build_sha256'], original['build_sha256'], stage_report.ref(pointer='/build_sha256'))
        hashes('stage payload: ' + name, build, stage['files'], stage_report.ref(pointer='/files'))
    hashes('original retained reports/failure/logs', build, original['original_reports'], inputs.ref(pointer='/implementation/original_reports'))
    hashes('original tool/environment records', build, original['original_environment_files'], inputs.ref(pointer='/implementation/original_environment_files'))
    environment = Report(build / 'logs/environment.json')
    version = json.loads(environment.text)['tools']['vivado']['version']
    expected_version = [line.strip() for line in version.splitlines() if line.strip().startswith(('Vivado ', 'SW Build ', 'IP Build '))]
    assert len(expected_version) == 3, 'Incomplete original Vivado identity'
    native = Report(reaudit / 'vivado-native.log')
    check('re-audit Vivado matches original version/build', [line in native.text for line in expected_version], [True] * 3,
          {'sources': [environment.ref(pointer='/tools/vivado/version'), native.ref()], 'expected_lines': expected_version})
    hashes('original generated IP files', build, original['original_ip_files'], inputs.ref(pointer='/implementation/original_ip_files'))
    actual_ips = sorted(str(p.relative_to(build)) for p in (build / BOARD).rglob('*.xci'))
    check('original generated IP file set', actual_ips, sorted(original['original_ip_files']), inputs.ref(pointer='/implementation/original_ip_files'))
    hashes('revised auditor snapshot', reaudit / 'auditor', meta['auditor']['files'], inputs.ref(pointer='/auditor/files'))
    hashes('original driver snapshot', reaudit / 'implementation-drivers', original['driver_hashes'], inputs.ref(pointer='/implementation/driver_hashes'))
    actual_evidence = {str(p.relative_to(reaudit)): sha(p) for directory in ('report', 'audit', 'hbm_audit', 'reset_replication_audit')
                       for p in (obj / directory).rglob('*') if p.is_file()}
    check('revised report set equals execution seal', actual_evidence, run['report_files'], execution.ref(pointer='/report_files'))
    check('revised report set equals acceptance seal', actual_evidence, data['evidence'], decision.ref(pointer='/evidence'))
    hashes('revised report hashes', reaudit, data['evidence'], decision.ref(pointer='/evidence'))
    actual_review = {name: sha(reaudit / name) for name in ('cdc-review.json', 'cdc-review.required.json') if (reaudit / name).is_file()}
    check('CDC review immutable file set', actual_review, data['review_files'], decision.ref(pointer='/review_files'))
    if data.get('cdc', {}).get('new_findings'):
        check('new CDC review required files', sorted(actual_review), ['cdc-review.json', 'cdc-review.required.json'], decision.ref(pointer='/cdc/new_findings'))
        review_report = Report(reaudit / 'cdc-review.json')
        review = json.loads(review_report.text)
        for key, expected in (('build_sha256', original['build_sha256']), ('checkpoint_sha256', checkpoint_sha),
                              ('cdc_report_sha256', sha(obj / 'audit/cdc.txt'))):
            check('CDC review ' + key, review[key], expected, review_report.ref(pointer='/' + key))
        check('CDC reviewed finding set', sorted(item['finding'] for item in review['reviewed_findings']),
              sorted(data['cdc']['new_findings']), review_report.ref(pointer='/reviewed_findings'))
        for index, item in enumerate(review['reviewed_findings']):
            hashes('CDC finding evidence ' + str(index), reaudit, {r['path']: r['sha256'] for r in item['evidence']},
                   review_report.ref(pointer='/reviewed_findings/' + str(index) + '/evidence'))
    summary = global_summary(Report(obj / 'report/timing.txt'))
    check('accepted global summary matches raw report', data['timing_summary'],
          [summary[key]['value'] for key in SUMMARY_NAMES], decision.ref(pointer='/timing_summary'))
    props = properties(Report(obj / 'audit/run_properties.txt'))
    check('accepted DUT properties match raw report', data['dut'], {k: v['value'] for k, v in props.items() if k != 'status'}, decision.ref(pointer='/dut'))
    check('accepted bus skew matches raw report', data['bus_skew_worst_ns'],
          bus_skew(Report(obj / 'audit/bus_skew.txt'))['worst_slack_ns']['value'], decision.ref(pointer='/bus_skew_worst_ns'))
    result['all_bindings_verified'] = all(item['passed'] for item in result['checks'])
    result['original_implementation'] = {key: value(original[key], inputs, pointer='/implementation/' + key)
            for key in ('execution_commit', 'build_sha256', 'checkpoint_path', 'checkpoint_sha256')}
    result['revised_audit'] = {'inputs': inputs.ref(), 'execution': execution.ref(), 'acceptance': decision.ref()}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, default=BASE / 'build04')
    parser.add_argument('--obj', type=Path)
    parser.add_argument('--physical', type=Path)
    parser.add_argument('--reaudit-root', type=Path, help='Independent read-only re-audit output; never inferred from an old physical failure')
    parser.add_argument('--reaudit-pass', type=Path, help='Explicit acceptance/ATTEMPT/reaudit.passed.json within --reaudit-root')
    parser.add_argument('--a100', type=Path, default=ARCHIVE / 'nm37-a100-reproduction')
    parser.add_argument('--a110', type=Path, default=ARCHIVE / 'nm37-a110-reset')
    parser.add_argument('--capacity-report', type=Path, default=ARCHIVE / 'nm37-a110-reset/A100/utilization_flat.txt')
    parser.add_argument('--flat-utilization', type=Path)
    parser.add_argument('--apb-pulse-width', type=Path)
    parser.add_argument('--supplement-manifest', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--allow-incomplete', action='store_true')
    args = parser.parse_args()
    assert bool(args.reaudit_root) == bool(args.reaudit_pass), 'Supply both --reaudit-root and --reaudit-pass'
    if args.reaudit_root:
        assert args.physical is None, 'Use --reaudit-pass, not --physical, for revised acceptance'
        args.physical = args.reaudit_pass
        if args.obj is None:
            args.obj = args.reaudit_root / 'obj'
        assert args.obj.resolve() == (args.reaudit_root / 'obj').resolve(), 'Do not mix original and revised report trees'
    for directory in (args.build, args.a100, args.a110, args.reaudit_root):
        if directory is not None:
            assert not args.out.resolve().is_relative_to(directory.resolve()), 'Summary output must not modify an input evidence tree'
    if args.obj is None:
        dirs = list((args.build / 'A110/fpga').glob('*/obj'))
        assert len(dirs) == 1, f'Expected one candidate obj directory, found {len(dirs)}'
        args.obj = dirs[0]
    if args.physical is None:
        args.physical = next((args.build / name for name in ('physical.passed.json', 'physical.failed.json') if (args.build / name).is_file()), None)
    flat = args.flat_utilization or (args.obj / 'report/utilization_flat.txt' if (args.obj / 'report/utilization_flat.txt').is_file() else None)
    pulse = args.apb_pulse_width or (args.obj / 'hbm_audit/apb_pulse_width.rpt' if (args.obj / 'hbm_audit/apb_pulse_width.rpt').is_file() else None)
    candidate = extract('A110_APB55_build04_reaudit' if args.reaudit_root else 'A110_APB55_build04',
                        args.obj, args.physical, args.capacity_report, args.build / 'build.json', flat, pulse)
    candidate['original_ip_configuration'] = guarded(ip_configuration, args.build / BOARD)
    a100 = extract('A100_reproduction', args.a100 / 'physical', args.a100 / 'physical.passed.json',
                   args.capacity_report, args.a100 / 'build.json')
    a110 = extract('A110_reset_previous', args.a110 / 'A110', args.a110 / 'physical.failed.review.json',
                   args.capacity_report, args.a110 / 'A110/seals/build.json')
    if args.reaudit_root:
        assert not args.supplement_manifest, 'Re-audit reports are bound by its own execution seal; do not mix a separate supplement'
        assert all(Path(p).resolve().is_relative_to(args.obj.resolve()) for p in (flat, pulse) if p), 'Re-audit report additions must be in its sealed obj tree'
        binding = guarded(verify_reaudit_bindings, args.build, args.physical, args.obj, args.reaudit_root)
    else:
        binding = guarded(verify_bindings, args.build, args.physical, args.obj,
                          [p for p in (args.flat_utilization, args.apb_pulse_width) if p], args.supplement_manifest)
    required = ('physical_decision', 'timing_summary', 'dut_properties', 'dut_setup', 'dut_hold',
                'apb_primitive', 'resources', 'bus_skew', 'route', 'drc', 'cdc', 'hbm_properties', 'original_ip_configuration')
    incomplete = [key for key in required if candidate[key].get('status') != 'available']
    if binding.get('all_bindings_verified') is not True:
        incomplete.append('physical_evidence_bindings')
    if not all(item['passed'] for item in candidate['internal_consistency_checks']):
        incomplete.append('DUT path/property inconsistency')
    result = {'schema': 1, 'created_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'extractor': {'path': str(Path(__file__).resolve()), 'sha256': sha(__file__)},
              'scope': 'Read-only raw report extraction and hash verification; not an independent Vivado run or hardware board signoff',
              'completeness_definition': 'candidate_complete means all requested evidence is available and hashes agree; timing/physical pass remains the separately sourced decision',
              'metric_provenance': 'source applies to every value in its containing record; derived values list all input sources',
              'candidate_complete': not incomplete, 'incomplete_candidate_sections': incomplete,
              'candidate': candidate, 'physical_evidence_bindings': binding,
              'baselines': {'A100': a100, 'A110_previous': a110},
              'candidate_minus_baseline': {'A100': differences(candidate, a100), 'A110_previous': differences(candidate, a110)}}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'output': str(args.out.resolve()), 'sha256': sha(args.out),
                      'candidate_complete': not incomplete, 'incomplete_candidate_sections': incomplete}, ensure_ascii=False))
    return 0 if not incomplete or args.allow_incomplete else 2


if __name__ == '__main__':
    raise SystemExit(main())
