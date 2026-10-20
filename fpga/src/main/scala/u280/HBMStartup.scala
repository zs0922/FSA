package chipyard.fpga.u280

import chisel3._
import chisel3.util.HasBlackBoxInline
import freechips.rocketchip.amba.axi4.{AXI4Bundle, AXI4BundleParameters}

/** A single-bit, three-stage CDC synchronizer with asynchronous assertion of
  * reset. Keep the attributes in the emitted RTL, including for stand-alone
  * verification; no clock-group or data-path timing exception is added here.
  */
class HBMAsyncLevelSynchronizer extends BlackBox with HasBlackBoxInline {
  val io = IO(new Bundle {
    val clock = Input(Clock())
    val reset = Input(Bool())
    val d = Input(Bool())
    val q = Output(Bool())
  })
  setInline("HBMAsyncLevelSynchronizer.sv", """
module HBMAsyncLevelSynchronizer (
  input wire clock,
  input wire reset,
  input wire d,
  output wire q
);
  (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [2:0] sync = 3'b000;
  always @(posedge clock or posedge reset) begin
    if (reset) sync <= 3'b000;
    else sync <= {sync[1:0], d};
  end
  assign q = sync[2];
endmodule
""")
}

/** HBM cold-start sequencing across independently clocked APB and AXI domains.
  *
  * reset is a common cold reset: every assertion resets the HBM controller,
  * PHY and stack via APB PRESET_N, as well as AXI. The surrounding harness must
  * also reset masters and interconnect on this event, discarding in-flight work.
  * An AXI-only warm reset after traffic has begun is not supported by PG276.
  *
  * Unexpected loss of completion after startup is fail-stop. It blocks traffic
  * until a new common cold reset; it does not attempt lossless transaction or
  * memory recovery. The clocks must be free-running; PLL lock loss must assert
  * reset even when either clock has stopped.
  */
class HBMStartupControl extends RawModule {
  val io = IO(new Bundle {
    val apbClock = Input(Clock())
    val axiClock = Input(Clock())
    val reset = Input(Bool())
    val apbComplete = Input(Bool())
    val apbResetN = Output(Bool())
    val axiResetN = Output(Bool())
    val trafficReady = Output(Bool())
    val initializationFault = Output(Bool())
  })

  private def synchronize(clock: Clock, reset: Bool, d: Bool, name: String): Bool = {
    val sync = Module(new HBMAsyncLevelSynchronizer).suggestName(name)
    sync.io.clock := clock
    sync.io.reset := reset
    sync.io.d := d
    sync.io.q
  }

  // Assertion does not require either clock. Release needs three APB edges,
  // meeting PG276's minimum APB reset pulse of one APB clock period.
  val apbResetN = synchronize(io.apbClock, io.reset, true.B, "apbResetSync")
  io.apbResetN := apbResetN

  // Do not release the AXI interface while the APB domain is still in reset.
  // In particular, an absent APB clock cannot accidentally permit AXI traffic.
  val axiResetN = synchronize(io.axiClock, io.reset || !apbResetN, true.B, "axiResetSync")
  io.axiResetN := axiResetN

  val completeApb = withClockAndReset(io.apbClock, (!apbResetN).asAsyncReset) {
    RegNext(io.apbComplete, false.B).suggestName("completeApb")
  }
  val completeAxi = synchronize(io.axiClock, !axiResetN, completeApb, "completeSync")

  withClockAndReset(io.axiClock, (!axiResetN).asAsyncReset) {
    val started = RegInit(false.B)
    val fault = RegInit(false.B)
    when (completeAxi) { started := true.B }
    when (started && !completeAxi) { fault := true.B }
    io.trafficReady := axiResetN && started && completeAxi && !fault
    io.initializationFault := fault
  }
}

/** Block all five AXI channels until HBM initialization has completed.
  * Both sides of every handshake are blocked together, so upstream cannot
  * consume a request or response that the downstream did not consume.
  * Once enabled this is combinational: no pipeline stage is added.
  */
class HBMAXIStartupGate(params: AXI4BundleParameters) extends RawModule {
  val io = IO(new Bundle {
    val enable = Input(Bool())
    val in = Flipped(new AXI4Bundle(params))
    val out = new AXI4Bundle(params)
  })

  io.out <> io.in
  io.out.aw.valid := io.in.aw.valid && io.enable
  io.in.aw.ready := io.out.aw.ready && io.enable
  io.out.w.valid := io.in.w.valid && io.enable
  io.in.w.ready := io.out.w.ready && io.enable
  io.out.ar.valid := io.in.ar.valid && io.enable
  io.in.ar.ready := io.out.ar.ready && io.enable
  io.in.b.valid := io.out.b.valid && io.enable
  io.out.b.ready := io.in.b.ready && io.enable
  io.in.r.valid := io.out.r.valid && io.enable
  io.out.r.ready := io.in.r.ready && io.enable
}
