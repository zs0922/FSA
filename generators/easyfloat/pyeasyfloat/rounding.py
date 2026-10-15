from pyeasyfloat.float import FloatPoint, RawFloatPoint, RoundingMode
from pyeasyfloat.fp_utils import low

# -> (x', carry_out)
def round_mantissa(sign: int, x: int, xw: int, ow: int,  rm: RoundingMode=RoundingMode.RNE) -> tuple[int, bool]:
    if xw <= ow:
        return (x << (ow - xw), 0)
    # xw > ow
    low_bits = low(x, xw - ow)
    x >>= (xw - ow)
    g = low(x, 1)
    if xw - ow == 1:
        sticky = 0
        r = bool(low_bits)
    else:
        sticky_bits = low(low_bits, xw - ow - 1)
        r = bool(low_bits >> (xw - ow - 1))
        sticky = sticky_bits != 0
    inexact = r or sticky
    match rm:
        case RoundingMode.RNE:
            roundup = (r and sticky) or (r and (not sticky) and g)
        case RoundingMode.RTZ:
            roundup = False
        case RoundingMode.RUP:
            roundup = inexact and (not sign)
        case RoundingMode.RDN:
            roundup = inexact and sign
        case RoundingMode.RMM:
            roundup = r
    carry_out = False
    if roundup:
        x += 1
        if x == (1 << ow):
            carry_out = True
    return (x, carry_out)


def round_raw_float(raw: RawFloatPoint, target_ew: int, target_mw: int, rm: RoundingMode=RoundingMode.RNE) -> FloatPoint:
    # round the result
    # add 1 to target_ew to account for the hidden bit
    target = FloatPoint(target_ew, target_mw)
    target.sign = raw.sign
    m_rounded, carry_out = round_mantissa(raw.sign, raw.mantissa, raw.mantissa.bit_length(), 1 + target_mw, rm)
    exp = raw.exp + int(carry_out)
    biased_exp = exp + target.bias

    if raw.is_nan:
        target.sign = False
        target.exp = target.max_exp
        target.mantissa = 1 << (target_mw - 1)
        return target
    elif raw.is_zero or biased_exp <= 0:
        # underflow
        target.exp = 0
        target.mantissa = 0
        return target
    elif raw.is_inf or biased_exp >= target.max_exp:
        # overflow
        target.exp = target.max_exp
        target.mantissa = 0
        return target
    else:
        target.exp = biased_exp
        # remove the hidden bit
        target.mantissa = low(m_rounded, target.mw)
        return target