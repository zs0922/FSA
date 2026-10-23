#!/usr/bin/env python3
"""Build/verify/extract a portable NM37 board-test package; never access hardware."""
import argparse
import gzip
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile
import os
import sys
sys.dont_write_bytecode = True

from board_test import CLOCKS, digest_ok, save, sha, software_files, validate_manifest


RESERVED = {'package.json', 'SHA256SUMS'}


def relative_name(name):
    if not isinstance(name, str) or not name or '\\' in name or '\n' in name or '\r' in name:
        raise ValueError('Invalid package relative path')
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ('..', '.') for part in path.parts) or path.as_posix() != name:
        raise ValueError('Package path must be normalized and relative')
    return name


def copy_checked(item, destination):
    if set(item) != {'path', 'sha256'} or not digest_ok(item['sha256']):
        raise ValueError('Artifact requires path and expected SHA-256')
    source = Path(item['path']).resolve(strict=True)
    if not source.is_file() or sha(source) != item['sha256']:
        raise ValueError(f'Input artifact identity mismatch: {source.name}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open('rb') as src, destination.open('xb') as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
    if sha(destination) != item['sha256']:
        raise ValueError('Source changed during package copy')


def inventory(root):
    result = {}
    for directory, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in ('.venv', '__pycache__'))
        for dirname in dirs:
            if (Path(directory) / dirname).is_symlink():
                raise ValueError('Package directory symlinks are prohibited')
        for filename in sorted(names):
            path = Path(directory) / filename
            if path.is_symlink() or not path.is_file():
                raise ValueError('Only regular package files are permitted')
            name = path.relative_to(root).as_posix()
            relative_name(name)
            if name not in RESERVED:
                result[name] = {'sha256': sha(path), 'bytes': path.stat().st_size}
    return result


def build(spec_path, output):
    spec = json.loads(Path(spec_path).read_text())
    if set(spec) - {'schema_version', 'software_root', 'configurations', 'attachments'} or spec.get('schema_version') != 1:
        raise ValueError('Package spec schema mismatch')
    configs = spec.get('configurations')
    if not isinstance(configs, list) or not configs:
        raise ValueError('At least one real configuration is required')
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError('Package destination must not exist')
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix='.' + output.name + '-', dir=output.parent))
    try:
        source = Path(spec['software_root']).resolve(strict=True)
        trees = [('generators/fsa/python', 'software/generators/fsa/python'),
                 ('generators/easyfloat', 'software/generators/easyfloat')]
        source_records = {}
        for source_name, target_name in trees:
            src = source / source_name
            files = software_files(src)
            if not files:
                raise ValueError(f'Missing software tree {source_name}')
            for name, digest in files.items():
                copy_checked({'path': str(src / name), 'sha256': digest}, tmp / target_name / name)
            source_records[target_name] = {'path': target_name, 'files': files}
        software = dict(source_records['software/generators/fsa/python'])
        software['easyfloat'] = source_records['software/generators/easyfloat']
        manifests = []
        ids = set()
        for cfg in configs:
            if set(cfg) != {'configuration_id', 'hardware_source_commit', 'build_id', 'source_identity_kind', 'bitstream', 'config'}:
                raise ValueError('Configuration spec keys mismatch')
            identity = cfg['configuration_id']
            if identity not in CLOCKS or identity in ids:
                raise ValueError('Unknown or duplicate configuration')
            ids.add(identity)
            if not digest_ok(cfg['hardware_source_commit'], 40) or not re.fullmatch(r'[A-Za-z0-9_.:-]+', cfg['build_id']):
                raise ValueError('Full hardware commit and portable non-path build_id required')
            manifest = {'schema_version': 2, 'configuration_id': identity, 'clock_mhz': CLOCKS[identity],
                        'hardware_source_commit': cfg['hardware_source_commit'], 'build_id': cfg['build_id'],
                        'source_identity_kind': cfg['source_identity_kind'],
                        'software': software}
            for kind, name in [('bitstream', f'bitstreams/{identity}.bit'), ('config', f'configs/{identity}.FSAConfig.json')]:
                copy_checked(cfg[kind], tmp / name)
                manifest[kind] = {'path': name, 'sha256': cfg[kind]['sha256']}
            name = f'{identity}.manifest.json'
            save(tmp / name, manifest)
            validate_manifest(tmp / name)
            manifests.append(name)
        tool_root = Path(__file__).resolve().parent
        for path in sorted(tool_root.iterdir()):
            if path.is_file() and (path.suffix == '.py' or path.name in ('README.md', 'git-dependencies.json')):
                copy_checked({'path': str(path), 'sha256': sha(path)}, tmp / 'board-tools' / path.name)
        docs = tool_root / 'docs'
        if docs.exists():
            if docs.is_symlink() or not docs.is_dir():
                raise ValueError('Tool documentation must be a regular directory')
            for path in sorted(docs.glob('*.md')):
                if path.is_symlink() or not path.is_file():
                    raise ValueError('Tool documentation must contain regular Markdown files')
                copy_checked({'path': str(path), 'sha256': sha(path)}, tmp / 'board-tools/docs' / path.name)
        for attachment in spec.get('attachments', []):
            if set(attachment) != {'path', 'sha256', 'destination'}:
                raise ValueError('Invalid attachment specification')
            name = relative_name(attachment['destination'])
            if name.split('/')[0] not in ('evidence', 'source', 'docs'):
                raise ValueError('Attachments belong under evidence/, source/, or docs/')
            copy_checked({k: attachment[k] for k in ('path', 'sha256')}, tmp / name)
        (tmp / 'START_HERE.txt').write_text(
            'NM37 board-test migration package. No board has been programmed or tested by packaging.\n'
            '1. python3 board-tools/package.py verify --root .\n'
            '2. Read board-tools/README.md; confirm hardware and program the selected bit manually.\n'
            '3. uv sync --frozen --project software/generators/fsa/python\n'
            '4. Run board_test.py --dry-run with a selected *.manifest.json, explicit device/BDF and declared loaded SHA.\n'
            '5. Use an external timeout for actual tests. Keep result directories outside this immutable package.\n'
            'Dependencies/tools are not vendored by default; source bundles/submodule archives must be explicit attachments.\n'
            'This package is not a complete offline FPGA build environment. No push is performed.\n')
        info = {'schema_version': 1, 'kind': 'nm37-board-test-transfer', 'manifests': sorted(manifests),
                'hardware_tested_by_packaging': False, 'source_build_environment_included': False,
                'files': inventory(tmp)}
        save(tmp / 'package.json', info)
        names = sorted(list(info['files']) + ['package.json'])
        (tmp / 'SHA256SUMS').write_text(''.join(f'{sha(tmp / name)}  {name}\n' for name in names))
        verify(tmp)
        tmp.rename(output)
        return {'root': str(output), 'configurations': sorted(ids), 'files': len(names),
                'package_sha256': sha(output / 'package.json')}
    except Exception:
        shutil.rmtree(tmp)
        raise


