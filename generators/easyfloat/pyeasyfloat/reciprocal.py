
from pyeasyfloat.float import *
from pyeasyfloat.fma import *

def reciprocal(x: FloatPoint) -> FloatPoint:
    """
    x: 1.xxx * 2^exp
    1/x =  1 / (1.xxx * 2^exp) = (1 / 1.xxx) * 2^-exp

    special cases:
    1 / zero -> inf
    1 / inf -> zero
    1 / nan -> nan
    """

    r = RawFloatPoint()
    r.sign = 0
    r.exp = 0
    r.mantissa = 1
    r.is_inf = 0
    r.is_nan = 0
    r.is_zero = 0
    r = round_raw_float(r, x.ew, x.mw)

    two = RawFloatPoint()
    two.sign = 0
    two.exp = 1
    two.mantissa = 1
    two.is_inf = 0
    two.is_nan = 0
    two.is_zero = 0
    two = round_raw_float(two, x.ew, x.mw)

    zero = FloatPoint.from_bits(0, x.ew, x.mw)

    xm = x.to_raw()
    xm.sign = True
    xm.exp = 0
    xm = round_raw_float(xm, x.ew, x.mw)

    xe = x.to_raw()
    xe.mantissa = 1
    xe.exp = -xe.exp
    xe.is_nan = x.is_nan
    xe.is_inf = x.is_zero
    xe.is_zero = x.is_inf
    xe = round_raw_float(xe, x.ew, x.mw)

    # min iterations needed without round off error
    min_iter = int(np.log2(x.mw + 1))
    # TODO: 4 is an empirical value, not a theoretical value
    iter = min_iter + 5
    for _ in range(iter):
        # 2 - r*d
        rd = fma(r, xm, two, x.ew, x.mw)
        # r*(2-rd)
        r = fma(r, rd, zero, x.ew, x.mw)

    return fma(r, xe, zero, x.ew, x.mw)
