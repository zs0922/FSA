package chipyard.fpga.nm37

import chisel3._
import chisel3.util._
import freechips.rocketchip.amba.axi4._
import freechips.rocketchip.util.ElaborationArtefacts
import org.chipsalliance.cde.config.Parameters
import freechips.rocketchip.prci.{ClockSinkNode, ClockSinkParameters}
import org.chipsalliance.diplomacy.lazymodule._

/*
 * Xilinx axi_dwidth_converter between the FSA DMA (32-byte beats, one ID) and
 * the 512-bit DDR4 MIG bus. The Xilinx IP requires an ID width of at least 1
 * and keeps SI/MI ID widths equal; the FSA DMA issues a constant 0 ID, so the
 * (unused) ID bit is padded/truncated at the connection site.
 */
class XilinxAXIDataWidthConverterIO
(
  siIdBits: Int, siAddrBits: Int, siDataBits: Int,
  miIdBits: Int, miAddrBits: Int, miDataBits: Int
) extends Bundle {
  val s_axi_aclk    = Input(Clock())
  val s_axi_aresetn = Input(Bool())

  // slave interface (towards the FSA DMA)
  val s_axi_awid      = Input(UInt(siIdBits.W))
  val s_axi_awaddr    = Input(UInt(siAddrBits.W))
  val s_axi_awlen     = Input(UInt(8.W))
  val s_axi_awsize    = Input(UInt(3.W))
  val s_axi_awburst   = Input(UInt(2.W))
  val s_axi_awlock    = Input(UInt(1.W))
  val s_axi_awcache   = Input(UInt(4.W))
  val s_axi_awprot    = Input(UInt(3.W))
  val s_axi_awregion  = Input(UInt(4.W))
  val s_axi_awqos     = Input(UInt(4.W))
  val s_axi_awvalid   = Input(Bool())
  val s_axi_awready   = Output(Bool())
  val s_axi_wdata     = Input(UInt(siDataBits.W))
  val s_axi_wstrb     = Input(UInt((siDataBits / 8).W))
  val s_axi_wlast     = Input(Bool())
  val s_axi_wvalid    = Input(Bool())
  val s_axi_wready    = Output(Bool())
  val s_axi_bid       = Output(UInt(siIdBits.W))
  val s_axi_bresp     = Output(UInt(2.W))
  val s_axi_bvalid    = Output(Bool())
  val s_axi_bready    = Input(Bool())
  val s_axi_arid      = Input(UInt(siIdBits.W))
  val s_axi_araddr    = Input(UInt(siAddrBits.W))
  val s_axi_arlen     = Input(UInt(8.W))
  val s_axi_arsize    = Input(UInt(3.W))
  val s_axi_arburst   = Input(UInt(2.W))
  val s_axi_arlock    = Input(UInt(1.W))
  val s_axi_arcache   = Input(UInt(4.W))
  val s_axi_arprot    = Input(UInt(3.W))
  val s_axi_arregion  = Input(UInt(4.W))
  val s_axi_arqos     = Input(UInt(4.W))
  val s_axi_arvalid   = Input(Bool())
  val s_axi_arready   = Output(Bool())
  val s_axi_rid       = Output(UInt(siIdBits.W))
  val s_axi_rdata     = Output(UInt(siDataBits.W))
  val s_axi_rresp     = Output(UInt(2.W))
  val s_axi_rlast     = Output(Bool())
  val s_axi_rvalid    = Output(Bool())
  val s_axi_rready    = Input(Bool())

  // master interface (towards the xbar / MIG side)
  val m_axi_awid      = Output(UInt(miIdBits.W))
  val m_axi_awaddr    = Output(UInt(miAddrBits.W))
  val m_axi_awlen     = Output(UInt(8.W))
  val m_axi_awsize    = Output(UInt(3.W))
  val m_axi_awburst   = Output(UInt(2.W))
  val m_axi_awlock    = Output(UInt(1.W))
  val m_axi_awcache   = Output(UInt(4.W))
  val m_axi_awprot    = Output(UInt(3.W))
  val m_axi_awregion  = Output(UInt(4.W))
  val m_axi_awqos     = Output(UInt(4.W))
  val m_axi_awvalid   = Output(Bool())
  val m_axi_awready   = Input(Bool())
  val m_axi_wdata     = Output(UInt(miDataBits.W))
  val m_axi_wstrb     = Output(UInt((miDataBits / 8).W))
  val m_axi_wlast     = Output(Bool())
  val m_axi_wvalid    = Output(Bool())
  val m_axi_wready    = Input(Bool())
  val m_axi_bid       = Input(UInt(miIdBits.W))
  val m_axi_bresp     = Input(UInt(2.W))
  val m_axi_bvalid    = Input(Bool())
  val m_axi_bready    = Output(Bool())
  val m_axi_arid      = Output(UInt(miIdBits.W))
  val m_axi_araddr    = Output(UInt(miAddrBits.W))
  val m_axi_arlen     = Output(UInt(8.W))
  val m_axi_arsize    = Output(UInt(3.W))
  val m_axi_arburst   = Output(UInt(2.W))
  val m_axi_arlock    = Output(UInt(1.W))
  val m_axi_arcache   = Output(UInt(4.W))
  val m_axi_arprot    = Output(UInt(3.W))
  val m_axi_arregion  = Output(UInt(4.W))
  val m_axi_arqos     = Output(UInt(4.W))
  val m_axi_arvalid   = Output(Bool())
  val m_axi_arready   = Input(Bool())
  val m_axi_rid       = Input(UInt(miIdBits.W))
  val m_axi_rdata     = Input(UInt(miDataBits.W))
  val m_axi_rresp     = Input(UInt(2.W))
  val m_axi_rlast     = Input(Bool())
  val m_axi_rvalid    = Input(Bool())
  val m_axi_rready    = Output(Bool())
}

