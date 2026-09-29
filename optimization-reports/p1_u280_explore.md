# U280 P1 工具优化报告（Explore 策略）

> 基于 2026-09-04 Vivado 2020.2 构建，P1 工具优化策略应用后的 U280 结果。
> 对比 baseline 见 `baseline_u280_preopt.md`。

## Build Info
- Board: Alveo U280
- Part: xcu280-fsvh2892-2L-e
- Vivado version: 2020.2_AR75986 (Build 3064766, lin64)
- Design: U280FPGATestHarness / EmptyU280Config（FSA 16×16, HBM AXI_00 256-bit）
- Build date: **2026-09-04**（report 00:36–00:42）
- **Clock constraints:**
  - dut 域 70 MHz（14.284 ns，clk_out1_harnessSysPLL）
  - sys_clock 300 MHz（BJ43/BJ44）
  - xdma_ref_clk 100 MHz
  - HBM_REF_CLK_0 100 MHz

## P1 优化内容

| 改动 | Baseline | P1 | 文件 |
|------|----------|-----|------|
| 综合策略 | 默认 | `PerformanceOptimized -retiming` | `synth.tcl` |
| 布局策略 | `Explore` | `Explore` | `place.tcl` |
| 布局后优化 | `Explore` | `Explore` | `place.tcl` |
| 布线后优化 | `Explore` | `Explore` | `route.tcl` |
| ILA | 2× AXI4ILA (fsa_master/fsa_config) | **移除** | `TestHarness.scala` |
| HBM XSDB | `USER_XSDB_INTF_EN TRUE` | **FALSE** | `AXIHBM.scala` |
| MAX_FANOUT | 无 | `64`（mxControl cmd/accumTimer） | `u280-p1.xdc` |

## Timing 对比

| 指标 | Baseline (Sep 3) | P1 Explore (Sep 4) | Δ |
|------|------------------|---------------------|---|
| **WNS (dut 70MHz)** | +0.003 ns | **+0.023 ns** | **+0.020 ns** ✅ |
| TNS | 0.000 ns | 0.000 ns | 0 |
| Failing endpoints | 0 / 190,654 | 0 / 91,923 | -98,731（ILA 移除） |
| WHS | +0.010 ns | +0.009 ns | -0.001 ns |
| Clock period | 14.286 ns | 14.284 ns | -0.002 ns |
| **Achieved Fmax** | **70.02 MHz** | **70.16 MHz** | **+0.2%** |

### 各时钟域 WNS

| Clock Domain | Baseline WNS | P1 WNS |
|-------------|-------------|--------|
| `clk_out1_harnessSysPLL`（dut 70M） | +0.003 ns | **+0.023 ns** |
| `xdma_ref_clk` | +7.439 ns | +7.423 ns |
| `sys_clock`（300M） | +1.216 ns | +1.216 ns |
| `HBM_REF_CLK_0` | +3.890 ns | +3.890 ns |
| `dbg_hub/INTERNAL_TCK` | +12.405 ns | N/A（ILA 移除） |

### Worst Path（dut 70MHz 域）

- **Source**: `dutDomain/fsa/fsa/mxControl/fsm_list_1/accumTimer_reg[1]/C`
- **Destination**: `dutDomain/fsa/fsa/accRAM_sram/.../ram_reg_0_31_56_69/RAMB/I`
- **Data Path**: 14.261 ns（logic 5.214 ns (36.5%) + route 9.047 ns (63.5%)）
- **Logic Levels**: 62（CARRY8×24, LUT2×6, LUT3×7, LUT4×4, LUT5×2, LUT6×19）
- **Slack**: +0.023 ns (MET)

次差路径：
- `accUnit_10/reciprocal` → 65 级逻辑（CARRY8×26, LUT6×20），slack +0.032 ns
- `accUnit_9/reciprocal` → 62 级逻辑，slack +0.034 ns

## Utilization

| 资源 | SLR0 | SLR1 | SLR2 | Total | Baseline Total | Δ |
|------|------|------|------|-------|---------------|---|
| CLB LUTs | 28,978 | 171,103 | 176,597 | **376,678** | 381,056 | **-4,378** (-1.2%) |
| CLB Registers | 32,340 | 15,598 | 16,525 | **64,463** | 72,510 | **-8,047** (-11.1%) |
| Block RAM Tile | 52 | 8 | 0 | **60** | 80 | **-20** (-25%) |
| DSPs | 0 | 158 | 162 | **320** | 320 | 0 |
| URAM | 0 | 0 | 0 | **0** | 0 | 0 |

