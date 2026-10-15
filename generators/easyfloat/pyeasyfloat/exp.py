from pyeasyfloat.fma import *

def pow2_pwl(num_pieces: int):
    """Piecewise linear approximation of 2^x for x in [-1, 0]."""
    breakpoints = np.linspace(-1, 0, num_pieces + 1, dtype=np.float64)
    slopes = np.zeros(num_pieces, dtype=np.float64)
    intercepts = np.zeros(num_pieces, dtype=np.float64)
    for i in range(num_pieces):
        x0, x1 = breakpoints[i], breakpoints[i + 1]
        y0 = np.float64(2) ** (x0)
        y1 = np.float64(2) ** (x1)
        slopes[i] = (y1 - y0) / (x1 - x0)
        intercepts[i] = y0 - slopes[i] * x0
    return slopes, intercepts

N_PIECES = 8
SLOPES, INTERCEPTS = pow2_pwl(num_pieces=N_PIECES)

def split_float(x: RawFloatPoint, pwl_segments: int) -> tuple[int, int, RawFloatPoint]:
    """Split a floating point number into its integer and fractional parts.
    x = Xi + Xf, |Xf| <- [0, 1)
    where Xi is the integer part and Xf is the fractional part.
    """
    res = RawFloatPoint()
    res.sign = x.sign
    res.is_zero = x.is_zero
    res.is_nan = x.is_nan
    res.is_inf = x.is_inf

    pwl_lookup_bits = int(np.log2(pwl_segments))
    frac_msb = 0

    if x.exp < 0:
        # x = 2 ^ exp < 2^0 = 1
        res.exp = x.exp
        res.mantissa = x.mantissa
        r_shift = -1 - x.exp
        if r_shift < pwl_lookup_bits:
            m = x.mantissa >> (x.mantissa.bit_length() - pwl_lookup_bits)
            frac_msb = m >> r_shift
        else:
            frac_msb = 0
        return (0, frac_msb, res)
    else:
        # x = 2 ^ exp >= 2^0 = 1
        shift_amt = x.exp
        # 1.xxx -> 1x.xx0
        mw = x.mantissa.bit_length()
        mantissa = x.mantissa << shift_amt
        xf = low(mantissa, mw-1)
        frac_msb = xf >> (mw - 1 - pwl_lookup_bits)
        xi = mantissa >> (mw-1)
        if xf == 0:
            res.exp = 0
            res.mantissa = 0
            res.is_zero = True
        else:
            lzc = mw - xf.bit_length()
            xf = xf << lzc
            assert xf.bit_length() == mw
            res.exp = -lzc
            res.mantissa = xf
        if x.sign:
            xi = -xi
        return (xi, frac_msb, res)

def pow2(
    x: FloatPoint,
    target_ew: int, target_mw: int,
    pwl_mul_ew: int, pwl_mul_mw: int,
    pwl_add_ew: int, pwl_add_mw: int,
    rm: RoundingMode=RoundingMode.RNE,
    pwl_pieces: int=N_PIECES
) -> FloatPoint:
    """Power of 2 for negative floating point numbers."""
    xi, frac_msb, xf = split_float(x.to_raw(), pwl_pieces)
    assert xf.exp < 0 or (xf.is_nan or xf.is_inf or xf.is_zero)
    if pwl_pieces == N_PIECES:
        slopes, intercepts = SLOPES, INTERCEPTS
    else:
        slopes, intercepts = pow2_pwl(num_pieces=pwl_pieces)
    slope, intercept = slopes[pwl_pieces - 1 - frac_msb], intercepts[pwl_pieces - 1 - frac_msb]
    slope = round_raw_float(FloatPoint.from_bits(slope.view(np.uint64), 11, 52).to_raw(), pwl_mul_ew, pwl_mul_mw)
    intercept = round_raw_float(FloatPoint.from_bits(intercept.view(np.uint64), 11, 52).to_raw(), pwl_add_ew, pwl_add_mw)
    mul = mul_unrounded(xf, slope.to_raw())
    add = add_unrounded(mul, intercept.to_raw())
    raw_res = RawFloatPoint()
    raw_res.sign = False
    raw_res.exp = xi + add.exp
    raw_res.mantissa = add.mantissa
    raw_res.is_zero = add.is_zero
    raw_res.is_nan = add.is_nan
    raw_res.is_inf = add.is_inf
    res = round_raw_float(raw_res, target_ew, target_mw, rm)
    return res

def exp(
    x: FloatPoint, lg2_e: FloatPoint,
    target_ew: int, target_mw: int,
    rm: RoundingMode=RoundingMode.RNE,
    mulType=np.float32,
    addType=np.float32
) -> FloatPoint:
    """e^x = 2^(x * log2(e)) for negative floating point numbers."""
    assert x.is_zero or x.sign, "x must be a zero or a negative number"
    x = mul(x, lg2_e, target_ew, target_mw, rm)
    return pow2(x, target_ew, target_mw, rm, mulType, addType)
