// Frozen historical oracle from easyfloat 44e1c369659c2c397548d2aecfc4ded3a74a111e; renamed test-only declarations.
package easyfloat

import circt.stage.ChiselStage
import chisel3._
import chisel3.util._


class A70LegacyRawFloat_SplitIF(expWidth: Int, mantissaWidth: Int, nMSBs: Int) extends Module {
  val io = IO(new Bundle {
    val in = Input(new RawFloat(expWidth, mantissaWidth))
    val outInt = Output(SInt(expWidth.W))
    val outFracMSBs = Output(UInt(nMSBs.W))
    val outRawFloat = Output(new RawFloat(expWidth, mantissaWidth))
  })

  val rawIn = io.in
  io.outRawFloat.sign := rawIn.sign
  io.outRawFloat.isZero := rawIn.isZero
  io.outRawFloat.isNaN := rawIn.isNaN
  io.outRawFloat.isInf := rawIn.isInf

  when(rawIn.exp < 0.S) {
    // the integer part is already zero
    val rightShiftAmt = (-1).S - rawIn.exp
    val fracAlignedToMinusOne = rawIn.mantissa >> rightShiftAmt.take(nMSBs.U.getWidth)
    io.outInt := 0.S
    io.outRawFloat.exp := rawIn.exp
    io.outRawFloat.mantissa := rawIn.mantissa
    io.outFracMSBs := Mux(rightShiftAmt < nMSBs.S, fracAlignedToMinusOne.head(nMSBs), 0.U)
  }.otherwise({
    // exp >= 0
    val exp = rawIn.exp.tail(1)
    /*
      rawIn:
      1.xxxx * 2^n  -> 1.xxx.x00 * 2^0
    */
    val intBits = expWidth - 1
    // we already have 1 integer bit
    val maxShift = intBits - 1
    val shiftedMantissa = (rawIn.mantissa << exp.take(maxShift.U.getWidth)).take(mantissaWidth + maxShift)
    val xi = shiftedMantissa.head(intBits)
    val xf = shiftedMantissa.tail(intBits)
    require(xf.getWidth == mantissaWidth - 1)
    when(exp > maxShift.U) {
      io.outInt := 0.S
      io.outRawFloat.exp := rawIn.exp
      io.outRawFloat.mantissa := rawIn.mantissa
      io.outRawFloat.isInf := true.B
      io.outFracMSBs := 0.U
    }.otherwise({
      io.outInt := Mux(rawIn.sign, -xi.zext, xi.zext)
      io.outFracMSBs := xf.head(nMSBs)
      when(xf === 0.U) {
        io.outRawFloat.exp := 0.S
        io.outRawFloat.mantissa := 0.U
        io.outRawFloat.isZero := true.B
      }.otherwise({
        val lzc = PriorityEncoder((0.U(1.W) ## xf).asBools.reverse)
        io.outRawFloat.exp := -lzc.zext
        io.outRawFloat.mantissa := (xf << lzc).take(mantissaWidth)
      })
    })
  })
}

class A70LegacyRawFloat_MulAddExp2
(
  mulExpWidth: Int, mulMantissaWidth: Int,
  addExpWidth: Int, addMantissaWidth: Int,
  pwlConst: Either[Seq[(BigInt, BigInt)], Int]
) extends Module {

  val fma = Module(new A70LegacyRawFloat_FMA(
    aEW = mulExpWidth, aMW = mulMantissaWidth,
    bEW = mulExpWidth, bMW = mulMantissaWidth,
    cEW = addExpWidth, cMW = addMantissaWidth
  ))

  val io = IO(new Bundle {
    val in_exp2 = Input(Bool())
    val in_a = Input(new RawFloat(mulExpWidth, mulMantissaWidth))
    val in_b = Input(new RawFloat(mulExpWidth, mulMantissaWidth))
    val in_c = Input(new RawFloat(addExpWidth, addMantissaWidth))
    val out = Output(new RawFloat(fma.outEW, addMantissaWidth + 2))
    val exp2_frac_msb = Output(UInt(pwlConst match {
      case Left(seq) => log2Up(seq.length).W
      case Right(n) => log2Up(n).W
    }))
  })

  val split = pwlConst match {
    case Left(seq) =>
      Module(new A70LegacyRawFloat_SplitIF(mulExpWidth, mulMantissaWidth, log2Up(seq.length)))
    case Right(n) =>
      Module(new A70LegacyRawFloat_SplitIF(mulExpWidth, mulMantissaWidth, log2Up(n)))
  }

  split.io.in := io.in_a
  fma.io.a := io.in_a
  fma.io.b := io.in_b
  fma.io.c := io.in_c
  io.out := fma.io.out
  io.exp2_frac_msb := split.io.outFracMSBs
  when(io.in_exp2) {
    fma.io.a := split.io.outRawFloat
    io.out.sign := false.B
    io.out.isInf := false.B
    io.out.isZero := fma.io.out.isInf
    io.out.exp := split.io.outInt + fma.io.out.exp
  }
  pwlConst match {
    case Left(seq) =>
      val (slopes, intercepts) = seq.unzip
      val mulIEEEWidth = 1 + mulExpWidth - 1 + mulMantissaWidth - 1
      val addIEEEWidth = 1 + addExpWidth - 1 + addMantissaWidth - 1
      val fSlopes = VecInit(slopes.map(_.U(mulIEEEWidth.W)).reverse)
      val fIntercepts = VecInit(intercepts.map(_.U(addIEEEWidth.W)).reverse)
      when(io.in_exp2) {
        fma.io.b.fromIEEE(fSlopes(split.io.outFracMSBs), mulExpWidth - 1, mulMantissaWidth - 1)
        fma.io.c.fromIEEE(fIntercepts(split.io.outFracMSBs), addExpWidth - 1, addMantissaWidth - 1)
      }
    case Right(n) =>
      // slopes and intercepts are provided outside
  }
}

