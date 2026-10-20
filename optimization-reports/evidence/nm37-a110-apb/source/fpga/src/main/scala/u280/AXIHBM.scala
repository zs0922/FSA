package chipyard.fpga.u280

import chisel3._
import chisel3.experimental.dataview._
import chisel3.reflect.DataMirror
import chisel3.util._
import freechips.rocketchip.amba.axi4._
import freechips.rocketchip.diplomacy.{AddressSet, RegionType, TransferSizes}
import freechips.rocketchip.util.ElaborationArtefacts
import org.chipsalliance.cde.config.Parameters
import freechips.rocketchip.prci.ClockSinkNode
import freechips.rocketchip.prci.ClockSinkParameters
import freechips.rocketchip.resources.SimpleDevice
import org.chipsalliance.diplomacy.lazymodule._
import freechips.rocketchip.util.BooleanToAugmentedBoolean
import fsa.arithmetic.FloatPoint

import scala.collection.immutable.SeqMap

class XilinxHBMIO(val params: Seq[AXI4BundleParameters], val is16GB: Boolean) extends Bundle {
  val AXI_00_ACLK = Input(Clock())
  val AXI_00_ARESET_N = Input(Reset())
  val HBM_REF_CLK_0 = Input(Clock())
  val HBM_REF_CLK_1 = if (is16GB) Some(Input(Clock())) else None
  val AXI_00 = Flipped(new XilinxAXI4UpperBundle(params(0), isAXI4Lite = false))
  val APB_0_PCLK = Input(Clock())
  val APB_1_PCLK = if (is16GB) Some(Input(Clock())) else None
  val APB_0_PRESET_N = Input(Reset())
  val APB_1_PRESET_N = if (is16GB) Some(Input(Reset())) else None
  val apb_complete_0 = Output(Bool())
  val apb_complete_1 = if (is16GB) Some(Output(Bool())) else None
}

class XilinxHBM
(
  bundleParams: Seq[AXI4BundleParameters],
  portNum: Int = 1,
  is16GB: Boolean = false,
  override val desiredName: String,
  apbFrequencyMHz: Double,
) extends BlackBox {
  val io = IO(new XilinxHBMIO(bundleParams, is16GB))
  require(!(portNum > 16 && !is16GB))
  require(portNum > 0 && portNum <= 1)
  // Vivado 2020.2 hbm_v1_0 USER_APB_PCLK_0/1 accepts 50..100 MHz.
  require(apbFrequencyMHz >= 50 && apbFrequencyMHz <= 100 &&
    apbFrequencyMHz == apbFrequencyMHz.toInt.toDouble,
    s"HBM APB clock must be an integer 50..100 MHz, got $apbFrequencyMHz MHz")
  private val apbClockSettings = (if (is16GB) Seq(0, 1) else Seq(0)).map { stack =>
    s"    CONFIG.USER_APB_PCLK_$stack {${apbFrequencyMHz.toInt}} \\\n"
  }.mkString

  ElaborationArtefacts.add(s"$desiredName.vivado.tcl",
    s"""
       |create_ip -name hbm -vendor xilinx.com -library ip -version 1.0 -module_name $desiredName
       |set_property -dict [list \\
       |    CONFIG.USER_APB_EN {false} \\
       |${apbClockSettings}    CONFIG.USER_CLK_SEL_LIST0 {AXI_00_ACLK} \\
       |    CONFIG.USER_CLK_SEL_LIST1 {AXI_16_ACLK} \\
       |    CONFIG.USER_HBM_DENSITY {8GB} \\
       |    CONFIG.USER_HBM_STACK {1} \\
        |    CONFIG.USER_MC_ENABLE_APB_01 {FALSE} \\
        |    CONFIG.USER_SWITCH_ENABLE_01 {FALSE} \\
        |    CONFIG.USER_XSDB_INTF_EN {FALSE} ] \\
       |[get_ips ${desiredName}]
       |""".stripMargin)
}

