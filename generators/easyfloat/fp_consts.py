import sys
import os
import argparse
import numpy as np

from pyeasyfloat.rounding import round_raw_float
from pyeasyfloat.float import FloatPoint
from pyeasyfloat.exp import pow2_pwl

def roundFloatToBits(x: np.float64, ew: int, mw: int) -> int:
    fx = FloatPoint.from_bits(x.view(np.uint64), 11, 52)
    rounded = round_raw_float(fx.to_raw(), ew, mw)
    bits = rounded.to_bits()
    return bits

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('type', type=str, choices=['slopes', 'intercepts', 'attentionScale'])
    parser.add_argument('--pwl-pieces', type=int, required=False, default=8)
    parser.add_argument('--dk', type=int, required=False, default=64)
    parser.add_argument('--ew', type=int, required=True)
    parser.add_argument('--mw', type=int, required=True)
    args = parser.parse_args()
    ew: int = args.ew
    mw: int = args.mw
    match args.type:
        case 'attentionScale':
            bits = roundFloatToBits(np.log2(np.e) / np.sqrt(args.dk, dtype=np.float64), ew, mw)
            print(f'{bits:x}')
        case 'slopes' | 'intercepts':
            slopes, intercepts = pow2_pwl(args.pwl_pieces)
            xs = slopes if args.type == 'slopes' else intercepts
            for x in xs:
                bits = roundFloatToBits(x, ew, mw)
                print(f'{bits:x}')