**ILA 移除收益**：-4,378 LUTs, -8,047 FFs, -20 BRAM

## SLR Crossing

| Crossing | SLLs | Baseline SLLs | Δ |
|----------|------|---------------|---|
| SLR2 ↔ SLR1 | 1,338 | 1,353 | -15 |
| SLR1 ↔ SLR0 | 642 | 643 | -1 |
| **Total** | **1,980** | **1,996** | **-16** |

TX_REG/RX_REG 补偿：全部为 0（与 baseline 一致）

## Power

| 指标 | Baseline | P1 | Δ |
|------|----------|-----|---|
| Total On-Chip | 20.239 W | 20.162 W | -0.077 W |
| FPGA Power | 15.244 W | 15.166 W | -0.078 W |
| HBM Power | 8.890 W | 4.996 W | -3.894 W* |
| Dynamic | 16.665 W | 16.589 W | -0.076 W |
| Static | 3.575 W | 3.573 W | -0.002 W |

*注：HBM Power 差异可能来自 Vivado vectorless 估算差异，非实际功耗变化。

## Route Status

| 指标 | Baseline | P1 |
|------|----------|-----|
| Logical nets | 854,522 | 854,999 |
| Routable nets | 473,261 | 473,207 |
| Fully routed | 473,261 (100%) | 473,207 (100%) |
| Routing errors | 0 | 0 |

## DRC

- **0 Errors**（baseline 也是 0 errors）
- 1,233 Warnings（ILA 移除后减少）

## 关键结论

1. **P1 构建成功**：timing met，bitstream 已生成，0 DRC errors
2. **WNS 提升有限**：+0.020 ns（从 +0.003 到 +0.023），Fmax 提升 ~0.2%
3. **ILA 移除释放资源**：-4,378 LUTs (-1.2%), -8,047 FFs (-11.1%), -20 BRAM (-25%)
4. **关键路径未变**：仍为 mxControl → accUnit reciprocal，62-65 级逻辑
5. **频率瓶颈在 RTL**：60+ 级组合逻辑是根本限制，需要 P2 RTL pipeline 才能实质提频
6. **`PerformanceOptimized -retiming` 效果**：综合阶段 retiming 优化了部分路径，但 place/route 保守策略（Explore）限制了进一步优化空间

## 板上验证（2026-09-04，P1 bitstream）

- **设备**: `/dev/xdma3`（U280，PCIe XDMA）
- **结果**: **功能正确**，与 baseline 一致

### 性能计数器

| 指标 | 数值 |
|------|------|
| Execution time | 4,956 cycles |
| Max bubble cycles | 3,195 cycles（64.5%）|
| Max active cycles | 179 cycles |
| DMA active cycles | 172 cycles |
| Raw instructions | 32 |
| Max instructions | 5 |
| DMA instructions | 4 |
| Fence instructions | 1 |
| Enqueue instructions | 32 |
| Dequeue instructions | 32 |

### 数值精度（vs Torch）

| 指标 | 数值 |
|------|------|
| MAE | 9.4124e-05 |
| MSE | 1.3156206e-08 |
| MaxErr | 3.1119585e-04 |
| **RelErr** | **1.8823991e-04**（与 NM37 baseline 一致）|

### 观察要点

- **bubble 占比 64.5%**（3195/4956）：指令间存在大量空闲周期，
  主要是控制开销与流水线未深化的体现，P2/P3 流水线化可压缩
- DMA active 172 cycles ≈ Max active 179 cycles：数据搬运与计算基本串行
  （无 overlap），P4（DMA inflight 加深）可改善
- 输出回读 addr 0x600, size 1024 正常

## 下一步：P2 RTL 优化

P1 证明仅靠工具优化提频空间有限。P2 需要从 RTL 层面减少关键路径逻辑级数：

- 方案 A：mxControl 输出寄存（`sp_read`/`acc_read`/`cmp_ctrl`/`pe_ctrl`/`acc_ctrl` 加 RegNext，-9~11 LUT 级）
- 方案 B：RawFloat_FMA 3 级流水线（AREG/BREG/CREG/MREG，-25 LUT 级）
- 方案 C：FSA.scala SA 控制输入寄存（-2 LUT 级）

建议顺序：先 A+C（低风险快速验证），视效果再上 B。
预期 P2 可将逻辑级数从 60+ 降至 30-40 级（A+C）或 15-20 级/Stage（A+B+C），Fmax 提升到 85-110 MHz。
