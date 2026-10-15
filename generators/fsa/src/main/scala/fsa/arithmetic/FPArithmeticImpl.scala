package fsa.arithmetic

import chisel3._
import chisel3.util._
import easyfloat._
import ArithmeticSyntax._

final class FloatPoint(val expWidth: Int, val mantissaWidth: Int) extends Bundle {
  val sign = Bool()
  val exp = UInt(expWidth.W)
  val mantissa = UInt(mantissaWidth.W)
}

object FloatPoint {
  def apply(ew: Int, mw: Int): FloatPoint = new FloatPoint(ew, mw)
}

class FPMacUnit(mulEW: Int, mulMW: Int, addEW: Int, addMW: Int, pwlPieces: Int,
  afterAdd: Boolean = false) extends
  MacUnit[FloatPoint, FloatPoint](FloatPoint(mulEW, mulMW), FloatPoint(addEW, addMW))
{
  val mulAddExp2 = Module(new RawFloat_MulAddExp2(
    1 + mulEW, 1 + mulMW, 1 + addEW, 1 + addMW, Right(pwlPieces), afterAdd = afterAdd
  ))
  mulAddExp2.io.in_a.fromIEEE(io.in_a.asUInt, mulEW, mulMW)
  mulAddExp2.io.in_b.fromIEEE(io.in_b.asUInt, mulEW, mulMW)
  mulAddExp2.io.in_c.fromIEEE(io.in_c.asUInt, addEW, addMW)
  mulAddExp2.io.in_exp2 := io.in_cmd === MacCMD.EXP2
  when(mulAddExp2.io.in_exp2) {
    // restore the correct exponent for in_c
    val c = WireInit(io.in_c)
    // set exp[MSB:1] to exp_bias[MSB:1]
    c.exp := Fill(addEW - 2, true.B) ## io.in_c.exp(0)
    mulAddExp2.io.in_c.fromIEEE(c.asUInt, addEW, addMW)
  }
  val pwlIndex = (io.in_c.exp >> 1.U).take(log2Up(pwlPieces))

  io.out_accType := Rounding.round(mulAddExp2.io.out, RoundingMode.RNE, addEW, addMW).asTypeOf(accType)
  io.out_elemType := Rounding.round(mulAddExp2.io.out, RoundingMode.RNE, mulEW, mulMW).asTypeOf(elemType)
  val exp2Hit = mulAddExp2.io.in_exp2 && pwlIndex === mulAddExp2.io.exp2_frac_msb
  io.out_exp2 := (if (afterAdd) RegNext(exp2Hit, false.B) else exp2Hit)
}



class FPAccUnit(addEW: Int, addMW: Int, pwlPieces: Int) extends
  MacUnit[FloatPoint, FloatPoint](FloatPoint(addEW, addMW), FloatPoint(addEW, addMW)) with HasMultiCycleIO
{
  // for accumulator, we use in-place pwl constants
  // TODO: avoid hard code projectDir
  val slopes = PyFPConst.slopes(addEW, addMW, projectDir = "generators/easyfloat", pieces = pwlPieces)
  val intercepts = PyFPConst.intercepts(addEW, addMW, projectDir = "generators/easyfloat", pieces = pwlPieces)
  val pwlConst = slopes.zip(intercepts)
  val mulAddExp2 = Module(new RawFloat_MulAddExp2(
    1 + addEW, 1 + addMW, 1 + addEW, 1 + addMW, Left(pwlConst),
    pipeline = true
  ))
  mulAddExp2.io.in_a.fromIEEE(io.in_a.asUInt, addEW, addMW)
  mulAddExp2.io.in_b.fromIEEE(io.in_b.asUInt, addEW, addMW)
  mulAddExp2.io.in_c.fromIEEE(io.in_c.asUInt, addEW, addMW)
  mulAddExp2.io.in_exp2 := io.in_cmd === MacCMD.EXP2

  val reciprocal = Module(new Reciprocal(addEW, addMW, fmaLatency = FPAccUnit.latency))
  reciprocal.io.in := io.in_a.asUInt
  reciprocal.io.in_valid := multiCycleIO.reciprocal_in_valid
  reciprocal.io.fma_rounded_result := io.out_accType.asUInt
  mulAddExp2.io.in_exp2 := !reciprocal.io.in_valid && io.in_cmd === MacCMD.EXP2
  when(reciprocal.io.in_valid) {
    mulAddExp2.io.in_a := reciprocal.io.fma_rawA
    mulAddExp2.io.in_b := reciprocal.io.fma_rawB
    mulAddExp2.io.in_c := reciprocal.io.fma_rawC
  }
  multiCycleIO.reciprocal_out_valid := reciprocal.io.out.valid

  // output: rounding is the combinational tail of the pipeline;
  // total in->out latency = FPAccUnit.latency
  io.out_accType := Rounding.round(mulAddExp2.io.out, RoundingMode.RNE, addEW, addMW).asTypeOf(accType)
  io.out_elemType := io.out_accType
  io.out_exp2 := mulAddExp2.io.in_exp2
}

