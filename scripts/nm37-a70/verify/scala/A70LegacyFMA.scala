// Frozen historical oracle from easyfloat 44e1c369659c2c397548d2aecfc4ded3a74a111e; renamed test-only declarations.
package easyfloat

import chisel3._
import circt.stage.ChiselStage
import chisel3.util._

class A70LegacyRawFloat_FMA(
  aEW: Int, aMW: Int,
  bEW: Int, bMW: Int,
  cEW: Int, cMW: Int
) extends Module {

  /* TODO: this can be optimized if
      we are sure that the output raw float will be directly rounded,
      so we can detect overflow/underflow earlier.
  */
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
  val prodPadBits = (cMW + 1) - (aMW + bMW - 2)
  val prodMantissa = if (prodPadBits > 0) {
    Cat(a.mantissa * b.mantissa, 0.U(prodPadBits.W))
  } else {
    a.mantissa * b.mantissa
  }
  val adderWidth = cMW + 2 + prodMantissa.getWidth + 1
  val prodExp = a.exp +& b.exp
  val prodSign = a.sign ^ b.sign
  val prodIsZero = a.isZero || b.isZero
  val doSub = prodSign ^ c.sign

  val initialExpDiff = cMW + 3
  val expDiff = prodExp +& initialExpDiff.S -& c.exp

  val initialCMantissa = Cat(Mux(c.isZero, 0.U(cMW.W), c.mantissa), 0.U(2.W), 0.U(prodMantissa.getWidth.W))
  val keepC = expDiff < 0.S || prodIsZero
  val alignShiftAmt = Mux(keepC, 0.U, expDiff.tail(1))
  val (cShift, cSticky) = ShiftRightJam(initialCMantissa, alignShiftAmt)

  val addProd = Mux(prodIsZero, 0.U(adderWidth.W), prodMantissa ## 0.U(1.W)).zext
  val addC = (cShift ## cSticky).zext
  val cMinusP = addC - addProd
  val pMinusC = Mux(doSub, -addC, addC) + addProd

  val cGtProd = pMinusC.head(1).asBool
  val adderOutAbs = Mux(cGtProd, cMinusP.tail(1), pMinusC.tail(1))

  // addend-anchored
  val cAnchored = (!c.isZero && expDiff <= cMW.S) || prodIsZero
  val cAnchoredMantissa = (adderOutAbs << alignShiftAmt).take(adderWidth + 1)
  val cAnchoredMantissaMSBs = cAnchoredMantissa.head(2)
  val cAnchoredNormMantissa = Mux(cAnchoredMantissaMSBs(1).asBool,
    cAnchoredMantissa,
    Mux(cAnchoredMantissaMSBs(0).asBool,
      cAnchoredMantissa.tail(1) ## 0.U(1.W),
      cAnchoredMantissa.tail(2) ## 0.U(2.W)
    )
  )
  val cAnchoredExp = Mux(cAnchoredMantissaMSBs(1).asBool,
    c.exp + 1.S,
    Mux(cAnchoredMantissaMSBs(0), c.exp, c.exp - 1.S)
  )


  // prod-anchored
  val pAnchoredMantissa = adderOutAbs.tail(cMW)
  val pAnchoredIsZero = pAnchoredMantissa === 0.U
  val lzc = PriorityEncoder(pAnchoredMantissa.asBools.reverse)
  val pAnchoredExp = (prodExp +& 3.S) - lzc.zext
  val pAnchoredNormMantissa = (pAnchoredMantissa << lzc).take(adderWidth - cMW)

  val hasNaN = a.isNaN || b.isNaN || c.isNaN
  val prodHasInf = a.isInf || b.isInf
  // +/-Inf + -/+ Inf is invalid
  val addInfInvalid = c.isInf && prodHasInf && (c.sign ^ prodSign)
  // 0 * Inf is invalid
  val mulInfInvalid = prodIsZero && prodHasInf

  io.out.sign := Mux(c.isInf,
    c.sign,
    Mux(prodHasInf,
      prodSign,
      Mux(c.isZero && prodIsZero,
        c.sign && prodSign,
        // (c.sign && prodSign) || ((c.sign | prodSign) && RM==RDN),
        Mux(!cAnchored && pAnchoredIsZero,
          false.B,
          Mux(cGtProd, c.sign, prodSign)
        )
      )
    )
  )
  io.out.exp := Mux(cAnchored, cAnchoredExp, pAnchoredExp)
  io.out.mantissa := Mux(cAnchored,
    cAnchoredNormMantissa.head(cMW + 1) ## cAnchoredNormMantissa.tail(cMW + 1).orR,
    pAnchoredNormMantissa.head(cMW + 1) ## pAnchoredNormMantissa.tail(cMW + 1).orR
  )
  io.out.isZero := (c.isZero && prodIsZero) || (!cAnchored && pAnchoredIsZero)
  io.out.isInf := !io.out.isNaN && (c.isInf || prodHasInf)
  io.out.isNaN := hasNaN || addInfInvalid || mulInfInvalid
}

