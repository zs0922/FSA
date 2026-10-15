#!/usr/bin/env python3
"""
fir_module_graph.py -- extract the module graph from a FIRRTL (.fir) file.

Zero-invasive: parses the .fir that every Chisel build already emits.
Commands:
  tree   <fir> [--filter SUB]      hierarchy tree with instance multiplicities
  module <fir> NAME [--flow]       ports (+ sibling dataflow) of one module
  dot    <fir> [--filter SUB]      graphviz DOT of the module graph
  json   <fir>                     machine-readable graph (for tooling)
Also usable as a library: parse_fir(path) -> Graph.
"""
import sys, re, json, argparse
from collections import defaultdict

# ---------------- FIRRTL type parser (width computation) ----------------

class Type:
    def __init__(self, kind, width=0, fields=None, n=None, elem=None, flip=False):
        self.kind, self.width, self.fields, self.n, self.elem, self.flip = \
            kind, width, fields, n, elem, flip
    def total_width(self):
        if self.kind == "bundle":
            return sum(f[1].total_width() for f in self.fields)
        if self.kind == "vec":
            return (self.n or 0) * (self.elem.total_width() if self.elem else 0)
        return self.width

def tokenize(t):
    # split respecting {} nesting; keep delimiters
    out, depth, cur = [], 0, ""
    for ch in t:
        if ch == "{":
            depth += 1; out.append(("{",)); cur = ""
        elif ch == "}":
            if cur.strip(): out.append(("id", cur.strip())); cur = ""
            depth -= 1; out.append(("}",))
        elif ch == "," and depth == 1:
            if cur.strip(): out.append(("id", cur.strip())); cur = ""
        else:
            cur += ch
    if cur.strip(): out.append(("id", cur.strip()))
    return out

def parse_type(t):
    t = t.strip()
    m = re.match(r"^(.*)\[(\d+)\]$", t)
    if m and not m.group(1).strip().endswith(">"):
        pass  # e.g. UInt<1>[4] handled below via suffix loop
    # vector suffix (possibly nested)
    while True:
        m = re.match(r"^(.*)\[(\d+)\]$", t)
        if m and (m.group(1).strip().endswith("}") or re.match(r"^(UInt|SInt|Analog)", m.group(1).strip()) or "]" in m.group(1)):
            elem = parse_type(m.group(1))
            return Type("vec", n=int(m.group(2)), elem=elem)
        break
    if t.startswith("{"):
        toks = [x for x in tokenize(t) if x[0] == "id"]
        fields, i = [], 0
        while i < len(toks):
            name = toks[i][1]
            flip = name.startswith("flip ")
            if flip:
                name = name[4:].strip()
            if i + 1 < len(toks) and toks[i + 1][1].startswith(":"):
                # tokenizer glued "name : type" pieces; rejoin
                raw = toks[i][1]
                name, _, tyrest = raw.partition(":")
                name = name.strip()
                flip = name.startswith("flip ")
                if flip: name = name[4:].strip()
                fields.append((name, parse_type(tyrest.strip()), flip))
                i += 1
                continue
            i += 1
        # fallback: re-parse fields by splitting on commas at depth 1
        if not fields:
            body = t[1:-1]
            parts, depth, cur = [], 0, ""
            for ch in body:
                if ch == "{": depth += 1
                if ch == "}": depth -= 1
                if ch == "," and depth == 0:
                    parts.append(cur); cur = ""
                else:
                    cur += ch
            if cur.strip(): parts.append(cur)
            for p in parts:
                p = p.strip()
                flip = p.startswith("flip ")
                if flip: p = p[4:].strip()
                name, _, ty = p.partition(":")
                fields.append((name.strip(), parse_type(ty.strip()), flip))
        return Type("bundle", fields=fields)
    m = re.match(r"^(UInt|SInt|Analog)(?:<(\d+)>)?$", t)
    if m:
        return Type(m.group(1), width=int(m.group(2)) if m.group(2) else 1)
    if t in ("Clock", "Reset", "AsyncReset", "ResetType"):
        return Type(t, width=1)
    return Type("unknown", width=0)

def resolve_path(port_type, path):
    """path: list of components like 'io', 'pe_ctrl[3]', 'bits.mac'.
    Component names walk bundles; '[i]' subindices walk vectors."""
    ty = port_type
    for comp in path:
        if ty is None: return None
        name, _, idx = comp.partition("[")
        idx = idx.rstrip("]")
        if ty.kind == "vec":
            # a bare index onto a vec (component was pure index or name[?])
            ty = ty.elem
            if not name:
                continue
        if ty.kind == "bundle":
            for n, t, f in ty.fields:
                if n == name:
                    ty = t
                    break
            else:
                return None
            # optional subindex into the resolved field
            while idx and ty is not None and ty.kind == "vec":
                ty = ty.elem
                idx = ""
        else:
            return None
    return ty

