#!/usr/bin/env python3
"""Initialize only pinned NM37 dependencies in a fresh Git clone. No build/push/reset."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

import source_bundle as s


def require(ok, message):
    if not ok:
        raise ValueError(message)


def clean(repo):
    require(not s.git(repo, 'status', '--porcelain', '--untracked-files=all', '--ignore-submodules=all'),
            'Tracked/untracked changes; initialize dependencies BEFORE shell/overlay/build: ' + str(repo))


def online_update(parent, relative):
    # Scoped to this command. Relative upstream URLs keep Git's native resolution.
    # Do not recurse into unused subtrees or persist URL rewrites globally.
    subprocess.run(['git', '-c', 'submodule.recurse=false',
                    '-c', 'url.https://github.com/.insteadOf=git@github.com:',
                    '-C', str(parent), 'submodule', 'update', '--init', '--checkout', '--', relative],
                   check=True, env={k: v for k, v in os.environ.items() if not k.startswith('GIT_')})


def initialize(root, profile, lock_path, bundles=None, plan=False, updater=online_update):
    root = Path(root).resolve(strict=True)
    require(Path(s.git(root, 'rev-parse', '--show-toplevel')).resolve() == root, 'Not a repository root')
    lock = json.loads(Path(lock_path).read_text())
    require(lock['schema_version'] == 1, 'Unsupported lock')
    selected = lock['profiles'][profile]
    require(s.git(root, 'rev-parse', 'HEAD') == selected['commit'], 'Wrong root commit for profile ' + profile)
    clean(root)
    root_links = s.links(root, selected['commit'])
    require(root_links == selected['root_gitlinks'], 'Root gitlink inventory mismatch')
    pins = lock['dependencies']
    wanted = {n: c for n, c in pins.items() if any(n == p or n.startswith(p + '/') for p in root_links)}
    for n,c in wanted.items():
        s.safe_path(n)
        require(s.commit_ok(c), 'Invalid pin')
    if plan:
        return {'profile':profile, 'root_commit':selected['commit'], 'dependencies':wanted,
                'count':len(wanted), 'plan_only':True}
    dep_root = dep_manifest = None
    if bundles:
        dep_root, dep_manifest = s.load_dependencies(Path(bundles) / 'dependencies.json')
        require(dep_manifest['source_lock_sha256'] == lock['source_lock_sha256'] and
                dep_manifest['source_lock_dependencies'] == pins and
                dep_manifest['excluded_dependencies'] == lock['excluded_dependencies'] and
                dep_manifest['out_of_scope_root_gitlinks'] == lock['out_of_scope_root_gitlinks'],
                'Offline dependency manifest differs from published lock')
    queue = [('.', root_links)]
    restored, omitted = {}, {}
    while queue:
        parent_name, children = queue.pop(0)
        parent = root if parent_name == '.' else root / parent_name
        for rel, commit in sorted(children.items()):
            name = rel if parent_name == '.' else parent_name + '/' + rel
            if name in lock['excluded_dependencies']:
                require(lock['excluded_dependencies'][name]['gitlink'] == commit, 'Excluded pin changed: ' + name)
                omitted[name] = 'explicit recursive exclusion'
                continue
            if name not in pins:
                require(parent_name == '.' and lock['out_of_scope_root_gitlinks'].get(name) == commit,
                        'Unclassified dependency: ' + name)
                omitted[name] = 'outside NM37 build scope'
                continue
            require(pins[name] == commit, 'Gitlink differs from published pin: ' + name)
            target = root / name
            if (target / '.git').exists():
                require(Path(s.git(target, 'rev-parse', '--show-toplevel')).resolve() == target, 'Invalid child repo')
                require(s.git(target, 'rev-parse', 'HEAD') == commit, 'Wrong existing dependency HEAD: ' + name)
                clean(target)
            else:
                require(not target.exists() or (target.is_dir() and not any(target.iterdir())),
                        'Nonempty uninitialized dependency; preserve failure and use a new clone: ' + name)
                if bundles:
                    record = dep_manifest['dependencies'][name]
                    s.clone_bundle(s.checked_bundle(dep_root, record), target, commit, record['refs'])
                else:
                    print('Initializing ' + name + ' @ ' + commit, flush=True)
                    updater(parent, rel)
            require(s.git(target, 'rev-parse', 'HEAD') == commit, 'Fetched wrong dependency: ' + name)
            clean(target)
            s.register_submodule(parent, rel, commit)
            restored[name] = commit
            queue.append((name, s.links(target, commit)))
    require(restored == wanted, 'Dependency traversal incomplete')
    return {'profile':profile, 'root_commit':selected['commit'], 'restored_dependencies':restored,
            'unpopulated_gitlinks':omitted, 'count':len(restored), 'hardware_built':False,
            'mode':'offline-bundles' if bundles else 'git-submodule-update', 'lock_sha256':s.digest(lock_path)}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--profile', choices=['A70-P3','A100','A110-APB55'], required=True)
    p.add_argument('--lock', type=Path, default=Path(__file__).with_name('git-dependencies.json'))
    p.add_argument('--bundles', type=Path, help='Optional existing migration-source-deps directory, no network')
    p.add_argument('--plan', action='store_true', help='Check root pin, print scope without fetching')
    p.add_argument('--report', type=Path, required=True, help='New report outside source checkout')
    a=p.parse_args()
    try:
        require(not a.report.exists(), 'Report must not exist')
        require(not a.report.resolve().is_relative_to(a.root.resolve()), 'Report must be outside source')
        result=initialize(a.root,a.profile,a.lock,a.bundles,a.plan)
        a.report.parent.mkdir(parents=True,exist_ok=True)
        with a.report.open('x') as f: json.dump(result,f,indent=2,sort_keys=True); f.write('\n')
        print(f'{a.profile}: {result["count"]} dependencies; report: {a.report}')
    except (ValueError,RuntimeError,KeyError,OSError,subprocess.CalledProcessError) as exc:
        print('ERROR: '+str(exc),file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':
    sys.exit(main())
