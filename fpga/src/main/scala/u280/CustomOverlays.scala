package chipyard.fpga.u280

import chisel3._
import org.chipsalliance.cde.config._
import org.chipsalliance.diplomacy._
import org.chipsalliance.diplomacy.lazymodule._
import org.chipsalliance.diplomacy.bundlebridge.BundleBridgeSource
import sifive.fpgashells.shell._
import sifive.fpgashells.shell.xilinx._
import sifive.fpgashells.ip.xilinx._
import freechips.rocketchip.prci._
import freechips.rocketchip.amba.axi4._


case class CustomXDMADesignInput(wrangler: ClockAdapterNode, masterClockNode: FixedClockBroadcastNode)(implicit val p: Parameters)

case class CustomXDMAOverlayOutput(master: AXI4OutwardNode, masterLite: AXI4OutwardNode, config: CustomXDMAParams)

trait CustomXDMAShellPlacer[Shell] extends ShellPlacer[CustomXDMADesignInput, PCIeShellInput, CustomXDMAOverlayOutput]

case object CustomXDMAOverlayKey extends Field[Seq[DesignPlacer[CustomXDMADesignInput, PCIeShellInput, CustomXDMAOverlayOutput]]](Nil)

abstract class CustomXDMAPlacedOverlay[IO <: Data]
(
  val name: String, val di: CustomXDMADesignInput, val si: PCIeShellInput
) extends IOPlacedOverlay[IO, CustomXDMADesignInput, PCIeShellInput, CustomXDMAOverlayOutput] {
  implicit val p = di.p
}

abstract class PCIeCustomXDMAPlacedOverlay(
  val shell: U280ShellBasicOverlays, name: String,
  val designInput: CustomXDMADesignInput, val shellInput: PCIeShellInput, config: CustomXDMAParams
) extends CustomXDMAPlacedOverlay[XDMATopPads](name, designInput, shellInput) {

  val pcie = LazyModule(new CustomXDMA(config))
  val masterClockConverter = LazyModule(new LazyAXI4ClockConverter("xdma_master", isAXILite = false))
  val masterLiteClockConverter = LazyModule(new LazyAXI4ClockConverter("xdma_master_lite", isAXILite = true))
  val bridge = BundleBridgeSource(() => new XDMABridge(config.lanes))
  val topBridge = shell {
    bridge.makeSink()
  }
  val axiClk = ClockSourceNode(freqMHz = config.axiMHz)
  val axiClkFixedNode = FixedClockBroadcast()

  axiClkFixedNode := designInput.wrangler := axiClk

  masterClockConverter.node := pcie.master
  masterLiteClockConverter.node := pcie.masterLite

  masterClockConverter.slaveClockNode := axiClkFixedNode
  masterLiteClockConverter.slaveClockNode := axiClkFixedNode
  masterClockConverter.masterClockNode := designInput.masterClockNode
  masterLiteClockConverter.masterClockNode := designInput.masterClockNode

  val master: AXI4OutwardNode = masterClockConverter.node
  val masterLite: AXI4OutwardNode = masterLiteClockConverter.node

  def overlayOutput = CustomXDMAOverlayOutput(
    master,
    masterLite,
    pcie.c
  )

  def ioFactory = new XDMATopPads(config.lanes)

  InModuleBody {
    val (axi, _) = axiClk.out.head
    val b = bridge.out.head._1

    b.lanes <> pcie.module.io.pads

    axi.clock := pcie.module.io.clocks.axi_aclk
    axi.reset := !pcie.module.io.clocks.axi_aresetn
    pcie.module.io.clocks.sys_rst_n := b.srstn
    pcie.module.io.clocks.sys_clk := b.ODIV2
    pcie.module.io.clocks.sys_clk_gt := b.O

    shell.sdc.addGroup(clocks = Seq(s"${name}_ref_clk"), pins = Seq(pcie.module.blackbox.io.axi_aclk))
    shell.sdc.addAsyncPath(Seq(pcie.module.blackbox.io.axi_aresetn))
  }

  shell {
    InModuleBody {
      val b = topBridge.in.head._1

      val ibufds = Module(new IBUFDS_GTE4)
      ibufds.suggestName(s"${name}_refclk_ibufds")
      ibufds.io.CEB := false.B
      ibufds.io.I := io.refclk.p
      ibufds.io.IB := io.refclk.n
      b.O := ibufds.io.O
      b.ODIV2 := ibufds.io.ODIV2
      b.srstn := shell.pcie_rst_n && !shell.pllReset
      io.lanes <> b.lanes

      shell.sdc.addClock(s"${name}_ref_clk", io.refclk.p, 100)
    }
  }
}

class U280PCICustomXDMAPlacedOverlay(
  shell: U280ShellBasicOverlays, name: String,
  designInput: CustomXDMADesignInput, shellInput: PCIeShellInput, config: CustomXDMAParams
) extends PCIeCustomXDMAPlacedOverlay(
  shell, name, designInput, shellInput, config
) {
  shell { InModuleBody {
    val ref = Seq("AR15", "AR14")

    // Lane pins from Xilinx AU280 board file (production/1.1/part0_pins.xml).
    val rxp = Seq("AL2", "AM4", "AN6", "AN2", "AP4", "AR2", "AT4", "AU2")
    val rxn = Seq("AL1", "AM3", "AN5", "AN1", "AP3", "AR1", "AT3", "AU1")
    val txp = Seq("AL11", "AM9", "AN11", "AP9", "AR11", "AR7", "AT9", "AU11")
    val txn = Seq("AL10", "AM8", "AN10", "AP8", "AR10", "AR6", "AT8", "AU10")

    def bind(io: Seq[IOPin], pad: Seq[String]) {
      (io zip pad) foreach { case (io, pad) => shell.xdc.addPackagePin(io, pad) }
    }
    bind(IOPin.of(io.refclk), ref)

    bind(IOPin.of(io.lanes.pci_exp_txp), txp)
    bind(IOPin.of(io.lanes.pci_exp_txn), txn)
    bind(IOPin.of(io.lanes.pci_exp_rxp), rxp)
    bind(IOPin.of(io.lanes.pci_exp_rxn), rxn)
  }}
}

class U280PCICustomXDMAShellPlacer(shell: U280ShellBasicOverlays, val shellInput: PCIeShellInput)(implicit val valName: ValName)
  extends CustomXDMAShellPlacer[U280ShellBasicOverlays] {
  val config = CustomXDMAParams(
    name = "u280_xdma",
    location = "PCIE4C_X1Y0",
    lanes = 8,
    gen = 3
  )
  def place(designInput: CustomXDMADesignInput) = new U280PCICustomXDMAPlacedOverlay(
    shell, valName.value, designInput, shellInput, config
  )
}
