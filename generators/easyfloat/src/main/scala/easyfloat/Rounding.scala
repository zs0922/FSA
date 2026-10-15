package easyfloat

import chisel3._
import chisel3.util._

object RoundingMode {
  def RNE = 0.U
  def RTZ = 1.U
  def RDN = 2.U
  def RUP = 3.U
  def RMM = 4.U
  def apply() = UInt(3.W)
}

object Rounding {
  // -> (rounded, carry_out)
  def round(
    raw: RawFloat, rm: UInt,
    targetExpWidth: Int,
    targetMantissaWidth: Int
  ): UInt = {
    import RoundingMode._
    // drop the hidden bit
    val roundInMantissa = raw.mantissa.tail(1).head(targetMantissaWidth)
    val g = roundInMantissa(0).asBool
    val r = raw.mantissa.tail(1 + targetMantissaWidth).head(1).asBool
    val sticky = raw.mantissa.tail(1 + targetMantissaWidth + 1).orR
    val inexact = r || sticky
    val roundUp = Mux1H(Seq(
      (rm === RNE) -> ((r && sticky) || (r && !sticky && g)),
      (rm === RTZ) -> false.B,
      (rm === RUP) -> (inexact && !raw.sign),
      (rm === RDN) -> (inexact && raw.sign),
      (rm === RMM) -> r
    ))
    val roundedMantissa = roundInMantissa +& roundUp
    
    // TODO: most of the time we don't need `+&`
    val roundedExp = raw.exp +& roundedMantissa.head(1).zext
    val overflow = roundedExp >= IEEEFloat.maxExp(targetExpWidth).S
    val underflow = roundedExp <= IEEEFloat.minExp(targetExpWidth).S
    val resSign = raw.sign && !raw.isNaN
    val resExp = Mux(raw.isInf || raw.isNaN || overflow,
      Fill(targetExpWidth, 1.U(1.W)),
      Mux(raw.isZero || underflow,
        0.U(targetExpWidth.W),
        (roundedExp.asUInt + IEEEFloat.expBias(targetExpWidth).U).take(targetExpWidth)
      )
    )
    val resMantissa = Mux(raw.isNaN,
      (BigInt(1) << (targetMantissaWidth - 1)).U,
      Mux(raw.isZero || raw.isInf || underflow || overflow,
        0.U(targetMantissaWidth.W),
        roundedMantissa.take(targetMantissaWidth)
      )
    )
    resSign ## resExp ## resMantissa
  }
}
