package easyfloat

import chisel3._
import chisel3.util._

/*
Newton-Raphson based reciprocal

d: 1.xxxxx <- [1, 2)
1/d: (1/2, 1]

R[0] = 1
R[j+1] = R[j](2 - R[j]d)
*/

/*
Important!!!
1. The module itself doesn't hold io.in!!!
    io.in must remain unchanged from in_valid to out.valid
2. The module is not pipelined (one Newton-Raphson op in flight at a time),
   but it tolerates a pipelined host FMA: with fmaLatency = L, each FMA
   result arrives L cycles after its operands are presented, so dependent
   ops are issued every (L + 1) cycles.
*/
class Reciprocal(expWidth: Int, mantissaWidth: Int, fmaLatency: Int = 0) extends Module {

  val io = IO(new Bundle {
    val in = Input(UInt((1 + expWidth + mantissaWidth).W))
    val in_valid = Input(Bool())
    val out = Valid(UInt((1 + expWidth + mantissaWidth).W))
    val fma_rawA = Output(new RawFloat(1 + expWidth, 1 + mantissaWidth))
    val fma_rawB = Output(new RawFloat(1 + expWidth, 1 + mantissaWidth))
    val fma_rawC = Output(new RawFloat(1 + expWidth, 1 + mantissaWidth))
    val fma_rounded_result = Input(UInt((1 + expWidth + mantissaWidth).W))
  })

  /* x = 1.xxx * 2^exp = (1.xxx * 2^0) * (1.0 * 2^exp)
     xm = 1.xxx * 2^0
     xe = 1.0 * 2^exp
   */
  val xRaw = Wire(new RawFloat(1 + expWidth, 1 + mantissaWidth))
  val xm = WireInit(xRaw)
  val xe = WireInit(xRaw)
  xRaw.fromIEEE(io.in, expWidth, mantissaWidth)
  // override
  xm.sign := true.B // to get -xm * R[j]
  xm.exp := 0.S
  // override
  xe.exp := -xRaw.exp // 1 / x^exp = x ^ -exp
  xe.mantissa := 1.B ## 0.U(mantissaWidth.W)
  xe.isNaN := xRaw.isNaN
  xe.isInf := xRaw.isZero
  xe.isZero := xRaw.isInf

  val nCycles = Reciprocal.nCycles(mantissaWidth, fmaLatency)
  // cnt = index of the FMA op currently presented (0 .. nOps, inclusive)
  val nOps = 2 * Reciprocal.nIterations(mantissaWidth)
  val cnt = RegInit(0.U((log2Up(nOps + 1)).W))
  // age = cycles elapsed since the current op was presented
  val age = RegInit(0.U((log2Up(fmaLatency + 1)).W))
  val resultHere = age === fmaLatency.U
  val done = cnt === nOps.U && resultHere

  when(done) {
    cnt := 0.U
    age := 0.U
  }.elsewhen(io.in_valid && resultHere) {
    // the result of op `cnt` is on fma_rounded_result this cycle:
    // consume it (below) and present the next op
    cnt := cnt + 1.U
    age := 0.U
  }.elsewhen(io.in_valid) {
    age := age + 1.U
  }

  // r0 = 1.0 TODO: improve r0
  val r0 = Wire(chiselTypeOf(xm))
  r0.sign := false.B
  r0.exp := 0.S
  r0.mantissa := (BigInt(1) << mantissaWidth).U
  r0.isZero := false.B
  r0.isInf := false.B
  r0.isNaN := false.B

  val two = Wire(chiselTypeOf(xm))
  two.sign := false.B
  two.exp := 1.S
  two.mantissa := (BigInt(1) << mantissaWidth).U
  two.isZero := false.B
  two.isInf := false.B
  two.isNaN := false.B

  val zero = Wire(chiselTypeOf(xm))
  zero := 0.U.asTypeOf(zero)
  zero.isZero := true.B

  val reg_r = RegInit(r0)
  val reg_p = Reg(chiselTypeOf(r0))

  when(io.in_valid && resultHere) {
    when(cnt === nOps.U) {
      // final result (R * 2^-exp) is on fma_rounded_result;
      // reset the iteration state for the next reciprocal
      reg_r := r0
    }.elsewhen(cnt(0)) {
      // R[j+1] = R[j] * P[j]
      reg_r.fromIEEE(io.fma_rounded_result, expWidth, mantissaWidth)
    }.otherwise({
      // P[j] = 2 - R[j] * d
      reg_p.fromIEEE(io.fma_rounded_result, expWidth, mantissaWidth)
    })
  }

  io.fma_rawA := reg_r
  when(cnt === nOps.U) {
    io.fma_rawB := xe
    io.fma_rawC := zero
  }.elsewhen(cnt(0)) {
    io.fma_rawB := reg_p
    io.fma_rawC := zero
  }.otherwise({
    io.fma_rawB := xm
    io.fma_rawC := two
  })

  io.out.valid := done
  io.out.bits := io.fma_rounded_result
}


object Reciprocal {
  // TODO: 4 is an empirical value to fix the round-off error
  def nIterations(ieeeMantissaWidth: Int) = log2Up(ieeeMantissaWidth + 1) + 4
  /*
     2 fma per iteration + last fma. With a pipelined host FMA (fmaLatency = L)
     each dependent FMA op occupies L + 1 cycles: op k is presented at cycle
     k * (L + 1) and its result is available at k * (L + 1) + L, so the whole
     sequence spans (nOps + 1) * (L + 1) cycles = 2*nIterations*(L+1) + L + 1.
   */
  def nCycles(ieeeMantissaWidth: Int, fmaLatency: Int = 0) =
    2 * nIterations(ieeeMantissaWidth) * (fmaLatency + 1) + fmaLatency + 1
}