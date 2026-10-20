package chipyard.fpga.u280

import chisel3._
import freechips.rocketchip.amba.axi4._
import _root_.circt.stage.ChiselStage

/** Test adapter around the production startup controller and AXI gate.
  * Packed payloads expose every field to the external randomized driver.
  * No alternate implementation of the controller or gate lives here.
  */
class HBMStartupVerificationHarness extends RawModule {
  val params = AXI4BundleParameters(addrBits = 34, dataBits = 256, idBits = 6)
  val io = IO(new Bundle {
    val apbClock = Input(Clock())
    val axiClock = Input(Clock())
    val reset = Input(Bool())
    val apbComplete = Input(Bool())
    val apbResetN = Output(Bool())
    val axiResetN = Output(Bool())
    val trafficReady = Output(Bool())
    val initializationFault = Output(Bool())
    val pllMonitorClock = Input(Clock())
    val pllRawReset = Input(Bool())
    val pllExternalReset = Input(Bool())
    val pllPcieResetN = Output(Bool())
    val controls = Input(UInt(10.W))
    val observed = Output(UInt(10.W))
    val aw = Input(UInt((new AXI4BundleAW(params)).getWidth.W))
    val w = Input(UInt((new AXI4BundleW(params)).getWidth.W))
    val b = Input(UInt((new AXI4BundleB(params)).getWidth.W))
    val ar = Input(UInt((new AXI4BundleAR(params)).getWidth.W))
    val r = Input(UInt((new AXI4BundleR(params)).getWidth.W))
    val payloadMatch = Output(Bool())
  })
  val startup = Module(new HBMStartupControl)
  startup.io.apbClock := io.apbClock
  startup.io.axiClock := io.axiClock
  startup.io.reset := io.reset
  startup.io.apbComplete := io.apbComplete
  io.apbResetN := startup.io.apbResetN
  io.axiResetN := startup.io.axiResetN
  io.trafficReady := startup.io.trafficReady
  io.initializationFault := startup.io.initializationFault

  // A non-power-of-two count catches terminal-count and reset/restart errors.
  // Product hardware uses 10000 cycles; the state machine is identical here.
  val pllReset = Module(new HBMPLLResetControl(stableCycles = 7))
  pllReset.io.monitorClock := io.pllMonitorClock
  pllReset.io.rawPllReset := io.pllRawReset
  pllReset.io.externalReset := io.pllExternalReset
  io.pllPcieResetN := pllReset.io.pcieResetN

  val gate = Module(new HBMAXIStartupGate(params))
  gate.io.enable := startup.io.trafficReady
  gate.io.in.aw.bits := io.aw.asTypeOf(gate.io.in.aw.bits)
  gate.io.in.w.bits := io.w.asTypeOf(gate.io.in.w.bits)
  gate.io.out.b.bits := io.b.asTypeOf(gate.io.out.b.bits)
  gate.io.in.ar.bits := io.ar.asTypeOf(gate.io.in.ar.bits)
  gate.io.out.r.bits := io.r.asTypeOf(gate.io.out.r.bits)
  gate.io.in.aw.valid := io.controls(0)
  gate.io.in.w.valid := io.controls(1)
  gate.io.in.ar.valid := io.controls(2)
  gate.io.in.b.ready := io.controls(3)
  gate.io.in.r.ready := io.controls(4)
  gate.io.out.aw.ready := io.controls(5)
  gate.io.out.w.ready := io.controls(6)
  gate.io.out.ar.ready := io.controls(7)
  gate.io.out.b.valid := io.controls(8)
  gate.io.out.r.valid := io.controls(9)
  io.observed := chisel3.util.Cat(
    gate.io.in.r.valid, gate.io.in.b.valid,
    gate.io.in.ar.ready, gate.io.in.w.ready, gate.io.in.aw.ready,
    gate.io.out.r.ready, gate.io.out.b.ready,
    gate.io.out.ar.valid, gate.io.out.w.valid, gate.io.out.aw.valid)
  io.payloadMatch := gate.io.out.aw.bits.asUInt === io.aw &&
    gate.io.out.w.bits.asUInt === io.w && gate.io.in.b.bits.asUInt === io.b &&
    gate.io.out.ar.bits.asUInt === io.ar && gate.io.in.r.bits.asUInt === io.r
}

object HBMStartupVerification {
  def main(args: Array[String]): Unit = {
    require(args.length == 1, "Expected a fresh RTL output directory")
    ChiselStage.emitSystemVerilogFile(new HBMStartupVerificationHarness,
      // Split output materializes HasBlackBoxInline files. Select the same
      // PATH-resolved firtool recorded by the reproducible build environment.
      Array("--target-dir", args(0), "--split-verilog", "--firtool-binary-path", "firtool"),
      Array("--disable-all-randomization", "--strip-debug-info", "--lowering-options=disallowLocalVariables"))
  }
}
