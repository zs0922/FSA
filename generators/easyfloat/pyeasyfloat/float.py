from enum import Enum, auto
from typing import Generator, Tuple
from pyeasyfloat.fp_utils import low
import numpy as np

class RoundingMode(Enum):
    RNE = auto()
    RTZ = auto()
    RUP = auto()
    RDN = auto()
    RMM = auto()

class RawFloatPoint:
    """Raw float point representation for normalized numbers (1.xxx * 2^exp).

    * It is used to represent the float point in the internal representation,
    which is not biased and has the hidden bit included in the mantissa.

    * Only normal numbers are represented in this format.
    Any special numbers: NaN, Inf, -Inf, 0, -0 are not represented in this format,
    they should be handled separately.
    """
    sign: bool
    exp: int
    mantissa: int

    is_zero: bool
    is_inf: bool
    is_nan: bool

    def __repr__(self):
        return f"sign: {int(self.sign)} exp: {bin(self.exp)} ({self.exp}) mantissa: {bin(self.mantissa)} Z: {int(self.is_zero)} I: {int(self.is_inf)} N: {int(self.is_nan)}"

class FloatPoint(RawFloatPoint):
    """IEEE 754 float point representation."""
    ew: int
    mw: int
    def __init__(self, ew: int, mw: int):
        self.ew = ew
        self.mw = mw

    @property
    def bias(self) -> int:
        return (1 << (self.ew - 1)) - 1

    @property
    def max_exp(self) -> int:
        return (1 << self.ew) - 1

    @property
    def is_nan(self) -> bool:
        return self.exp == self.max_exp and self.mantissa != 0

    @property
    def is_inf(self) -> bool:
        return self.exp == self.max_exp and self.mantissa == 0

    @property
    def is_subnormal(self) -> bool:
        return self.exp == 0 and self.mantissa != 0

    @property
    def is_zero(self) -> bool:
        return self.exp == 0 and self.mantissa == 0

    def to_raw(self) -> RawFloatPoint:
        raw = RawFloatPoint()
        raw.sign = self.sign
        raw.exp = self.exp - self.bias
        raw.mantissa = (1 << self.mw) | self.mantissa
        raw.is_inf = self.is_inf
        raw.is_nan = self.is_nan
        raw.is_zero = self.is_zero | self.is_subnormal
        return raw

    @classmethod
    def from_bits(cls, x: int, ew: int, mw: int) -> "FloatPoint":
        x = int(x)
        ret = cls(ew, mw)
        ret.mantissa = low(x, mw)
        x >>= mw
        ret.exp = low(x, ew)
        x >>= ew
        ret.sign = low(x, 1)
        x >>= 1
        assert x == 0
        return ret

    @classmethod
    def from_numpy(cls, x: np.float16 | np.float32 | np.float64) -> "FloatPoint":
        """Convert from numpy float."""
        match x.dtype:
            case np.float16:
                return cls.from_bits(int(x.view(np.uint16)), 5, 10)
            case np.float32:
                return cls.from_bits(int(x.view(np.uint32)), 8, 23)
            case np.float64:
                return cls.from_bits(int(x.view(np.uint64)), 11, 52)
            case _:
                raise ValueError("Unsupported float point format")

    def to_bits(self) -> int:
        packed = (self.sign << (self.ew + self.mw)) | (self.exp << self.mw) | self.mantissa
        return packed

    def to_numpy(self) -> np.float64 | np.float32 | np.float16:
        """Convert to numpy float."""
        match (self.ew, self.mw):
            case (11, 52):
                return np.uint64(self.to_bits()).view(np.float64)
            case (8, 23):
                return np.uint32(self.to_bits()).view(np.float32)
            case (5, 10):
                return np.uint16(self.to_bits()).view(np.float16)
            case _:
                raise ValueError("Unsupported float point format")

    def get_all_normal_numbers(ew: int, mw: int, postive: bool=True, negative: bool=True) -> Generator["FloatPoint", None, None]:
        exp_range = range(1, (1 << ew) - 1)  # 1 to (2^ew - 2), inclusive
        mantissa_range = range(0, (1 << mw)) # 0 to (2^mw - 1), inclusive
        sign_range = []
        if postive:
            sign_range.append(False)
        if negative:
            sign_range.append(True)
        for sign in sign_range:
            for exp in exp_range:
                for mantissa in mantissa_range:
                    fp = FloatPoint(ew, mw)
                    fp.sign = sign
                    fp.exp = exp
                    fp.mantissa = mantissa
                    yield fp

    def random_normal(ew: int, mw: int) -> "FloatPoint":
        fp = FloatPoint(ew, mw)
        fp.sign = np.random.randint(0, 2) == 1
        fp.exp = np.random.randint(1, (1 << ew) - 1)
        fp.mantissa = np.random.randint(0, (1 << mw))
        return fp