#!/usr/bin/env python3
"""Bind phase results and immutable build drivers to the frozen source identity."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess


def sha(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write(p, obj):
    with p.open('x') as f:
        json.dump(obj, f, indent=2)
        f.write('\n')


def inventory(paths, base, exclude=()):
    result = {}
    for path in paths:
        assert path.exists(), 'Missing required artifact: ' + str(path)
        for p in sorted(path.rglob('*')) if path.is_dir() else [path]:
            if any(p == x or x in p.parents for x in exclude):
                continue
            if p.is_file() and '__pycache__' not in p.parts:
                result[str(p.relative_to(base))] = sha(p)
    assert result, 'Empty inventory'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['record', 'check', 'seal', 'gate'])
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--phase')
    parser.add_argument('paths', nargs='*', type=Path)
    a = parser.parse_intermixed_args()
    out = a.out.resolve()
    manifest = json.loads((out / 'build.json').read_text())
    assert Path(os.environ['A70_SOURCE']).resolve() == Path(manifest['source']).resolve(), 'Output belongs to another checkout'
    assert os.environ['A70_STAGE'] == manifest['stage'], 'Output belongs to another historical stage'
    helper = Path(__file__).resolve().parent
    if a.action == 'record':
        write(out / 'drivers.json', inventory([helper], helper))
        write(out / 'shell.json', inventory([out / 'shell'], out))
        return
    drivers = json.loads((out / 'drivers.json').read_text())
    assert drivers == inventory([helper], helper), 'Build drivers changed'
    assert json.loads((out / 'shell.json').read_text()) == inventory([out / 'shell'], out), 'Frozen shell changed'
    state = json.loads((Path(manifest['source']) / '.nm37-a70/patch-state.json').read_text())
    assert state['identity']['stage'] == manifest['stage'], 'Wrong historical stage'
    lock = out / 'source-lock.json'
    assert sha(lock) == manifest['source_lock_sha256'], 'Source lock changed'
    subprocess.run(['python3', str(helper / 'source_lock.py'), 'verify', '--source', manifest['source'], '--lock', str(lock)], check=True)
    if a.action == 'seal':
        assert a.phase and a.paths
        paths = [p.resolve() for p in a.paths]
        roots = [str(p.relative_to(out)) for p in paths]
        exclusions = [p / 'obj' for p in paths if p.is_dir()] if a.phase == 'elaborate' else []
        write(out / (a.phase + '.passed.json'), {'source_lock_sha256': sha(lock), 'roots': roots,
              'files': inventory(paths, out, exclusions)})
    elif a.action == 'gate':
        phases = ['assembly', 'sim4', 'sim16', 'elaborate']
        if a.phase == 'bitstream':
            phases += ['implement']
        if manifest['stage'] == 'p3':
            phases += ['units', 'formal']
        for phase in phases:
            data = json.loads((out / (phase + '.passed.json')).read_text())
            assert data['source_lock_sha256'] == sha(lock), phase
            paths = [out / name for name in data['roots']]
            exclusions = [p / 'obj' for p in paths if p.is_dir()] if phase == 'elaborate' else []
            assert data['files'] and data['files'] == inventory(paths, out, exclusions), 'Phase artifacts changed: ' + phase
        if a.phase == 'bitstream':
            review = json.loads((out / 'cdc-constraint-review.json').read_text())
            assert review['accepted_for_board_test'] is True and review['board_tested'] is False
            assert review['source_lock_sha256'] == sha(lock)
            assert review['analysis'].strip() and review['known_boundaries'], 'Missing review rationale/boundaries'
            obj, = (out / 'A70/fpga').glob('*/obj')
            required = ['post_route.dcp', 'audit/cdc.txt', 'audit/check_timing.txt',
                        'audit/clock_interaction.txt', 'audit/exceptions.txt']
            assert review['evidence_sha256'] == {name: sha(obj / name) for name in required}, 'Review is for different reports/checkpoint'
        print('PASS: required verification and elaboration bound to the same source')


if __name__ == '__main__':
    main()
