import numpy as np
from pyeasyfloat.backend import *
from pyeasyfloat.float import FloatPoint

def test_reciprocal(dut: HwBackend, ref: PyEasyFloatBackend, seed: int = 0):
    for i in range((1 << 16) - 1):
        fx = FloatPoint.from_bits(i, 5, 10)
        dut_v = dut.reciprocal(fx)
        ref_v = ref.reciprocal(fx)
        if dut_v.to_bits() != ref_v.to_bits():
            if not (dut_v.is_zero and ref_v.is_zero):
                dut_f = np.uint16(dut_v.to_bits()).view(np.float16)
                ref_f = np.uint16(ref_v.to_bits()).view(np.float16)
                print(f"error: {i} [{dut_f}] [{ref_f}]")
                print(fx)
                return



test_reciprocal(
    HwBackend('MulAddExp2Rec.sv'),
    PyEasyFloatBackend()
)