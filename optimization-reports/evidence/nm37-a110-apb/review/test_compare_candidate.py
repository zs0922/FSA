#!/usr/bin/env python3
"""Mutation tests for evidence rejection; uses only owned temporary fixtures."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('comparison', HERE/'compare_candidate.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
records = []


def fixture(base):
    (base/'generator.jar').write_bytes(b'fixture-generator')
    (base/'build.json').write_text(json.dumps({'root_commit': '0'*40,
        'generator_sha256': m.file_sha(base/'generator.jar')}))
    for stage in ('sim4', 'sim8', 'sim16', 'elaborate'):
        (base/stage).mkdir()
        (base/stage/'file.txt').write_bytes(stage.encode())
        (base/f'{stage}.passed.json').write_text(json.dumps({
            'build_sha256': m.file_sha(base/'build.json'),
            'files': {f'{stage}/file.txt': m.file_sha(base/stage/'file.txt')}}))


def rewrite_seal(base, mutate):
    path = base/'sim4.passed.json'
    data = json.loads(path.read_text())
    mutate(data)
    path.write_text(json.dumps(data))


def check(name, mutate, expected_error=None):
    with tempfile.TemporaryDirectory(prefix='fsa-compare-selftest-') as td:
        base = Path(td)
        fixture(base)
        mutate(base)
        try:
            result, _ = m.verify_new_stage_seals(base)
            if expected_error:
                raise RuntimeError(f'Mutation incorrectly accepted: {name}')
            if set(result['stages']) != {'sim4','sim8','sim16','elaborate'}:
                raise RuntimeError('Wrong verified stage set')
            records.append({'test': name, 'result': 'pass'})
        except ValueError as e:
            if not expected_error or expected_error not in str(e):
                raise
            records.append({'test': name, 'result': 'pass', 'rejected_with': str(e)})


check('valid_stage_set', lambda b: None)
check('changed_artifact', lambda b: (b/'sim4/file.txt').write_bytes(b'changed'), 'sealed artifact')
check('missing_artifact', lambda b: (b/'sim4/file.txt').unlink(), 'Missing or escaped')
check('missing_seal', lambda b: (b/'sim4.passed.json').unlink(), 'Missing or escaped')
check('wrong_build_binding', lambda b: rewrite_seal(b, lambda d: d.update(build_sha256='f'*64)), 'build identity')
check('changed_build_json', lambda b: (b/'build.json').write_text((b/'build.json').read_text()+' '), 'build identity')
check('changed_generator', lambda b: (b/'generator.jar').write_bytes(b'changed'), 'Generator/build binding')
check('empty_sealed_file_map', lambda b: rewrite_seal(b, lambda d: d.update(files={})), 'empty or invalid')
check('path_traversal', lambda b: rewrite_seal(b, lambda d: d.update(files={'../outside': 'a'*64})), 'Unsafe evidence path')
check('absolute_path', lambda b: rewrite_seal(b, lambda d: d.update(files={'/tmp/outside': 'a'*64})), 'Unsafe evidence path')
check('duplicate_json_key', lambda b: (b/'sim4.passed.json').write_text('{"files":{},"files":{}}'), 'Duplicate JSON key')
with tempfile.TemporaryDirectory(prefix='fsa-compare-selftest-') as td:
    base = Path(td)
    fixture(base)
    _, manifests = m.verify_new_stage_seals(base)
    extra = base/'sim4/unsealed.sv'
    extra.write_bytes(b'module unexpected; endmodule\n')
    try:
        m.require_sealed_path(base, manifests, 'sim4', extra)
        raise RuntimeError('Unsealed compared input incorrectly accepted')
    except ValueError as e:
        if 'not in stage seal' not in str(e): raise
        records.append({'test':'unsealed_compared_artifact', 'result':'pass','rejected_with':str(e)})
archive = HERE.parent/'src/optimization-reports/evidence/nm37-a110-reset'
old_rtl=m.unique_json(archive/'fsa-a110-rtl-comparison.json')
old_metrics=m.unique_json(archive/'fsa-a110-metrics.json')
history=m.verify_historical_archive(archive,old_rtl,old_metrics)
if history['rtl_fingerprints_bound_to_historical_seals'] != 313 or len(history['cases']) != 6:
    raise RuntimeError('Incomplete historical evidence coverage')
records.append({'test':'real_partial_historical_archive', 'result':'pass',
                'verified_archived_files':len(history['verified_archived_files']),
                'rtl_raw_fingerprints':313,'counter_cases':6,
                'unarchived_compared_files_explicitly_recorded':len(history['compared_files_not_archived'])})
corrupt_metrics=copy.deepcopy(old_metrics)
corrupt_metrics['cases'][0]['counters']['execTime']+=1
try:
    m.verify_historical_archive(archive,old_rtl,corrupt_metrics)
    raise RuntimeError('Wrong historical counter accepted')
except ValueError as e:
    if 'raw log counters' not in str(e): raise
    records.append({'test':'historical_counter_disagrees_with_raw_log','result':'pass','rejected_with':str(e)})
corrupt_metrics=copy.deepcopy(old_metrics)
corrupt_metrics['cases'][0]['input_sha256']['inst.bin']='0'*64
try:
    m.verify_historical_archive(archive,old_rtl,corrupt_metrics)
    raise RuntimeError('Wrong historical input hash accepted')
except ValueError as e:
    if 'metric/seal binding' not in str(e): raise
    records.append({'test':'historical_input_hash_disagrees_with_seal','result':'pass','rejected_with':str(e)})
corrupt_rtl=copy.deepcopy(old_rtl)
corrupt_rtl['board']['files'][0]['raw_sha256']['A110']='0'*64
try:
    m.verify_historical_archive(archive,corrupt_rtl,old_metrics)
    raise RuntimeError('Wrong historical RTL raw hash accepted')
except ValueError as e:
    if 'RTL raw fingerprint/seal' not in str(e): raise
    records.append({'test':'historical_rtl_hash_disagrees_with_seal','result':'pass','rejected_with':str(e)})
report={'passed': True,'test_count':len(records),'script_sha256':m.file_sha(HERE/'compare_candidate.py'),
        'selftest_sha256':m.file_sha(Path(__file__)), 'tests':records}
(HERE/'comparison-selftest.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'passed':True,'tests':len(records),'output':str(HERE/'comparison-selftest.json')}))
