# Historical A70 P2/P3 verification overlays

These files are test-only overlays. Install them **before** freezing the stage source identity and before building any generator JAR. The production design is still the historical fixed-revision source plus the declared cumulative board/P2/P3 patches.

- Both stages: copy `scala/A70SinglePortConfig.scala` into `generators/chipyard/src/main/scala/config/`. This adds a simulation configuration with one FSA memory port and an explicit 32-byte AXI beat (`useMBusBeatBytes=false`), as on NM37. The inherited 64-byte beat derived from the accumulator row would exceed the 32-byte scratchpad row and fail elaboration. It does not change the selected FPGA config.
- P3 only: copy the other four `scala/*.scala` into `generators/fsa/src/test/scala/fsa/`. Their Scala packages separate the historical `easyfloat` oracle from the FSA harness.
- Oracle source provenance is in `legacy-oracle.json`. The oracle was extracted from easyfloat commit `44e1c369659c2c397548d2aecfc4ded3a74a111e`, with test-only class renaming. It is independent of the P3 refactor, but shares unchanged decoding/rounding utility implementations.

P3 unit and miter emission commands from the stage root:

```sh
java -jar scripts/sbt-launch.jar -Dsbt.supershell=false \
  'set fsa / Test / sources := (fsa / Test / sources).value.filter(f => Set("A70AccumulatorVerification.scala", "A70LegacyFMA.scala", "A70LegacyExp2.scala", "A70LegacyReciprocal.scala").contains(f.getName))' \
  'fsa/Test/runMain fsa.A70AccumulatorVerification' \
  'fsa/Test/runMain fsa.A70AccumulatorVerification emit /absolute/new/rtl-dir'
python3 /path/to/verify/window_proof/run-proof.py --rtl /absolute/new/rtl-dir --out /absolute/new/formal-dir
```

The exact four-file test source whitelist is intentional: historical `ControlSignalTest.scala` contains four calls to an obsolete two-argument `AttentionScoreExecPlan` constructor and fails test compilation. Its original source is retained and that old suite is not claimed as executed. `p3-test-api-compat.patch` is retained only as an unused diagnostic proposal; it is not applied to the final stage. The new directed tests and full-system regressions provide the acceptance evidence for this task. The formal emitter uses `disallowLocalVariables,disallowPackedArrays` to generate a Verilog representation supported by Yosys; this does not alter the DUT algorithm.

The continuous test changes inputs and MAC/EXP2 selection every cycle, checks exactly three-cycle delayed rounded results, and drains the final samples. It includes signed zero, subnormals, normals, extrema, infinities, signalling/quiet NaNs and unrestricted random bit patterns. A separate closed-loop test checks 525 reciprocal transactions without reset between them, compares old and new results, and asserts completion at 19 and 76 presented cycles respectively. The test obeys the reciprocal input-valid/operand-hold protocol; it does not claim arbitrary aborted-operation correctness.

The formal miter compares actual P3 `FPAccUnit` against the frozen old combinational `RawFloat_MulAddExp2`, delayed by three registers, for unrestricted input bits and MAC/EXP2 selection after fill. It has no input/reset/initial-state assumptions. This proves arithmetic latency equivalence; it does not prove the accumulator scale register, SRAM address/writeback schedule or full FSA protocol.

The default proof mechanically expands the emitted netlist through three rising edges and rejects unsupported sequential/unknown cells. It proves both the original miter output and an unmasked data comparison, and checks that the final cone has no initial-state leaves. Thus the unrestricted identity applies to every subsequent three-edge window, rather than relying on a bounded reset trace. Twelve semantic and rejection fixtures compare supported register behavior against native Yosys sequential SAT. See `window_proof/proof-notes.txt` for the trusted transformation boundary. The original `verify_equivalence.py` induction attempt remains available for provenance; the historical long-running attempt was interrupted without a conclusion after the window proof passed, and is not counted as a passing proof.

For **both** stages, build fresh simulators for `AXI4FSA4X4Fp16Config` and `A70SinglePort16X16Fp16Config`. Run `verify_sim.py` at sequences 4/8 and 16/32 respectively. The driver preserves exact Q/K/V and actual/reference output files and hashes the binary/config. Redirect each run's stdout and stderr to a distinct log to retain the hardware counters emitted by the testbench. A passing test requires bitwise equality with the PyEasyFloat reference, not a numerical tolerance. The 16x16 single-port configuration deliberately avoids the historical multiport simulation row-offset defect; no multiport regression claim is made.

Do not use these tests with a cached generator assembled before overlays/patches. Bind all stage source, test overlays, emitted RTL, JAR, simulator, configs and result files in the build manifest. Only start implementation after the required unit/formal/system passes are bound to the same stage identity.
