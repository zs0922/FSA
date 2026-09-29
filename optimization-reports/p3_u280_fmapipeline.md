# U280 P3 数据路径切断报告（accumulator FMA 流水线化）

> 基于 2026-09-05 构建 + 板上验证。对比 P2 见 `p2_u280_ctrlpipe.md`，总计划见 `optimization_plan_p1_p5.md`。

## P3 改动内容

**核心**：accumulator 的 FMA 流水线化（L=3 级），切断 P2 后的 56 级数据路径。

```
in mux/fromIEEE → [R0 入口寄存器(MulAddExp2)] → 乘法器 → [R1] → 对齐+加法 → [R2] → 归一化+舍入(组合尾)
```

### 文件清单（patch: `fpga/patches/p3/{fsa,easyfloat}-p3.patch`）

| 文件 | 改动 |
|---|---|
| `easyfloat/FMA.scala` | `RawFloat_FMA` 加 `nStages` 参数（0=组合保持 PE 不变；2=两级内部流水 mult\|add\|norm）|
| `easyfloat/Exp2.scala` | `MulAddExp2` 加 `pipeline` 参数（入口寄存 + split.outInt 3 拍对齐）|
| `easyfloat/Reciprocal.scala` | `fmaLatency` 参数 + cnt/age FSM（按结果到达推进），nCycles=2n(L+1)+L+1 |
| `fsa/arithmetic/Arithmetic.scala` | trait 加 `accLatency` |
| `fsa/arithmetic/FPArithmeticImpl.scala` | FPAccUnit 启用流水（PE 不变），latency=3 |
| `fsa/Accumulator.scala` | valid/cmd/set/sram_in 延迟 L 拍对齐 FMA 输出 |
| `fsa/FSA.scala` | accRAM 写回 valid/addr 延迟 L+1；plan closure 传 ap |
| `fsa/ExecutionPlan.scala` | EXP_S2 +L、RECIPROCAL 起点 +L、LseNorm sem +L、accumulateMaxCycle +L（drain 保护）|

### 调试过程中发现并修复的 bug

1. **Chisel 6 宽度推断**：`RegNext(x)` 推断宽度使下游 `.tail()/.take()` 报错 → 改用 `Reg(chiselTypeOf(x))`
2. **stageReg 单刀 bug**：初版 FMA 只切一刀（实际 L=2），所有 L=3 适配错位 → 补第二刀（adder 后），独立 TB 验证 8/8 行对齐

### 关键设计约束

- **L=3 上限**：AttentionScore 的 EXP_S1→ACC_SA 波前窗口 8 拍，链需 2L+1+margin ≤ 8
- **Reciprocal 歪打正着**：单刀 bug 时它因"晚 1 拍消费重复结果"数值仍对，掩盖了 ACC 流的错位

## 仿真验证发现（重要）

**`AXI4FSA8X8/16X16` 仿真 config 存在既有 bug**（+2 行错位，上游原始 RTL 即复现，与 P1-P3 无关；4x4 及任意 tile 数正常）。板上 `EmptyU280Config`（nMemPorts=1）不受影响。**8x8+ 仿真数值不可作为回归判据**，以 4x4 仿真 + 板上验证为准。

## Timing 对比

| 指标 | Baseline | P1 | P2 | **P3** |
|------|----------|-----|-----|--------|
| **WNS (dut 70MHz)** | +0.003 | +0.023 | +0.062 | **+0.225 ns** |
| Failing endpoints | 0 | 0 | 0 | **0 / 109,719** |
| 等效 Fmax | 70.02 | 70.16 | 70.31 | **71.12 MHz**（+1.6%）>
| xdma_ref_clk WNS | +7.439 | +7.423 | +7.204 | +7.215 |
| 数据路径 | 60+ 级 | 60+ 级 | 56 级 | 最长段 ~24 级 |

### dut 域关键路径（P3 后）

- **Source**: `sa/mesh_8_15/reg_mantissa_reg[4]/C`
- **Destination**: `sa/pipe_b_1016_exp_reg[3]/D`
- **51 级**（CARRY8×15, LUT6×16），13.642ns（logic 33% + route 67%）
- **即 PE mesh 内 MAC 组合路径——P3b 的目标**（accumulator 路径已让位）

## 资源对比

| 资源 | P2 | P3 | Δ |
|------|-----|-----|---|
| LUTs | 374,086 | **382,162** | +8,076（流水寄存器）|
| FF | 64,688 | **71,323** | +6,635 |
| BRAM Tile | 60 | 52 | -8（综合优化）|
| **DSPs** | 320 | **288** | **-32**（流水切开后 24×24 乘法映射更高效：accUnit 4→2 DSP/单元）|
| SLL 穿越 | 1,900 | 2,029 | +129 |

SLR 分布（LUT）：SLR0 52,070 / SLR1 190,287 / SLR2 139,805。

## 功耗

| 指标 | P2 | P3 |
|------|-----|-----|
| Total On-Chip | 20.164 W | **19.894 W**（-0.27）|
| FPGA / Dynamic / Static | 15.168/16.591/3.573 | 14.898/16.328/3.566 W |

## 板上验证（2026-09-05，P3 bitstream）

- **功能正确：RelErr = 1.8823991e-04，与 baseline/P1/P2 完全一致**（MAE 9.4124e-05，MaxErr 3.112e-04）

### 性能计数器

| 指标 | Baseline | P1 | P2 | **P3** | P3 vs P2 |
|------|----------|-----|-----|--------|----------|
| Execution time | 4,956 | 4,956 | 5,087 | **5,320** | +233（+4.6%）|
| Max bubble | 3,195 | 3,195 | 3,349 | **3,203** | -146 |
| Max active | 179 | 179 | 179 | **251** | +72（reciprocal 76 拍 + drain）|
| DMA active | 172 | 172 | 171 | **171** | 持平 |

周期成本仅 +4.6%（reciprocal 17→76 拍 ×1 条指令 ≈ +59，各 acc 指令 drain +3，流水填充），被 bubble 池完全吸收。

## 结论与下一步

1. **P3 数据路径切断成功**：WNS +0.062→+0.225ns（×3.6），等效 Fmax 70.3→71.1 MHz。
   > **勘误**：初版报告误写 101.6 MHz——那是把提升系数 1.016 的百分比形式当成了 MHz。P3 的真正价值是
   > 切断 accumulator 56 级路径、使瓶颈唯一化（PE MAC 51 级）并兑现 DSP-32/功耗-0.27W 红利；
   > **频率红利的兑现需要 P3b**（PE MAC 流水化），71.1→100+ 的通道已由 P3 打开。
2. **板上功能零损**：RelErr 逐位一致，周期成本 +4.6%
3. **新瓶颈**：PE mesh 内 MAC（51 级组合）→ **P3b**（PE FMA 流水化 + 全 plan 周期均匀 +L，mesh 波前对齐）
4. **计划调整（勘误后）**：P3b 升为最高优先级（71.1MHz 上限的根源就是 PE MAC 51 级）；
   P5a 频率小步试探(75/78)可与 P3b 并行评估，P4 顺延

## 提交信息

- 主仓库 commit：`fpga/patches/p3/{fsa,easyfloat}-p3.patch` + apply 脚本更新
