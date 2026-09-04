package chipyard.fpga.nm37

import chisel3._
import chipyard.harness.HasHarnessInstantiators
import org.chipsalliance.diplomacy._
import org.chipsalliance.diplomacy.lazymodule._
import org.chipsalliance.cde.config.Parameters
import freechips.rocketchip.amba.axi4._
import freechips.rocketchip.diplomacy.AddressSet
import freechips.rocketchip.prci._
import fsa.{AXI4FSA, Configs, FpFSAImplKey}
import sifive.fpgashells.ip.xilinx._
import sifive.fpgashells.clocks.PLLFactoryKey
import sifive.fpgashells.shell._
import sifive.fpgashells.shell.xilinx._

class NM37FPGATestHarness(implicit p: Parameters) extends NM37ShellBasicOverlays {
  def dp = designParameters

  require(dp(ClockInputOverlayKey).nonEmpty)
  val sysClkNode = dp(ClockInputOverlayKey).head.place(ClockInputDesignInput()).overlayOutput.node

  // System clock is the 100MHz differential pair on BH42/BJ42 (board 100MHz
  // oscillator via U7/U38). The post-IBUFDS clock feeds the harness PLL
  // (dut clock generation) AND the HBM reference clock (the HBM IP's
  // HBM_REF_CLK_0 is a plain clock input port, driven here from fabric —
  // the same structure as the U280 reference path, whose dedicated
  // G31/F31 pair is left unconnected on the NM37).
  val sysClkBroadcast = FixedClockBroadcast(Some(ClockParameters(freqMHz = 100)))
  sysClkBroadcast := sysClkNode

  val harnessSysPLL = dp(PLLFactoryKey)()
  harnessSysPLL := sysClkBroadcast

  val wrangler = LazyModule(new ResetWrangler)

  val dutFreqMHz = 70
  val dutFixedClockNode = FixedClockBroadcast()
  val dutClockGroup = ClockGroup()
  val dutClockNode = ClockSinkNode(freqMHz = dutFreqMHz, jitterPS = 230)
  dutFixedClockNode := wrangler.node := dutClockGroup := harnessSysPLL
  dutClockNode := dutFixedClockNode

  val placedXDMA = dp(chipyard.fpga.u280.CustomXDMAOverlayKey).head.place(
    chipyard.fpga.u280.CustomXDMADesignInput(wrangler.node, dutFixedClockNode)
  )

  val dutDomain = LazyModule(new ClockSinkDomain)
  dutDomain.clockNode := dutFixedClockNode

  val (ram, fsa) = dutDomain {
    // The VU37P HBM (8GB, single stack) is used as the FSA main memory,
    // exactly as on the U280. Its AXI_00 port is 256-bit, matching both the
    // XDMA M_AXI (256-bit) and the FSA 16x16 DMA (32-byte spad rows), so no
    // width conversion is needed anywhere.
    val ram = LazyModule(new chipyard.fpga.u280.LazyXilinxHBMController("test"))

    val xbar = LazyModule(new AXI4Xbar())

    val fsa = LazyModule(new AXI4FSA(p(FpFSAImplKey).get))

    ram.node(0) :=
      AXI4UserYanker(capMaxFlight = Some(8)) :=
      xbar.node :=
      AXI4Fragmenter() :=
      placedXDMA.overlayOutput.master

    // HBM AXI interface clock: the dut clock domain (as on the U280)
    ram.slaveClockNodes(0) := dutFixedClockNode
    // HBM PHY reference clock: 100MHz from the board oscillator (IBUFDS path)
    ram.HBMRefClockNode := sysClkBroadcast

    xbar.node := fsa.memNode

    fsa.configNode :=
      AXI4Fragmenter() :=
      AXI4Buffer() :=
      placedXDMA.overlayOutput.masterLite

    (ram, fsa)
  }

  override lazy val module = new NM37TestHarnessImpl(this)
}

class NM37TestHarnessImpl(outer: NM37FPGATestHarness) extends LazyRawModuleImp(outer) with HasHarnessInstantiators {

  val prst_n = IO(Input(Bool())).suggestName("prst_n")
  outer.xdc.addPackagePin(prst_n, "BF5")
  outer.xdc.addIOStandard(prst_n, "LVCMOS18")
  outer.xdc.addPullup(prst_n)
  outer.sdc.addAsyncPath(Seq(prst_n))

  val pcie_rst_n_ibuff = Module(new IBUF)
  pcie_rst_n_ibuff.suggestName("pcie_rst_n_ibuff")
  pcie_rst_n_ibuff.io.I := prst_n
  outer.pcie_rst_n := pcie_rst_n_ibuff.io.O

  val sysclk: Clock = outer.sysClkNode.out.head._1.clock

  val powerOnReset: Bool = PowerOnResetFPGAOnly(sysclk)
  outer.sdc.addAsyncPath(Seq(powerOnReset))

  outer.pllReset := powerOnReset

  val hReset = Wire(Reset())
  hReset := outer.dutClockNode.in.head._1.reset

  def referenceClockFreqMHz = outer.dutFreqMHz
  def referenceClock = outer.dutClockNode.in.head._1.clock
  def referenceReset = hReset
  def success = { require(false, "Unused"); false.B }

  childClock := referenceClock
  childReset := referenceReset

  instantiateChipTops()
}
