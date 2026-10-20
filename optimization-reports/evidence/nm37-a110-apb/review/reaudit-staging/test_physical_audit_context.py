#!/usr/bin/env python3
"""Synthetic-fixture tests for audit relocation; these are NOT board evidence."""
import ast
import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

import physical_audit as audit

HELPER = Path(__file__).resolve().parent
# Canonical inspect assert AST list from the exact executed implementation's
# Git blob (not the current auditor). This preserves the baseline check when
# these tests are moved from staging into scripts/nm37-a110 in any checkout.
ORIGINAL_COMMIT = 'eeb1a1d0ac6f47497eb2806e412d9df9ad4b6b67'
ORIGINAL_SOURCE_SHA256 = '967b4f7595298cd8c3f83e9540f127c084116de4769939a2bf7bfd6c8155f6e6'
ORIGINAL_INSPECT_ASSERT_COUNT = 28
ORIGINAL_INSPECT_ASSERT_SHA256 = 'e620a076190f7f14ccdb9c609a354c1091c6a74279cce956e41427322ab98469'


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def snapshot(root):
    return {str(p.relative_to(root)): audit.file_sha(p) for p in root.rglob('*') if p.is_file()}


def xci(path, fields):
    root = ET.Element('config')
    for key, value in fields.items():
        ET.SubElement(root, 'configurableElementValue', referenceId=key).text = str(value)
    write(path, ET.tostring(root, encoding='unicode'))


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nm37-reaudit-context-test-')
        self.root = Path(self.temp.name)
        self.original = self.root / 'original'
        self.ip = self.original / 'A110/fpga/design'
        self.checkpoint = self.ip / 'obj/post_route.dcp'
        self.review = self.root / 'new-review'
        self.obj = self.review / 'reports'
        self.obj.mkdir(parents=True)
        write(self.original / 'build.json', '{"root_commit": "synthetic-fixture-only"}\n')
        write(self.checkpoint, 'synthetic fixture, not a Vivado DCP\n')
        write(self.original / 'physical.failed.json', '{"original_failure": "must stay unchanged"}\n')
        write(self.original / 'cdc-review.json', '{"must_not_reuse_original_review": true}\n')
        self.context = audit.AuditContext(self.original, self.checkpoint, self.ip, self.review)
        self.helper = mock.patch.object(audit, 'HELPER', HELPER)
        self.helper.start()
        self.make_reports()

    def tearDown(self):
        self.helper.stop()
        self.temp.cleanup()

    def make_reports(self):
        write(self.obj / 'audit/run_properties.txt', 'part=xcvu37p_CIV-fsvh2892-2L-e\ndut_period=9.091\ndut_setup_wns=0.2\ndut_hold_whs=0.1\n')
        write(self.obj / 'report/timing.txt', 'WNS(ns) TNS(ns) setup_fail setup_total WHS(ns) THS(ns) hold_fail hold_total WPWS(ns) TPWS(ns) pulse_fail pulse_total\n------ --------\n0.2 0 0 10 0.1 0 0 10 0.3 0 0 10\n')
        write(self.obj / 'report/route_status.txt', '# of routable nets.... : 10\n# of fully routed nets.... : 10\n# of nets with routing errors.... : 0\n')
        write(self.obj / 'audit/bus_skew.txt', 'Slack (MET) : 0.2ns\n')
        write(self.obj / 'report/drc.txt', '| EXAMPLE | Warning | synthetic only | 1 |\n')
        write(self.obj / 'audit/audit_status.txt', 'failures=\n')
        reset = self.obj / 'reset_replication_audit'
        common = 'signature FDCE upstream launch loads {a b} load_details {a detail_a b detail_b} '
        before = common + 'cells {original {signature FDCE attributes {INIT 0} loads {a b} output_ports {}}}\n'
        after = common + 'cells {replica0 {signature FDCE attributes {INIT 0} loads {a} output_ports {}} replica1 {signature FDCE attributes {INIT 0} loads {b} output_ports {}}}\n'
        write(reset / 'before.dict', before)
        write(reset / 'after_replication.dict', after)
        write(reset / 'postroute.dict', after)
        for name in ('replication_equivalence.txt', 'postroute_equivalence.txt'):
            write(reset / name, 'PASS: identical FDCE signatures\n')
        checks = [f'{cell}/{pin} {kind} slack=0.1' for cell in ('replica0', 'replica1') for pin in ('D', 'CLR') for kind in ('max', 'min')]
        write(reset / 'postroute_timing/endpoint_coverage.txt', '\n'.join(checks + ['failures=']) + '\n')
        hbm = {'status': 'PASS', 'synchronizer_stages': '9', 'completion_crossing_timed': '1',
               'pcie_reset_monitor_clock': 'sys_clock', 'ref_clock': 'sys_clock',
               'pcie_reset_synchronizer_stages': '3', 'pcie_reset_counter_bits': '14', 'pcie_reset_feedback_free': '1',
               'apb_period': '18.182', 'axi_period': '9.091', 'ref_period': '10.000', 'ref_hard_period': '10.000',
               'apb_high': '9.091', 'apb_low': '9.091'}
        write(self.obj / 'hbm_audit/properties.txt', ''.join(f'{k}={v}\n' for k, v in hbm.items()))
        xci(self.ip / 'ip/testxilinx_hbm.xci', {'PARAM_VALUE.USER_APB_PCLK_0': 55, 'PARAM_VALUE.USER_APB_PCLK_PERIOD_0': '18.182'})
        xci(self.ip / 'ip/harnessSysPLL.xci', {
            'PARAM_VALUE.NUM_OUT_CLKS': 2, 'PARAM_VALUE.PRIM_IN_FREQ': 100,
            'PARAM_VALUE.CLKOUT1_REQUESTED_OUT_FREQ': 110, 'PARAM_VALUE.CLKOUT2_REQUESTED_OUT_FREQ': 55,
            'MODELPARAM_VALUE.C_CLKOUT0_ACTUAL_FREQ': 110, 'MODELPARAM_VALUE.C_CLKOUT1_ACTUAL_FREQ': 55,
            'PARAM_VALUE.CLKOUT1_JITTER': 200, 'PARAM_VALUE.CLKOUT2_JITTER': 210,
            'PARAM_VALUE.JITTER_OPTIONS': 'PS', 'PARAM_VALUE.CLKIN1_UI_JITTER': 0.01,
            'PARAM_VALUE.CLKIN1_JITTER_PS': 100, 'PARAM_VALUE.JITTER_SEL': 'No_Jitter'})
        names = ['complete_apb_to_axi']
        names += [f'{chain}_stage{stage}' for chain in ('apb_reset', 'axi_reset', 'completion_sync') for stage in ('01', '12')]
        names += [f'{chain}_clr{stage}' for chain in ('apb_reset', 'axi_reset', 'completion_sync') for stage in (0, 1, 2)]
        names += ['pcie_reset_stage01', 'pcie_reset_stage12', 'pcie_released_clr'] + [f'pcie_counter_clr{bit}' for bit in range(14)]
        write(self.obj / 'hbm_audit/endpoint_timing.tsv', 'name\tkind\tslack\tstart\tend\n' +
              ''.join(f'{name}\t{kind}\t0.1\tfixture/start\tfixture/end\n' for name in names for kind in ('max', 'min')))
        kinds = ['no_clock', 'constant_clock', 'pulse_width_clock', 'unconstrained_internal_endpoints',
                 'no_input_delay', 'no_output_delay', 'multiple_clock', 'generated_clocks', 'loops',
                 'partial_input_delay', 'partial_output_delay', 'latch_loops']
        check = ''.join(f'{i}. checking {key} ({30 if key == "unconstrained_internal_endpoints" else 1 if key == "no_input_delay" else 0})\n'
                        for i, key in enumerate(kinds, 1))
        check += ''.join(f'dutDomain/ram_hbm/inst/ONE_STACK.u_hbm_top/ONE_STACK_HBM.hbm_one_stack_intf/HBM_SNGLBLI_INTF_AXI<{i}>_INST/{pin}\n'
                         for i in range(1, 16) for pin in ('BREADY', 'RREADY'))
        write(self.obj / 'audit/check_timing.txt', check + 'prst_n\n')
        write(self.obj / 'audit/cdc.txt', 'Source Clock: fixture_source\nDestination Clock: fixture_destination\n' +
              '1  CDC-7  Critical  synthetic reset finding  0  False Path  fixture/launch/C  fixture/reset/CLR\n' +
              '2  CDC-15  Warning  synthetic enable finding  0  False Path  fixture/launch/C  fixture/enable/CE\n')

    def findings(self):
        return audit.cdc_rows((self.obj / 'audit/cdc.txt').read_text())

    def template(self):
        with self.assertRaisesRegex(AssertionError, 'require .*cdc-review.json'):
            audit.review_cdc(self.obj, self.findings(), context=self.context)
        return json.loads((self.review / 'cdc-review.required.json').read_text())

    def accept(self):
        data = self.template()
        data['reviewer'] = 'synthetic unit test; not a physical acceptance'
        evidence = self.obj / 'hbm_audit/endpoint_timing.tsv'
        for item in data['reviewed_findings']:
            item.update(disposition='accepted', classification='tool_recognition_limit',
                        rationale='Synthetic fixture used only to exercise validation policy',
                        evidence=[{'path': str(evidence.relative_to(self.review)), 'sha256': audit.file_sha(evidence)}])
        write(self.review / 'cdc-review.json', json.dumps(data))
        return data

    def inspect(self):
        result = {}
        audit.inspect(self.obj, result, context=self.context)
        return result

    def test_full_inspect_reads_old_inputs_writes_only_new_tree(self):
        original = snapshot(self.original)
        self.accept()
        result = self.inspect()
        self.assertTrue(result['constrained_sta_passed'])
        self.assertFalse(result['board_signoff'])
        self.assertEqual(result['reset_replication']['covered_endpoint_checks'], 8)
        self.assertEqual(result['cdc']['critical_count'], 1)
        self.assertEqual(result['cdc']['warning_count'], 1)
        self.assertFalse(result['cdc']['review_required'])
        self.assertEqual(result['audit_context']['checkpoint'], str(self.checkpoint))
        self.assertEqual(result['hbm_ip_configuration'][0]['path'], str(self.ip / 'ip/testxilinx_hbm.xci'))
        self.assertEqual(snapshot(self.original), original)
        self.assertFalse((self.original / 'physical.passed.json').exists())
        self.assertFalse((self.review / 'physical.passed.json').exists())

    def test_missing_review_produces_bound_template_only_in_new_root(self):
        original = snapshot(self.original)
        data = self.template()
        self.assertEqual(data['build_sha256'], audit.file_sha(self.original / 'build.json'))
        self.assertEqual(data['checkpoint_sha256'], audit.file_sha(self.checkpoint))
        self.assertEqual(data['cdc_report_sha256'], audit.file_sha(self.obj / 'audit/cdc.txt'))
        self.assertEqual(len(data['reviewed_findings']), 2)
        self.assertEqual(snapshot(self.original), original)
        self.assertFalse((self.original / 'cdc-review.required.json').exists())

    def test_context_rejects_overlap_and_wrong_original_checkpoint(self):
        for directory in (self.original, self.original / 'new-review', self.root):
            with self.subTest(directory=directory):
                directory.mkdir(exist_ok=True)
                context = audit.AuditContext(self.original, self.checkpoint, self.ip, directory)
                with self.assertRaisesRegex(AssertionError, 'disjoint'):
                    context.validate(self.obj)
        wrong = self.ip / 'obj/post_place.dcp'
        write(wrong, 'wrong phase')
        with self.assertRaisesRegex(AssertionError, 'original post_route.dcp'):
            audit.AuditContext(self.original, wrong, self.ip, self.review).validate(self.obj)

    def test_context_rejects_symlinked_reports_or_output_escape(self):
        link = self.obj / 'borrowed.rpt'
        link.symlink_to(self.checkpoint)
        with self.assertRaisesRegex(AssertionError, 'symlinks'):
            self.context.validate(self.obj)
        link.unlink()
        target = self.original / 'do-not-change.json'
        write(target, 'original content')
        (self.review / 'cdc-review.required.json').symlink_to(target)
        with self.assertRaisesRegex(AssertionError, 'escapes'):
            audit.review_cdc(self.obj, self.findings(), context=self.context)
        self.assertEqual(target.read_text(), 'original content')

    def test_atomic_template_does_not_write_through_hard_link(self):
        target = self.original / 'do-not-change.json'
        write(target, 'original content')
        (self.review / 'cdc-review.required.json').hardlink_to(target)
        self.template()
        self.assertEqual(target.read_text(), 'original content')

    def test_review_must_bind_build_checkpoint_and_new_cdc_report(self):
        valid = self.accept()
        for key in ('build_sha256', 'checkpoint_sha256', 'cdc_report_sha256'):
            with self.subTest(binding=key):
                changed = copy.deepcopy(valid)
                changed[key] = '0' * 64
                write(self.review / 'cdc-review.json', json.dumps(changed))
                with self.assertRaisesRegex(AssertionError, 'stale: ' + key):
                    self.inspect()

    def test_review_must_cover_each_finding_once(self):
        valid = self.accept()
        variants = [valid['reviewed_findings'][:1], valid['reviewed_findings'] * 2]
        foreign = copy.deepcopy(valid['reviewed_findings'])
        foreign[0]['finding'][-1] = 'different-clock-domain'
        variants.append(foreign)
        for rows in variants:
            with self.subTest(rows=len(rows)):
                changed = copy.deepcopy(valid)
                changed['reviewed_findings'] = rows
                write(self.review / 'cdc-review.json', json.dumps(changed))
                with self.assertRaisesRegex(AssertionError, 'Incomplete|Duplicate/foreign'):
                    self.inspect()

    def test_review_cannot_waive_real_critical_or_use_missing_evidence(self):
        valid = self.accept()
        for field, replacement, message in (
                ('classification', 'timed_related_clock_crossing', 'real CDC defect'),
                ('disposition', 'pending', 'not accepted'),
                ('rationale', '', 'not accepted'),
                ('evidence', [], 'actual evidence')):
            with self.subTest(field=field):
                changed = copy.deepcopy(valid)
                critical = next(r for r in changed['reviewed_findings'] if r['finding'][1] == 'Critical')
                critical[field] = replacement
                write(self.review / 'cdc-review.json', json.dumps(changed))
                with self.assertRaisesRegex(AssertionError, message):
                    self.inspect()

    def test_changed_or_escaped_review_evidence_rejected(self):
        valid = self.accept()
        data = copy.deepcopy(valid)
        data['reviewed_findings'][0]['evidence'][0]['sha256'] = '0' * 64
        write(self.review / 'cdc-review.json', json.dumps(data))
        with self.assertRaisesRegex(AssertionError, 'Changed/missing'):
            self.inspect()
        data = copy.deepcopy(valid)
        data['reviewed_findings'][0]['evidence'][0] = {'path': str(self.original / 'build.json'), 'sha256': audit.file_sha(self.original / 'build.json')}
        write(self.review / 'cdc-review.json', json.dumps(data))
        with self.assertRaises(ValueError):
            self.inspect()

    def test_each_original_physical_gate_still_rejects_failures(self):
        self.accept()
        mutations = [
            ('audit/run_properties.txt', 'dut_setup_wns=0.2', 'dut_setup_wns=-0.1'),
            ('report/timing.txt', '0.3 0 0 10', '-0.1 -0.1 1 10'),
            ('report/route_status.txt', 'fully routed nets.... : 10', 'fully routed nets.... : 9'),
            ('audit/bus_skew.txt', '0.2ns', '-0.1ns'),
            ('report/drc.txt', '| Warning |', '| Critical Warning |'),
            ('audit/audit_status.txt', 'failures=', 'failures=buffer_error'),
            ('reset_replication_audit/postroute.dict', 'loads {a}', 'loads {a b}'),
            ('reset_replication_audit/postroute_timing/endpoint_coverage.txt', 'slack=0.1', 'slack=-0.1'),
            ('hbm_audit/properties.txt', 'pcie_reset_feedback_free=1', 'pcie_reset_feedback_free=0'),
            ('hbm_audit/properties.txt', 'completion_crossing_timed=1', 'completion_crossing_timed=0'),
            ('hbm_audit/endpoint_timing.tsv', 'complete_apb_to_axi\tmax\t0.1', 'complete_apb_to_axi\tmax\t-0.1'),
            ('audit/check_timing.txt', 'checking no_clock (0)', 'checking no_clock (1)'),
        ]
        for relative, old, new in mutations:
            with self.subTest(report=relative, mutation=new):
                path = self.obj / relative
                prior = path.read_text()
                self.assertIn(old, prior)
                path.write_text(prior.replace(old, new, 1))
                try:
                    with self.assertRaises(AssertionError):
                        self.inspect()
                finally:
                    path.write_text(prior)

    def test_original_ip_settings_are_not_replaced_by_local_reaudit_copies(self):
        self.accept()
        local = self.obj.parent / 'ip/testxilinx_hbm.xci'
        shutil.copytree(self.ip / 'ip', local.parent)
        original = self.ip / 'ip/testxilinx_hbm.xci'
        original.write_text(original.read_text().replace('>55<', '>110<'))
        with self.assertRaisesRegex(AssertionError, 'configured APB clock differs'):
            self.inspect()

    def test_missing_hbm_endpoint_is_rejected(self):
        self.accept()
        path = self.obj / 'hbm_audit/endpoint_timing.tsv'
        lines = path.read_text().splitlines()
        path.write_text('\n'.join(lines[:-1]) + '\n')
        with self.assertRaisesRegex(AssertionError, 'coverage incomplete'):
            self.inspect()

    def test_all_original_inspect_assertions_are_unchanged(self):
        node = next(n for n in ast.parse(Path(audit.__file__).read_text()).body
                    if isinstance(n, ast.FunctionDef) and n.name == 'inspect')
        assertions = [ast.dump(n, include_attributes=False) for n in ast.walk(node)
                      if isinstance(n, ast.Assert)]
        self.assertEqual(len(assertions), ORIGINAL_INSPECT_ASSERT_COUNT)
        canonical = json.dumps(assertions, separators=(',', ':')).encode()
        self.assertEqual(hashlib.sha256(canonical).hexdigest(), ORIGINAL_INSPECT_ASSERT_SHA256,
                         f'inspect assertions differ from frozen implementation {ORIGINAL_COMMIT}')

    def test_default_inspect_signature_remains_compatible(self):
        legacy_obj = self.ip / 'obj'
        shutil.copytree(self.obj, legacy_obj, dirs_exist_ok=True)
        old_review = self.original / 'cdc-review.json'
        old_review.unlink()
        rows = audit.cdc_rows((legacy_obj / 'audit/cdc.txt').read_text())
        with self.assertRaisesRegex(AssertionError, 'require .*cdc-review.json'):
            audit.review_cdc(legacy_obj, rows)
        template = json.loads((self.original / 'cdc-review.required.json').read_text())
        template['reviewer'] = 'legacy synthetic unit test'
        evidence = legacy_obj / 'hbm_audit/endpoint_timing.tsv'
        for finding in template['reviewed_findings']:
            finding.update(disposition='accepted', classification='tool_recognition_limit', rationale='synthetic test',
                           evidence=[{'path': str(evidence.relative_to(self.original)), 'sha256': audit.file_sha(evidence)}])
        old_review.write_text(json.dumps(template))
        result = {}
        audit.inspect(legacy_obj, result)
        self.assertTrue(result['constrained_sta_passed'])
        self.assertNotIn('audit_context', result)


if __name__ == '__main__':
    unittest.main(verbosity=2)
