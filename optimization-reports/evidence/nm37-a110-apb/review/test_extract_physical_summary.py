#!/usr/bin/env python3
"""Exercise archive parsing and rejection of incomplete/tampered evidence."""
import json
import tempfile
import unittest
from pathlib import Path
import extract_physical_summary as e


class ExtractorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a100 = e.ARCHIVE / 'nm37-a100-reproduction/physical'
        cls.a110 = e.ARCHIVE / 'nm37-a110-reset/A110'
        cls.capacity = e.choose(e.ARCHIVE / 'nm37-a110-reset/A100/utilization_flat.txt')

    def test_original_global_summary_and_compressed_primitive_checks(self):
        old = e.choose(self.a100 / 'report/timing.txt')
        failed = e.choose(self.a110 / 'report/timing.txt')
        a, b = e.global_summary(old), e.global_summary(failed)
        self.assertEqual(len([key for key in a if key in e.SUMMARY_NAMES]), 12)
        self.assertTrue(a['constraints_met']['value'])
        self.assertFalse(b['constraints_met']['value'])
        self.assertEqual(b['wpws_ns']['value'], -0.909)
        checks = e.apb_checks([failed])
        self.assertEqual(len(checks['raw_checks']), 5)
        self.assertEqual(checks['minimum_slack_ns'], -0.909)
        self.assertEqual(checks['raw_checks'][0]['source']['decoded_sha256'],
                         '46a52cfc5f6ed9b5543715e319547b047c44ce914e0c5385046d13a3e6af9faa')

    def test_counts_are_summary_counts_not_detailed_duplicate_rows(self):
        self.assertEqual(e.drc(e.choose(self.a100 / 'report/drc.txt'))['total']['value'], 1114)
        self.assertEqual(e.cdc(e.choose(self.a100 / 'audit/cdc.txt'))['severity_counts']['Critical']['value'], 30)
        skew = e.bus_skew(e.choose(self.a100 / 'audit/bus_skew.txt'))
        self.assertEqual(skew['worst_slack_ns']['value'], 3.142)
        self.assertEqual(skew['violated_reported_path_checks']['value'], 0)
        self.assertEqual(e.route(e.choose(self.a100 / 'report/route_status.txt'))['counts']['fully routed nets']['value'], 497497)

    def test_path_split_and_capacity_sources(self):
        paths = e.critical_paths(e.choose(self.a100 / 'audit/dut_setup_top100.txt'))
        self.assertEqual(len(paths['paths']), 3)
        self.assertEqual(paths['paths'][0]['data_delay_ns']['value'], 9.082)
        self.assertEqual(paths['paths'][0]['logic_delay_ns']['value'], 0.175)
        self.assertEqual(paths['paths'][0]['route_delay_ns']['value'], 8.907)
        r = e.resources(e.choose(self.a110 / 'report/utilization.txt'), self.capacity)['resources']
        self.assertEqual(r['Total LUTs']['used']['value'], 356006)
        self.assertEqual(r['Total LUTs']['available']['value'], 1303680)
        self.assertAlmostEqual(r['Total LUTs']['percent_of_device_capacity']['value'], 27.307774914089343)
        self.assertNotEqual(r['Total LUTs']['used']['source']['path'], r['Total LUTs']['available']['source']['path'])

    def test_missing_primitive_check_is_not_waveform_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'clock-only.rpt'
            path.write_text('apb_period=18.182\napb_high=9.091\napb_low=9.091\n')
            self.assertEqual(e.apb_checks([e.Report(path)])['status'], 'missing')
            original = e.choose(self.a100 / 'report/timing.txt')
            lines = [line for line in original.lines if 'HBM_SNGLBLI_INTF_APB/PCLK' in line and not line.startswith('Min Period')]
            path.write_text('\n'.join(lines))
            result = e.apb_checks([e.Report(path)])
            self.assertEqual(result['status'], 'missing')
            self.assertEqual(result['missing_check_types'], ['Min Period'])

    def test_native_pulse_width_report_allows_blank_location_column(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'native.rpt'
            path.write_text('Check Type Corner Lib Pin Reference Pin Required(ns) Actual(ns) Slack(ns) Location Pin\n' +
                '\n'.join(f'{kind}  {corner}  HBM_SNGLBLI_INTF_APB/PCLK  n/a  {required}  {actual}  {slack}       fixture/HBM_SNGLBLI_INTF_APB_INST/PCLK'
                    for kind, corner, required, actual, slack in (
                        ('Min Period', 'n/a', 10, 18.182, 8.182),
                        ('Low Pulse Width', 'Slow', 5, 9.091, 4.091),
                        ('Low Pulse Width', 'Fast', 5, 9.091, 4.091),
                        ('High Pulse Width', 'Slow', 5, 9.091, 4.091),
                        ('High Pulse Width', 'Fast', 5, 9.091, 4.091))))
            result = e.apb_checks([e.Report(path)])
            self.assertEqual(result['status'], 'available')
            self.assertEqual(len(result['raw_checks']), 5)
            self.assertTrue(all(row['site'] is None for row in result['raw_checks']))
            self.assertEqual(result['minimum_slack_ns'], 4.091)

    def test_malformed_summary_and_wrong_device_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'broken.rpt'
            path.write_text('WNS(ns) TNS(ns)\n------ --------\n0 0\n')
            self.assertEqual(e.guarded(e.global_summary, e.Report(path))['status'], 'parse_error')
            path.write_text(self.capacity.text.replace('xcvu37p_CIVfsvh2892-2L', 'different_device'))
            result = e.guarded(e.resources, e.choose(self.a100 / 'report/utilization.txt'), e.Report(path))
            self.assertEqual(result['status'], 'parse_error')
            self.assertIn('device differs', result['reason'])

    def test_physical_manifest_and_supplement_tampering_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            obj = root / 'obj'
            obj.mkdir()
            files = {'build.json': '{}', 'formal.passed.json': '{}', 'obj/post_route.dcp': 'dcp', 'obj/timing.rpt': 'timing'}
            for path, content in files.items():
                (root / path).write_text(content)
            decision = root / 'physical.passed.json'
            payload = {'build_sha256': e.sha(root / 'build.json'), 'checkpoint_sha256': e.sha(obj / 'post_route.dcp'),
                       'stage_seals': {'formal.passed.json': e.sha(root / 'formal.passed.json')},
                       'evidence': {'obj/timing.rpt': e.sha(obj / 'timing.rpt')}}
            decision.write_text(json.dumps(payload))
            self.assertTrue(e.verify_bindings(root, decision, obj, [], None)['all_bindings_verified'])
            extra = root / 'extra.rpt'
            extra.write_text('extra')
            manifest = root / 'extra.json'
            data = {'report_generation_completed': True, 'checkpoint_sha256': payload['checkpoint_sha256'],
                    'build_manifest_sha256': payload['build_sha256'], 'reports': {str(extra): e.sha(extra)}}
            manifest.write_text(json.dumps(data))
            self.assertTrue(e.verify_bindings(root, decision, obj, [extra], manifest)['all_bindings_verified'])
            data['checkpoint_sha256'] = '0' * 64
            manifest.write_text(json.dumps(data))
            self.assertFalse(e.verify_bindings(root, decision, obj, [extra], manifest)['all_bindings_verified'])
            (obj / 'timing.rpt').write_text('changed after seal')
            self.assertFalse(e.verify_bindings(root, decision, obj, [], None)['all_bindings_verified'])

    def reaudit_fixture(self, root):
        build, out = root / 'original', root / 'reaudit'
        obj = out / 'obj'
        def put(path, content):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        def dump(path, data):
            put(path, json.dumps(data))
        put(build / 'generator.jar', 'synthetic original generator')
        lock_path = out / 'implementation-drivers/fpga/baselines/nm37-a110/source-lock.json'
        put(lock_path, '{}')
        dump(build / 'build.json', {'root_commit': 'a' * 40, 'lock_sha256': e.sha(lock_path)})
        put(build / e.BOARD / 'obj/post_route.dcp', 'synthetic routed DCP')
        # The old failure deliberately differs from revised timing; only the
        # new obj must feed extracted physical metrics.
        put(build / e.BOARD / 'obj/report/timing.txt', 'old physical report: FAIL -99ns')
        put(build / e.BOARD / 'ip/testxilinx_hbm.xci', '<xci>original HBM</xci>')
        put(build / e.BOARD / 'ip/harnessSysPLL.xci', '<xci>original PLL</xci>')
        put(build / 'sealed.payload', 'original sealed validation output')
        for stage in ('units', 'formal', 'hbm-startup', 'sim4', 'sim8', 'sim16', 'elaborate'):
            dump(build / (stage + '.passed.json'), {'build_sha256': e.sha(build / 'build.json'),
                 'files': {'sealed.payload': e.sha(build / 'sealed.payload')}})
        put(out / 'implementation-drivers/scripts/nm37-a110/check.py', '# original checker')
        put(out / 'auditor/physical_audit.py', '# revised audit fixture only; never imported')
        put(obj / 'report/timing.txt', 'WNS(ns) ...\n-------\n0.2 0 0 10 0.1 0 0 10 0.3 0 0 10\n')
        put(obj / 'audit/run_properties.txt', 'part=synthetic\ndut_period=9.091\ndut_setup_wns=0.2\ndut_hold_whs=0.1\n')
        put(obj / 'audit/bus_skew.txt', 'Slack (MET) : 0.2ns\n')
        for name in ('vivado.log', 'vivado-native.log', 'vivado.jou'):
            put(out / name, 'synthetic fixture log ' + name)
        version = 'Vivado synthetic-fixture-only\nSW Build fixture\nIP Build fixture\n'
        put(build / 'logs/tools.txt', version)
        dump(build / 'logs/environment.json', {'tools': {'vivado': {'version': version}}})
        put(out / 'vivado-native.log', version)
        original = {'build': str(build), 'execution_commit': 'a' * 40,
                    'build_sha256': e.sha(build / 'build.json'), 'source_lock_sha256': e.sha(lock_path),
                    'generator_sha256': e.sha(build / 'generator.jar'),
                    'checkpoint_path': e.BOARD + '/obj/post_route.dcp',
                    'checkpoint_sha256': e.sha(build / e.BOARD / 'obj/post_route.dcp'),
                    'stage_seals': {p.name: e.sha(p) for p in build.glob('*.passed.json')},
                    'original_reports': {e.BOARD + '/obj/report/timing.txt': e.sha(build / e.BOARD / 'obj/report/timing.txt')},
                    'original_environment_files': {name: e.sha(build / name) for name in ('logs/tools.txt', 'logs/environment.json')},
                    'original_ip_files': {str(p.relative_to(build)): e.sha(p) for p in build.rglob('*.xci')},
                    'driver_hashes': {'scripts/nm37-a110/check.py': e.sha(out / 'implementation-drivers/scripts/nm37-a110/check.py')}}
        auditor = {'files': {'physical_audit.py': e.sha(out / 'auditor/physical_audit.py')}}
        manifest = {'schema': 1, 'operation': 'read_only_routed_reaudit', 'hardware_reimplemented': False,
                    'output': str(out), 'implementation': original, 'auditor': auditor}
        dump(out / 'inputs.json', manifest)
        evidence = {str(p.relative_to(out)): e.sha(p) for p in obj.rglob('*') if p.is_file()}
        execution = {'inputs_sha256': e.sha(out / 'inputs.json'), 'exit_code': 0,
                     'log_sha256': e.sha(out / 'vivado.log'), 'tool_logs': {name: e.sha(out / name) for name in ('vivado-native.log', 'vivado.jou')},
                     'report_files': evidence}
        dump(out / 'vivado-result.json', execution)
        acceptance = {'status': 'passed', 'constrained_sta_passed': True, 'hbm_startup_physical_passed': True,
                      'hardware_reimplemented': False, 'board_signoff': False, 'implementation': original,
                      'auditor': auditor, 'inputs_sha256': e.sha(out / 'inputs.json'),
                      'execution_sha256': e.sha(out / 'vivado-result.json'), 'evidence': evidence, 'review_files': {},
                      'timing_summary': [0.2, 0, 0, 10, 0.1, 0, 0, 10, 0.3, 0, 0, 10],
                      'dut': {'part': 'synthetic', 'dut_period': '9.091', 'dut_setup_wns': '0.2', 'dut_hold_whs': '0.1'},
                      'bus_skew_worst_ns': 0.2}
        decision = out / 'acceptance/example/reaudit.passed.json'
        dump(decision, acceptance)
        return build, out, obj, decision, acceptance

    def test_reaudit_uses_new_reports_and_binds_original_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            build, out, obj, decision, acceptance = self.reaudit_fixture(Path(temp))
            result = e.verify_reaudit_bindings(build, decision, obj, out)
            self.assertTrue(result['all_bindings_verified'])
            self.assertEqual(result['original_implementation']['execution_commit']['value'], 'a' * 40)
            with self.assertRaisesRegex(AssertionError, 'new obj'):
                e.verify_reaudit_bindings(build, decision, build / e.BOARD / 'obj', out)
            acceptance['timing_summary'][0] = 99.0
            decision.write_text(json.dumps(acceptance))
            self.assertFalse(e.verify_reaudit_bindings(build, decision, obj, out)['all_bindings_verified'])

    def test_reaudit_changed_original_ip_or_new_report_cannot_pass_bindings(self):
        with tempfile.TemporaryDirectory() as temp:
            build, out, obj, decision, acceptance = self.reaudit_fixture(Path(temp))
            path = build / e.BOARD / 'ip/harnessSysPLL.xci'
            prior = path.read_text()
            path.write_text('changed original IP')
            self.assertFalse(e.verify_reaudit_bindings(build, decision, obj, out)['all_bindings_verified'])
            path.write_text(prior)
            path = obj / 'report/timing.txt'
            path.write_text(path.read_text().replace('0.2 0', '-0.2 0'))
            self.assertFalse(e.verify_reaudit_bindings(build, decision, obj, out)['all_bindings_verified'])

    def test_reaudit_resealed_log_still_requires_original_vivado_version(self):
        with tempfile.TemporaryDirectory() as temp:
            build, out, obj, decision, acceptance = self.reaudit_fixture(Path(temp))
            log = out / 'vivado-native.log'
            log.write_text('Vivado different-version\nSW Build different\nIP Build different\n')
            execution_path = out / 'vivado-result.json'
            execution = json.loads(execution_path.read_text())
            execution['tool_logs']['vivado-native.log'] = e.sha(log)
            execution_path.write_text(json.dumps(execution))
            acceptance['execution_sha256'] = e.sha(execution_path)
            decision.write_text(json.dumps(acceptance))
            result = e.verify_reaudit_bindings(build, decision, obj, out)
            self.assertFalse(result['all_bindings_verified'])
            failed = [r['check'] for r in result['checks'] if not r['passed']]
            self.assertEqual(failed, ['re-audit Vivado matches original version/build'])

    def test_ip_configuration_reads_only_the_explicit_original_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original, unrelated = root / 'original', root / 'reaudit'
            original.mkdir()
            unrelated.mkdir()
            (unrelated / 'testxilinx_hbm.xci').write_text('<ignored/>')
            result = e.ip_configuration(original)
            self.assertEqual(result['status'], 'missing')
            self.assertEqual(result['files'], [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
