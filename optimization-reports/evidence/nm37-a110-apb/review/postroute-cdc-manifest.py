#!/usr/bin/env python3
"""Bind independent routed CDC evidence; never issue a physical acceptance."""
import collections
import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    directory, checkpoint, build, script = [Path(v).resolve() for v in sys.argv[1:]]
    base = build.parent
    expected = base / "review/build04-postroute-cdc-review"
    assert directory == expected and build.name == "build04"
    assert not (directory / "manifest.json").exists(), "Refusing to overwrite manifest"
    props = dict(line.split("=", 1) for line in (directory / "properties.txt").read_text().splitlines())
    inputs = {}
    for line in (directory / "input-sha256.tsv").read_text().splitlines()[1:]:
        digest, name = line.split("\t", 1)
        path = Path(name)
        assert path.is_absolute() and path.is_file()
        assert name not in inputs
        assert sha(path) == digest, "Input changed during evidence generation: " + name
        inputs[name] = digest
    assert inputs[str(checkpoint)] == props["checkpoint_sha256"]
    assert inputs[str(script)] == sha(script)
    parser = base / "src/scripts/nm37-a110/physical_audit.py"
    spec = importlib.util.spec_from_file_location("routed_cdc_frozen_parser", parser)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    baseline_path = parser.with_name("cdc-baseline.json")
    baseline = json.loads(baseline_path.read_text())
    known = {tuple(row) for row in baseline["findings"]}
    rows = []
    all_rows = []
    if (directory / "cdc.rpt").is_file():
        rows = module.cdc_rows((directory / "cdc.rpt").read_text())
    if (directory / "cdc-all-checks.rpt").is_file():
        all_rows = module.cdc_rows((directory / "cdc-all-checks.rpt").read_text())
    significant = {tuple(row) for row in rows if row[1] in {"Critical", "Warning"}}
    new = sorted(significant - known)
    details = []
    for finding in new:
        item = {"finding": list(finding), "review_disposition": "pending"}
        if finding[6].startswith("wrangler/"):
            mapped = list(finding)
            mapped[6] = mapped[6].replace("nodeOut_reset_catcher_2/", "nodeOut_reset_catcher_1/")
            for index in (7, 8):
                mapped[index] = mapped[index].replace("clk_out2_harnessSysPLL", "clk_out1_harnessSysPLL")
            if tuple(mapped) in known:
                item["baseline_exact_after_declared_clock_and_catcher_mapping"] = mapped
        if finding[6].startswith("pcie/blackbox/inst/pcie4c_ip_i/inst/"):
            same_destination = [
                list(row) for row in sorted(known)
                if row[0] == finding[0] and row[6] == finding[6] and row[8] == finding[8]
            ]
            if same_destination:
                item["baseline_same_rule_destination_clock"] = same_destination
        details.append(item)
    review = {
        "schema_version": 1,
        "mode": "independent-routed-cdc-evidence",
        "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "candidate_physical_acceptance": False,
        "review_required": True,
        "evidence_generation_complete": props.get("report_generation") == "completed",
        "structural_mismatches": int(props["structural_mismatches"]) if "structural_mismatches" in props else None,
        "checkpoint": str(checkpoint),
        "checkpoint_sha256": sha(checkpoint),
        "build_manifest_sha256": sha(build / "build.json"),
        "script_sha256": sha(script),
        "manifest_writer_sha256": sha(Path(__file__)),
        "inputs": inputs,
        "reports": {str(p): sha(p) for p in sorted(directory.iterdir()) if p.is_file()},
        "ordinary_cdc_row_count": len(rows),
        "all_checks_cdc_row_count": len(all_rows),
        "ordinary_cdc_rule_counts": dict(collections.Counter(row[0] for row in rows)),
        "all_checks_cdc_rule_counts": dict(collections.Counter(row[0] for row in all_rows)),
        "new_warning_critical_count": len(new),
        "new_warning_critical_findings": details,
        "additional_warning_critical_only_in_all_checks": [
            list(row) for row in sorted(
                {tuple(r) for r in all_rows if r[1] in {"Critical", "Warning"}} - significant
            )
        ],
        "limits": [
            "This artifact supplies actual routed evidence and proposed endpoint correspondence only; every new finding remains pending independent review.",
            "The original old A110 DCP is unavailable. Baseline mapping uses archived CDC rows and source correspondence, not a claimed reopened old DCP.",
            "No timing exception or constraint was modified. No implementation command or checkpoint write was executed.",
            "A true asynchronous fundamental-reset interface is not equivalent to per-endpoint reset synchronization.",
            "Startup digital tests and this evidence do not demonstrate physical board operation or PCIe reference-clock stability.",
        ],
    }
    (directory / "manifest.json").write_text(json.dumps(review, indent=2) + "\n")
    print(directory / "manifest.json")


if __name__ == "__main__":
    main()
