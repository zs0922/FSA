#!/usr/bin/env python3
"""Create a transfer manifest from existing artifacts; does not certify P&R or program a board."""
import argparse
from pathlib import Path
import sys
sys.dont_write_bytecode = True
from board_test import CLOCKS, sha, software_files, save, validate_manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, required=True, help='New JSON at transfer root; all payload paths must be below its parent')
    p.add_argument('--configuration-id', choices=CLOCKS, required=True)
    p.add_argument('--hardware-source-commit', required=True)
    p.add_argument('--build-id', required=True)
    p.add_argument('--source-identity-kind', choices=('built_commit', 'reconstructed_source_snapshot'), required=True)
    p.add_argument('--bitstream', type=Path, required=True)
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--software', type=Path, required=True)
    args = p.parse_args()
    if args.out.exists():
        raise ValueError('Refusing to overwrite manifest')
    root = args.out.parent.resolve(strict=True)
    def relative(path):
        return path.resolve(strict=True).relative_to(root).as_posix()
    manifest = {'schema_version': 2, 'configuration_id': args.configuration_id,
                'clock_mhz': CLOCKS[args.configuration_id], 'hardware_source_commit': args.hardware_source_commit,
                'build_id': args.build_id, 'source_identity_kind': args.source_identity_kind,
                'bitstream': {'path': relative(args.bitstream), 'sha256': sha(args.bitstream)},
                'config': {'path': relative(args.config), 'sha256': sha(args.config)},
                'software': {'path': relative(args.software), 'files': software_files(args.software),
                             'easyfloat': {'path': relative(args.software / '../../easyfloat'),
                                           'files': software_files(args.software / '../../easyfloat')}}}
    save(args.out, manifest)
    try:
        validate_manifest(args.out)
    except Exception:
        args.out.unlink()
        raise
    print(args.out)


if __name__ == '__main__':
    main()
