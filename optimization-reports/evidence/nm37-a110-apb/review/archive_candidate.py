#!/usr/bin/env python3
"""Archive selected NM37 A110/APB evidence from a completed, quiescent build.

This is deliberately NOT a self-contained build backup. Large binaries remain
in the original local build. Stage seals are authenticated against their source
files before packing, and an inventory identifies every sealed file not packed.
No source, build, driver, comparison result, or existing archive is modified.
"""
import argparse
import codecs
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import types

STAGES = ('units', 'formal', 'hbm-startup', 'sim4', 'sim8', 'sim16', 'elaborate')
BOARD = 'A110/fpga/chipyard.fpga.nm37.NM37FPGATestHarness.EmptyNM37Config'
SHA256 = re.compile(r'[0-9a-f]{64}\Z')
CHUNK = 1024 * 1024
TEXT_SUFFIXES = {'.txt', '.log', '.rpt', '.json', '.sv', '.v', '.vh', '.svh',
                 '.tcl', '.xdc', '.sdc', '.xci', '.xml', '.dict', '.tsv', '.csv',
                 '.ys', '.py', '.sh', '.scala', '.f', '.md', '.patch', '.sha256',
                 '.cpp', '.cc', '.c', '.h', '.hpp', '.jou'}
LARGE_SUFFIXES = {'.bit', '.dcp', '.jar', '.o', '.a', '.so', '.wdb', '.vcd', '.fst'}


def fail(message):
    raise ValueError(message)


def unique_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                fail(f'Duplicate JSON key in {path}: {key}')
            result[key] = value
        return result
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=pairs)


def digest_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(CHUNK), b''):
            digest.update(block)
    return digest.hexdigest()


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode('utf-8')


def relative_path(value):
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
        fail(f'Invalid relative path: {value!r}')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or str(path) != value or value == '.':
        fail(f'Unsafe/noncanonical relative path: {value!r}')
    return path


def no_symlinks(path):
    for part in (path, *path.parents):
        if part.is_symlink():
            fail(f'Symlinks are not allowed in evidence paths: {part}')


def root_directory(path):
    if not path.is_absolute():
        fail(f'Absolute directory required: {path}')
    no_symlinks(path)
    result = path.resolve(strict=True)
    if not result.is_dir():
        fail(f'Not a directory: {result}')
    return result


def safe_file(root, relative):
    rel = relative_path(relative)
    result = root.joinpath(*rel.parts)
    no_symlinks(result)
    if not result.is_file() or not result.resolve(strict=True).is_relative_to(root):
        fail(f'Missing or escaped evidence file: {result}')
    return result


def walk_files(directory):
    if not directory.is_dir():
        fail(f'Missing evidence directory: {directory}')
    no_symlinks(directory)
    for current, directories, filenames in os.walk(directory, followlinks=False):
        for name in directories + filenames:
            path = Path(current) / name
            if path.is_symlink():
                fail(f'Unexpected symlink in evidence tree: {path}')
        for name in sorted(filenames):
            path = Path(current) / name
            if not path.is_file():
                fail(f'Unexpected nonregular evidence file: {path}')
            yield path


def fingerprint_stat(path):
    state = path.stat()
    return state.st_dev, state.st_ino, state.st_size, state.st_mtime_ns, state.st_ctime_ns


