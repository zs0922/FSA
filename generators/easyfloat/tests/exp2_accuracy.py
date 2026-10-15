import numpy as np
from pyeasyfloat.backend import *
from pyeasyfloat.float import FloatPoint


backend = PyEasyFloatBackend()


def error_analysis(pwl_pieces: int) -> tuple[float, float]:
    abs_err_list = []
    rel_err_list = []
    for fp16 in FloatPoint.get_all_normal_numbers(5, 10, postive=False, negative=True):
        fp16: FloatPoint
        exp2_dut = backend.exp2(fp16, 5, 10, 5, 10, 8, 23, pwlPieces=pwl_pieces)
        exp2_dut = exp2_dut.to_numpy()
        exp2_ref = np.exp2(fp16.to_numpy())
        exp2_ref = np.float32(exp2_ref)  # Convert to float32 for comparison
        abs_error = np.abs(exp2_dut - exp2_ref)
        rel_error = abs_error / np.abs(exp2_ref) if exp2_ref != 0 else abs_error
        abs_err_list.append(abs_error)
        rel_err_list.append(rel_error)

    mean_abs_error = np.mean(abs_err_list)
    mean_rel_error = np.mean(rel_err_list)
    return mean_abs_error, mean_rel_error


for pwl_pieces in [1, 2, 4, 8, 16, 32, 64, 128]:
    mean_abs_error, mean_rel_error = error_analysis(pwl_pieces)
    print(f"Error analysis for {pwl_pieces} PWL pieces:")
    print(f"Mean Absolute Error: {mean_abs_error}")
    print(f"Mean Relative Error: {mean_rel_error}")