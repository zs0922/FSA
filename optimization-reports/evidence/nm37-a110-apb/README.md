# NM37 A110/APB selected evidence

This directory is a **partial evidence package**, not a self-contained build backup.

This is selected evidence, not a self-contained raw build or a complete source checkout.

Stage seals refer to original uncompressed bytes. Sealed files omitted from this package are explicitly listed; they were present and SHA-256 verified in the local build when archived.

Generator JAR, simulation executables, Vivado DCP and bitstream bytes remain only at the recorded local absolute paths; hashes and sizes do not make those binaries available elsewhere.

Source snapshots cover frozen drivers, shell inputs and selected changed/test sources. The full source-lock additionally identifies source files and dependencies not copied here.

Archive completion means packaging succeeded. Physical pass/fail, CDC classifications, formal scope, simulation results and board_signoff retain the meaning stated in their original records.

Simulation cycle comparisons exclude board HBM calibration, PCIe retraining and host reinitialization latency, and are not measured board throughput.

A listed bitstream was hashed as a local artifact; its presence alone is not evidence of successful on-board validation.

Historical baseline evidence remains in the existing nm37-a110-reset archive and has the partial-archive limitations recorded by the comparison report.

Git ignore rules can omit raw .log files and archived directories. Force-add this new evidence directory and run verify_candidate_archive.py --require-index before committing; a filesystem-only archive is not proof that every file entered Git.

See `inventory.json` for source-relative paths, raw and archived SHA-256/size, compression, all seven stage bindings, all six case files, and omitted local artifacts. The archive layout preserves each path under `build/`, `source/`, or `review/`. Files ending in an added `.gz` must be decompressed before checking raw stage hashes.
