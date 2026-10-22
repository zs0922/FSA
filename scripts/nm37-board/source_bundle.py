#!/usr/bin/env python3
"""Export pinned Git objects and restore NM37 source offline, without shared alternates.

No compiler caches, binaries, toolchain installations or Vivado are included.
Gitlinks outside the supplied build source-lock are explicitly left unpopulated.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def save(path, value):
    path = Path(path)
    pending = path.with_name(path.name + '.pending')
    pending.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    pending.replace(path)


def safe_path(name):
    if not isinstance(name, str) or not name or '\\' in name or '\n' in name or '\r' in name:
        raise ValueError('Invalid relative path')
    p = PurePosixPath(name)
    if p.is_absolute() or p.as_posix() != name or '..' in p.parts or name == '.' or '.git' in p.parts:
        raise ValueError('Unsafe relative path')
    return name


def commit_ok(commit):
    return isinstance(commit, str) and re.fullmatch('[0-9a-f]{40}', commit) is not None


def git(repo, *args, input_text=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0')
    command = ['git', '-c', 'protocol.allow=never', '-c', 'protocol.file.allow=always',
               '-c', 'core.hooksPath=' + os.devnull, '-c', 'submodule.recurse=false',
               '-c', 'gc.auto=0', '-c', 'pack.threads=2', '-c', 'pack.windowMemory=128m']
    if repo is not None:
        command += ['-C', str(repo)]
    command += list(args)
    run = subprocess.run(command, input=input_text, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    if run.returncode:
        raise RuntimeError('git ' + ' '.join(args[:3]) + ': ' + run.stderr.strip())
    return run.stdout.strip()


def links(repo, commit):
    result = {}
    for row in git(repo, 'ls-tree', '-r', '-z', commit).split('\0'):
        if row:
            metadata, name = row.split('\t', 1)
            mode, kind, value = metadata.split()
            if mode == '160000':
                safe_path(name)
                result[name] = value
    return result


def source_repo(path, commit):
    path = Path(path).resolve(strict=True)
    if not commit_ok(commit):
        raise ValueError('Full 40-character pinned commit required')
    if git(path, 'rev-parse', '--is-shallow-repository') != 'false':
        raise ValueError('Shallow sources cannot supply complete standalone history')
    actual = git(path, 'rev-parse', commit + '^{commit}')
    if actual != commit:
        raise ValueError('Pinned commit mismatch')
    return path


def empty_verify(bundle, expected_refs):
    """Verify with an empty object DB, so a prerequisite cannot silently use local objects."""
    bundle = Path(bundle).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='nm37-bundle-verify-') as temp:
        repo = Path(temp) / 'empty.git'
        git(None, 'init', '--bare', str(repo))
        git(repo, 'bundle', 'verify', str(bundle))
    refs = {}
    for line in git(None, 'bundle', 'list-heads', str(bundle)).splitlines():
        commit, ref = line.split(' ', 1)
        refs[ref] = commit
    if refs != expected_refs:
        raise ValueError('Bundle refs differ from manifest')


def export_bundle(sources, output):
    """sources: ref -> (local repository, exact commit). No source repository is mutated."""
    output = Path(output)
    if output.exists():
        raise ValueError('Refusing to overwrite bundle')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.bundle-export-', dir=output.parent) as temp:
        bare = Path(temp) / 'objects.git'
        git(None, 'init', '--bare', str(bare))
        for ref, (repository, commit) in sources.items():
            if not re.fullmatch(r'refs/heads/[A-Za-z0-9_.-]+', ref):
                raise ValueError('Invalid portable branch label')
            repository = source_repo(repository, commit)
            git(bare, 'fetch', '--no-tags', '--no-recurse-submodules', str(repository), commit)
            git(bare, 'update-ref', ref, commit)
        if (bare / 'objects/info/alternates').exists():
            raise ValueError('Export scratch object DB unexpectedly depends on alternates')
        git(bare, 'symbolic-ref', 'HEAD', next(iter(sources)))
        pending = Path(temp) / 'standalone.bundle'
        git(bare, 'bundle', 'create', str(pending.absolute()), *sources.keys())
        expected = {ref: item[1] for ref, item in sources.items()}
        empty_verify(pending, expected)
        pending.rename(output)
    return {'sha256': digest(output), 'bytes': output.stat().st_size, 'refs': expected}


def read_lock(path):
    lock = json.loads(Path(path).read_text())
    pins = lock['dependencies']
    excluded = lock.get('excluded_dependencies', {})
    if not pins or not isinstance(pins, dict):
        raise ValueError('Missing dependency pins')
    for name, commit in pins.items():
        safe_path(name)
        if not commit_ok(commit):
            raise ValueError('Dependency requires full commit')
    for name, item in excluded.items():
        safe_path(name)
        if not commit_ok(item['gitlink']) or not item['reason']:
            raise ValueError('Excluded dependency requires gitlink and reason')
    if set(pins) & set(excluded):
        raise ValueError('Dependency cannot be both included and excluded')
    return lock


def parent_of(name, pins):
    parents = [x for x in pins if name.startswith(x + '/')]
    return max(parents, key=len) if parents else '.'


def select_paths(pins, selection):
    if not selection:
        return set(pins)
    for name in selection:
        if safe_path(name) not in pins:
            raise ValueError('Selected dependency is absent from lock')
    # A selected subtree brings its pinned descendants and all containing repositories.
    return {p for p in pins if any(p == s or p.startswith(s + '/') or s.startswith(p + '/') for s in selection)}


def export_dependencies(source, lock_path, output, selection=None, resume=False):
    source, output = Path(source).resolve(strict=True), Path(output).absolute()
    lock = read_lock(lock_path)
    pins, excluded = lock['dependencies'], lock.get('excluded_dependencies', {})
    selected = select_paths(pins, selection)
    root_head = git(source, 'rev-parse', 'HEAD')
    root_links = links(source, root_head)
    absent = {p: c for p, c in root_links.items() if p not in pins and p not in excluded}
    if output.exists():
        if not resume:
            raise ValueError('Dependency output exists; explicit --resume required')
        manifest = json.loads((output / 'dependencies.json').read_text())
        if manifest['source_lock_dependencies'] != pins or manifest['excluded_dependencies'] != excluded:
            raise ValueError('Resume source-lock pins/exclusions differ')
        records = manifest['dependencies']
    else:
        output.mkdir(parents=True)
        records = {}
    manifest = {'schema_version': 1, 'kind': 'nm37-pinned-dependencies',
                'source_lock_sha256': digest(lock_path), 'lock_root_commit': lock.get('root_commit'),
                'source_repository_head': root_head, 'source_lock_dependencies': pins,
                'excluded_dependencies': excluded, 'out_of_scope_root_gitlinks': absent,
                'dependencies': records, 'complete': False, 'scope': 'subset' if selection else 'full'}
    # A source lock is content evidence; no host paths are copied into the metadata.
    shutil.copyfile(lock_path, output / 'source-lock.json')
    def checkpoint():
        manifest['not_exported_dependencies'] = sorted(set(pins) - set(records))
        manifest['complete'] = set(records) == set(pins)
        save(output / 'dependencies.json', manifest)
    checkpoint()
    by_commit = {record['commit']: record for record in records.values()}
    for index, name in enumerate(sorted(selected, key=lambda x: (x.count('/'), x))):
        commit = pins[name]
        parent = parent_of(name, pins)
        parent_repo = source if parent == '.' else source / parent
        parent_commit = root_head if parent == '.' else pins[parent]
        relative = name if parent == '.' else name[len(parent) + 1:]
        if links(parent_repo, parent_commit).get(relative) != commit:
            raise ValueError(f'Pinned dependency does not match parent gitlink: {name}')
        repository = source_repo(source / name, commit)
        if Path(git(repository, 'rev-parse', '--show-toplevel')).resolve() != repository:
            raise ValueError(f'Dependency is not its own Git repository: {name}')
        bundle_name = f'bundles/{commit}.bundle'
        if name in records:
            record = records[name]
            if record['commit'] != commit or digest(output / record['bundle']) != record['sha256']:
                raise ValueError('Previously exported bundle identity changed')
        else:
            if commit in by_commit:
                prior = by_commit[commit]
                if digest(output / prior['bundle']) != prior['sha256']:
                    raise ValueError('Deduplicated bundle changed')
                bundle = {k: prior[k] for k in ('sha256', 'bytes', 'refs')}
                bundle_name = prior['bundle']
            else:
                bundle = export_bundle({'refs/heads/locked': (repository, commit)}, output / bundle_name)
            records[name] = {'commit': commit, 'parent': parent, 'parent_relative_gitlink': relative,
                             'bundle': bundle_name, **bundle}
            by_commit[commit] = records[name]
        checkpoint()
        print(json.dumps({'exported': name, 'index': index + 1, 'selected': len(selected),
                          'unique_bundles': len(by_commit)}), flush=True)
    return manifest


def export_main(spec_path, output):
    spec = json.loads(Path(spec_path).read_text())
    if set(spec) != {'profiles'} or not spec['profiles']:
        raise ValueError('Main spec requires profiles')
    output = Path(output).absolute()
    if output.exists():
        raise ValueError('Main bundle directory must not exist')
    sources, profiles = {}, {}
    for name, profile in spec['profiles'].items():
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', name) or set(profile) != {'repository', 'revision'}:
            raise ValueError('Invalid main profile')
        repository = Path(profile['repository']).resolve(strict=True)
        commit = git(repository, 'rev-parse', profile['revision'] + '^{commit}')
        source_repo(repository, commit)
        sources['refs/heads/' + name] = (repository, commit)
        profiles[name] = {'commit': commit, 'root_gitlinks': links(repository, commit)}
    output.mkdir(parents=True)
    record = export_bundle(sources, output / 'main.bundle')
    manifest = {'schema_version': 1, 'kind': 'nm37-main-source', 'bundle': 'main.bundle',
                'profiles': profiles, **record}
    save(output / 'main.json', manifest)
    return manifest


def checked_bundle(root, record, verify_objects=True):
    name = safe_path(record['bundle'])
    path = (root / name).resolve(strict=True)
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('Bundle escapes metadata root')
    if digest(path) != record['sha256'] or path.stat().st_size != record['bytes']:
        raise ValueError('Bundle SHA/size mismatch')
    if verify_objects:
        empty_verify(path, record['refs'])
    return path


def clone_bundle(bundle, target, commit, refs):
    target = Path(target)
    if target.exists():
        if not target.is_dir() or any(target.iterdir()):
            raise ValueError('Restore target must be absent or an empty gitlink directory')
        target.rmdir()
    target.parent.mkdir(parents=True, exist_ok=True)
    git(None, 'clone', '--no-checkout', '--no-recurse-submodules', str(bundle), str(target))
    for ref, sha in refs.items():
        git(target, 'update-ref', ref, sha)
    git(target, 'remote', 'remove', 'origin')
    git(target, 'checkout', '--detach', '--force', commit)
    gitdir = Path(git(target, 'rev-parse', '--absolute-git-dir'))
    if (gitdir / 'objects/info/alternates').exists() or git(target, 'remote'):
        raise ValueError('Restored repository unexpectedly has alternates or a remote')
    if git(target, 'rev-parse', 'HEAD') != commit:
        raise ValueError('Restored HEAD mismatch')
    git(target, 'fsck', '--connectivity-only', '--no-dangling')


def load_dependencies(path):
    path = Path(path).resolve(strict=True)
    data = json.loads(path.read_text())
    if data.get('schema_version') != 1 or data.get('kind') != 'nm37-pinned-dependencies':
        raise ValueError('Invalid dependency manifest')
    if digest(path.parent / 'source-lock.json') != data['source_lock_sha256']:
        raise ValueError('Archived source-lock SHA mismatch')
    lock = read_lock(path.parent / 'source-lock.json')
    if lock['dependencies'] != data['source_lock_dependencies'] or lock.get('excluded_dependencies', {}) != data['excluded_dependencies']:
        raise ValueError('Archived source-lock and dependency manifest disagree')
    for name, record in data['dependencies'].items():
        safe_path(name)
        if record['commit'] != data['source_lock_dependencies'][name]:
            raise ValueError('Dependency manifest and source-lock disagree')
    return path.parent, data


def restore_dependencies(manifest_path, output, selection):
    """Independent dependency smoke restore; checks pinned parent relationships when parent is selected."""
    root, data = load_dependencies(manifest_path)
    chosen = select_paths(data['source_lock_dependencies'], selection)
    if not chosen <= set(data['dependencies']):
        raise ValueError('Requested dependencies have not all been exported')
    output = Path(output).absolute()
    if output.exists():
        raise ValueError('Restore destination must not exist')
    output.mkdir(parents=True)
    restored = {}
    for name in sorted(chosen, key=lambda x: (x.count('/'), x)):
        record = data['dependencies'][name]
        if record['parent'] != '.':
            parent = output / record['parent']
            if links(parent, git(parent, 'rev-parse', 'HEAD')).get(record['parent_relative_gitlink']) != record['commit']:
                raise ValueError('Restored parent gitlink mismatch')
        clone_bundle(checked_bundle(root, record), output / name, record['commit'], record['refs'])
        restored[name] = record['commit']
    save(output / 'restore.json', {'kind': 'dependency-only-smoke', 'restored': restored,
                                 'hardware_built': False, 'no_remotes_or_alternates': True})
    return restored


def restore(main_path, dependencies_path, profile, output):
    main_path = Path(main_path).resolve(strict=True)
    main = json.loads(main_path.read_text())
    if main.get('kind') != 'nm37-main-source' or main.get('schema_version') != 1:
        raise ValueError('Invalid main manifest')
    selected = main['profiles'][profile]
    deps_root, deps = load_dependencies(dependencies_path)
    output = Path(output).absolute()
    if output.exists():
        raise ValueError('Restore destination must not exist')
    clone_bundle(checked_bundle(main_path.parent, main), output, selected['commit'], main['refs'])
    root_links = links(output, selected['commit'])
    if root_links != selected['root_gitlinks']:
        raise ValueError('Main profile gitlinks differ from actual tree')
    restored, omitted = {}, {}
    queue = [('.', selected['commit'], root_links)]
    while queue:
        parent, parent_commit, children = queue.pop(0)
        for relative, commit in sorted(children.items()):
            name = relative if parent == '.' else parent + '/' + relative
            if name in deps['excluded_dependencies']:
                excluded = deps['excluded_dependencies'][name]
                if excluded['gitlink'] != commit:
                    raise ValueError('Excluded gitlink changed')
                omitted[name] = excluded
                continue
            if name not in deps['source_lock_dependencies']:
                if parent != '.' or deps['out_of_scope_root_gitlinks'].get(name) != commit:
                    raise ValueError('Unclassified gitlink is not covered by pins or recorded scope exclusions: ' + name)
                omitted[name] = {'gitlink': commit, 'reason': 'Outside supplied NM37 source-lock build scope'}
                continue
            if deps['source_lock_dependencies'][name] != commit:
                raise ValueError('Profile gitlink is not covered by this pinned dependency set')
            record = deps['dependencies'].get(name)
            if record is None:
                raise ValueError('Incomplete dependency export for profile: ' + name)
            clone_bundle(checked_bundle(deps_root, record), output / name, commit, record['refs'])
            restored[name] = commit
            queue.append((name, commit, links(output / name, commit)))
    result = {'profile': profile, 'root_commit': selected['commit'], 'restored_dependencies': restored,
              'unpopulated_gitlinks': omitted, 'no_remotes_or_alternates': True,
              'hardware_built': False, 'toolchains_included': False}
    # Keep report outside the source checkout so exact source status remains reviewable.
    save(output.with_name(output.name + '-restore.json'), result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    dep = sub.add_parser('dependencies')
    dep.add_argument('--source', type=Path, required=True)
    dep.add_argument('--lock', type=Path, required=True)
    dep.add_argument('--out', type=Path, required=True)
    dep.add_argument('--only', action='append')
    dep.add_argument('--resume', action='store_true')
    src = sub.add_parser('main')
    src.add_argument('--spec', type=Path, required=True)
    src.add_argument('--out', type=Path, required=True)
    smoke = sub.add_parser('restore-dependencies')
    smoke.add_argument('--dependencies', type=Path, required=True)
    smoke.add_argument('--out', type=Path, required=True)
    smoke.add_argument('--only', action='append', required=True)
    full = sub.add_parser('restore')
    full.add_argument('--main', type=Path, required=True)
    full.add_argument('--dependencies', type=Path, required=True)
    full.add_argument('--profile', required=True)
    full.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    if args.command == 'dependencies':
        result = export_dependencies(args.source, args.lock, args.out, args.only, args.resume)
        print(json.dumps({'complete': result['complete'], 'exported': len(result['dependencies']),
                          'not_exported': len(result['not_exported_dependencies'])}))
    elif args.command == 'main':
        result = export_main(args.spec, args.out)
        print(json.dumps({'profiles': result['profiles'], 'bundle_sha256': result['sha256']}, indent=2))
    elif args.command == 'restore-dependencies':
        print(json.dumps(restore_dependencies(args.dependencies, args.out, args.only), indent=2))
    else:
        result = restore(args.main, args.dependencies, args.profile, args.out)
        print(json.dumps({'profile': result['profile'], 'root_commit': result['root_commit'],
                          'restored': len(result['restored_dependencies']),
                          'unpopulated_gitlinks': len(result['unpopulated_gitlinks'])}, indent=2))


if __name__ == '__main__':
    main()
