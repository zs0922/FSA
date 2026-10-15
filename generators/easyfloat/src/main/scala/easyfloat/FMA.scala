package easyfloat

import chisel3._
import circt.stage.ChiselStage
import chisel3.util._

class RawFloat_FMA(
  aEW: Int, aMW: Int,
  bEW: Int, bMW: Int,
  cEW: Int, cMW: Int,
  nStages: Int = 0,
  afterAdd: Boolean = false
) extends Module {

  /* TODO: this can be optimized if
      we are sure that the output raw float will be directly rounded,
      so we can detect overflow/underflow earlier.
  */
  require(nStages == 0 || nStages == 2, "RawFloat_FMA supports 0 or 2 pipeline stages")
  require(!afterAdd || nStages == 0, "afterAdd is the independent one-stage pipeline")

  val outEW = Seq(aEW + 2, bEW + 2, cEW).max

  val io = IO(new Bundle {
    val a = Input(new RawFloat(aEW, aMW))
    val b = Input(new RawFloat(bEW, bMW))
    val c = Input(new RawFloat(cEW, cMW))
    val out = Output(new RawFloat(outEW, cMW + 2))
  })

  val (a, b, c, out) = (io.a, io.b, io.c, io.out)
  require(cMW >= Seq(aMW, bMW).max)

  /* Initial placement of C and A * B
     c:  1.??????gs
     a*b:          ??.??????????

     000000000000000000000000000100000111001010100000100011111001100100000000001
     1111111111111111111111110000000000000000000000000000000000000000000000000000
  */
  // ---------------- Stage A (combinational): multiplier + exponent diff + C alignment ----------------
  val prodPadBits = (cMW + 1) - (aMW + bMW - 2)
  val prodMantissa_s1 = if (prodPadBits > 0) {
    Cat(a.mantissa * b.mantissa, 0.U(prodPadBits.W))
  } else {
    a.mantissa * b.mantissa
  }
  val adderWidth = cMW + 2 + prodMantissa_s1.getWidth + 1
  val prodExp_s1 = a.exp +& b.exp
  val prodSign_s1 = a.sign ^ b.sign
  val prodIsZero_s1 = a.isZero || b.isZero

  val initialExpDiff = cMW + 3
  val expDiff_s1 = prodExp_s1 +& initialExpDiff.S -& c.exp

  val initialCMantissa = Cat(Mux(c.isZero, 0.U(cMW.W), c.mantissa), 0.U(2.W), 0.U(prodMantissa_s1.getWidth.W))
  val keepC_s1 = expDiff_s1 < 0.S || prodIsZero_s1
  val alignShiftAmt_s1 = Mux(keepC_s1, 0.U, expDiff_s1.tail(1))
  val (cShift_s1, cSticky_s1) = ShiftRightJam(initialCMantissa, alignShiftAmt_s1)

  // pipeline registers between stage A and stage B
  private val pipelined = nStages > 0
  // NOTE: must materialize the register with the cloned (statically-widthed)
  // type of x -- a bare RegNext(x) has inferred width, which breaks the
  // .tail/.take width computation downstream in Chisel 6
  def stageReg[T <: Data](x: T): T = {
    if (!pipelined) {
      x
    } else {
      val r = Reg(chiselTypeOf(x))
      r := x
      r
    }
  }

  val prodMantissa = stageReg(prodMantissa_s1)
  val prodExp = stageReg(prodExp_s1)
  val prodSign = stageReg(prodSign_s1)
  val prodIsZero = stageReg(prodIsZero_s1)
  val expDiff = stageReg(expDiff_s1)
  val alignShiftAmt = stageReg(alignShiftAmt_s1)
  val cShift = stageReg(cShift_s1)
  val cSticky = stageReg(cSticky_s1)
  val cSign = stageReg(c.sign)
  val cExp = stageReg(c.exp)
  val cIsZero = stageReg(c.isZero)
  val cIsInf = stageReg(c.isInf)
  val cIsNaN = stageReg(c.isNaN)
  val aIsNaN = stageReg(a.isNaN)
  val bIsNaN = stageReg(b.isNaN)
  val aIsInf = stageReg(a.isInf)
  val bIsInf = stageReg(b.isInf)

  // ---------------- Stage B1 (combinational): adders ----------------
  val doSub = prodSign ^ cSign

  val addProd = Mux(prodIsZero, 0.U(adderWidth.W), prodMantissa ## 0.U(1.W)).zext
  val addC = (cShift ## cSticky).zext
  val cMinusP = addC - addProd
  val pMinusC = Mux(doSub, -addC, addC) + addProd

  val cGtProd = pMinusC.head(1).asBool
  val adderOutAbs_s1 = Mux(cGtProd, cMinusP.tail(1), pMinusC.tail(1))

  // Independent after-add cut. nStages == 2 uses both cuts; afterAdd uses
  // only this cut, leaving multiply/alignment/add ahead of one register.
  private def stageReg2[T <: Data](x: T): T = if (pipelined || afterAdd) {
    val r = Reg(chiselTypeOf(x)); r := x; r
  } else x
  val adderOutAbs = stageReg2(adderOutAbs_s1)
  val cGtProd2 = stageReg2(cGtProd)
  val expDiff2 = stageReg2(expDiff)
  val alignShiftAmt2 = stageReg2(alignShiftAmt)
  val cSign2 = stageReg2(cSign)
  val cExp2 = stageReg2(cExp)
  val cIsZero2 = stageReg2(cIsZero)
  val cIsInf2 = stageReg2(cIsInf)
  val cIsNaN2 = stageReg2(cIsNaN)
  val prodSign2 = stageReg2(prodSign)
  val prodIsZero2 = stageReg2(prodIsZero)
  val prodExp2 = stageReg2(prodExp)
  val hasNaN2 = stageReg2(aIsNaN || bIsNaN || cIsNaN)
  val prodHasInf2 = stageReg2(aIsInf || bIsInf)
  val addInfInvalid2 = stageReg2(cIsInf && (aIsInf || bIsInf) && (cSign ^ prodSign))
  val mulInfInvalid2 = stageReg2(prodIsZero && (aIsInf || bIsInf))

  // ---------------- Stage B2 (combinational): normalization + special cases ----------------
  // addend-anchored
  val cAnchored = (!cIsZero2 && expDiff2 <= cMW.S) || prodIsZero2
  val cAnchoredMantissa = (adderOutAbs << alignShiftAmt2).take(adderWidth + 1)
  val cAnchoredMantissaMSBs = cAnchoredMantissa.head(2)
  val cAnchoredNormMantissa = Mux(cAnchoredMantissaMSBs(1).asBool,
    cAnchoredMantissa,
    Mux(cAnchoredMantissaMSBs(0).asBool,
      cAnchoredMantissa.tail(1) ## 0.U(1.W),
      cAnchoredMantissa.tail(2) ## 0.U(2.W)
    )
  )
  val cAnchoredExp = Mux(cAnchoredMantissaMSBs(1).asBool,
    cExp2 + 1.S,
    Mux(cAnchoredMantissaMSBs(0), cExp2, cExp2 - 1.S)
  )


  // prod-anchored
  val pAnchoredMantissa = adderOutAbs.tail(cMW)
  val pAnchoredIsZero = pAnchoredMantissa === 0.U
  val lzc = PriorityEncoder(pAnchoredMantissa.asBools.reverse)
  val pAnchoredExp = (prodExp2 +& 3.S) - lzc.zext
  val pAnchoredNormMantissa = (pAnchoredMantissa << lzc).take(adderWidth - cMW)

  io.out.sign := Mux(cIsInf2,
    cSign2,
    Mux(prodHasInf2,
      prodSign2,
      Mux(cIsZero2 && prodIsZero2,
        cSign2 && prodSign2,
        // (c.sign && prodSign) || ((c.sign | prodSign) && RM==RDN),
        Mux(!cAnchored && pAnchoredIsZero,
          false.B,
          Mux(cGtProd2, cSign2, prodSign2)
        )
      )
    )
  )
  io.out.exp := Mux(cAnchored, cAnchoredExp, pAnchoredExp)
  io.out.mantissa := Mux(cAnchored,
    cAnchoredNormMantissa.head(cMW + 1) ## cAnchoredNormMantissa.tail(cMW + 1).orR,
    pAnchoredNormMantissa.head(cMW + 1) ## pAnchoredNormMantissa.tail(cMW + 1).orR
  )
  io.out.isZero := (cIsZero2 && prodIsZero2) || (!cAnchored && pAnchoredIsZero)
  io.out.isInf := !io.out.isNaN && (cIsInf2 || prodHasInf2)
  io.out.isNaN := hasNaN2 || addInfInvalid2 || mulInfInvalid2
}

// a * b + c
class FMA
(
  mulExpWidth: Int, mulMantissaWidth: Int,
  addExpWidth: Int, addMantissaWidth: Int,
  afterAdd: Boolean = false
) extends Module {

  val io = IO(new Bundle {
    val a = Input(UInt((1 + mulExpWidth + mulMantissaWidth).W))
    val b = Input(UInt((1 + mulExpWidth + mulMantissaWidth).W))
    val c = Input(UInt((1 + addExpWidth + addMantissaWidth).W))
    val out = Output(UInt((1 + addExpWidth + addMantissaWidth).W))
  })

  val rawFMA = Module(new RawFloat_FMA(
    1 + mulExpWidth, 1 + mulMantissaWidth,
    1 + mulExpWidth, 1 + mulMantissaWidth,
    1 + addExpWidth, 1 + addMantissaWidth,
    afterAdd = afterAdd
  ))

  rawFMA.io.a.fromIEEE(io.a, mulExpWidth, mulMantissaWidth)
  rawFMA.io.b.fromIEEE(io.b, mulExpWidth, mulMantissaWidth)
  rawFMA.io.c.fromIEEE(io.c, addExpWidth, addMantissaWidth)

  io.out := Rounding.round(rawFMA.io.out, RoundingMode.RNE, addExpWidth, addMantissaWidth)
}

object FMA {
  def main(args: Array[String]): Unit = {
    ChiselStage.emitSystemVerilogFile(
      new FMA(8, 23, 8, 23)
    )
  }
}
