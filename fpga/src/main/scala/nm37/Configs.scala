package chipyard.fpga.nm37

import fsa.{Configs, WithFpFSA}
import org.chipsalliance.cde.config._
import freechips.rocketchip.subsystem.ExtMem

// Keep the retained 70 MHz baseline selectable while evaluating the pipeline.
// Both the design clock metadata and the actual harness PLL use this value.
object NM37DUTClock {
  val frequencyMHz: Int = sys.props.get("fsa.nm37MHz").map(_.toInt).getOrElse(70)
  require(Set(70, 100, 110).contains(frequencyMHz), "fsa.nm37MHz must be 70, 100, or 110")
}

class WithNM37DDRMemBase extends Config((site, here, up) => {
  case ExtMem => up(ExtMem, site).map(x => x.copy(master = x.master.copy(base = BigInt(0))))
})

class WithNM37Tweaks extends Config(
  new WithNM37DDRMemBase ++
  new WithNM37AXIMemHarnessBinder ++
  new chipyard.harness.WithTieOffL2FBusAXI ++
  // clocking
  new chipyard.harness.WithAllClocksFromHarnessClockInstantiator ++
  new chipyard.harness.WithHarnessBinderClockFreqMHz(NM37DUTClock.frequencyMHz) ++
  new chipyard.config.WithUniformBusFrequencies(NM37DUTClock.frequencyMHz) ++
  new testchipip.serdes.WithNoSerialTL ++
  new testchipip.soc.WithNoScratchpads
)

class EmptyNM37Config extends Config (
  new WithNM37Tweaks ++
  new chipyard.EmptyChipTopConfig ++
  new WithFpFSA(params = Configs.fsa16x16.copy(nMemPorts = 1))
)
