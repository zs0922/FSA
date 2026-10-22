package chipyard

import freechips.rocketchip.subsystem.MasterPortParams

/** Simulation-only topology matching NM37: one 256-bit AXI memory port. */
class A70SinglePort16X16Fp16Config extends FSADirectAXI4Config(
  fsa.Configs.fsa16x16.copy(nMemPorts = 1),
  memParams = MasterPortParams(base = 0x80000000L, size = 0x10000000L,
    beatBytes = 32, idBits = 1, maxXferBytes = 512),
  useMBusBeatBytes = false
)
