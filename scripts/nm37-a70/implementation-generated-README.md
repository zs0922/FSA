# Implementation-generated artifact boundary

This policy applies to the reviewed A70 P3 layout produced by Vivado
`v2020.2_AR75986`, top `NM37FPGATestHarness`. The catalog names exactly 141
implementation additions. It is not a directory-wide or extension-wide exclusion.
P2's common generated layout was inspected, but its extra
`iter_2_CongestedCLBsAndNets.txt` is deliberately not accepted by this catalog.
Unknown layouts fail closed and require explicit review and a policy update.

`implementation_generated.py` first verifies every original elaboration file
against its original SHA. Removed or changed files always fail. It then checks
all additions against the exact catalog, verifies the five IP XCI identities and
every explicit CONFIG value against original sealed IP Tcl, and restricts every
XPR source/constraint reference to sealed inputs, source-list entries, shell
inputs, or the known IP files. For AXI4-Lite only, Vivado canonicalizes the inactive
Tcl ID_WIDTH=1 placeholder to XCI zero; this exact case is recorded explicitly.
Generated RTL, constraints, XCI, project metadata and journals are all SHA-bound.
There are no mutable-file or ignored-new-file exceptions.

`record` creates a new `implementation-generated.passed.json`; it never rewrites
an old seal. The sidecar binds the original elaboration-seal SHA, source-lock
SHA, classifier/catalog SHAs, exact 141-file inventory, two additional obj/IP
XCI dependencies, IP configuration audit and XPR references. `check` repeats the
whole comparison, including raw hashes after semantic XCI checks. Symlinks,
path traversal, missing files, extra files and changed sidecars fail.

For new builds, `artifacts.py seal --phase implement` records the sidecar and
adds its SHA to the implementation seal. `gate --phase bitstream` verifies the
sidecar and that binding while preserving the original elaboration seal.
Pre-implementation `gate` continues to require the original exact inventory.

Standalone commands for an explicitly reviewed transition of an existing build:

```bash
python3 implementation_generated.py classify --out /path/to/output
python3 implementation_generated.py record --out /path/to/output
python3 implementation_generated.py check --out /path/to/output
```

`--seal PATH` selects a new/existing sidecar location for record/check;
`--report PATH` creates a separate audit report. Python callers may use
`classify(out)`, `record(out, seal=None)` and `check(out, seal=None)`.
These functions validate artifact identity; callers must separately verify the
original source lock, frozen drivers, phase seals, accepted CDC evidence and
physical checks. They never authorize bitstream export themselves.

For an old implementation seal, the new sidecar cannot retroactively become a
field in that immutable seal. An explicit method-update record must instead bind
the old seal SHA, new sidecar SHA, old/new frozen driver inventories, and accepted
physical/CDC evidence. Do not modify the old `drivers.json` or phase seals or
silently substitute the new artifacts.py for the old driver identity.

Tests copy real P3 collateral and exercise mutations only in temporary fixtures:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 test_implementation_generated.py \
  --reference /path/to/final-p3-v2 --result /path/to/new-tests.json
```

The 24 checks include six actual artifacts.py CLI invocations with the original
full source-lock verification, plus protected/new RTL and Tcl changes, XCI
configuration and raw-byte changes, unknown paths, deletion, symlinks, project
source injection and sidecar-binding tampering. Frozen build inputs are untouched.
