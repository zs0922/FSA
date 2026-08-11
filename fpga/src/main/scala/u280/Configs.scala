package chipyard.fpga.u280

import fsa.{Configs, WithFpFSA}
import org.chipsalliance.cde.config._
import freechips.rocketchip.subsystem.ExtMem

class WithU280HBMMemBase extends Config((site, here, up) => {
  case ExtMem => up(ExtMem, site).map(x => x.copy(master = x.master.copy(base = BigInt(0))))
})

class WithU280Tweaks extends Config(
  new WithU280HBMMemBase ++
  new WithU280AXIMemHarnessBinder ++
  new chipyard.harness.WithTieOffL2FBusAXI ++
  // clocking
  new chipyard.harness.WithAllClocksFromHarnessClockInstantiator ++
  new chipyard.harness.WithHarnessBinderClockFreqMHz(70) ++
  new chipyard.config.WithUniformBusFrequencies(70) ++
  new testchipip.serdes.WithNoSerialTL ++
  new testchipip.soc.WithNoScratchpads
)

class EmptyU280Config extends Config (
  new WithU280Tweaks ++
  new chipyard.EmptyChipTopConfig ++
  new WithFpFSA(params = Configs.fsa16x16.copy(nMemPorts = 1))
)