# ---------------- FIR parser ----------------

class Module:
    __slots__ = ("name", "kind", "loc", "ports", "insts", "connects")
    def __init__(self, name, kind, loc):
        self.name, self.kind, self.loc = name, kind, loc
        self.ports = []      # (dir, name, type_str, type_obj, loc)
        self.insts = []      # (inst_name, module_name, loc)
        self.connects = []   # (lhs, rhs)

class Graph:
    def __init__(self):
        self.modules = {}
        self.top = None

MOD_RE = re.compile(r"^  (public |)(module|extmodule) (\w+) :\s*(?:@\[(.+?)\])?\s*$")
PORT_RE = re.compile(r"^    (input|output) (\w+) : (.*?)(?:\s+@\[(.+?)\])?\s*$")
INST_RE = re.compile(r"^    inst (\w+) of (\w+)\s*(?:@\[(.+?)\])?\s*$")
CONN_RE = re.compile(r"^    connect (.+?), (.+?)(?:\s+@\[.*\])?\s*$")

def parse_fir(path):
    g = Graph()
    cur = None
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            m = MOD_RE.match(line)
            if m:
                cur = Module(m.group(3), m.group(2), m.group(4))
                g.modules[cur.name] = cur
                continue
            if line.startswith("circuit "):
                g.top = line.split()[1].rstrip(":")
                continue
            if cur is None:
                continue
            m = PORT_RE.match(line)
            if m:
                t = parse_type(m.group(3))
                cur.ports.append((m.group(1), m.group(2), m.group(3)[:120], t, m.group(4)))
                continue
            m = INST_RE.match(line)
            if m:
                cur.insts.append((m.group(1), m.group(2), m.group(3)))
                continue
            m = CONN_RE.match(line)
            if m:
                cur.connects.append((m.group(1), m.group(2)))
    return g

# ---------------- analyses ----------------

def hierarchy(g, filt=None):
    """expand from top; returns dict module -> instance count"""
    counts = defaultdict(int)
    def walk(mod, mult):
        counts[mod] += mult
        for inst, child, _ in g.modules[mod].insts:
            if child in g.modules:
                walk(child, mult)
    walk(g.top, 1)
    return counts

def tree_str(g, filt=None, max_depth=99):
    lines = []
    def unique_children(m):
        agg = defaultdict(int)
        for inst, child, _ in m.insts:
            if child in g.modules:
                agg[child] += 1
        return agg
    def walk(mod, mult, depth, prefix, parent_shown):
        if depth > max_depth: return
        m = g.modules[mod]
        # a filter match turns display ON from this node downward
        shown = parent_shown or (filt is not None and filt.lower() in mod.lower())
        if shown:
            pw = sum(p[3].total_width() for p in m.ports)
            lines.append(f"{prefix}{mod} ×{mult}  [{m.kind}, {len(m.ports)} ports/{pw}b IO, {short(m.loc)}]")
        for child, n in unique_children(m).items():
            walk(child, mult * n, depth + 1,
                 prefix + "  " if shown else prefix, shown)
    walk(g.top, 1, 0, "", filt is None)
    return "\n".join(lines)

def short(loc):
    if not loc: return "?"
    return "/".join(loc.split("/")[-2:]).split(" ")[0]

def module_report(g, name, flow=False):
    m = g.modules.get(name)
    if m is None:
        return f"module '{name}' not found"
    out = [f"=== {m.name} ({m.kind}) loc={m.loc} ==="]
    out.append(f"instances ({len(m.insts)}):")
    agg = defaultdict(int)
    for inst, child, loc in m.insts:
        agg[child] += 1
    for child, n in sorted(agg.items()):
        out.append(f"    {n:4d} × {child}")
    out.append(f"ports ({len(m.ports)}):")
    for d, n, ts, t, loc in m.ports:
        out.append(f"    {d:6s} {n:24s} {t.total_width():6d}b  {ts[:80]}{'...' if len(ts)>80 else ''}")
    if flow:
        out.append(sibling_flow(g, m))
    return "\n".join(out)

