#!/usr/bin/env python3
"""Exact finite-cone expansion of Yosys JSON; no assumptions or restrictions."""
import argparse
import collections
import hashlib
import json
from pathlib import Path

# Explicitly limited to the combinational RTLIL types required by this miter,
# plus $alu (a multi-output combinational cell exercised by the semantic fixtures).
COMBINATIONAL = frozenset('''$mux $and $or $not $sub $add $reduce_or $logic_not
$shl $lt $eq $reduce_and $gt $xor $shr $pmux $mul $ne $alu'''.split())
SEQUENTIAL = frozenset(('$dff', '$sdff'))

def expand(old, targets, time, require_no_initial=True):
    assert time >= 0
    clock = old['ports']['clock']['bits']
    assert len(clock) == 1
    for name, c in old['cells'].items():
        assert c['type'] in COMBINATIONAL | SEQUENTIAL | {'$scopeinfo'}, (name, c['type'])
        if c['type'] in SEQUENTIAL:
            assert c['connections']['CLK'] == clock, ('unsupported clock', name)
            assert int(c['parameters']['CLK_POLARITY'], 2) == 1, ('negedge', name)
    new = dict(attributes={}, ports={}, cells={}, netnames={})
    drivers, primary = {}, {}
    for name, p in old['ports'].items():
        if p['direction'] == 'input':
            for i, b in enumerate(p['bits']):
                assert b not in primary
                primary[b] = (name, i)
    for name, c in old['cells'].items():
        for p, direction in c['port_directions'].items():
            if direction == 'output':
                for i, b in enumerate(c['connections'][p]):
                    if isinstance(b, int):
                        assert b not in drivers, ('multiple drivers', b, name)
                        drivers[b] = (name, p, i)
    nextbit = 2
    memo, done, initial, input_refs = {}, set(), [], []
    def alloc():
        nonlocal nextbit
        b = nextbit
        nextbit += 1
        return b
    def bits(bs, t):
        return [bit(b, t) for b in bs]
    def port(name, bs):
        new['ports'][name] = dict(direction='input', bits=bs)
        new['netnames'][name] = dict(hide_name=0, bits=bs, attributes={})
    def bit(b, t):
        if isinstance(b, str):
            assert b in ('0', '1'), ('undefined constant', b)
            return b
        k = (b, t)
        if k in memo:
            return memo[k]
        if b in primary:
            name, i = primary[b]
            out = alloc()
            memo[k] = out
            port(f'{name}_bit{i}_t{t}', [out])
            input_refs.append((name, i, t))
            return out
        name, p, i = drivers[b]
        c = old['cells'][name]
        typ, cn = c['type'], c['connections']
        if typ in SEQUENTIAL:
            assert p == 'Q'
            if t == 0:
                out = alloc()
                memo[k] = out
                pname = f'initial_{b}'
                port(pname, [out])
                initial.append((b, pname))
                return out
            d = bit(cn['D'][i], t-1)
            if typ == '$dff':
                memo[k] = d
                return d
            sel = bit(cn['SRST'][0], t-1)
            pol = int(c['parameters']['SRST_POLARITY'], 2)
            assert pol in (0, 1)
            const = c['parameters']['SRST_VALUE'][::-1][i]
            assert const in ('0', '1')
            out = alloc()
            memo[k] = out
            new['cells'][f'srst_{b}_t{t}'] = dict(hide_name=0, type='$mux', parameters={'WIDTH':'1'}, attributes={}, port_directions={'A':'input','B':'input','S':'input','Y':'output'}, connections={'A':[d if pol else const], 'B':[const if pol else d], 'S':[sel], 'Y':[out]})
            return out
        assert typ in COMBINATIONAL
        ck = (name, t)
        if ck not in done:
            done.add(ck)
            # Allocate ALL output ports before recursive input traversal. This
            # preserves fanout, aliasing and shared cells with multiple outputs.
            for op, direction in c['port_directions'].items():
                if direction == 'output':
                    for ob in cn[op]:
                        if isinstance(ob, int):
                            memo[(ob,t)] = alloc()
            clone = json.loads(json.dumps(c))
            clone['attributes'] = {}
            clone['connections'] = {cp:bits(v,t) for cp,v in cn.items()}
            new['cells'][f'{name}_t{t}'] = clone
        return memo[k]
    for name, target in targets.items():
        bs = bits(target, time)
        new['ports'][name] = dict(direction='output', bits=bs)
        new['netnames'][name] = dict(hide_name=0, bits=bs, attributes={})
    if require_no_initial:
        assert not initial, ('initial-state leaves remain', initial)
    assert all(c['type'] in COMBINATIONAL for c in new['cells'].values())
    grouped = collections.Counter(f'{n}@t{t}' for n,i,t in input_refs)
    report = dict(time=time, initial_state_leaves=initial, free_input_groups=dict(sorted(grouped.items())), input_constraints=[], reset_constraints=[], cutpoint_assumptions=[], cells=len(new['cells']), inputs=len(input_refs), allowed_combinational_types=sorted(COMBINATIONAL), actual_combinational_types=sorted({c['type'] for c in new['cells'].values()}), clock_check='All original DFF/SDFF CLK pins exactly match top clock, positive edge', scope='Exact target outputs after the specified rising clocks from arbitrary state. Empty initial-state cone establishes the identity for every later window.')
    return new, report

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    p = args.work.resolve()
    old = json.loads((p/'structure.json').read_text())['modules']['A70AccumulatorMiter']
    ne_cells = [c for c in old['cells'].values() if c['type']=='$ne']
    assert len(ne_cells) == 1
    new, report = expand(old, {'io_mismatch':old['ports']['io_mismatch']['bits'], 'data_mismatch':ne_cells[0]['connections']['Y']}, 3)
    assert report['free_input_groups'] == {'io_a@t0':32, 'io_b@t0':32, 'io_c@t0':32, 'io_exp2@t0':1, 'reset@t0':1, 'reset@t1':1, 'reset@t2':1}
    report.update(source_rtl_sha256=hashlib.sha256((p/'A70AccumulatorMiter.sv').read_bytes()).hexdigest(), source_json_sha256=hashlib.sha256((p/'structure.json').read_bytes()).hexdigest())
    (p/'unrolled.json').write_text(json.dumps({'creator':'exact temporal expansion from Yosys JSON', 'modules':{'A70AccumulatorMiterUnrolled':new}}, indent=2)+'\n')
    (p/'unroll-audit.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__ == '__main__':
    main()
