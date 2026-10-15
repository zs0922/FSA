import numpy as np
from pyeasyfloat.backend import *
from pyeasyfloat.float import FloatPoint

def test_exp2(dut: HwBackend, ref: PyEasyFloatBackend, seed: int = 0):
    # exp2 only works for negative numbers
    for i in range(1 << 15,  (1 << 16) - 1):
        fx = FloatPoint.from_bits(i, 5, 10)
        dut_v = dut.exp2(fx, 5, 10, 5, 10, 5, 10)
        ref_v = ref.exp2(fx, 5, 10, 5, 10, 5, 10)
        if dut_v.to_bits() != ref_v.to_bits():
            if not (dut_v.is_zero and ref_v.is_zero):
                dut_f = np.uint16(dut_v.to_bits()).view(np.float16)
                ref_f = np.uint16(ref_v.to_bits()).view(np.float16)
                np_f = np.exp2(np.uint16(i).view(np.float16))
                print(f"error: {i} [{dut_f}] [{ref_f}] [{np_f}]")
                print(fx)
                return


test_exp2(
    HwBackend('MulAddExp2Rec.sv'),
    PyEasyFloatBackend()
)