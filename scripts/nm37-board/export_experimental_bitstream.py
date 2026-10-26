#!/usr/bin/env python3
"""Export a routed checkpoint for board experiments, without release-review seals."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone

TOP = 'chipyard.fpga.nm37.NM37FPGATestHarness.EmptyNM37Config'
FREQUENCIES = {'A70-P3': ('A70', 70), 'A100': ('A100', 100), 'A110-APB55': ('A110', 110)}


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build-out', type=Path, required=True)
    p.add_argument('--profile', choices=FREQUENCIES, required=True)
    p.add_argument('--out', type=Path, required=True, help='New experiment output directory; never overwritten')
    p.add_argument('--vivado', default='vivado')
    a = p.parse_args()
    root, out = a.build_out.resolve(), a.out.resolve()
    cfg, mhz = FREQUENCIES[a.profile]
    obj = root / cfg / 'fpga' / TOP / 'obj'
    dcp = obj / 'post_route.dcp'
    exe = shutil.which(a.vivado)
    if not exe:
        p.error('Vivado not found; source settings64.sh or specify --vivado /absolute/path/bin/vivado')
    if not dcp.is_file() or not (root/'build.json').is_file():
        p.error(f'Missing routed checkpoint or build.json under {root}; complete implementation first')
    if out.exists():
        p.error(f'Output already exists: {out}; preserve it and select a new --out')
    script = Path(__file__).with_name('export_experimental_bitstream.tcl')
    if not script.is_file():
        p.error(f'Missing companion Tcl: {script}')
    out.mkdir(parents=True)
    evidence = out/'evidence'
    evidence.mkdir()
    records = []
    candidates = list(obj.glob('audit/*.txt')) + list(obj.glob('report/*.txt'))
    candidates += [root/n for n in ['build.json', 'source-lock.json', 'physical.passed.json',
                  'physical.failed.json', 'cdc-review.required.json', 'cdc-review.json',
                  'cdc-constraint-review.json']]
    candidates += list(root.glob('*.passed.json'))
    candidates += list(obj.parent.glob('*.FSAConfig.json'))
    for f in sorted(set(candidates)):
        if f.is_file():
            target = evidence/f.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, target)
            records.append({'source': str(f), 'copy': str(target.relative_to(out)), 'sha256': sha(target)})
    shutil.copyfile(script, out/'export.tcl')
    manifest = {'purpose': 'EXPERIMENTAL_BOARD_TEST', 'release_approved': False,
                'review_status': 'NOT_CLOSED_BY_THIS_EXPORT', 'profile': a.profile,
                'created_utc': datetime.now(timezone.utc).isoformat(),
                'build_out': str(root), 'dcp': str(dcp), 'dcp_sha256': sha(dcp),
                'evidence': records, 'status': 'running', 'vivado': exe,
                'export_tcl_sha256': sha(out/'export.tcl'), 'exporter_sha256': sha(Path(__file__))}
    def save():
        (out/'experiment.json').write_text(json.dumps(manifest, indent=2)+'\n')
    save()
    (out/'EXPERIMENTAL.txt').write_text(
        'Board experiment only. CDC/constraint review is NOT declared closed.\n'
        'No release acceptance seal is created. See experiment.json and evidence/.\n')
    bit = out/f'NM37_{a.profile}_EXPERIMENTAL.bit'
    print(f'START: {a.profile}; experimental export, not release approval.\nLog: {out / "vivado.log"}', flush=True)
    with (out/'console.log').open('w') as log:
        result = subprocess.run([exe, '-mode', 'batch', '-source', str(out/'export.tcl'),
            '-log', str(out/'vivado.log'), '-journal', str(out/'vivado.jou'),
            '-tclargs', str(dcp), str(out), str(mhz), str(bit)], cwd=out,
            stdout=log, stderr=subprocess.STDOUT)
    unchanged = sha(dcp) == manifest['dcp_sha256']
    complete = result.returncode == 0 and bit.is_file() and bit.stat().st_size > 0 and unchanged
    manifest.update(status='exported' if complete else 'failed', exit_code=result.returncode,
                    dcp_unchanged=unchanged)
    if complete:
        digest = sha(bit)
        manifest.update(bitstream=str(bit), bitstream_sha256=digest)
        (out/'SHA256SUMS').write_text(f'{digest}  {bit.name}\n')
    save()
    if not complete:
        print(f'ERROR: export failed; preserve {out}. Inspect console.log / vivado.log. No acceptance seal written.', file=sys.stderr)
        return 1
    print(f'EXPORTED (experimental): {bit}\nSHA256: {digest}\nCDC/release review remains separate.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
