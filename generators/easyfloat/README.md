# 🧮 Floating-Point HW/SW Library Without Subnormal Number Support

Floating-point operations can be [hard](https://github.com/ucb-bar/berkeley-hardfloat) — especially due to subnormal values.  
We **flush all subnormals to zero** and make life easier.

### ✅ Currently Supported Operations

- **Fused Multiply-Add (FMA)**
- **Exponential Base 2 (EXP2)**  
  Uses piecewise-linear interpolation and reuses the FMA unit.
- **Reciprocal**  
  Based on Newton-Raphson iterations, also reusing the FMA unit.

---

- 🔧 Chisel hardware implementations: [`src/`](src/)  
- 🐍 Python software reference library: [`pyeasyfloat/`](pyeasyfloat/)