object FPAccUnit {
  // pipeline depth of the accumulator datapath:
  //   1 (operand register in RawFloat_MulAddExp2)
  // + 2 (internal stages of RawFloat_FMA)
  // NOTE: constrained to 3 by AttentionScore's fixed wavefront budget
  // (EXP_S1 -> [2L+1] -> ACC_SA must fit in 8 cycles)
  def latency: Int = 3
}

class FPCmpUnit(ew: Int, mw: Int, afterAdd: Boolean = false) extends CmpUnit(FloatPoint(ew, mw)) {
  val fma = Module(new FMA(ew, mw, ew, mw, afterAdd = afterAdd))
  val negB = WireInit(io.in_b)
  negB.sign := !io.in_b.sign
  // always compute a - b = a * 1.0 + -b
  fma.io.a := io.in_a.asUInt
  fma.io.b := accType.one.asUInt
  fma.io.c := negB.asUInt
  // The feedback recurrence must remain combinational when diff is pipelined.
  // Match the rounded subtraction sign, including FTZ and canonical positive NaN.
  val max = if (afterAdd) {
    val aNaN = io.in_a.exp.andR && io.in_a.mantissa.orR
    val bNaN = io.in_b.exp.andR && io.in_b.mantissa.orR
    val aInf = io.in_a.exp.andR && !io.in_a.mantissa.orR
    val bInf = io.in_b.exp.andR && !io.in_b.mantissa.orR
    val aZero = io.in_a.exp === 0.U
    val bZero = io.in_b.exp === 0.U
    val aMagnitude = Mux(aZero, 0.U, io.in_a.exp ## io.in_a.mantissa)
    val bMagnitude = Mux(bZero, 0.U, io.in_b.exp ## io.in_b.mantissa)
    val finiteLess = Mux(io.in_a.sign =/= io.in_b.sign,
      io.in_a.sign,
      Mux(io.in_a.sign, aMagnitude > bMagnitude, aMagnitude < bMagnitude))
    val bothZeroSign = io.in_a.sign && !io.in_b.sign
    val diffNegative = Mux(aNaN || bNaN || (aInf && bInf && io.in_a.sign === io.in_b.sign),
      false.B,
      Mux(aZero && bZero, bothZeroSign, finiteLess))
    Mux(diffNegative, io.in_b, io.in_a)
  } else {
    Mux(fma.io.out.asTypeOf(accType).sign, io.in_b, io.in_a)
  }
  io.out_max := max
  io.out_diff := fma.io.out.asTypeOf(accType)
}

object FPArithmeticImpl {
  def pePipelineEnabled: Boolean = sys.props.get("fsa.pePipeline") match {
    case None | Some("false") => false
    case Some("true") => true
    case Some(value) => throw new IllegalArgumentException(s"fsa.pePipeline must be true or false, got $value")
  }
}

class FPArithmeticImpl(mulEW: Int, mulMW: Int, addEW: Int, addMW: Int, pwlPieces: Int = 8,
  pePipeline: Boolean = FPArithmeticImpl.pePipelineEnabled)
  extends ArithmeticImpl[FloatPoint, FloatPoint]
{

  require(mulEW <= addEW && mulMW <= addMW)

  val isMixedPrecision = !(mulEW == addEW && mulMW == addMW)

  override def elemType: FloatPoint = FloatPoint(mulEW, mulMW)

  override def accType: FloatPoint = FloatPoint(addEW, addMW)

  override def peMac: MacUnit[FloatPoint, FloatPoint] = new FPMacUnit(mulEW, mulMW, addEW, addMW, pwlPieces, afterAdd = pePipeline)

  override def accUnit: MacUnit[FloatPoint, FloatPoint] with HasMultiCycleIO = new FPAccUnit(addEW, addMW, pwlPieces)

  override def accCmp: CmpUnit[FloatPoint] = new FPCmpUnit(addEW, addMW, afterAdd = pePipeline)

  override val peLatency: Int = if (pePipeline) 1 else 0
  override val cmpLatency: Int = if (pePipeline) 1 else 0
  override val spadLatency: Int = if (pePipeline) 2 else 1

  override val reciprocalLatency: Int = Reciprocal.nCycles(addMW, FPAccUnit.latency)
  override val accLatency: Int = FPAccUnit.latency

  override val exp2PwlPieces: Int = pwlPieces


  /*
    Slopes and intercepts for exp2 piecewise linear approximation.
    The slopes and intercepts generated by python are ordered by increasing x.
    In our PE, we use the exponent MSBs to select the piece,
    since x is in range (-1, 0], a larger exponent means smaller value,
    we reverse the order to get decreasing values.

    We have to attach the piece index information with the intercepts for
    PEs to select the right intercept.

    Notice that all intercepts are in the range of (0.5, 1], their exponent
    must be either 0 or -1, in the IEEE format, it would be exp_bias or exp_bias - 1,
    where only the lsb can be different, so we can safely place the counter in the
    exponent field.

    Here we replace exponent[MSB:1] with the counter value and only keep the lsb.
    Inside the PE, we can simply use `exp_bias[MSB:1] ## exp[0]` to get the exponent back.
  */
  override def exp2PwlIntercepts: Seq[FloatPoint] = {
    require(addEW - 1 >= log2Up(pwlPieces))
    val rawIntercepts = PyFPConst.intercepts(
      addEW, addMW,
      projectDir = "generators/easyfloat", pieces = pwlPieces
    ).reverse
    rawIntercepts.zipWithIndex.map{ case (raw, i) =>
      val rawFp = WireInit(raw.U((1 + addEW + addMW).W).asTypeOf(FloatPoint(addEW, addMW)))
      val fp = WireInit(rawFp)
      fp.exp := i.U ## rawFp.exp(0)
      fp
    }
  }

  override def exp2PwlSlopes: Seq[FloatPoint] = PyFPConst.slopes(
    mulEW, mulMW,
    projectDir = "generators/easyfloat", pieces = pwlPieces
  ).map(_.U((1 + mulEW + mulMW).W).asTypeOf(FloatPoint(mulEW, mulMW))).reverse

  override def viewAasE: FloatPoint => FloatPoint = { (a: FloatPoint) =>
    if (isMixedPrecision) {
      a.asUInt.take(elemType.getWidth).asTypeOf(elemType)
    } else a
  }

  override def viewEasA: FloatPoint => FloatPoint = { (e: FloatPoint) =>
    if (isMixedPrecision) {
      (0.U((accType.getWidth - elemType.getWidth).W) ## e.asUInt).asTypeOf(accType)
    } else e
  }

  override def cvtAtoE: FloatPoint => FloatPoint = { (a: FloatPoint) =>
    if (isMixedPrecision) {
      val rawA = Wire(new RawFloat(1 + addEW, 1 + addMW))
      rawA.fromIEEE(a.asUInt, addEW, addMW)
      Rounding.round(rawA, RoundingMode.RNE, mulEW, mulMW).asTypeOf(elemType)
    } else {
      a
    }
  }
}
