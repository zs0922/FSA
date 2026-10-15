package easyfloat

import circt.stage.ChiselStage
import chisel3._
import chisel3.util._


class RawFloat_SplitIF(expWidth: Int, mantissaWidth: Int, nMSBs: Int) extends Module {
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

class RawFloat_MulAddExp2
(
  mulExpWidth: Int, mulMantissaWidth: Int,
  addExpWidth: Int, addMantissaWidth: Int,
  pwlConst: Either[Seq[(BigInt, BigInt)], Int],
  pipeline: Boolean = false,
  afterAdd: Boolean = false
) extends Module {

  val fma = Module(new RawFloat_FMA(
    aEW = mulExpWidth, aMW = mulMantissaWidth,
    bEW = mulExpWidth, bMW = mulMantissaWidth,
    cEW = addExpWidth, cMW = addMantissaWidth,
    nStages = if (pipeline) 2 else 0,
    afterAdd = afterAdd
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
      Module(new RawFloat_SplitIF(mulExpWidth, mulMantissaWidth, log2Up(seq.length)))
    case Right(n) =>
      Module(new RawFloat_SplitIF(mulExpWidth, mulMantissaWidth, log2Up(n)))
  }

  split.io.in := io.in_a

  // FMA operands after exp2 piecewise-linear overrides (stage-0 combinational)
  val fa = Wire(chiselTypeOf(io.in_a))
  val fb = Wire(chiselTypeOf(io.in_b))
  val fc = Wire(chiselTypeOf(io.in_c))
  fa := io.in_a
  fb := io.in_b
  fc := io.in_c
  when(io.in_exp2) {
    fa := split.io.outRawFloat
  }
  pwlConst match {
    case Left(seq) =>
      val (slopes, intercepts) = seq.unzip
      val mulIEEEWidth = 1 + mulExpWidth - 1 + mulMantissaWidth - 1
      val addIEEEWidth = 1 + addExpWidth - 1 + addMantissaWidth - 1
      val fSlopes = VecInit(slopes.map(_.U(mulIEEEWidth.W)).reverse)
      val fIntercepts = VecInit(intercepts.map(_.U(addIEEEWidth.W)).reverse)
      when(io.in_exp2) {
        fb.fromIEEE(fSlopes(split.io.outFracMSBs), mulExpWidth - 1, mulMantissaWidth - 1)
        fc.fromIEEE(fIntercepts(split.io.outFracMSBs), addExpWidth - 1, addMantissaWidth - 1)
      }
    case Right(n) =>
      // slopes and intercepts are provided outside
  }

  io.exp2_frac_msb := split.io.outFracMSBs

  if (pipeline) {
    // entry pipeline register (1 cycle) in front of the (also pipelined) FMA
    val faR = Reg(chiselTypeOf(fa)); faR := fa
    val fbR = Reg(chiselTypeOf(fb)); fbR := fb
    val fcR = Reg(chiselTypeOf(fc)); fcR := fc
    fma.io.a := faR
    fma.io.b := fbR
    fma.io.c := fcR
    // delay-match the split integer part to the FMA output:
    // total latency = 1 (entry reg) + 2 (FMA internal regs) = 3
    val inExp2D = ShiftRegister(io.in_exp2, 3)
    val outIntD = ShiftRegister(split.io.outInt, 3)
    io.out := fma.io.out
    when(inExp2D) {
      io.out.sign := false.B
      io.out.isInf := false.B
      io.out.isZero := fma.io.out.isInf
      io.out.exp := outIntD + fma.io.out.exp
    }
  } else {
    fma.io.a := fa
    fma.io.b := fb
    fma.io.c := fc
    io.out := fma.io.out
    val inExp2D = if (afterAdd) RegNext(io.in_exp2, false.B) else io.in_exp2
    val outIntD = if (afterAdd) RegNext(split.io.outInt) else split.io.outInt
    when(inExp2D) {
      io.out.sign := false.B
      io.out.isInf := false.B
      io.out.isZero := fma.io.out.isInf
      io.out.exp := outIntD + fma.io.out.exp
    }
  }
}

class MulAddExp2(expWidth: Int, mantissaWidth: Int, pwlConst: Seq[(BigInt, BigInt)]) extends Module {
  val io = IO(new Bundle {
    val in_exp2 = Input(Bool())
    val in_a = Input(UInt((1 + expWidth + mantissaWidth).W))
    val in_b = Input(UInt((1 + expWidth + mantissaWidth).W))
    val in_c = Input(UInt((1 + expWidth + mantissaWidth).W))
    val out = Output(UInt((1 + expWidth + mantissaWidth).W))
  })
  val raw = Module(new RawFloat_MulAddExp2(
    expWidth + 1, mantissaWidth + 1,
    expWidth + 1, mantissaWidth + 1,
    Left(pwlConst)
  ))
  raw.io.in_exp2 := io.in_exp2
  raw.io.in_a.fromIEEE(io.in_a, expWidth, mantissaWidth)
  raw.io.in_b.fromIEEE(io.in_b, expWidth, mantissaWidth)
  raw.io.in_c.fromIEEE(io.in_c, expWidth, mantissaWidth)
  io.out := Rounding.round(raw.io.out, RoundingMode.RNE, expWidth, mantissaWidth)
}

class MulAddExp2Rec(expWidth: Int, mantissaWidth: Int, pwlConst: Seq[(BigInt, BigInt)]) extends Module {
  val io = IO(new Bundle {
    val in_exp2 = Input(Bool())
    val in_reciprocal = Input(Bool())
    val in_a = Input(UInt((1 + expWidth + mantissaWidth).W))
    val in_b = Input(UInt((1 + expWidth + mantissaWidth).W))
    val in_c = Input(UInt((1 + expWidth + mantissaWidth).W))
    val out = Output(UInt((1 + expWidth + mantissaWidth).W))
    val out_reciprocal = Output(Bool())
  })
  val reciprocal = Module(new Reciprocal(expWidth, mantissaWidth))
  val fma = Module(new RawFloat_MulAddExp2(
    expWidth + 1, mantissaWidth + 1,
    expWidth + 1, mantissaWidth + 1,
    Left(pwlConst)
  ))
  reciprocal.io.in := io.in_a
  reciprocal.io.in_valid := io.in_reciprocal
  reciprocal.io.fma_rounded_result := io.out
  fma.io.in_exp2 := io.in_exp2
  when(io.in_reciprocal) {
    fma.io.in_a := reciprocal.io.fma_rawA
    fma.io.in_b := reciprocal.io.fma_rawB
    fma.io.in_c := reciprocal.io.fma_rawC
  }.otherwise({
    fma.io.in_a.fromIEEE(io.in_a, expWidth, mantissaWidth)
    fma.io.in_b.fromIEEE(io.in_b, expWidth, mantissaWidth)
    fma.io.in_c.fromIEEE(io.in_c, expWidth, mantissaWidth)
  })
  io.out := Rounding.round(fma.io.out, RoundingMode.RNE, expWidth, mantissaWidth)
  io.out_reciprocal := reciprocal.io.out.valid
}

object MulAddExp2 {
  def main(args: Array[String]): Unit = {
    val slopes = PyFPConst.slopes(5, 10)
    val intercepts = PyFPConst.intercepts(5, 10)
    ChiselStage.emitSystemVerilogFile(new MulAddExp2Rec(5, 10, slopes.zip(intercepts)))
  }
}

