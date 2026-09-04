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
}

class XilinxHBM
(
  bundleParams: Seq[AXI4BundleParameters],
  portNum: Int = 1,
  is16GB: Boolean = false,
  override val desiredName: String,
) extends BlackBox {
  val io = IO(new XilinxHBMIO(bundleParams, is16GB))
  require(!(portNum > 16 && !is16GB))
  require(portNum > 0 && portNum <= 1)

  ElaborationArtefacts.add(s"$desiredName.vivado.tcl",
    s"""
       |create_ip -name hbm -vendor xilinx.com -library ip -version 1.0 -module_name $desiredName
       |set_property -dict [list \\
       |    CONFIG.USER_APB_EN {false} \\
       |    CONFIG.USER_CLK_SEL_LIST0 {AXI_00_ACLK} \\
       |    CONFIG.USER_CLK_SEL_LIST1 {AXI_16_ACLK} \\
       |    CONFIG.USER_HBM_DENSITY {8GB} \\
       |    CONFIG.USER_HBM_STACK {1} \\
        |    CONFIG.USER_MC_ENABLE_APB_01 {FALSE} \\
        |    CONFIG.USER_SWITCH_ENABLE_01 {FALSE} \\
        |    CONFIG.USER_XSDB_INTF_EN {FALSE} ] \\
       |[get_ips ${desiredName}]
       |""".stripMargin)
}

class LazyXilinxHBMController(moduleNamePrefix: String, portNum: Int = 1, is16GB: Boolean = false)(implicit p: Parameters) extends LazyModule {
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

  lazy val module = new LazyRawModuleImp(this) {
    val bundleParams = node.map { a => a.in.head._2.bundle }
    val hbm = Module(new XilinxHBM(bundleParams, portNum, is16GB, moduleNamePrefix + "xilinx_hbm"))
    val slaveClocks = slaveClockNodes.map(a => a.in.head._1.clock)
    val slaveResets = slaveClockNodes.map(a => a.in.head._1.reset)

    val hbmIO = hbm.io

    hbmIO.HBM_REF_CLK_0 := HBMRefClockNode.in.head._1.clock
    if (is16GB) {
      hbmIO.HBM_REF_CLK_1.get := HBMRefClockNode.in.head._1.clock
    }

    require(portNum <= 1)
    for (i <- 0 until portNum) {
      val (in, _) = node(i).in.head
      val hbmPort = hbmIO.AXI_00

      hbmIO.AXI_00_ACLK := slaveClocks(i)
      hbmIO.AXI_00_ARESET_N := !(slaveResets(i).asBool)

      hbmIO.APB_0_PCLK := slaveClocks(i)
      if (is16GB) { hbmIO.APB_1_PCLK.get := slaveClocks(i) }

      hbmIO.APB_0_PRESET_N := !(slaveResets(i).asBool)
      if (is16GB) { hbmIO.APB_1_PRESET_N.get := !(slaveResets(i).asBool) }

      hbmPort.drivenByStandardAXI4(in, slaveClocks(i), slaveResets(i).asBool)
    }
  }
}