class LazyXilinxHBMController(
  moduleNamePrefix: String,
  portNum: Int = 1,
  is16GB: Boolean = false,
  apbFrequencyMHz: Double,
)(implicit p: Parameters) extends LazyModule {
  require(apbFrequencyMHz >= 50 && apbFrequencyMHz <= 100 &&
    apbFrequencyMHz == apbFrequencyMHz.toInt.toDouble,
    s"HBM APB clock must be an integer 50..100 MHz, got $apbFrequencyMHz MHz")
  val node = Seq.fill(portNum)(AXI4SlaveNode(
    Seq(AXI4SlavePortParameters(
      Seq(AXI4SlaveParameters(
        address = Seq(AddressSet(0x00000000L, 0x3FFFFFFFFL)),
        resources = new SimpleDevice("hbm", Seq()).reg("hbm"),
        regionType = RegionType.UNCACHED,
        executable = true,
        supportsRead = TransferSizes(32, 512),
        supportsWrite = TransferSizes(32, 512),
      )),
      beatBytes = 32,
      requestKeys = Seq(),
      responseFields = Nil,
    ))
  ))

  override def shouldBeInlined: Boolean = true

  val slaveClockNodes = Seq.fill(portNum)(ClockSinkNode(Seq(ClockSinkParameters())))
  val HBMRefClockNode = ClockSinkNode(Seq(ClockSinkParameters()))
  // APB management clock/reset are independent of the AXI data interface.
  // Make the management frequency part of the diplomacy contract, so this
  // sink has a defined clock even behind a fanout FixedClockBroadcast.
  val apbClockNode = ClockSinkNode(freqMHz = apbFrequencyMHz)

  lazy val module = new LazyRawModuleImp(this) {
    val bundleParams = node.map { a => a.in.head._2.bundle }
    val slaveClocks = slaveClockNodes.map(a => a.in.head._1.clock)
    val slaveResets = slaveClockNodes.map(a => a.in.head._1.reset)

    val apbClock = apbClockNode.in.head._1.clock
    val apbReset = apbClockNode.in.head._1.reset
    val apbFrequency = apbClockNode.in.head._2.clock.get.freqMHz
    require(apbFrequency == apbFrequencyMHz,
      s"HBM APB negotiated $apbFrequency MHz, expected $apbFrequencyMHz MHz")
    require(apbFrequency >= 50 && apbFrequency <= 100,
      s"HBM APB clock must be 50..100 MHz, got $apbFrequency MHz")
    val hbm = Module(new XilinxHBM(bundleParams, portNum, is16GB,
      moduleNamePrefix + "xilinx_hbm", apbFrequency))
    val hbmIO = hbm.io

    // NM37 connects both clock nodes through the same ResetWrangler, so PLL
    // lock loss and PCIe reset reset the masters, fabric and complete HBM stack.
    val startup = Module(new HBMStartupControl)
    startup.io.apbClock := apbClock
    startup.io.axiClock := slaveClocks.head
    startup.io.reset := apbReset.asBool || slaveResets.head.asBool
    startup.io.apbComplete := hbmIO.apb_complete_0 &&
      hbmIO.apb_complete_1.getOrElse(true.B)
    hbmIO.APB_0_PCLK := apbClock
    hbmIO.APB_0_PRESET_N := startup.io.apbResetN
    if (is16GB) {
      hbmIO.APB_1_PCLK.get := apbClock
      hbmIO.APB_1_PRESET_N.get := startup.io.apbResetN
    }

    hbmIO.HBM_REF_CLK_0 := HBMRefClockNode.in.head._1.clock
    if (is16GB) {
      hbmIO.HBM_REF_CLK_1.get := HBMRefClockNode.in.head._1.clock
    }

    require(portNum <= 1)
    for (i <- 0 until portNum) {
      val (in, _) = node(i).in.head
      val hbmPort = hbmIO.AXI_00

      hbmIO.AXI_00_ACLK := slaveClocks(i)
      hbmIO.AXI_00_ARESET_N := startup.io.axiResetN

      val gate = Module(new HBMAXIStartupGate(bundleParams(i)))
      gate.io.enable := startup.io.trafficReady
      gate.io.in <> in
      hbmPort.drivenByStandardAXI4(gate.io.out, slaveClocks(i), !startup.io.axiResetN)
    }
  }
}
