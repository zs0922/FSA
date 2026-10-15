from pyeasyfloat.float import *
from pyeasyfloat.rounding import round_raw_float

def pad_mantissa(a: RawFloatPoint, b: RawFloatPoint) -> int:
    # pad the mantissa to the same width
    a_mw = a.mantissa.bit_length()
    b_mw = b.mantissa.bit_length()
    if a_mw < b_mw:
        a.mantissa <<= (b_mw - a_mw)
    elif a_mw > b_mw:
        b.mantissa <<= (a_mw - b_mw)
    return max(a_mw, b_mw)

# res <- a * b
def mul_unrounded(a: RawFloatPoint, b: RawFloatPoint) -> RawFloatPoint:
    pad_mantissa(a, b)
    # always let a.exp < b.exp for convenience
    if a.exp > b.exp:
        a, b = b, a
    
    # left shift b to align the exp
    shift_amt = b.exp - a.exp
    a_m = 0 if a.is_zero else a.mantissa
    b_m = 0 if b.is_zero else b.mantissa
    bm_shifted = b_m << shift_amt
    m_product = a_m * bm_shifted
    m_width = a.mantissa.bit_length() + b.mantissa.bit_length() + shift_amt
    """
    a_m <- [1, 2)  1.?...?
    b_m <- [1, 2)  1.?...?
    res_m <- [1, 4) ?1.?...?

    Here we first assume that the result is in [2, 4), so we add 1 to (exp_a + exp_b),
    then we check the msb of the product,
    if it is 0, it means the product is in [1, 2), so we need to subtract 1 back
    """
    e_sum = a.exp + b.exp + 1
    if m_product != 0 and m_product.bit_length() != m_width:
        assert m_product.bit_length() == m_width - 1
        e_sum -= 1
    
    # unrounded result as we have infinite precision
    res = RawFloatPoint()
    res.sign = a.sign ^ b.sign
    res.exp = 0 if (a.is_zero or b.is_zero) else e_sum # note: bias is not added here
    res.mantissa = m_product # note: hidden bit is included here
    res.is_zero = a.is_zero or b.is_zero
    res.is_nan = a.is_nan or b.is_nan or \
        (a.is_zero and b.is_inf or a.is_inf and b.is_zero)
    res.is_inf = a.is_inf or b.is_inf
    return res

def add_unrounded(a: RawFloatPoint, b: RawFloatPoint) -> RawFloatPoint:
    res = RawFloatPoint()
    res.is_nan = a.is_nan or b.is_nan or (a.is_inf and b.is_inf and a.sign != b.sign)
    res.is_inf = a.is_inf or b.is_inf
    if a.is_inf or b.is_inf:
        res.sign = a.sign if a.is_inf else b.sign
        res.exp = 0
        res.mantissa = 0
        res.is_zero = False
        return res
    if a.is_zero and b.is_zero:
        res.sign = a.sign if (a.sign == b.sign) else False
        res.exp = 0
        res.mantissa = 0
        res.is_zero = True
        return res
    elif a.is_zero or b.is_zero:
        nz = b if a.is_zero else a
        res.sign = nz.sign
        res.exp = nz.exp
        res.mantissa = nz.mantissa
        res.is_zero = False
        return res
    
    pad_mantissa(a, b)
    # always let a.exp < b.exp for convenience
    if a.exp > b.exp:
        a, b = b, a
    shift_amt = b.exp - a.exp
    a_m = a.mantissa
    b_m = b.mantissa
    b_m_shifted = b_m << shift_amt
    if a.sign == b.sign:
        m_sum = a_m + b_m_shifted
        res.sign = a.sign
    else:
        if a.sign and not b.sign:
            m_sum = b_m_shifted - a_m
        elif not a.sign and b.sign:
            m_sum = a_m - b_m_shifted
        res.sign = m_sum < 0
        if res.sign:
            m_sum = -m_sum
    if m_sum == 0:
        res.is_zero = True
        res.sign = False
        res.exp = 0
    else:
        res.is_zero = False
        a_m_width = a.mantissa.bit_length()
        res_m_width = m_sum.bit_length()
        res.exp = a.exp + (res_m_width - a_m_width)
    res.mantissa = m_sum
    return res

def mul(a: FloatPoint, b: FloatPoint, target_ew: int, target_mw: int, rm: RoundingMode=RoundingMode.RNE) -> FloatPoint:
    raw = mul_unrounded(a.to_raw(), b.to_raw())
    return round_raw_float(raw, target_ew, target_mw, rm)

def add(a: FloatPoint, b: FloatPoint, target_ew: int, target_mw: int, rm: RoundingMode=RoundingMode.RNE) -> FloatPoint:
    raw = add_unrounded(a.to_raw(), b.to_raw())
    return round_raw_float(raw, target_ew, target_mw, rm)

def fma(a: FloatPoint, b: FloatPoint, c: FloatPoint, target_ew: int, target_mw: int, rm: RoundingMode=RoundingMode.RNE) -> FloatPoint:
    raw_mul = mul_unrounded(a.to_raw(), b.to_raw())
    # print(f"mul: {raw_mul}")
    raw_add = add_unrounded(raw_mul, c.to_raw())
    # print(f"add: {raw_add}")
    return round_raw_float(raw_add, target_ew, target_mw, rm)