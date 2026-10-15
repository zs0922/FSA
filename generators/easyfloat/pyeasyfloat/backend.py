from abc import ABC
from pyeasyfloat.fma import fma
from pyeasyfloat.exp import pow2
from pyeasyfloat.reciprocal import reciprocal
from pyeasyfloat.float import FloatPoint
from pyverilator import PyVerilator

class BaseFPBackend(ABC):
    def fma(self, a: FloatPoint, b: FloatPoint, c: FloatPoint, targetExpWidth: int, targetMantissaWidth: int) -> FloatPoint:
        """return a * b + c in precision of c"""
        pass

    def exp2(self, x: FloatPoint, targetExpWidth: int, targetMantissaWidth: int,
             pwlMulExpWidth: int, pwlMulMantissaWidth: int,
             pwlAddExpWidth: int, pwlAddMantissaWidth: int,
             pwlPieces: int = 8,  # Number of pieces for piecewise linear approximation
             ) -> FloatPoint:
        pass
    
    def reciprocal(self, x: FloatPoint) -> FloatPoint:
        pass


class PyEasyFloatBackend(BaseFPBackend):

    def __init__(self):
        super().__init__()

    def fma(self, a: FloatPoint, b: FloatPoint, c: FloatPoint, targetExpWidth: int, targetMantissaWidth: int) -> FloatPoint:
        """return a * b + c in precision of c"""
        return fma(a, b, c, targetExpWidth, targetMantissaWidth)
    
    def exp2(self, x: FloatPoint, targetExpWidth: int, targetMantissaWidth: int,
             pwlMulExpWidth: int, pwlMulMantissaWidth: int,
             pwlAddExpWidth: int, pwlAddMantissaWidth: int,
             pwlPieces: int = 8
             ) -> FloatPoint:
        return pow2(x,
                    targetExpWidth, targetMantissaWidth,
                    pwlMulExpWidth, pwlMulMantissaWidth,
                    pwlAddExpWidth, pwlAddMantissaWidth, pwl_pieces=pwlPieces)
    
    def reciprocal(self, x: FloatPoint) -> FloatPoint:
        return reciprocal(x)

class HwBackend(BaseFPBackend):

    def __init__(self, svTopFile: str):
        super().__init__()
        self.sim = PyVerilator.build(svTopFile)
        self.sim.io.reset = 1
        self.sim.clock.tick()
        self.sim.io.reset = 0
    

    def fma(self, a: FloatPoint, b: FloatPoint, c: FloatPoint, targetExpWidth: int, targetMantissaWidth: int) -> FloatPoint:
        self.sim.io.io_in_exp2 = 0
        self.sim.io.io_in_a = a.to_bits()
        self.sim.io.io_in_b = b.to_bits()
        self.sim.io.io_in_c = c.to_bits()
        ret = self.sim.io.io_out
        return FloatPoint.from_bits(ret, c.ew, c.mw)


    def exp2(self, x: FloatPoint, targetExpWidth: int, targetMantissaWidth: int,
             pwlMulExpWidth: int, pwlMulMantissaWidth: int,
             pwlAddExpWidth: int, pwlAddMantissaWidth: int,
             pwlPieces: int = 8
             ) -> FloatPoint:
        self.sim.io.io_in_exp2 = 1
        self.sim.io.io_in_a = x.to_bits()
        self.sim.io.io_in_b = 0
        self.sim.io.io_in_c = 0
        ret = self.sim.io.io_out
        # self.sim.clock.tick()
        return FloatPoint.from_bits(ret, targetExpWidth, targetMantissaWidth)
    
    def reciprocal(self, x: FloatPoint) -> FloatPoint:
        self.sim.io.io_in_reciprocal = 1
        self.sim.io.io_in_a = x.to_bits()
        while not self.sim.io.io_out_reciprocal:
            self.sim.clock.tick()
        ret = self.sim.io.io_out
        self.sim.io.io_in_reciprocal = 0
        self.sim.clock.tick()
        return FloatPoint.from_bits(ret, x.ew, x.mw)
            