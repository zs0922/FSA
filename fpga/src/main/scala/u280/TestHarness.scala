package chipyard.fpga.u280

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

class SysClockU280PlacedOverlay
(
  val shell: U280ShellBasicOverlays,
  name: String,
  val designInput: ClockInputDesignInput,
  val shellInput: ClockInputShellInput,
  pPin: String, nPin: String,
  freqMHz: Int = 300
) extends LVDSClockInputXilinxPlacedOverlay(name, designInput, shellInput) {
  val node = shell { ClockSourceNode(freqMHz = freqMHz, jitterPS = 50)(ValName(name)) }

  shell { InModuleBody {
    shell.xdc.addPackagePin(io.n, nPin)
    shell.xdc.addPackagePin(io.p, pPin)
    shell.xdc.addIOStandard(io.n, "LVDS")
    shell.xdc.addIOStandard(io.p, "LVDS")
  }}
}

class SysClockU280ShellPlacer
(
  shell: U280ShellBasicOverlays,
  val shellInput: ClockInputShellInput,
  pPin: String, nPin: String,
  freqMHz: Int = 300
)(implicit val valName: ValName) extends ClockInputShellPlacer[U280ShellBasicOverlays] {
  override def place(di: ClockInputDesignInput) = new SysClockU280PlacedOverlay(shell, valName.value, di, shellInput, pPin, nPin, freqMHz)
}

abstract class U280ShellBasicOverlays()(implicit p: Parameters) extends UltraScaleShell {
  val pllReset = InModuleBody { Wire(Bool()) }
  val pcie_rst_n = InModuleBody { Wire(Bool()) }

  val sys_clock = Overlay(ClockInputOverlayKey, new SysClockU280ShellPlacer(this, ClockInputShellInput(), "BJ43", "BJ44"))
  val hbm_clock = Overlay(ClockInputOverlayKey, new SysClockU280ShellPlacer(this, ClockInputShellInput(), "G31", "F31", freqMHz = 100))
  val xdma = Overlay(CustomXDMAOverlayKey, new U280PCICustomXDMAShellPlacer(this, PCIeShellInput()))
}

class U280FPGATestHarness(implicit p: Parameters) extends U280ShellBasicOverlays {
  def dp = designParameters

  require(dp(ClockInputOverlayKey).nonEmpty)
  val sysClkNode = dp(ClockInputOverlayKey).head.place(ClockInputDesignInput()).overlayOutput.node
  val hbmClkNode = dp(ClockInputOverlayKey).last.place(ClockInputDesignInput()).overlayOutput.node

  // System clock is 300MHz on U280 (BJ43/BJ44)
  val sysClkBroadcast = FixedClockBroadcast(Some(ClockParameters(freqMHz = 300)))
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

  val placedXDMA = dp(CustomXDMAOverlayKey).head.place(CustomXDMADesignInput(
    wrangler.node, dutFixedClockNode
  ))

  val dutDomain = LazyModule(new ClockSinkDomain)
  dutDomain.clockNode := dutFixedClockNode

  val (ram, fsa) = dutDomain {
    val ram = LazyModule(new LazyXilinxHBMController(
      "test", is16GB = false
    ))

    val xbar = LazyModule(new AXI4Xbar())

    val fsa = LazyModule(new AXI4FSA(p(FpFSAImplKey).get))

    ram.node(0) := AXI4UserYanker(capMaxFlight = Some(8)) := xbar.node := AXI4Fragmenter() := placedXDMA.overlayOutput.master
    ram.slaveClockNodes(0) := dutFixedClockNode
    ram.HBMRefClockNode := hbmClkNode

    xbar.node := AXI4ILA("fsa_master") := fsa.memNode

    fsa.configNode := AXI4ILA("fsa_config") := AXI4Fragmenter() := AXI4Buffer() := placedXDMA.overlayOutput.masterLite

    (ram, fsa)
  }

  override lazy val module = new U280TestHarnessImpl(this)
}

class U280TestHarnessImpl(outer: U280FPGATestHarness) extends LazyRawModuleImp(outer) with HasHarnessInstantiators {

  val prst_n = IO(Input(Bool())).suggestName("prst_n")
  outer.xdc.addPackagePin(prst_n, "BH26")
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
