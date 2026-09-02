package chipyard.fpga.nm37

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

import chipyard.fpga.u280.{CustomXDMAParams, CustomXDMA, CustomXDMAOverlayKey, LazyAXI4ClockConverter}

class SysClockNM37PlacedOverlay
(
  val shell: NM37ShellBasicOverlays,
  name: String,
  val designInput: ClockInputDesignInput,
  val shellInput: ClockInputShellInput,
  pPin: String, nPin: String,
  freqMHz: Int = 100
) extends LVDSClockInputXilinxPlacedOverlay(name, designInput, shellInput) {
  val node = shell { ClockSourceNode(freqMHz = freqMHz, jitterPS = 50)(ValName(name)) }

  shell { InModuleBody {
    shell.xdc.addPackagePin(io.n, nPin)
    shell.xdc.addPackagePin(io.p, pPin)
    // DDR4 SODIMM system clock pair on the NM37 (DIFF_SSTL12, per nexst top.xdc)
    shell.xdc.addIOStandard(io.n, "DIFF_SSTL12")
    shell.xdc.addIOStandard(io.p, "DIFF_SSTL12")
  }}
}

class SysClockNM37ShellPlacer
(
  shell: NM37ShellBasicOverlays,
  val shellInput: ClockInputShellInput,
  pPin: String, nPin: String,
  freqMHz: Int = 100
)(implicit val valName: ValName) extends ClockInputShellPlacer[NM37ShellBasicOverlays] {
  override def place(di: ClockInputDesignInput) = new SysClockNM37PlacedOverlay(shell, valName.value, di, shellInput, pPin, nPin, freqMHz)
}

abstract class NM37ShellBasicOverlays()(implicit p: Parameters) extends UltraScaleShell {
  val pllReset = InModuleBody { Wire(Bool()) }
  val pcie_rst_n = InModuleBody { Wire(Bool()) }

  // 100MHz differential DDR4 system clock on BH42/BJ42.
  // The post-IBUFDS output feeds both the harness PLL and the DDR4 MIG
  // (which consumes it in No_Buffer mode).
  val sys_clock = Overlay(ClockInputOverlayKey, new SysClockNM37ShellPlacer(this, ClockInputShellInput(), "BH42", "BJ42"))
  val xdma = Overlay(CustomXDMAOverlayKey, new NM37PCICustomXDMAShellPlacer(this, PCIeShellInput()))
}

abstract class PCIeNM37CustomXDMAPlacedOverlay(
  val shell: NM37ShellBasicOverlays, name: String,
  val designInput: chipyard.fpga.u280.CustomXDMADesignInput, val shellInput: PCIeShellInput, config: CustomXDMAParams
) extends chipyard.fpga.u280.CustomXDMAPlacedOverlay[XDMATopPads](name, designInput, shellInput) {

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

  def overlayOutput = chipyard.fpga.u280.CustomXDMAOverlayOutput(
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
      // power-on reset must also reset the PCIe hard core so that a JTAG
      // (re)programmed bitstream re-initializes the link (same fix as U280)
      b.srstn := shell.pcie_rst_n && !shell.pllReset
      io.lanes <> b.lanes

      shell.sdc.addClock(s"${name}_ref_clk", io.refclk.p, 100)
    }
  }
}

class NM37PCICustomXDMAPlacedOverlay(
  shell: NM37ShellBasicOverlays, name: String,
  designInput: chipyard.fpga.u280.CustomXDMADesignInput, shellInput: PCIeShellInput, config: CustomXDMAParams
) extends PCIeNM37CustomXDMAPlacedOverlay(
  shell, name, designInput, shellInput, config
) {
  shell { InModuleBody {
    // PCIe EP reference clock pair (GTY Quad 227, same die/package as the U280)
    val ref = Seq("AR15", "AR14")

    def bind(io: Seq[IOPin], pad: Seq[String]) {
      (io zip pad) foreach { case (io, pad) => shell.xdc.addPackagePin(io, pad) }
    }
    bind(IOPin.of(io.refclk), ref)

    // GT lane pins are NOT constrained on the NM37: the XDMA IP places its
    // GTs from en_gt_selection (GTY_Quad_227) + pcie_blk_locn, mirroring the
    // nexst Xiangshan shell flow for this board.
  }}
}

class NM37PCICustomXDMAShellPlacer(shell: NM37ShellBasicOverlays, val shellInput: PCIeShellInput)(implicit val valName: ValName)
  extends chipyard.fpga.u280.CustomXDMAShellPlacer[NM37ShellBasicOverlays] {
  // x8 Gen3 via the PCIE4C hard block, 256-bit M_AXI @250MHz: matches the HBM
  // AXI_00 port width bit-for-bit (as on the U280), so the host path needs
  // neither width nor narrow-burst conversion.
  val config = CustomXDMAParams(
    name = "nm37_xdma",
    location = "PCIE4C_X1Y0",
    lanes = 8,
    gen = 3
  )
  def place(designInput: chipyard.fpga.u280.CustomXDMADesignInput) = new NM37PCICustomXDMAPlacedOverlay(
    shell, valName.value, designInput, shellInput, config
  )
}
