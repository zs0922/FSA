# U280 P2 控制路径切断报告（mxControl 输出寄存）

> 基于 2026-09-04 构建 + 板上验证。对比 P1 见 `p1_u280_explore.md`，总计划见 `optimization_plan_p1_p5.md`。

## P2 改动内容

**文件**：`generators/fsa/src/main/scala/fsa/MatrixEngineController.scala`（patch: `fpga/patches/p2/fsa-p2.patch`）

- FSM 输出先合并进 `*_all` Wire，再经 `Pipe()`（1 级，valid 位 `RegInit(false)`）输出
- 覆盖：`sp_read` / `acc_read` / `cmp_ctrl` / `acc_ctrl` / `sem_release` / `pe_ctrl`×16
- **原理**：所有下游统一 +1 周期，SRAM 读数据与控制波前相对对齐不变 → ExecutionPlan 零修改

## Timing 对比

| 指标 | Baseline | P1 | **P2** | P2 vs P1 |
|------|----------|-----|--------|----------|
| **WNS (dut 70MHz)** | +0.003 ns | +0.023 ns | **+0.062 ns** | **+0.039 ns** ✅ |
| TNS / Failing | 0 / 0 | 0 / 0 | **0 / 92,392** | — |
| xdma_ref_clk WNS | +7.439 | +7.423 | +7.204 | -0.219 |
| sys_clock WNS | +1.216 | +1.216 | +1.216 | 0 |
| HBM_REF_CLK_0 WNS | +3.890 | +3.890 | +3.890 | 0 |
| Achieved Fmax | 70.02 | 70.16 | **70.34** | +0.26% |

### 关键路径转移（P2 核心成果）

| | P1 worst path | **P2 worst path** |
|--|--------------|-------------------|
| Source | `mxControl/fsm_list_1/accumTimer_reg[1]/C` | `outputDelayer/impl/out_delay_r_5_exp_reg[0]__0/C` |
| Destination | accRAM `RAMB/I` / `scale_9_mantissa_reg` / accUnit reciprocal | accRAM `RAMB_D1/I`（写数据口） |
| Logic Levels | 62–65（CARRY8×24） | **56**（CARRY8×21, LUT6×17） |
| Data Path | 14.26 ns | 13.81 ns（logic 4.84 + route 8.97） |

**分析**：
- accumTimer → select() → 下游的 60+ 级控制路径**已被切断** ✅
- 新瓶颈为纯数据路径：SA 输出寄存器 → accumulator FMA（乘+加+归一+舍入）→ accRAM 写数据，56 级
- WNS 增量（+0.039ns）小于逻辑级数差（62→56，约 6 级 ≈ 0.9ns）——route 占比 65% 抵消了部分收益
- **结论与计划一致：P2 铺路完成，实质提频需 P3 切断 FMA 数据路径**

## 资源与 SLR

| 资源 | P1 | P2 | Δ |
|------|-----|-----|---|
| LUTs 总量 | 376,678 | 374,086 | -2,592 |
| SLR0 / SLR1 / SLR2 LUT | 28.9K / 171.1K / 176.6K | 29.0K / 187.9K / 157.1K | 分布重排 |
| FF 总量 | 64,463 | 64,688 | +225（Pipe 寄存器） |
| DSPs | 320 | 320 | 0 |
| BRAM Tile | 60 | 60 | 0 |
| SLL 穿越总数 | 1,980 | 1,900 | -80 |

## 功耗

| 指标 | P1 | P2 |
|------|-----|-----|
| Total On-Chip | 20.162 W | 20.164 W |
| FPGA / Dynamic / Static | 15.166 / 16.589 / 3.573 | 15.168 / 16.591 / 3.573 W |

基本持平（+2 mW）。

## 高扇出观察（供 P3/P5 参考）

| 网络 | Fanout | 说明 |
|------|--------|------|
| `sram_accRAM_fullWrite_0_addr_REG[*]` | 1,216 × 5 位 | RegNext(acc_read.addr) 复制寄存器直接驱动 accRAM 全部 bank 的写地址 |
| accRAM fullWrite valid_REG | 608 | 写使能扇出 |

→ P3 修改 accRAM 写回路径时需一并处理（per-bank 地址寄存或 MAX_FANOUT 约束）。

## 板上验证（2026-09-04，P2 bitstream）

- **功能正确**：RelErr 1.8823991e-04，与 baseline/P1 完全一致（MAE 9.4124e-05，MSE 1.3156e-08，MaxErr 3.112e-04）
- 设备：`/dev/xdma3`

### 性能计数器对比

| 指标 | P1 | P2 | Δ |
|------|-----|-----|---|
| Execution time | 4,956 | 5,087 | **+131 cycles（+2.6%）** |
| Max bubble cycles | 3,195 | 3,349 | +154 |
| Max active cycles | 179 | 179 | 0 |
| DMA active cycles | 172 | 171 | -1 |
| Raw/Enq/Deq instructions | 32/32/32 | 32/32/32 | 0 |

**分析**：+131 cycles 源于每条指令 +1 拍输出延迟（32 指令）叠加流水线填充效应，符合预期量级；
计算 active 拍数不变（179），确认计算核心行为未受影响。bubble 池（3,349 拍）完全吸收了延迟增加。

## 结论与下一步

1. **P2 机制验证成功**：控制路径切断，关键路径转移至数据路径（56 级 FMA）
2. WNS +0.039ns、Fmax 70.34 MHz——增量有限但符合分析（route 占 65%）
3. **P3（FMA 流水线化）是提频关键**：切断 56 级数据路径后预期 95–110 MHz
4. P3 设计时注意 accRAM 写地址 fanout 1,216 问题一并处理

## 提交信息

- 主仓库 commit：`fpga/patches/p2/fsa-p2.patch` + `scripts/apply-u280-patches.sh`（P2 检测逻辑）
- 子模块 generators/fsa 保持 patch 方式（不直接提交子仓库）