def verify(root):
    root = Path(root).resolve(strict=True)
    info = json.loads((root / 'package.json').read_text())
    if info.get('schema_version') != 1 or info.get('kind') != 'nm37-board-test-transfer':
        raise ValueError('Invalid package identity')
    if inventory(root) != info['files']:
        raise ValueError('Package inventory mismatch (changed, missing or additional file)')
    lines = (root / 'SHA256SUMS').read_text().splitlines()
    sums = {}
    for line in lines:
        digest, name = line.split('  ', 1)
        relative_name(name)
        if not digest_ok(digest) or name in sums or name == 'SHA256SUMS':
            raise ValueError('Malformed or duplicate SHA256SUMS entry')
        sums[name] = digest
    if set(sums) != set(info['files']) | {'package.json'}:
        raise ValueError('SHA256SUMS coverage mismatch')
    for name, digest in sums.items():
        if sha(root / name) != digest:
            raise ValueError(f'Package checksum mismatch: {name}')
    manifests = info['manifests']
    if not manifests or len(set(manifests)) != len(manifests):
        raise ValueError('Missing/duplicate manifests')
    identities = []
    for name in manifests:
        relative_name(name)
        if name not in info['files']:
            raise ValueError('Manifest is not covered by inventory')
        manifest, _, _ = validate_manifest(root / name)
        identities.append(manifest['configuration_id'])
    if len(set(identities)) != len(identities):
        raise ValueError('Duplicate configuration manifests')
    return {'verified': True, 'files': len(sums), 'configurations': identities}


def archive(root, destination):
    root, destination = Path(root).resolve(strict=True), Path(destination)
    verify(root)
    if destination.exists() or destination.is_symlink():
        raise ValueError('Archive must not exist')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('xb') as raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed:
        # PAX preserves original evidence names longer than USTAR's basename
        # limit. Normalize metadata before addfile generates any PAX headers.
        with tarfile.open(fileobj=compressed, mode='w|', format=tarfile.PAX_FORMAT) as tar:
            package_info = json.loads((root / 'package.json').read_text())
            for name in sorted(set(package_info['files']) | RESERVED):
                path = root / name
                if path.is_file():
                    info = tar.gettarinfo(str(path), arcname=path.relative_to(root).as_posix())
                    info.uid = info.gid = info.mtime = 0
                    info.uname = info.gname = ''
                    info.mode = 0o644
                    info.pax_headers = {}
                    with path.open('rb') as f:
                        tar.addfile(info, f)
    return {'archive': str(destination), 'sha256': sha(destination), 'bytes': destination.stat().st_size}


def extract(source, output, expected_sha):
    source, output = Path(source), Path(output).absolute()
    if not digest_ok(expected_sha) or sha(source) != expected_sha:
        raise ValueError('Archive SHA-256 mismatch')
    if output.exists() or output.is_symlink():
        raise ValueError('Extraction destination must not exist')
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix='.' + output.name + '-', dir=output.parent))
    try:
        names = set()
        with tarfile.open(source, 'r:gz') as tar:
            for member in tar:
                name = relative_name(member.name)
                if not member.isfile() or name in names:
                    raise ValueError('Only unique regular archive files allowed; no links/devices/directories')
                names.add(name)
                target = tmp / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as src, target.open('xb') as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
        result = verify(tmp)
        tmp.rename(output)
        return result
    except Exception:
        shutil.rmtree(tmp)
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    create = sub.add_parser('build')
    create.add_argument('--spec', type=Path, required=True)
    create.add_argument('--out', type=Path, required=True)
    create.add_argument('--archive', type=Path)
    check = sub.add_parser('verify')
    check.add_argument('--root', type=Path, required=True)
    unpack = sub.add_parser('extract')
    unpack.add_argument('--archive', type=Path, required=True)
    unpack.add_argument('--sha256', required=True)
    unpack.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    if args.command == 'build':
        result = build(args.spec, args.out)
        if args.archive:
            result['archive'] = archive(args.out, args.archive)
    elif args.command == 'verify':
        result = verify(args.root)
    else:
        result = extract(args.archive, args.out, args.sha256)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
