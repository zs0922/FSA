package chipyard.fpga.nm37

import fsa.{Configs, WithFpFSA}
import org.chipsalliance.cde.config._
import freechips.rocketchip.subsystem.ExtMem

class WithNM37DDRMemBase extends Config((site, here, up) => {
  case ExtMem => up(ExtMem, site).map(x => x.copy(master = x.master.copy(base = BigInt(0))))
})

class WithNM37Tweaks extends Config(
  new WithNM37DDRMemBase ++
  new WithNM37AXIMemHarnessBinder ++
  new chipyard.harness.WithTieOffL2FBusAXI ++
  // clocking
  new chipyard.harness.WithAllClocksFromHarnessClockInstantiator ++
  new chipyard.harness.WithHarnessBinderClockFreqMHz(70) ++
  new chipyard.config.WithUniformBusFrequencies(70) ++
  new testchipip.serdes.WithNoSerialTL ++
  new testchipip.soc.WithNoScratchpads
)

class EmptyNM37Config extends Config (
  new WithNM37Tweaks ++
  new chipyard.EmptyChipTopConfig ++
  new WithFpFSA(params = Configs.fsa16x16.copy(nMemPorts = 1))
)
