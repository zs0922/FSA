#!/usr/bin/env python3
"""Isolated fixture tests only; never access a real candidate build or archive."""
import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent


def module(name):
    spec = importlib.util.spec_from_file_location(name, HERE/(name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


archive = module('archive_candidate')
verify = module('verify_candidate_archive')
RESULTS = []


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, (dict, list)):
        value = json.dumps(value, indent=2) + '\n'
    path.write_bytes(value.encode() if isinstance(value, str) else value)


def rewrite(path, change):
    data = json.loads(path.read_text())
    change(data)
    write(path, data)


def reject(name, action):
    try:
        action()
    except (ValueError, FileNotFoundError, KeyError, OSError) as error:
        RESULTS.append(dict(test=name, status='pass', rejected_with=str(error)))
        return
    raise AssertionError('Expected rejection: ' + name)


@contextmanager
def fixture():
    with tempfile.TemporaryDirectory(prefix='fsa-archive-fixture-') as tmp:
        base = Path(tmp)
        build, source, review = (base/name for name in ('build04', 'source', 'newreviews'))
        for path in (build, source, review):
            path.mkdir()
        (source/'optimization-reports/evidence').mkdir(parents=True)
        subprocess.run(['git', 'init', '-q', str(source)], check=True, capture_output=True)
        write(source/'.gitignore', '*.log\n*.bin\n')
        selected_sources = ('fpga/src/main/scala/u280/HBMStartup.scala',
            'fpga/src/main/scala/u280/AXIHBM.scala', 'fpga/src/main/scala/u280/TestHarness.scala',
            'fpga/src/main/scala/nm37/TestHarness.scala',
            'fpga/src/test/scala/chipyard/fpga/u280/HBMStartupVerification.scala',
            'fpga/baselines/nm37-a110/FSAConfig.json', 'fpga/baselines/nm37-a110/fpga-shells.patch')
        for name in selected_sources:
            write(source/name, '{}\n' if name.endswith('.json') else '// locked source\n')
        write(source/'scripts/nm37-a110/check.py', '# frozen driver\n')
        write(build/'shell/example.scala', '// shell\n')
        lock = dict(configuration={'freq':110,'apb':55},
                    source_hashes={name:sha(source/name) for name in selected_sources},
                    shell_hashes={'example.scala':sha(build/'shell/example.scala')})
        write(source/'fpga/baselines/nm37-a110/source-lock.json', lock)
        write(build/'generator.jar', b'fixture jar')
        identity = dict(root_commit='fixture-eeb1a1d0', configuration=lock['configuration'],
                        lock_sha256=sha(source/'fpga/baselines/nm37-a110/source-lock.json'),
                        generator_sha256=sha(build/'generator.jar'),
                        driver_hashes={'scripts/nm37-a110/check.py':sha(source/'scripts/nm37-a110/check.py')})
        write(build/'build.json', identity)
        build_hash = sha(build/'build.json')
        for name in ('logs/tools.txt','logs/environment.json','logs/assembly.log','logs/unit.log',
                     'score-events.json','A110/elaboration.log','A110/implementation.log'):
            write(build/name, {} if name.endswith('.json') else ('fixture raw log\n'*20))
        for name in ('formal/mac/check.sv','formal/mac/prove.ys','formal/mac/yosys.log','formal/results.json'):
            write(build/name, [] if name.endswith('.json') else 'module fixture; endmodule\n')
        for name in ('hbm-startup/rtl/HBM.sv','hbm-startup/emission.log','hbm-startup/verilator-build.log',
                     'hbm-startup/simulation.log','hbm-startup/obj/VHBMStartupVerificationHarness','hbm-startup/obj/test.o'):
            write(build/name, 'fixture HBM data\n')
        write(build/'hbm-startup/results.json', dict(passed=True,
              inputs_sha256={str(build/'hbm-startup/rtl/HBM.sv'):sha(build/'hbm-startup/rtl/HBM.sv'),
                             str(source/selected_sources[0]):sha(source/selected_sources[0])},
              binary_sha256=sha(build/'hbm-startup/obj/VHBMStartupVerificationHarness')))
        for size in (4,8,16):
            config = f'chipyard.harness.TestHarness.AXI4FSA{size}X{size}Fp16Config'
            prefix = build/f'sim{size}'
            write(prefix/'simulator', b'fixture simulation binary')
            write(prefix/'build.log', 'simulation compile log\n')
            write(prefix/'generated-src'/config/f'{config}.FSAConfig.json', {'rows':size})
            write(prefix/'generated-src'/config/'gen-collateral/PE.sv','module PE; endmodule\n')
            for seq in (size, size*2):
                for name in ('inst.bin','mem.elf','actual.npy','expected.npy','result.json','test.log'):
                    write(prefix/f'seq{seq}'/name, {} if name.endswith('.json') else ('fixture '+name+'\n'))
        board = build/archive.BOARD
        write(board/'gen-collateral/PE.sv','module PE; endmodule\n')
        write(board/'example.vivado.tcl','# generated IP settings\n')
        write(board/'example.FSAConfig.json', {})
        evidence = {}
        for directory in ('report','audit','reset_replication_audit','hbm_audit'):
            path = board/'obj'/directory/'timing.txt'
            write(path, 'fixture physical report\n'*30)
            evidence[str(path.relative_to(build))] = sha(path)
        for name in ('harnessSysPLL','testxilinx_hbm'):
            write(board/'obj/ip'/name/f'{name}.xci', '<ip/>\n')
        write(board/'obj/post_route.dcp', b'fixture routed checkpoint')
        write(build/'bitstream/NM37_A110.bit', b'fixture bitstream')
        write(build/'bitstream/timing.txt', 'fixture bitstream timing\n')
        write(build/'logs/bitstream.log', 'fixture bitstream log\n')
        seal_sets = {
            'units': [build/'logs/unit.log', build/'score-events.json', build/'formal/mac/check.sv'],
            'formal': [path for path in (build/'formal').rglob('*') if path.is_file()],
            'hbm-startup': [path for path in (build/'hbm-startup').rglob('*') if path.is_file()],
            'elaborate': [path for path in board.rglob('*') if path.is_file() and 'obj' not in path.relative_to(board).parts],
        }
        for size in (4,8,16):
            seal_sets[f'sim{size}'] = [path for path in (build/f'sim{size}').rglob('*') if path.is_file()]
        for stage, paths in seal_sets.items():
            write(build/f'{stage}.passed.json', dict(build_sha256=build_hash,
                files={str(path.relative_to(build)):sha(path) for path in paths}))
        write(build/'physical.passed.json', dict(build_sha256=build_hash, constrained_sta_passed=True,
            board_signoff=False, stage_seals={f'{stage}.passed.json':sha(build/f'{stage}.passed.json') for stage in archive.STAGES},
            checkpoint_sha256=sha(board/'obj/post_route.dcp'), evidence=evidence))
        for name in ('archive_candidate.py','verify_candidate_archive.py','test_archive_candidate.py'):
            shutil.copyfile(HERE/name,review/name)
        for name in ('compare_candidate.py','test_compare_candidate.py'):
            write(review/name, '# fixture independent method\n')
        for name in ('comparison-selftest.json','scanner-validation.json','archive-selftest.json'):
            write(review/name, {'passed':True})
        write(review/'candidate-comparison.json', dict(status='pass',script_sha256=sha(review/'compare_candidate.py'),
            build_identity=dict(build_sha256=build_hash,stages={stage:{'sha256':sha(build/f'{stage}.passed.json')}
              for stage in ('sim4','sim8','sim16','elaborate')})))
        write(review/'newreviews/final-review.json', {'accepted':True})
        write(review/'newreviews/review.log', 'new review original log\n'*20)
        write(review/'newreviews/checkpoint.dcp', b'large review artifact reference')
        args = SimpleNamespace(build=build,source=source,review=review,
            destination=source/'optimization-reports/evidence/nm37-a110-apb',
            gzip_threshold=256,max_binary_bytes=32*1024*1024,review_extra=['newreviews'])
        yield args


def refresh_inventory(root, change):
    path=root/'inventory.json'
    rewrite(path,change)
    (root/'inventory.sha256').write_text(sha(path)+'  inventory.json\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=HERE/'archive-selftest.json')
    args=parser.parse_args()
    with fixture() as f:
        plan=archive.Plan(f).prepare()
        assert not f.destination.exists()
        assert len(plan.cases)==6 and len(plan.seals)==7
        assert plan.git_ignored
        assert ('review','newreviews/final-review.json') in plan.selected
        assert 'review::newreviews/checkpoint.dcp' in plan.large
        RESULTS.append(dict(test='build04_newreviews_complete_readonly_plan',status='pass'))
        plan.write()
        result=verify.verify(f.destination)
        assert result['actual_simulation_cases']==6 and not result['full_build_backup']
        inventory=json.loads((f.destination/'inventory.json').read_text())
        assert any(item['encoding']=='gzip' for item in inventory['files'])
        assert not list(f.destination.rglob('*.dcp')) and not list(f.destination.rglob('*.bit'))
        assert not list(f.destination.rglob('*.jar')) and not list(f.destination.rglob('simulator'))
        assert {item['role'] for item in inventory['local_large_artifacts']} >= {'programming_bitstream','fsa_simulator','hbm_verification_simulator','assembly_generator','vivado_checkpoint','explicit_review_nontext_artifact'}
        RESULTS.append(dict(test='pack_verify_gzip_six_cases_and_binary_references',status='pass'))
        reject('refuse_archive_overwrite',lambda:archive.Plan(f))
        subprocess.run(['git','-C',str(f.source),'add','--',str(f.destination)],check=True,capture_output=True)
        reject('ordinary_git_add_misses_ignored_logs',lambda:verify.verify(f.destination,f.source,True))
        subprocess.run(['git','-C',str(f.source),'add','-f','--',str(f.destination)],check=True,capture_output=True)
        verify.verify(f.destination,f.source,True)
        RESULTS.append(dict(test='force_add_all_bytes_verified_in_index',status='pass'))
        target='optimization-reports/evidence/nm37-a110-apb/README.md'
        blob=subprocess.run(['git','-C',str(f.source),'hash-object','-w','--stdin'],input=b'wrong index bytes',check=True,capture_output=True).stdout.decode().strip()
        subprocess.run(['git','-C',str(f.source),'update-index','--cacheinfo','100644,'+blob+','+target],check=True,capture_output=True)
        reject('index_blob_differs_from_disk',lambda:verify.verify(f.destination,f.source,True))
    for name,value in [('absolute_review_path','/tmp/escape'),('parent_review_escape','../escape'),('missing_new_review','newreviews/missing.json')]:
        with fixture() as f:
            f.review_extra=[value]
            reject(name,lambda:archive.Plan(f).prepare())
            assert not f.destination.exists()
    with fixture() as f:
        (f.review/'newreviews/link.json').symlink_to(f.review/'candidate-comparison.json')
        reject('review_symlink',lambda:archive.Plan(f).prepare())
    with fixture() as f:
        (f.review/'symlink-root').symlink_to(f.review/'newreviews',target_is_directory=True)
        f.review_extra=['symlink-root']
        reject('review_directory_symlink',lambda:archive.Plan(f).prepare())
    for name,mutate in [
        ('changed_sealed_case',lambda f:write(f.build/'sim4/seq4/inst.bin',b'changed')),
        ('missing_case_output',lambda f:(f.build/'sim16/seq32/actual.npy').unlink()),
        ('wrong_comparison_build',lambda f:rewrite(f.review/'candidate-comparison.json',lambda x:x['build_identity'].update(build_sha256='f'*64))),
        ('wrong_stage_build',lambda f:rewrite(f.build/'sim4.passed.json',lambda x:x.update(build_sha256='f'*64))),
        ('seal_parent_escape',lambda f:rewrite(f.build/'sim4.passed.json',lambda x:x['files'].update({'../outside':'a'*64}))),
        ('seal_absolute_path',lambda f:rewrite(f.build/'sim4.passed.json',lambda x:x['files'].update({'/tmp/outside':'a'*64}))),
    ]:
        with fixture() as f:
            mutate(f)
            reject(name,lambda:archive.Plan(f).prepare())
            assert not f.destination.exists()
    with fixture() as f:
        plan=archive.Plan(f).prepare()
        write(f.review/'newreviews/final-review.json',{'changed_after_plan':True})
        reject('changed_after_plan',plan.write)
        assert not f.destination.exists()
    for name,mutate in [
        ('tampered_raw_archive',lambda root:write(root/'build/sim4/seq4/inst.bin',b'tampered')),
        ('tampered_gzip_archive',lambda root:write(next(root.rglob('*.gz')),b'not gzip')),
        ('archive_undeclared_file',lambda root:write(root/'unexpected.txt','extra')),
        ('archive_parent_escape',lambda root:refresh_inventory(root,lambda x:x['files'][0].update(archived_relative_path='../escape'))),
        ('archive_absolute_path',lambda root:refresh_inventory(root,lambda x:x['files'][0].update(archived_relative_path='/tmp/escape'))),
        ('incomplete_archive',lambda root:write(root/'ARCHIVE_INCOMPLETE.json',{'incomplete':True})),
        ('omitted_seal_reference_deleted',lambda root:refresh_inventory(root,lambda x:x['unpackaged_stage_files'].pop())),
        ('large_artifact_reference_changed',lambda root:refresh_inventory(root,lambda x:next(item for item in x['local_large_artifacts'] if item['source_relative_path']=='generator.jar').update(raw_sha256='f'*64))),
    ]:
        with fixture() as f:
            archive.Plan(f).prepare().write()
            mutate(f.destination)
            reject(name,lambda:verify.verify(f.destination))
    with fixture() as f:
        archive.Plan(f).prepare().write()
        path=f.destination/'build/sim4/seq4/inst.bin'
        backup=f.review/'copy.bin'
        shutil.copyfile(path,backup)
        path.unlink()
        path.symlink_to(backup)
        reject('archive_file_symlink',lambda:verify.verify(f.destination))
    with fixture() as f:
        archive.Plan(f).prepare().write()
        first=next(f.destination.rglob('*.gz'))
        raw=first.read_bytes()
        assert raw[4:8]==b'\x00'*4
        RESULTS.append(dict(test='gzip_zero_timestamp',status='pass'))
    result=dict(passed=True,test_count=len(RESULTS),scope='Isolated synthetic fixtures only; no actual candidate build/archive accessed',
        archive_script_sha256=sha(HERE/'archive_candidate.py'),
        verification_script_sha256=sha(HERE/'verify_candidate_archive.py'),
        selftest_sha256=sha(Path(__file__)),tests=RESULTS)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'passed':True,'test_count':len(RESULTS),'output':str(args.output)},indent=2))


if __name__=='__main__':
    main()