class XilinxAXIDataWidthConverter
(
  siIdBits: Int, siAddrBits: Int, siDataBits: Int,
  miIdBits: Int, miAddrBits: Int, miDataBits: Int,
  override val desiredName: String
) extends BlackBox {
  val io = IO(new XilinxAXIDataWidthConverterIO(siIdBits, siAddrBits, siDataBits, miIdBits, miAddrBits, miDataBits))

  require(siDataBits <= miDataBits, "only SI->MI up-sizing is supported")

  ElaborationArtefacts.add(s"$desiredName.vivado.tcl",
    s"""
       |create_ip -name axi_dwidth_converter -vendor xilinx.com -library ip -version 2.1 -module_name $desiredName -dir $$ipdir -force
       |set_property -dict [list \\
       |  CONFIG.PROTOCOL {AXI4} \\
       |  CONFIG.ADDR_WIDTH {${miAddrBits max siAddrBits}} \\
       |  CONFIG.SI_DATA_WIDTH {${siDataBits}} \\
       |  CONFIG.MI_DATA_WIDTH {${miDataBits}} \\
       |  CONFIG.SI_ID_WIDTH {${siIdBits}} \\
       |  CONFIG.MI_ID_WIDTH {${miIdBits}} \\
       |  CONFIG.MAX_SPLIT_BEATS {16} \\
       |] [get_ips $desiredName]
       |""".stripMargin)
}

class LazyXilinxAXIDataWidthConverter(moduleNamePrefix: String, beatBytes: Int)(implicit p: Parameters) extends LazyModule {
  // present a `beatBytes` slave view to the upstream master (the FSA DMA)
  val node = AXI4AdapterNode(slaveFn = { sp => sp.copy(beatBytes = beatBytes) })
  val slaveClockNode = ClockSinkNode(Seq(ClockSinkParameters()))