class Plan:
    def __init__(self, args):
        self.roots = {name: root_directory(getattr(args, name))
                      for name in ('build', 'source', 'review')}
        self.build, self.source, self.review = (self.roots[name]
                                               for name in ('build', 'source', 'review'))
        if any(a == b or a.is_relative_to(b) or b.is_relative_to(a)
               for i, a in enumerate(self.roots.values())
               for b in list(self.roots.values())[i + 1:]):
            fail('Build, source and review directories must be separate, nonnested roots')
        destination = args.destination
        if not destination.is_absolute():
            fail('Archive destination must be absolute')
        no_symlinks(destination)
        if destination.exists():
            fail(f'Refusing to overwrite existing destination: {destination}')
        self.destination = destination.resolve(strict=False)
        expected_parent = self.source / 'optimization-reports/evidence'
        if self.destination.parent != expected_parent or not expected_parent.is_dir():
            fail(f'Destination must be a new immediate child of {expected_parent}')
        if self.destination.name in ('.', '..'):
            fail('Invalid destination name')
        self.gzip_threshold = args.gzip_threshold
        self.max_binary_bytes = args.max_binary_bytes
        if self.gzip_threshold < 1 or self.max_binary_bytes < 1:
            fail('Byte limits must be positive')
        self.cache = {}
        self.selected = {}
        self.expected = {}
        self.stage_manifests = {}
        self.large = {}
        self.seals = {}
        self.review_extra = list(getattr(args, 'review_extra', []))
        self.extra_review_files = []
        self.git_ignored = []
        self.reaudit = None
        self.reaudit_pass = getattr(args, 'reaudit_pass', None)
        self.reaudit_root = getattr(args, 'reaudit', None)
        self.reaudit_bitstream = getattr(args, 'reaudit_bitstream', None)
        if bool(self.reaudit_root) != bool(self.reaudit_pass):
            fail('--reaudit and --reaudit-pass must be supplied together')
        if self.reaudit_bitstream and not self.reaudit_root:
            fail('--reaudit-bitstream requires an accepted --reaudit')
        if self.reaudit_root:
            self.roots['reaudit'] = root_directory(self.reaudit_root)
            for original in (self.build, self.source):
                if self.roots['reaudit'].is_relative_to(original) or original.is_relative_to(self.roots['reaudit']):
                    fail('Re-audit tree overlaps original build/source')
            relative_path(self.reaudit_pass)
        if self.reaudit_bitstream:
            self.roots['reaudit_bitstream'] = root_directory(self.reaudit_bitstream)

    def inspect(self, path):
        no_symlinks(path)
        state = fingerprint_stat(path)
        previous = self.cache.get(path)
        if previous:
            if state != previous['state']:
                fail(f'File changed while preparing archive: {path}')
            return previous
        digest = digest_file(path)
        if fingerprint_stat(path) != state:
            fail(f'File changed while hashing: {path}')
        result = dict(state=state, sha256=digest, bytes=state[2])
        self.cache[path] = result
        return result

    def check_hash(self, path, expected, label):
        if not isinstance(expected, str) or not SHA256.fullmatch(expected):
            fail(f'Invalid SHA-256 for {label}')
        observed = self.inspect(path)['sha256']
        if observed != expected:
            fail(f'{label}: SHA-256 mismatch: {path}')
        old = self.expected.get(path)
        if old is not None and old != expected:
            fail(f'Conflicting sealed hashes for {path}')
        self.expected[path] = expected

    def add(self, root_name, relative, category):
        path = safe_file(self.roots[root_name], relative)
        if path.suffix.lower() in LARGE_SUFFIXES or path.name == 'simulator' or \
                (root_name == 'build' and relative.startswith('hbm-startup/obj/')):
            fail(f'Refusing to pack excluded build binary: {path}')
        record = self.inspect(path)
        if path.suffix.lower() not in TEXT_SUFFIXES and record['bytes'] > self.max_binary_bytes:
            fail(f'Unclassified/large binary exceeds copy limit: {path}')
        key = (root_name, relative)
        if key not in self.selected:
            self.selected[key] = dict(path=path, categories=set())
        self.selected[key]['categories'].add(category)
        return path

    def add_tree(self, root_name, relative, category, predicate=None):
        directory = self.roots[root_name] / relative_path(relative)
        found = []
        for path in walk_files(directory):
            rel = path.relative_to(self.roots[root_name]).as_posix()
            if predicate is None or predicate(path):
                self.add(root_name, rel, category)
                found.append(rel)
        if not found:
            fail(f'No selected evidence in required directory: {directory}')
        return found

    def reference_large(self, path, role, root_name='build'):
        record = self.inspect(path)
        relative = path.relative_to(self.roots[root_name]).as_posix()
        key = relative if root_name == 'build' else root_name + '::' + relative
        self.large[key] = dict(source_root=root_name, source_relative_path=relative,
            source_absolute_path=str(path), raw_sha256=record['sha256'], raw_bytes=record['bytes'],
            archived=False, role=role, reason='Generated binary or nontext supplement retained only at local source path')

    def add_review_extras(self):
        for relative in self.review_extra:
            path = self.review / relative_path(relative)
            no_symlinks(path)
            paths = list(walk_files(path)) if path.is_dir() else [safe_file(self.review, relative)]
            for selected in paths:
                name = selected.relative_to(self.review).as_posix()
                if selected.suffix.lower() in TEXT_SUFFIXES:
                    self.add('review', name, 'explicit_supplemental_review')
                    archived = True
                else:
                    self.reference_large(selected, 'explicit_review_nontext_artifact', 'review')
                    archived = False
                self.extra_review_files.append(dict(source_relative_path=name, archived=archived))

    def check_git_ignores(self):
        top = subprocess.run(['git', '-C', str(self.source), 'rev-parse', '--show-toplevel'],
                             check=True, capture_output=True, text=True).stdout.strip()
        if Path(top).resolve() != self.source:
            fail('Source root must be the Git checkout root for index/ignore verification')
        archive_relative = self.destination.relative_to(self.source)
        paths = [str(archive_relative / item['archive_relative']) for item in self.selected.values()]
        paths.extend(str(archive_relative / name) for name in ('inventory.json', 'inventory.sha256', 'README.md'))
        query = b''.join(os.fsencode(name) + b'\0' for name in paths)
        result = subprocess.run(['git', '-C', str(self.source), 'check-ignore', '--no-index', '-z', '--stdin'],
                                input=query, capture_output=True)
        if result.returncode not in (0, 1):
            fail('Git ignore query failed: ' + result.stderr.decode(errors='replace'))
        self.git_ignored = sorted(os.fsdecode(name) for name in result.stdout.split(b'\0') if name)
        if not set(self.git_ignored).issubset(paths):
            fail('Git returned an unexpected ignored path')

    def prepare_reaudit(self):
        root = self.roots['reaudit']
        if (root / 'INCOMPLETE.json').exists():
            fail('Re-audit report generation is marked incomplete')
        inputs_path = self.add('reaudit', 'inputs.json', 'revised_audit_identity')
        inputs = unique_json(inputs_path)
        implementation = inputs['implementation']
        if implementation['build'] != str(self.build) or implementation['source'] != str(self.source):
            fail('Re-audit original build/source paths differ from archive inputs')
        if implementation['build_sha256'] != self.build_hash or implementation['execution_commit'] != self.identity['root_commit']:
            fail('Re-audit hardware implementation identity mismatch')
        for name, digest in inputs['auditor']['files'].items():
            self.check_hash(safe_file(root, 'auditor/' + str(relative_path(name))), digest, 'Revised auditor')
        script = safe_file(root, 'auditor/reaudit.py')
        module = types.ModuleType('archive_reaudit_verifier')
        module.__file__ = str(script)
        sys.modules[module.__name__] = module
        exec(compile(script.read_bytes(), str(script), 'exec'), module.__dict__)
        accepted_path = safe_file(root, self.reaudit_pass)
        verified_inputs, accepted = module.verify_pass(root, accepted_path)
        if verified_inputs != inputs:
            fail('Re-audit inputs changed during verification')
        # The original driver snapshot must also match the original Git commit,
        # not merely an unsigned copy whose manifest was changed with it.
        for name, digest in self.identity['driver_hashes'].items():
            payload = subprocess.check_output(['git', '-C', str(self.source), 'show',
                                               self.identity['root_commit'] + ':' + str(relative_path(name))])
            if hashlib.sha256(payload).hexdigest() != digest:
                fail('Original Git driver/build identity mismatch: ' + name)
        for path, digest in implementation['original_reports'].items():
            self.check_hash(self.add('build', path, 'original_audit_including_failure'), digest,
                            'Original report/re-audit binding')
        for path, digest in implementation['original_ip_files'].items():
            self.check_hash(self.add('build', path, 'original_generated_ip'), digest, 'Original IP/re-audit binding')
        for path, digest in implementation['original_environment_files'].items():
            self.check_hash(self.add('build', path, 'original_tool_environment'), digest, 'Original environment/re-audit binding')
        for path in walk_files(root):
            if '__pycache__' in path.relative_to(root).parts:
                continue
            relative = path.relative_to(root).as_posix()
            if path.suffix.lower() not in TEXT_SUFFIXES:
                self.reference_large(path, 'revised_audit_nontext_artifact', 'reaudit')
            else:
                self.add('reaudit', relative, 'revised_audit_original_output')
        self.reaudit = dict(inputs='inputs.json', inputs_sha256=self.inspect(inputs_path)['sha256'],
            acceptance=self.reaudit_pass, acceptance_sha256=self.inspect(accepted_path)['sha256'],
            execution_commit=implementation['execution_commit'],
            checkpoint_sha256=implementation['checkpoint_sha256'],
            build_sha256=self.build_hash, status=accepted['status'], hardware_reimplemented=False,
            original_acceptance_rewritten=False, board_signoff=False, bitstream_export=None)

    def prepare_reaudit_bitstream(self):
        root = self.roots['reaudit_bitstream']
        if (root / 'INCOMPLETE.json').exists():
            fail('Re-audit bitstream export is marked incomplete')
        record_path = self.add('reaudit_bitstream', 'export.passed.json', 'reaudit_bitstream_export_identity')
        record = unique_json(record_path)
        if record.get('operation') != 'bitstream_from_reaudit' or record.get('exit_code') != 0 or record.get('board_signoff') is not False:
            fail('Invalid re-audit bitstream export status')
        for key in ('checkpoint_sha256', 'build_sha256', 'acceptance_sha256', 'inputs_sha256'):
            if record[key] != self.reaudit[key]:
                fail('Re-audit bitstream binding mismatch: ' + key)
        expected_acceptance = self.roots['reaudit'] / self.reaudit_pass
        if record['acceptance_path'] != str(expected_acceptance):
            fail('Bitstream acceptance path differs')
        bit = safe_file(root, 'export/NM37_A110.bit')
        if record['bitstream']['path'] != str(bit) or record['bitstream']['bytes'] != bit.stat().st_size:
            fail('Bitstream path/size differs from export record')
        self.check_hash(bit, record['bitstream']['sha256'], 'Re-audit bitstream')
        self.check_hash(safe_file(root, 'vivado.log'), record['log_sha256'], 'Bitstream stdout log')
        if set(record['tool_logs']) != {'vivado-native.log', 'vivado.jou'}:
            fail('Bitstream native logs are incomplete')
        for name, digest in record['tool_logs'].items():
            self.check_hash(safe_file(root, name), digest, 'Bitstream native log')
        for path in walk_files(root):
            relative = path.relative_to(root).as_posix()
            if path.suffix.lower() in LARGE_SUFFIXES or path.suffix.lower() not in TEXT_SUFFIXES:
                self.reference_large(path, 'programming_bitstream' if path.suffix.lower() == '.bit'
                                     else 'bitstream_export_nontext_artifact', 'reaudit_bitstream')
            else:
                self.add('reaudit_bitstream', relative, 'reaudit_bitstream_export_report')
        self.bitstreams.append('reaudit_bitstream::export/NM37_A110.bit')
        self.reaudit['bitstream_export'] = dict(record='export.passed.json',
            record_sha256=self.inspect(record_path)['sha256'], bitstream_sha256=record['bitstream']['sha256'])

    def prepare(self):
        build_file = self.add('build', 'build.json', 'identity')
        self.identity = unique_json(build_file)
        self.build_hash = self.inspect(build_file)['sha256']
        self.driver_snapshot = dict(root='source', prefix='')
        if self.reaudit_root:
            self.prepare_reaudit()
            self.driver_snapshot = dict(root='reaudit', prefix='implementation-drivers/')
        lock_relative = 'fpga/baselines/nm37-a110/source-lock.json'
        lock_file = self.add('source', lock_relative, 'source_lock')
        self.check_hash(lock_file, self.identity['lock_sha256'], 'Source lock/build binding')
        lock = unique_json(lock_file)
        if lock['configuration'] != self.identity['configuration']:
            fail('Source lock and build configuration differ')
        generator = safe_file(self.build, 'generator.jar')
        self.check_hash(generator, self.identity['generator_sha256'], 'Generator/build binding')
        self.reference_large(generator, 'assembly_generator')
        drivers = self.identity['driver_hashes']
        if not isinstance(drivers, dict) or not drivers:
            fail('Build driver fingerprint map is empty')
        for relative, digest in sorted(drivers.items()):
            path = self.add(self.driver_snapshot['root'], self.driver_snapshot['prefix'] + relative, 'frozen_driver_snapshot')
            self.check_hash(path, digest, 'Driver/build binding')
        for relative, digest in sorted(lock['shell_hashes'].items()):
            path = self.add('build', f'shell/{relative}', 'frozen_shell_snapshot')
            self.check_hash(path, digest, 'Shell/source-lock binding')
        for relative in ('fpga/src/main/scala/u280/HBMStartup.scala',
                         'fpga/src/main/scala/u280/AXIHBM.scala',
                         'fpga/src/main/scala/u280/TestHarness.scala',
                         'fpga/src/main/scala/nm37/TestHarness.scala',
                         'fpga/src/test/scala/chipyard/fpga/u280/HBMStartupVerification.scala',
                         'fpga/baselines/nm37-a110/FSAConfig.json',
                         'fpga/baselines/nm37-a110/fpga-shells.patch'):
            path = self.add('source', relative, 'selected_product_and_verification_sources')
            self.check_hash(path, lock['source_hashes'][relative], 'Selected source/source-lock binding')
        for relative in ('logs/tools.txt', 'logs/environment.json', 'logs/assembly.log',
                         'logs/unit.log', 'score-events.json', 'A110/elaboration.log',
                         'A110/implementation.log'):
            self.add('build', relative, 'build_and_validation_logs')
        for stage in STAGES:
            seal_path = self.add('build', f'{stage}.passed.json', 'stage_seal')
            seal = unique_json(seal_path)
            if seal['build_sha256'] != self.build_hash:
                fail(f'{stage}: wrong build identity')
            files = seal['files']
            if not isinstance(files, dict) or not files:
                fail(f'{stage}: empty file manifest')
            for relative, digest in files.items():
                self.check_hash(safe_file(self.build, relative), digest, f'{stage} seal')
            self.stage_manifests[stage] = files
            self.seals[stage] = dict(source_relative_path=f'{stage}.passed.json',
                raw_sha256=self.inspect(seal_path)['sha256'], verified_file_count=len(files),
                build_sha256=self.build_hash)
        self.add_tree('build', 'formal', 'unit_and_formal_rtl_proof_logs',
                      lambda path: path.suffix.lower() not in LARGE_SUFFIXES)
        self.add_tree('build', 'hbm-startup/rtl', 'actual_hbm_verification_rtl')
        for name in ('emission.log', 'verilator-build.log', 'simulation.log', 'results.json'):
            self.add('build', f'hbm-startup/{name}', 'hbm_verification')
        hbm = unique_json(safe_file(self.build, 'hbm-startup/results.json'))
        self.check_hash(safe_file(self.build, 'hbm-startup/obj/VHBMStartupVerificationHarness'),
                        hbm['binary_sha256'], 'HBM verification binary/result binding')
        self.reference_large(safe_file(self.build, 'hbm-startup/obj/VHBMStartupVerificationHarness'),
                             'hbm_verification_simulator')
        for name, digest in hbm['inputs_sha256'].items():
            path = Path(name)
            no_symlinks(path)
            matching = [(key, base) for key, base in self.roots.items() if path.is_relative_to(base)]
            if len(matching) != 1:
                fail(f'HBM test input is outside declared evidence roots: {name}')
            key, base = matching[0]
            self.add(key, path.relative_to(base).as_posix(), 'hbm_verification_actual_input')
            self.check_hash(path, digest, 'HBM verification input/result binding')
        self.cases = []
        for size in (4, 8, 16):
            stage = f'sim{size}'
            config = f'chipyard.harness.TestHarness.AXI4FSA{size}X{size}Fp16Config'
            config_path = f'{stage}/generated-src/{config}/{config}.FSAConfig.json'
            self.add('build', config_path, 'simulation_config')
            self.add('build', f'{stage}/build.log', 'simulation_build_log')
            self.add_tree('build', f'{stage}/generated-src/{config}/gen-collateral',
                          'simulation_generated_rtl', lambda path: path.suffix in ('.sv', '.v'))
            self.reference_large(safe_file(self.build, f'{stage}/simulator'), 'fsa_simulator')
            for seq in (size, size * 2):
                directory = f'{stage}/seq{seq}'
                required = ('inst.bin', 'mem.elf', 'actual.npy', 'expected.npy', 'result.json', 'test.log')
                for name in required:
                    path = self.add('build', f'{directory}/{name}', 'six_case_actual_inputs_outputs_logs')
                    relative = path.relative_to(self.build).as_posix()
                    if relative not in self.stage_manifests[stage]:
                        fail(f'Case evidence not in stage seal: {relative}')
                if config_path not in self.stage_manifests[stage]:
                    fail(f'Simulation configuration not in stage seal: {config_path}')
                for path in walk_files(self.build / directory):
                    if path.suffix in ('.bin', '.elf', '.npy', '.json', '.log'):
                        self.add('build', path.relative_to(self.build).as_posix(), 'six_case_supplement')
                self.cases.append(dict(array_size=size, seq=seq, directory=directory,
                                       configuration=config_path, required_files=list(required)))
        self.add_tree('build', f'{BOARD}/gen-collateral', 'board_generated_rtl',
                      lambda path: path.suffix in ('.sv', '.v'))
        for path in sorted((self.build / BOARD).iterdir()):
            if path.is_file() and path.suffix in ('.tcl', '.xdc', '.sdc', '.f', '.json'):
                self.add('build', path.relative_to(self.build).as_posix(), 'board_generated_configuration')
        for directory in ('report', 'audit', 'reset_replication_audit', 'hbm_audit'):
            self.add_tree('build', f'{BOARD}/obj/{directory}', 'physical_' + directory)
        self.xci_files = {}
        for name in ('harnessSysPLL.xci', 'testxilinx_hbm.xci'):
            paths = sorted((self.build / BOARD).rglob(name))
            if not paths:
                fail(f'Missing actual generated {name}')
            self.xci_files[name] = []
            for path in paths:
                relative = path.relative_to(self.build).as_posix()
                self.add('build', relative, 'actual_generated_ip_configuration')
                self.xci_files[name].append(relative)
        self.physical = {}
        for filename in ('physical.passed.json', 'physical.failed.json'):
            path = self.build / filename
            if not path.exists():
                continue
            self.add('build', filename, 'physical_acceptance_or_failure_record')
            result = unique_json(path)
            if 'build_sha256' in result and result['build_sha256'] != self.build_hash:
                fail(f'{filename}: incorrect build identity')
            for relative, digest in result.get('evidence', {}).items():
                self.check_hash(safe_file(self.build, relative), digest, filename + ' evidence')
                self.add('build', relative, 'physical_result_bound_evidence')
            for relative, digest in result.get('stage_seals', {}).items():
                self.check_hash(safe_file(self.build, relative), digest, filename + ' seal binding')
            for key in ('hbm_ip_configuration', 'pll_configuration'):
                for record in result.get(key, []):
                    ip_path = Path(record['path'])
                    if not ip_path.is_absolute() or not ip_path.is_relative_to(self.build):
                        fail(f'{filename}: generated IP path outside build: {ip_path}')
                    ip_relative = ip_path.relative_to(self.build).as_posix()
                    self.check_hash(safe_file(self.build, ip_relative), record['sha256'],
                                    filename + ' generated IP binding')
                    self.add('build', ip_relative, 'physical_result_bound_ip')
            if 'checkpoint_sha256' in result:
                self.check_hash(safe_file(self.build, f'{BOARD}/obj/post_route.dcp'),
                                result['checkpoint_sha256'], filename + ' checkpoint')
            self.physical[filename] = result
        if not self.physical and not self.reaudit:
            fail('No physical pass/fail result; archive only after the physical gate was run')
        if 'physical.passed.json' in self.physical:
            result = self.physical['physical.passed.json']
            if result.get('build_sha256') != self.build_hash or not result.get('constrained_sta_passed'):
                fail('Malformed physical pass record')
            if set(result.get('stage_seals', {})) != {f'{stage}.passed.json' for stage in STAGES}:
                fail('Physical pass record does not bind all seven functional/elaboration stages')
            if not result.get('evidence') or not result.get('checkpoint_sha256'):
                fail('Physical pass record lacks routed evidence/checkpoint binding')
        for filename in ('cdc-review.json', 'cdc-review.required.json'):
            if (self.build / filename).exists():
                self.add('build', filename, 'cdc_review')
        for path in (self.build / BOARD / 'obj').rglob('*.dcp'):
            self.reference_large(path, 'vivado_checkpoint')
        self.bitstreams = []
        bitstream_dir = self.build / 'bitstream'
        if bitstream_dir.exists():
            for path in walk_files(bitstream_dir):
                relative = path.relative_to(self.build).as_posix()
                if path.suffix.lower() in ('.bit', '.bin', '.dcp'):
                    self.reference_large(path, 'programming_bitstream' if path.suffix.lower() != '.dcp'
                                         else 'bitstream_checkpoint')
                    if path.suffix.lower() == '.bit':
                        self.bitstreams.append(relative)
                elif path.suffix.lower() in TEXT_SUFFIXES:
                    self.add('build', relative, 'bitstream_generation_reports')
        if (self.build / 'logs/bitstream.log').exists():
            self.add('build', 'logs/bitstream.log', 'bitstream_generation_log')
        if self.reaudit_bitstream:
            self.prepare_reaudit_bitstream()
        for filename in ('archive_candidate.py', 'compare_candidate.py', 'test_compare_candidate.py',
                         'comparison-selftest.json', 'scanner-validation.json', 'candidate-comparison.json',
                         'verify_candidate_archive.py', 'test_archive_candidate.py', 'archive-selftest.json'):
            self.add('review', filename, 'independent_comparison_and_archive_method')
        self.add_review_extras()
        comparison_file = safe_file(self.review, 'candidate-comparison.json')
        self.comparison = unique_json(comparison_file)
        if self.comparison.get('build_identity', {}).get('build_sha256') != self.build_hash:
            fail('Candidate comparison does not bind this exact build')
        self.check_hash(safe_file(self.review, 'compare_candidate.py'), self.comparison['script_sha256'],
                        'Comparison script/result binding')
        compared_stages = self.comparison['build_identity']['stages']
        for stage in ('sim4', 'sim8', 'sim16', 'elaborate'):
            if compared_stages[stage]['sha256'] != self.seals[stage]['raw_sha256']:
                fail(f'Comparison result refers to a different {stage} seal')
        self.unpacked = []
        selected_build = {relative for root, relative in self.selected if root == 'build'}
        referenced = {}
        for stage, files in self.stage_manifests.items():
            for relative, digest in files.items():
                if relative not in selected_build:
                    item = referenced.setdefault(relative, dict(source_root='build',
                        source_relative_path=relative, source_absolute_path=str(self.build / relative),
                        raw_sha256=digest, raw_bytes=self.inspect(self.build / relative)['bytes'],
                        verified_present_in_local_build=True, archived=False, sealed_by=[]))
                    item['sealed_by'].append(stage)
        for relative, item in sorted(referenced.items()):
            item['reason'] = ('Large/generated binary omitted' if relative in self.large
                              or relative.startswith('hbm-startup/obj/')
                              else 'Outside the selected evidence subset')
            self.unpacked.append(item)
        for relative in self.bitstreams:
            if self.large[relative]['raw_bytes'] == 0:
                fail(f'Empty bitstream is not valid programming output: {relative}')
        self.validate_destinations()
        self.check_git_ignores()
        return self

    def validate_destinations(self):
        destinations = set()
        for (root, relative), item in sorted(self.selected.items()):
            item['encoding'] = 'identity'
            if self.inspect(item['path'])['bytes'] >= self.gzip_threshold and \
                    item['path'].suffix.lower() in TEXT_SUFFIXES:
                decoder = codecs.getincrementaldecoder('utf-8')('strict')
                with item['path'].open('rb') as stream:
                    for block in iter(lambda: stream.read(CHUNK), b''):
                        if b'\x00' in block:
                            fail(f'Text evidence contains NUL: {item["path"]}')
                        decoder.decode(block)
                decoder.decode(b'', final=True)
                item['encoding'] = 'gzip'
            destination = f'{root}/{relative}' + ('.gz' if item['encoding'] == 'gzip' else '')
            relative_path(destination)
            if destination in destinations:
                fail(f'Archive filename collision: {destination}')
            destinations.add(destination)
            item['archive_relative'] = destination

    def check_quiescent(self):
        for path, record in self.cache.items():
            no_symlinks(path)
            if fingerprint_stat(path) != record['state']:
                fail(f'Build/review/source changed during archive operation: {path}')

    def summary(self):
        return dict(build=str(self.build), execution_commit=self.identity['root_commit'],
                    build_sha256=self.build_hash, destination=str(self.destination),
                    selected_file_count=len(self.selected),
                    selected_raw_bytes=sum(self.cache[item['path']]['bytes'] for item in self.selected.values()),
                    stage_count=len(self.seals), simulation_case_count=len(self.cases),
                    unpackaged_sealed_file_count=len(self.unpacked),
                    local_large_artifact_count=len(self.large), bitstreams=self.bitstreams,
                    constrained_sta_passed=bool(self.reaudit) or self.physical.get('physical.passed.json', {}).get('constrained_sta_passed', False),
                    acceptance_basis='revised_read_only_audit' if self.reaudit else 'original_physical_gate',
                    original_acceptance_status=('passed' if 'physical.passed.json' in self.physical else
                                                'failed' if 'physical.failed.json' in self.physical else 'no_physical_marker'),
                    revised_acceptance_status='passed' if self.reaudit else 'not_requested',
                    hardware_reimplemented=False if self.reaudit else None,
                    comparison_status=self.comparison.get('status'),
                    git_ignored_file_count=len(self.git_ignored), full_build_backup=False)

    def write(self):
        self.check_quiescent()
        no_symlinks(self.destination.parent)
        # exist_ok=False is also the final atomic refusal to reuse a destination.
        self.destination.mkdir(exist_ok=False)
        marker = self.destination / 'ARCHIVE_INCOMPLETE.json'
        marker.write_bytes(json_bytes(dict(status='incomplete', reason='Packing/verification in progress',
                                           summary=self.summary())))
        files = []
        try:
            for (root_name, relative), item in sorted(self.selected.items()):
                source = item['path']
                expected = self.inspect(source)
                target = self.destination / item['archive_relative']
                target.parent.mkdir(parents=True, exist_ok=True)
                raw_digest = hashlib.sha256()
                raw_size = 0
                with source.open('rb') as reader, target.open('xb') as output:
                    writer = gzip.GzipFile(filename='', mode='wb', fileobj=output, mtime=0, compresslevel=9) \
                        if item['encoding'] == 'gzip' else output
                    try:
                        for block in iter(lambda: reader.read(CHUNK), b''):
                            raw_digest.update(block)
                            raw_size += len(block)
                            writer.write(block)
                    finally:
                        if writer is not output:
                            writer.close()
                if raw_digest.hexdigest() != expected['sha256'] or raw_size != expected['bytes']:
                    fail(f'Source changed during copy: {source}')
                record = dict(source_root=root_name, source_relative_path=relative,
                    source_absolute_path=str(source), raw_sha256=expected['sha256'], raw_bytes=raw_size,
                    archived_relative_path=item['archive_relative'], archived_sha256=digest_file(target),
                    archived_bytes=target.stat().st_size, encoding=item['encoding'],
                    categories=sorted(item['categories']))
                files.append(record)
            self.check_quiescent()
            limitations = [
                'This is selected evidence, not a self-contained raw build or a complete source checkout.',
                'Stage seals refer to original uncompressed bytes. Sealed files omitted from this package are explicitly listed; they were present and SHA-256 verified in the local build when archived.',
                'Generator JAR, simulation executables, Vivado DCP and bitstream bytes remain only at the recorded local absolute paths; hashes and sizes do not make those binaries available elsewhere.',
                'Source snapshots cover frozen drivers, shell inputs and selected changed/test sources. The full source-lock additionally identifies source files and dependencies not copied here.',
                'Archive completion means packaging succeeded. Physical pass/fail, CDC classifications, formal scope, simulation results and board_signoff retain the meaning stated in their original records.',
                'Simulation cycle comparisons exclude board HBM calibration, PCIe retraining and host reinitialization latency, and are not measured board throughput.',
                'A listed bitstream was hashed as a local artifact; its presence alone is not evidence of successful on-board validation.',
                'Historical baseline evidence remains in the existing nm37-a110-reset archive and has the partial-archive limitations recorded by the comparison report.',
                'Git ignore rules can omit raw .log files and archived directories. Force-add this new evidence directory and run verify_candidate_archive.py --require-index before committing; a filesystem-only archive is not proof that every file entered Git.'
            ]
            inventory = dict(schema_version=3, archive_status='complete_selected_evidence',
                created_utc=datetime.now(timezone.utc).isoformat(), archive_script_sha256=digest_file(Path(__file__)),
                full_build_backup=False, source_roots={key: str(value) for key, value in self.roots.items()},
                summary=self.summary(), stage_verification=self.seals,
                six_simulation_cases=self.cases, actual_generated_ip_files=self.xci_files,
                physical_record_files=sorted(self.physical),
                driver_snapshot=self.driver_snapshot, revised_audit=self.reaudit,
                supplemental_review_inputs=self.review_extra, supplemental_review_files=self.extra_review_files,
                git_ignored_archive_paths=self.git_ignored,
                bitstream_status='present_local_only' if self.bitstreams else 'no_bitstream_found',
                files=files, local_large_artifacts=[value for _, value in sorted(self.large.items())],
                unpackaged_stage_files=self.unpacked, limitations=limitations)
            inventory_bytes = json_bytes(inventory)
            (self.destination / 'inventory.json').write_bytes(inventory_bytes)
            (self.destination / 'inventory.sha256').write_text(
                hashlib.sha256(inventory_bytes).hexdigest() + '  inventory.json\n', encoding='ascii')
            readme = ('# NM37 A110/APB selected evidence\n\n'
                      'This directory is a **partial evidence package**, not a self-contained build backup.\n\n'
                      + '\n\n'.join(limitations) + '\n\n'
                      'See `inventory.json` for source-relative paths, raw and archived SHA-256/size, '
                      'compression, all seven stage bindings, all six case files, and omitted local artifacts. '
                      'The archive layout preserves each path under `build/`, `source/`, or `review/`. '
                      'Files ending in an added `.gz` must be decompressed before checking raw stage hashes.\n')
            (self.destination / 'README.md').write_text(readme, encoding='utf-8')
            self.check_quiescent()
            marker.unlink()
        except Exception as error:
            marker.write_bytes(json_bytes(dict(status='incomplete', error=str(error),
                instruction='Do not treat this directory as complete. It is intentionally not removed or overwritten.')))
            raise
        return self.summary()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True, help='Completed, quiescent build directory')
    parser.add_argument('--source', type=Path, required=True, help='Source checkout matching the frozen build')
    parser.add_argument('--review', type=Path, required=True, help='Independent comparison scripts/results directory')
    parser.add_argument('--destination', type=Path, required=True, help='New directory directly under source/optimization-reports/evidence')
    parser.add_argument('--gzip-threshold', type=int, default=256 * 1024)
    parser.add_argument('--max-binary-bytes', type=int, default=32 * 1024 * 1024)
    parser.add_argument('--review-extra', action='append', default=[],
                        help='Explicit review-relative file/directory to include; repeat as needed. Nontext/binary files are hash-referenced only.')
    parser.add_argument('--reaudit', type=Path, help='Separate immutable re-audit output tree')
    parser.add_argument('--reaudit-pass', help='Re-audit-relative acceptance/.../reaudit.passed.json')
    parser.add_argument('--reaudit-bitstream', type=Path, help='Separate bitstream-from-reaudit export directory')
    parser.add_argument('--plan-only', action='store_true', help='Validate and hash all inputs, print plan, create nothing')
    args = parser.parse_args()
    try:
        plan = Plan(args).prepare()
        result = plan.summary() if args.plan_only else plan.write()
        print(json.dumps(dict(status='validated_plan' if args.plan_only else 'archived', **result), indent=2))
        return 0
    except Exception as error:
        print(json.dumps(dict(status='failed', error=f'{type(error).__name__}: {error}')), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
