"""Fail-closed fingerprint and validation gates for the reproducible A100 build."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / 'fpga/baselines/nm37-a100'
LOCK = json.loads((ASSETS / 'source-lock.json').read_text())
OUT = Path(os.environ.get('A100_OUT', '/nonexistent-a100-output'))

def sha(path):
    data = os.fsencode(os.readlink(path)) if path.is_symlink() else path.read_bytes()
    return hashlib.sha256(data).hexdigest()

def verify(base, hashes):
    for p, h in hashes.items():
        if not ((base / p).is_file() or (base / p).is_symlink()) or sha(base / p) != h:
            raise RuntimeError(f'Fingerprint mismatch: {base / p}')

def write(path, data):
    path.write_text(json.dumps(data, indent=2) + '\n')

# Whole tracked input trees, including extensionless resources and helpers.
# Drivers are independently frozen in build.json; the manifest cannot hash itself.
SOURCE_PREFIXES = ('generators/', 'tools/', 'project/', 'sims/', 'fpga/', 'scripts/')
SOURCE_EXCLUDES = ('scripts/nm37-a100/', 'fpga/scripts/m1_a100_bitstream.tcl',
                   'fpga/baselines/nm37-a100/source-lock.json')
GENERATED_DIRS = {'target', '.venv', '__pycache__', '.bsp', '.metals',
                  'generated-src', 'test_run_dir'}
UNTRACKED_SOURCE_SUFFIXES = {'.scala', '.sbt', '.sc', '.v', '.sv', '.vh', '.svh',
    '.c', '.cc', '.cpp', '.h', '.hpp', '.py', '.sh', '.tcl', '.xdc', '.mk'}

def selected(path):
    if path.startswith(SOURCE_EXCLUDES):
        return False
    if path.startswith(SOURCE_PREFIXES):
        return True
    return '/' not in path and (Path(path).suffix in {'.mk', '.sbt'} or path == '.gitmodules')

def source_modes():
    entries = subprocess.check_output(['git', '-C', str(ROOT), 'ls-files', '--stage', '-z'], text=True)
    modes = {}
    for entry in entries.split('\0'):
        if not entry:
            continue
        metadata, path = entry.split('\t', 1)
        mode, _, stage = metadata.split()
        assert stage == '0', 'Unmerged input: ' + path
        if mode != '160000' and selected(path):
            modes[path] = mode
    return modes

def source_paths():
    return set(source_modes())

def reject_untracked_sources(repo, root_scope=False, allowed=()):
    # Do not honor arbitrary .gitignore: an ignored Scala file is still compiled.
    args = ['git', '-C', str(repo), 'ls-files', '--others', '-z']
    args += ['--exclude=' + name + '/' for name in sorted(GENERATED_DIRS)]
    if repo == ROOT:
        args += ['--exclude=generators/easyfloat/build/']
    paths = subprocess.check_output(args, text=True).split('\0')
    unexpected = [name for name in paths if name and name not in allowed and
        (not root_scope or selected(name)) and
        (Path(name).suffix in UNTRACKED_SOURCE_SUFFIXES or Path(name).name in {'Makefile', 'makefile'})]
    assert not unexpected, f'Untracked build source in {repo}: {unexpected[:30]}'

def root_sources():
    modes = source_modes()
    assert modes == LOCK['source_modes'], 'Tracked build input file set/modes differ from source lock'
    assert set(modes) == set(LOCK['source_hashes']), 'Manifest file/mode sets differ'
    verify(ROOT, LOCK['source_hashes'])
    for name, mode in modes.items():
        path = ROOT / name
        actual = '120000' if path.is_symlink() else ('100755' if path.stat().st_mode & 0o111 else '100644')
        assert actual == mode, 'Working-tree mode mismatch: ' + name
    reject_untracked_sources(ROOT, root_scope=True)

def sources():
    root_sources()
    verify(ROOT / 'fpga/fpga-shells', LOCK['shell_hashes'])
    head = subprocess.check_output(['git', '-C', str(ROOT / 'fpga/fpga-shells'), 'rev-parse', 'HEAD'], text=True).strip()
    assert head == LOCK['shell_commit'], 'Wrong shell revision'
    shell_changes = subprocess.check_output(['git', '-C', str(ROOT / 'fpga/fpga-shells'), 'diff', 'HEAD', '--name-only'], text=True).splitlines()
    assert all(p in LOCK['shell_hashes'] for p in shell_changes), 'Unrecorded shell source changes'
    reject_untracked_sources(ROOT / 'fpga/fpga-shells', allowed=LOCK['shell_hashes'])
    # Gitlinks pin revisions; diff checks additionally reject dirty tracked inputs.
    sub = subprocess.check_output(['git', '-C', str(ROOT), 'submodule', 'status', '--recursive', 'generators', 'fpga/fpga-shells', 'tools/cde', 'tools/dsptools', 'tools/firrtl2', 'tools/tapeout', 'tools/fixedpoint', 'tools/rocket-dsp-utils', 'tools/DRAMSim2'], text=True)
    unused_nested = {'generators/constellation/espresso', 'generators/cva6/src/main/resources/cva6/vsrc/cva6', 'tools/dsptools/fixedpoint', 'tools/rocket-dsp-utils/tools/dsptools', 'tools/rocket-dsp-utils/tools/rocket-chip'}
    bad = [line for line in sub.splitlines() if not line.startswith(' ') and not (line.startswith('-') and line.split()[1] in unused_nested)]
    assert not bad, 'Uninitialized or wrong build dependency: ' + repr(bad)
    for line in sub.splitlines():
        if line.startswith('-'):
            continue
        path = line.split()[1]
        if path == 'fpga/fpga-shells':
            continue
        changes = subprocess.check_output(['git', '-C', str(ROOT / path), 'diff', 'HEAD', '--name-only', '--ignore-submodules=all'], text=True).splitlines()
        assert not changes, f'Dirty build dependency {path}: {changes}'
        reject_untracked_sources(ROOT / path)

    # FireSim target-side libraries are compiled, but unrelated platform nested
    # modules are not required by this FPGA/simulator build.
    firesim = ROOT / 'sims/firesim'
    expected = subprocess.check_output(['git', '-C', str(ROOT), 'ls-tree', 'HEAD', '--', 'sims/firesim'], text=True).split()[2]
    actual = subprocess.check_output(['git', '-C', str(firesim), 'rev-parse', 'HEAD'], text=True).strip()
    assert actual == expected, 'Wrong FireSim revision'
    changes = subprocess.check_output(['git', '-C', str(firesim), 'diff', 'HEAD', '--name-only', '--ignore-submodules=all'], text=True).splitlines()
    assert not changes, 'Dirty FireSim build dependency: ' + repr(changes)
    # Scan the two actual libraries, not platform/tool output directories.
    for subdir in ('sim/firesim-lib', 'sim/midas/targetutils'):
        library = firesim / subdir
        assert library.is_dir(), 'Missing FireSim build library: ' + subdir
        reject_untracked_sources(library)

def run():
    sources()
    record = json.loads((OUT / 'build.json').read_text())
    verify(ROOT, record['driver_hashes'])
    assert record['lock_sha256'] == sha(ASSETS / 'source-lock.json')
    assert record['generator_sha256'] == sha(OUT / 'generator.jar')
    verify(OUT / 'shell', LOCK['shell_hashes'])
    assert {str(p.relative_to(OUT / 'shell')) for p in (OUT / 'shell').rglob('*') if p.is_file()} == set(LOCK['shell_hashes']), 'Unexpected shell snapshot files'
    return record

def seal(stage):
    run()
    if stage == 'units':
        log = (OUT / 'logs/unit.log').read_text()
        for marker in ['PASS: FPMac delay equivalence', 'PASS: PE EXP2 scans', 'All tests passed.', 'Tests: succeeded 5, failed 0']:
            assert marker in log, marker
        write(OUT / 'formal/source-hashes.json', {p:h for p,h in LOCK['source_hashes'].items() if p.endswith('.scala')})
        paths = [OUT / 'logs/unit.log', OUT / 'score-events.json'] + list((OUT / 'formal').rglob('*.sv')) + [OUT / 'formal/source-hashes.json']
    elif stage == 'formal':
        proofs = json.loads((OUT / 'formal/results.json').read_text())
        assert {p['proof_type'] for p in proofs} == {'macseq', 'cmpeq', 'cmpseq'}
        assert all(p['all_inputs_equivalent'] and p['exit_code'] == 0 for p in proofs)
        paths = [p for p in (OUT / 'formal').rglob('*') if p.is_file()]
    elif stage.startswith('sim'):
        size = int(stage[3:])
        assert size in (4, 8, 16)
        for seq in (size, size * 2):
            result = json.loads((OUT / stage / f'seq{seq}/result.json').read_text())
            assert result['bitwise_equal'] and result['actual_sha256'] == result['expected_sha256']
        paths = [p for p in (OUT / stage).rglob('*') if p.is_file() and (p.suffix in ('.json','.npy','.bin','.elf','.log','.sv','.v') or p.name == 'simulator')]
    elif stage == 'elaborate':
        paths = [p for p in (OUT / 'A100/fpga').rglob('*') if p.is_file()]
        configs = list((OUT / 'A100/fpga').glob('*/*.FSAConfig.json'))
        assert len(configs) == 1 and json.loads(configs[0].read_text()) == json.loads((ASSETS / 'FSAConfig.json').read_text())
    else:
        raise ValueError(stage)
    assert paths
    write(OUT / f'{stage}.passed.json', {'build_sha256': sha(OUT / 'build.json'), 'files': {str(p.relative_to(OUT)): sha(p) for p in paths}})

def gate():
    run()
    for stage in ['units', 'formal', 'sim4', 'sim8', 'sim16', 'elaborate']:
        proof = json.loads((OUT / f'{stage}.passed.json').read_text())
        assert proof['build_sha256'] == sha(OUT / 'build.json')
        verify(OUT, proof['files'])

def physical():
    run()
    obj, = (OUT / 'A100/fpga').glob('*/obj')
    props = dict(line.split('=', 1) for line in (obj / 'audit/run_properties.txt').read_text().splitlines())
    assert props['part'] == 'xcvu37p_CIV-fsvh2892-2L-e' and float(props['dut_period']) == 10
    assert float(props['dut_setup_wns']) >= 0 and float(props['dut_hold_whs']) >= 0
    timing = (obj / 'report/timing.txt').read_text()
    match = re.search(r'WNS\(ns\).*?\n\s*-+[^\n]*\n\s*([^\n]+)', timing, re.S)
    assert match, 'Missing timing summary'
    values = list(map(float, match[1].split()))
    assert len(values) == 12 and all(values[i] >= 0 for i in (0, 4, 8)), values
    assert all(values[i] == 0 for i in (1, 2, 5, 6, 9, 10)), values
    route = (obj / 'report/route_status.txt').read_text()
    counts = [int(re.search(re.escape(k) + r'\.+\s*:\s*(\d+)', route)[1]) for k in ['# of routable nets', '# of fully routed nets', '# of nets with routing errors']]
    assert counts[0] == counts[1] and counts[2] == 0, counts
    bus = (obj / 'audit/bus_skew.txt').read_text()
    slacks = [float(v) for v in re.findall(r'Slack\s*(?:\((?:MET|VIOLATED)\))?\s*:\s*([-\d.]+)ns', bus)]
    assert slacks and min(slacks) >= 0, 'Missing/failing bus skew'
    drc = (obj / 'report/drc.txt').read_text()
    assert not re.search(r'\|\s*(?:Error|Critical Warning)\s*\|', drc), 'DRC error/critical'
    assert (obj / 'audit/cdc.txt').is_file() and (obj / 'audit/check_timing.txt').is_file()
    write(OUT / 'physical.passed.json', {'constrained_sta_passed': True, 'board_signoff': False, 'cdc_review_required': True,
          'dut': props, 'timing_summary': values, 'route': counts, 'bus_skew_worst_ns': min(slacks),
          'checkpoint_sha256': sha(obj / 'post_route.dcp')})

if __name__ == '__main__':
    action = sys.argv[1]
    if action == 'sources': sources()
    elif action == 'record':
        sources()
        drivers = list((ROOT / 'scripts/nm37-a100').glob('*')) + [ROOT / 'fpga/scripts/m1_a100_bitstream.tcl']
        write(OUT / 'build.json', {'root_commit': subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip(),
              'lock_sha256': sha(ASSETS / 'source-lock.json'), 'generator_sha256': sha(OUT / 'generator.jar'),
              'configuration': LOCK['configuration'], 'driver_hashes': {str(p.relative_to(ROOT)):sha(p) for p in drivers if p.is_file()}})
    elif action == 'run': run()
    elif action == 'seal': seal(sys.argv[2])
    elif action == 'gate': gate()
    elif action == 'physical': physical()
    else: raise ValueError(action)
    print(f'PASS: {action}')