def sibling_flow(g, m):
    instset = {i for i, c, _ in m.insts}
    portmap = {c: g.modules[c] for i, c, _ in m.insts if c in g.modules}
    edges = defaultdict(lambda: [0, 0])  # (src,dst) -> [nconn, width]
    def split_ref(ref):
        base = ref.split(".")[0].split("[")[0]
        return base, ref
    for lhs, rhs in m.connects:
        lb, lref = split_ref(lhs)
        rb, rref = split_ref(rhs)
        if lb in instset and rb in instset and lb != rb:
            # rhs drives lhs
            if lref.split(".")[1:2] == ["clock"] or rref.split(".")[1:2] == ["clock"]:
                continue
            if "reset" in lref.split(".")[1:2] or "reset" in rref.split(".")[1:2]:
                continue
            e = edges[(rb, lb)]
            e[0] += 1
            w = resolve_width(portmap, rb, rref) or resolve_width(portmap, lb, lref)
            if w: e[1] += w
    out = ["sibling dataflow (instA -> instB: #connects, resolved width):"]
    for (s, d), (n, w) in sorted(edges.items(), key=lambda kv: -kv[1][0]):
        out.append(f"    {s:18s} -> {d:18s}  {n:3d} conn, {w}b")
    return "\n".join(out)

def make_resolver(m):
    def resolve(inst, ref):
        for i, c, _ in m.insts:
            if i == inst:
                child = g_modules.get(c)
                if not child: return None
                parts = [p for p in ref.split(".")[1:]]
                parts = parts  # keep subindex info for resolve_path
                first = parts[0] if parts else None
                for d, n, ts, t, loc in child.ports:
                    if n == first:
                        ty = resolve_path(t, parts[1:])
                        return ty.total_width() if ty else None
        return None
    return resolve

g_modules = {}

# ---------------- DOT / JSON ----------------

def dot_str(g, filt=None):
    counts = hierarchy(g)
    keep = {n for n in g.modules if filt is None or filt.lower() in n.lower() or True}
    lines = ["digraph fir {"]
    shown = set()
    def walk(mod, parent, pmult):
        if mod not in g.modules: return
        if filt and filt.lower() not in mod.lower() and filt.lower() not in (parent or "").lower():
            # still walk children (filter matches subtrees)
            pass
        m = g.modules[mod]
        n = counts.get(mod, 0)
        if filt is None or filt.lower() in mod.lower() or (parent and parent in shown):
            shown.add(mod)
            if parent in shown and parent is not None:
                lines.append(f'  "{parent}" -> "{mod}" [label="×{counts_in(parent, mod)}"];')
        for i, c, _ in m.insts:
            walk(c, mod, n)
    def counts_in(parent, child):
        pm = g.modules[parent]
        return sum(1 for i, c, _ in pm.insts if c == child)
    walk(g.top, None, 1)
    lines.append("}")
    return "\n".join(lines)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["tree", "module", "dot", "json"])
    ap.add_argument("fir")
    ap.add_argument("name", nargs="?", help="module name for 'module'")
    ap.add_argument("--filter", default=None)
    ap.add_argument("--flow", action="store_true")
    args = ap.parse_args()

    g = parse_fir(args.fir)
    global g_modules
    g_modules = g.modules
    if args.cmd == "tree":
        print(tree_str(g, args.filter))
    elif args.cmd == "module":
        m = g.modules.get(args.name)
        if m and args.flow:
            # rebind resolver
            print(module_report(g, args.name, flow=False))
            print(sibling_flow2(g, m))
        else:
            print(module_report(g, args.name))
    elif args.cmd == "dot":
        print(dot_str(g, args.filter))
    elif args.cmd == "json":
        out = {"top": g.top, "modules": {}}
        for n, m in g.modules.items():
            out["modules"][n] = {
                "kind": m.kind, "loc": m.loc,
                "ports": [{"dir": d, "name": n2, "width": t.total_width(),
                           "type": ts} for d, n2, ts, t, l in m.ports],
                "insts": [{"inst": i, "of": c, "loc": l} for i, c, l in m.insts],
            }
        print(json.dumps(out, indent=1))

def sibling_flow2(g, m):
    instset = {i for i, c, _ in m.insts}
    resolve = make_resolver(m)
    edges = defaultdict(lambda: [0, 0, 0])
    for lhs, rhs in m.connects:
        lb = lhs.split(".")[0].split("[")[0]
        rb = rhs.split(".")[0].split("[")[0]
        if lb in instset and rb in instset and lb != rb:
            lpath = lhs.split(".")
            rpath = rhs.split(".")
            if len(lpath) > 1 and lpath[1] in ("clock", "reset"): continue
            if len(rpath) > 1 and rpath[1] in ("clock", "reset"): continue
            e = edges[(rb, lb)]
            e[0] += 1
            w = resolve(rb, rhs) or resolve(lb, lhs)
            if w:
                e[1] += w
                e[2] += 1
    out = [f"sibling dataflow inside '{m.name}' (driver -> driven):"]
    for (s, d), (n, w, nres) in sorted(edges.items(), key=lambda kv: -kv[1][0]):
        out.append(f"    {s:16s} -> {d:16s}  {n:4d} connects, {w:6d}b resolved ({nres}/{n})")
    return "\n".join(out)

if __name__ == "__main__":
    main()