  lazy val module = new LazyRawModuleImp(this) {
    require(node.in.size == 1 && node.out.size == 1)
    val (in, inEdge) = node.in.head
    val (out, outEdge) = node.out.head
    val slaveClock = slaveClockNode.in.head._1.clock
    val slaveReset = slaveClockNode.in.head._1.reset

    // the Xilinx IP needs at least one ID bit; the FSA DMA has a constant 0 ID
    val siIdBits = inEdge.bundle.idBits max 1
    val miIdBits = outEdge.bundle.idBits max 1

    val converter = Module(new XilinxAXIDataWidthConverter(
      siIdBits = siIdBits,
      siAddrBits = inEdge.bundle.addrBits,
      siDataBits = inEdge.bundle.dataBits,
      miIdBits = miIdBits,
      miAddrBits = outEdge.bundle.addrBits,
      miDataBits = outEdge.bundle.dataBits,
      desiredName = moduleNamePrefix + "_axi_data_width_converter"))

    converter.io.s_axi_aclk := slaveClock
    converter.io.s_axi_aresetn := !slaveReset.asBool

    // SI side driven by the FSA DMA
    converter.io.s_axi_awregion := 0.U
    converter.io.s_axi_awid := in.aw.bits.id
    converter.io.s_axi_awaddr := in.aw.bits.addr
    converter.io.s_axi_awlen := in.aw.bits.len
    converter.io.s_axi_awsize := in.aw.bits.size
    converter.io.s_axi_awburst := in.aw.bits.burst
    converter.io.s_axi_awlock := in.aw.bits.lock
    converter.io.s_axi_awcache := in.aw.bits.cache
    converter.io.s_axi_awprot := in.aw.bits.prot
    converter.io.s_axi_awqos := in.aw.bits.qos
    converter.io.s_axi_awvalid := in.aw.valid
    in.aw.ready := converter.io.s_axi_awready

    converter.io.s_axi_wdata := in.w.bits.data
    converter.io.s_axi_wstrb := in.w.bits.strb
    converter.io.s_axi_wlast := in.w.bits.last
    converter.io.s_axi_wvalid := in.w.valid
    in.w.ready := converter.io.s_axi_wready

    in.b.bits.id := converter.io.s_axi_bid
    in.b.bits.resp := converter.io.s_axi_bresp
    in.b.valid := converter.io.s_axi_bvalid
    converter.io.s_axi_bready := in.b.ready

    converter.io.s_axi_arregion := 0.U
    converter.io.s_axi_arid := in.ar.bits.id
    converter.io.s_axi_araddr := in.ar.bits.addr
    converter.io.s_axi_arlen := in.ar.bits.len
    converter.io.s_axi_arsize := in.ar.bits.size
    converter.io.s_axi_arburst := in.ar.bits.burst
    converter.io.s_axi_arlock := in.ar.bits.lock
    converter.io.s_axi_arcache := in.ar.bits.cache
    converter.io.s_axi_arprot := in.ar.bits.prot
    converter.io.s_axi_arqos := in.ar.bits.qos
    converter.io.s_axi_arvalid := in.ar.valid
    in.ar.ready := converter.io.s_axi_arready

    in.r.bits.id := converter.io.s_axi_rid
    in.r.bits.data := converter.io.s_axi_rdata
    in.r.bits.resp := converter.io.s_axi_rresp
    in.r.bits.last := converter.io.s_axi_rlast
    in.r.valid := converter.io.s_axi_rvalid
    converter.io.s_axi_rready := in.r.ready

    // MI side drives the xbar
    out.aw.bits.id := converter.io.m_axi_awid
    out.aw.bits.addr := converter.io.m_axi_awaddr
    out.aw.bits.len := converter.io.m_axi_awlen
    out.aw.bits.size := converter.io.m_axi_awsize
    out.aw.bits.burst := converter.io.m_axi_awburst
    out.aw.bits.lock := converter.io.m_axi_awlock
    out.aw.bits.cache := converter.io.m_axi_awcache
    out.aw.bits.prot := converter.io.m_axi_awprot
    out.aw.bits.qos := converter.io.m_axi_awqos
    out.aw.valid := converter.io.m_axi_awvalid
    converter.io.m_axi_awready := out.aw.ready

    out.w.bits.data := converter.io.m_axi_wdata
    out.w.bits.strb := converter.io.m_axi_wstrb
    out.w.bits.last := converter.io.m_axi_wlast
    out.w.valid := converter.io.m_axi_wvalid
    converter.io.m_axi_wready := out.w.ready

    converter.io.m_axi_bid := out.b.bits.id
    converter.io.m_axi_bresp := out.b.bits.resp
    converter.io.m_axi_bvalid := out.b.valid
    out.b.ready := converter.io.m_axi_bready

    out.ar.bits.id := converter.io.m_axi_arid
    out.ar.bits.addr := converter.io.m_axi_araddr
    out.ar.bits.len := converter.io.m_axi_arlen
    out.ar.bits.size := converter.io.m_axi_arsize
    out.ar.bits.burst := converter.io.m_axi_arburst
    out.ar.bits.lock := converter.io.m_axi_arlock
    out.ar.bits.cache := converter.io.m_axi_arcache
    out.ar.bits.prot := converter.io.m_axi_arprot
    out.ar.bits.qos := converter.io.m_axi_arqos
    out.ar.valid := converter.io.m_axi_arvalid
    converter.io.m_axi_arready := out.ar.ready

    converter.io.m_axi_rid := out.r.bits.id
    converter.io.m_axi_rdata := out.r.bits.data
    converter.io.m_axi_rresp := out.r.bits.resp
    converter.io.m_axi_rlast := out.r.bits.last
    converter.io.m_axi_rvalid := out.r.valid
    out.r.ready := converter.io.m_axi_rready
  }
}
