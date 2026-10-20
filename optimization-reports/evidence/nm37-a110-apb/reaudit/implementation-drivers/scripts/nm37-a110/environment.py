"""Record tool executables, versions and explicitly relevant build settings."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

root = Path(__file__).resolve().parents[2]
out = Path(os.environ['A110_OUT'])
def digest(p):
    h = hashlib.sha256()
    with p.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

tools = {}
commands = {'java': ['java', '-version'], 'firtool': ['firtool', '--version'],
            'verilator': ['verilator', '--version'], 'vivado': ['vivado', '-version'],
            'cc': [os.environ.get('CC', 'gcc'), '--version'],
            'cxx': [os.environ.get('CXX', 'g++'), '--version'],
            'path_gxx': ['g++', '--version'], 'ld': ['ld', '--version'],
            'python': [os.environ.get('FSA_PYTHON', str(root / 'generators/fsa/python/.venv/bin/python')), '--version'],
            'yosys': [os.environ.get('YOSYS', 'yowasp-yosys'), '-V']}
for name, argv in commands.items():
    executable = shutil.which(argv[0])
    if executable is None:
        raise RuntimeError(f'Missing tool: {argv[0]}')
    actual = Path(executable).resolve()
    version = subprocess.check_output(argv, text=True, stderr=subprocess.STDOUT)
    tools[name] = dict(command=argv, executable=str(actual), sha256=digest(actual), version=version)
libs = {}
for name in ('libfesvr.a', 'libriscv.so'):
    p = Path(os.environ['RISCV']) / 'lib' / name
    libs[str(p)] = digest(p)
keys = ['RISCV', 'CC', 'CXX', 'PATH', 'LD_LIBRARY_PATH', 'JAVA_TOOL_OPTIONS',
        'USE_CHISEL6', 'CHISEL_FIRTOOL_PATH', 'YOSYS', 'FSA_PYTHON', 'PYTHONPATH', 'A110_JOBS', 'COURSIER_CACHE', 'XDG_CACHE_HOME', 'XDG_RUNTIME_DIR']
record = dict(tools=tools, libraries=libs, environment={k: os.environ.get(k) for k in keys})
(out / 'logs/environment.json').write_text(json.dumps(record, indent=2) + '\n')
print('Recorded build tool identity and compiler/runtime environment')
