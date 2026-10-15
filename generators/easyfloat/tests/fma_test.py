import subprocess
import sys
from pyeasyfloat.backend import *
from pyeasyfloat.float import FloatPoint

TEST_FLOAT = "../berkeley-testfloat-3/build/Linux-x86_64-GCC/testfloat_gen"
TEST_FLAGS = "-tininessafter -exact -rnear_even"

def fma_test_builder(ew: int, mw: int, backend: BaseFPBackend, n: int=-1, seed: int=0, level=1):
    match (ew, mw):
        case (8, 23):
            fmt = 'f32'
        case (5, 10):
            fmt = 'f16'
        case _:
            raise ValueError(f"Unsupported format E={ew} M={mw}")
    test_cmd = f"{TEST_FLOAT} {TEST_FLAGS} -seed {seed} -level {level} {fmt}_mulAdd"

    process = subprocess.Popen(test_cmd,
                               stdout=subprocess.PIPE,
                               stderr=sys.stderr,
                               shell=True,
                               text=True)
    try:
        cnt = 0
        skipped = 0
        flushed = 0

        for line in process.stdout:
            a, b, c, ref, flags = [int(x, base=16) for x in line.split()]
            a, b, c, ref = (FloatPoint.from_bits(x, ew, mw) for x in [a, b, c, ref])
            if a.is_subnormal or b.is_subnormal or c.is_subnormal:
                skipped += 1
                continue
            cnt += 1
            dut: FloatPoint = backend.fma(a, b, c)
            if ref.is_subnormal or (flags & 0b00010):
                ref.exp = 0
                ref.mantissa = 0
                flushed += 1
            if dut.to_bits() != ref.to_bits():
                print(f"Test {cnt} failed:")
                print(f"Input: {line}")
                print(f"a : {a}")
                print(f"b : {b}")
                print(f"c : {c}")
                print(f"-------------")
                print(f"ref: {ref}")
                print(f"-------------")
                print(f"dut: {dut}")
                break
            if cnt == n:
                break
    finally:
        process.stdout.close()
        process.wait()
        print(f"Test finished! tested:: {cnt} skipped: {skipped} flushed: {flushed}")
            
            

fma_test_builder(8, 23, HwBackend('MulAddExp2.sv'), n=0)