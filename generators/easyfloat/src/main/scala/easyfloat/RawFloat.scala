package easyfloat

import chisel3._

object IEEEFloat {
  def maxExp(expWidth: Int) = BigInt(1) << (expWidth - 1)
  def expBias(expWidth: Int) = maxExp(expWidth) - 1
  def minExp(expWidth: Int) = -expBias(expWidth)
}

class RawFloat(expWidth: Int, mantissaWidth: Int) extends Bundle {
  val isZero = Bool()
  val isInf = Bool()
  val isNaN = Bool()

  val sign = Bool()
  val exp = SInt(expWidth.W)
  val mantissa = UInt(mantissaWidth.W)

  def fromIEEE(x: UInt, ew: Int, mw: Int): Unit = {
    val biasedExp = x.tail(1).head(ew)
    val hiddenBitMantissa = x.take(mw)

    isZero := biasedExp === 0.U // we flush subnormal to zero as well
    isInf := biasedExp.andR && !hiddenBitMantissa.orR
    isNaN := biasedExp.andR && hiddenBitMantissa.orR

    sign := x.head(1).asBool
    exp := biasedExp.zext - IEEEFloat.expBias(ew).S
    if (mantissaWidth > 1 + mw) {
      mantissa := 1.U(1.W) ## hiddenBitMantissa ## 0.U((mantissaWidth - mw - 1).W)
    } else {
      require(mantissaWidth == 1 + mw)
      mantissa := 1.U(1.W) ## hiddenBitMantissa
    }
  }

}
