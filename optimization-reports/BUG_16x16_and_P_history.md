# 16×16 调度 Bug 详解 与 P 系列优化史

> 更新日期：2026-09-24
> 目的：① 说清当前源码主线上躺着的 16×16 调度 bug（OPTIMIZATION_PLAN.md 任务 #4），
> 供修复方案讨论；② 厘清两套 "P" 编号各自的优化内容与实测结果。
> 主要依据：`generators/fsa/OPTIMIZATION_PLAN.md`（2026-09-15 版）、本目录四份历史报告、
> 2026-09 板上实测。注意：原始 markdown 内部存在自相矛盾处，本文如实标注（§2.6）。

---

## 1. 版本谱系（先分清两套 P 编号，否则必然混淆）

本仓库存在**两条互不相同的 P 编号序列**，时间上先后衔接、内容上属于两个维度：

| 谱系 | 维度 | 阶段 | 产物（比特流） | 数值状态 |
|---|---|---|---|---|
| **资源/时序线**（本目录 baseline/p1/p2/p3 报告） | Vivado 工具/物理实现 + 控制与累加器流水 | baseline(9/3) → P1(9/4) → P2(9/5) → P3(9/5) | 仓库根 `U280FPGATestHarness-p1/-p2/-p3.bit` | ✅ **全部数值正确**（p1 bit 经 2026-09 板上 seq=256/1024 实测，与 PyEasyFloat 逐位一致） |
| **数据通路线**（`generators/fsa/OPTIMIZATION_PLAN.md`） | PE/mesh 数据通路流水化 + 调度锚点重构 + exp2 重写 | P3b → P4 → P5 → P6(+清理+参数化) | `fpga/generated-src/.../EmptyU280Config/obj/U280FPGATestHarness.bit`（9/14 14:29，下称 **Current**） | 🔴 **16×16 调度 bug**（任务 #4，本文 §2） |

要点：
- 根目录三个 `.bit` 是**资源线**的快照（用户日常在跑、数值可信的那份）；
- `generated-src` 里的最新构建是**数据通路线**的成果，跑 16×16 会得到错误结果；
- 两线的 P2/P3 编号撞名但内容不同：资源线 P2=控制输出加寄存、P3=累加器 FMA 流水；
  数据线没有 P1/P2，从 P3b 起步。

---

## 2. 16×16 调度 bug（Current 构建，任务 #4）

### 2.1 现象与证据（2026-09-14 首跑 + VCD 考古）

- **首跑**（`--seq_q 16 --seq_kv 16`，16×16 阵列单块）：MAE 0.038 / MaxErr 0.133
  （对照：正确构建应为 MAE ~9e-05 量级）；execTime 20,633 拍 / bubble 13,799
- **仿真与 FPGA 逐位一致**（MAE 0.038154013 / MaxErr 0.13313407 完全相同）
  → **RTL/比特流生成无问题，纯调度（锚点）问题**
- 数值形态：输出**全矩阵软性偏差**，每行 LSE 错约 ±20%，不是个别 lane 错
- VCD 细节（`/tmp/cur16.vcd`，指令基 ≈2490ns）：
  - ACC_SA（内部锚 140）采到**累加中途的部分和** [26.1, 25.6, 23.8, 21.0]
    （终值应为 [11.75, 11.72, 11.99, 12.53]），drain 流持续演化到 ~plan 157 才停
  - **delayer 之前的 acc_out 原始流即已污染**：col0 全程 0（求和从未到达）、
    col5 早停于 15.41（≠终值）、col10/15 振荡且超过终值
    → 求和链本身被污染，不是 OutputDelayer 对齐问题

### 2.2 已排除项（这些是对的，别再查）

- 仿真器构建与工具链（conda Verilator 5.022；系统 5.020 有 trace 代码生成 bug）
- EXP_S1 锚 `deltaBeat = 2R+9+saBeatLatency` 在 16×16 **正确**（Δm=−inf 拍精确命中；
  `saBeatLatency = 2R+2C−1 = 63` 结构公式实测吻合）
- `exp2(−inf)=0` 的边界处理

### 2.3 嫌疑锚点（4×4 调好的 `2·rows+const` 锚点族在 16×16 交错冲突）

R=C=16 代入后的拍数推导（嫌疑按严重度）：

1. **Q@K 尾部与晚列 phase-1 重叠**：Q@K MAC 持续到 ~plan 76
   （=1+2(R−1)+(R−1)+2(C−1)），而 phase-1 锚 2R+8=40 经列控制链 +2(C−1)=30 后
   在晚列 ~71 拍才触发 → **与晚列的 Q@K 尾部叠在一起**。
   4×4 上 MAC 结束于 ~10 拍 ≪ phase-1 的 18+6=24 拍，余量巨大，故 4×4 从不暴露
2. **UPDATE 窗口与 cmp_j beat 窗错位**：`UPDATE(2R+2=34, repeat=16)` 的窗口与
   晚列 cmp（j 大）的 beat 到达窗 [61..76] 错位 → 晚列行 max 不完整
   （与 §2.1 的 col0/col5/col10/15 现象吻合）
3. **load_reg_ui 斜率失配**：斜率 1 的重装载假设 S beat 以 1 拍/周期的均匀流到达，
   16 beat 下与 2 拍/跳的列斜切失配

### 2.4 修复方法论（OPTIMIZATION_PLAN 任务 #4 规划）

1. 沿用 4×4 考古方法论对 16×16 逐段核：各 PE 的 Q@K 完成时刻、cmp_j 的 UPDATE/beat
   窗、load/phase-1/phase-2 的实际到达拍
2. **把全部中段锚点改写为波前结构式**——关键基准：S 完成 ≈ 1+2(R−1)+(R−1)+2(C−1)，
   其余下游事件以此为基推导（而不是 4×4 上调好的 `2·rows+const` 经验族）
3. 验收顺序：16×16 仿真先与 PyEasyFloat 逐位一致 → 8×8 复核公式普适性 →
   重出 U280 比特流复测（板上再与仿真对拍）

### 2.5 影响范围与规避

| 配置 | 状态 |
|---|---|
| Current 构建 × 16×16 | 🔴 错（单块即错，多块同错） |
| Current 构建 × 8×8 | 大概率同病（同一锚点族），待验证 |
| Current 构建 × 4×4 | ✅ 已验证正确 |
| 资源线 p1/p2/p3 bit × 16×16 多块 | ✅ 正确（2026-09 板上 seq 256/1024 实测，与 PyEasyFloat 逐位一致） |
| 8×8/16×16 **仿真 config**（多口版） | 另有一个"既有的 +2 行错位"历史问题（见 §3 P3 节），与调度 bug 不同源 |

**规避实践**：在任务 #4 修复前，一切需要正确数值的板测都应使用资源线 bit；
一切 RTL 改动的回归基线用 4×4 仿真。

### 2.6 文档内部矛盾记录（如实标注，修复前须核实）

- P3b/P5/P6 各行标注"16×16（多块）仿真与金模型一致"，但 9/14 板跑与仿真对拍
  均为 MAE 0.038。两种可能：① 当时"一致"的结论来自后续被 P6 清理/参数化改动的
  版本；② 当时的 16×16 回归实际未覆盖最终配置（§三 验证命令速查只列了 4×4 config）。
  **开工第一步应重跑当前源码的 16×16 仿真回归，确认起点状态**，再进入 §2.4 流程。

---

## 3. 资源/时序线 P0–P3 详解（数值全部正确的那条线）

> 补丁位置：`fpga/patches/{u280,p0p1,p2,p3}/`。板测值均为 seq=16 单块。

### P0（9/3）：报告基建 + baseline

- report.tcl / collect_baseline.tcl 集成（timing/fanout/drc/power/congestion/SLR 全套）
- baseline 实测（ILA 未移除版本）：WNS **+0.003ns**（70.02MHz 压线）；最差路径
  `mxControl/fsm accumTimer → accumulator/accUnit reciprocal`：**60 级组合**，
  logic 4.7ns + route 9.1ns（66%）；`acc_ctrl_cmd[1]` fanout **1007**（LUT6 组合驱动，
  slack 0.003ns，≈256 PE×4 处使用）；DRC 1,260（DPIP-2 593 / DPOP-3 321 / DPOP-4 321，
  320 个 DSP 的 AREG/MREG/PREG 全关）；LUT 381K(29.1%) / DSP 320(3.5%) / URAM 0；
  SLR1/2 局部 ~52%；功耗 20.2W
- 板测 seq16：execTime 7,852 拍，bubble 67.7%（注：与 P1 报告引用的 4,956 拍不一致，
  属不同批次/主机状态，见 ROADMAP §7 勘误表）

### P1（9/4）：纯工具级，不动 RTL

- 改动：综合 `PerformanceOptimized -retiming`；移除 2× AXI4ILA（−4,378 LUT / −8,047 FF
  / −20 BRAM）；HBM XSDB 关闭；XDC `MAX_FANOUT=64`（mxControl cmd/accumTimer 复制点）
- 实测：WNS +0.003 → **+0.023ns**（70.16MHz，+0.2%）；最差路径家族不变
  （accumTimer → accRAM BRAM，62 级）→ **结论：瓶颈在 RTL，工具级到顶**
- 板测 seq16：4,956 拍 / bubble 3,195（64.5%）/ MAE 9.41e-05（正确）
- 产物：`U280FPGATestHarness-p1.bit`（用户在用的这份）

### P2（9/5）：控制路径切割

- 改动：mxControl 全部输出加 source pipe（sp_read/acc_read/acc_ctrl/sem_release
  `Pipe(+1)`，pe_ctrl 改 RegNext）——打断 timer→数据通路的长组合，tamplate 见
  `MatrixEngineController.scala:250-282` 的 P2/P4 注释
- 实测：WNS → **+0.062ns**（70.31MHz）；最差路径离开 mxControl；代价 execTime
  +131 拍（4,956→5,087，流水延迟常数）；数值不变

### P3（9/5）：累加器数据通路流水化

- 改动（补丁 `fpga/patches/p3/`）：
  - `RawFloat_FMA` 加 `nStages` 参数；accUnit `fmaStages=2`（乘|加|归一 三段，
    **accLatency=3**，受 score 计划 EXP_S1→ACC_SA 8 拍波前窗约束不得更深）
  - `Reciprocal` 多拍化（nCycles=2n(L+1)+L+1，按 FMA 结果到达推进的 cnt/age FSM）
  - `Accumulator.scala` valid/cmd/set/sram_in 延迟 L 拍对齐；`FSA.scala` accRAM 写回
    延迟 L+1；`ExecutionPlan.scala` 相关锚点 +L
- 实测：WNS → **+0.225ns**（71.12MHz）；最长段 56 级 → ~24 级；DSP 320→**288**
  （流水切开后 accUnit 4→2 DSP/单元）；功耗 −0.27W；execTime 5,087→5,320（+4.6%，
  reciprocal 17→76 拍等，被 bubble 池吸收）；数值不变
- **新瓶颈暴露**：最差路径换成 **PE mesh 内 MAC 51 级组合** → 引出数据线 P3b
- 过程中发现并修复：Chisel6 宽度推断（RegNext→Reg(chiselTypeOf)）；FMA 单刀错位 bug
- **附带发现**：`AXI4FSA8X8/16X16` 多口仿真 config 存在既有 "+2 行错位"（上游原始
  RTL 即复现，与本线改动无关）——8x8+ 仿真数值长期不可作回归判据，以 4×4 仿真+板测为准
- 勘误：初版报告"101.6MHz"系把提升系数 1.016 误当 MHz，实为 71.12

---

## 4. 数据通路线 P3b–P6 详解（Current 构建的来源，带 16×16 bug）

### P3b：PE MAC 切割 + mesh 管道化 + 控制器 ROM 化

- PE MAC 单级切割（`RawFloat_FMA` fmaStages=1）；**mesh 全部互连管道 1→2 深，
  `meshSlope=2`**（`FSA.scala:19-21`）——数据/控制/部分和波前统一 2 拍/跳，
  吞吐维持 1 MAC/拍；ControlGen 改逐周期 ROM 直通（`ControlGen.scala:169-179`）
- 实测（综合）：mxControl **−12.4K LUT**（18.6K→6.2K）；PE/mesh FF 大增（换时序）
- 当时仿真：4×4 单块 + 16×16 多块与金模型一致（时间线矛盾见 §2.6）

### P4：切割后波前重对齐（正确性恢复）

- cmp_ctrl **不带** source pipe（cmp 数据通路净 +2 拍需对齐）；`load_reg_ui` 斜率
  保持 1（S beat 1 拍/周期注入 vs 下行链 2 拍/行）；锚点逐项修正——详细理由见
  `ExecutionPlan.scala` 与 `MatrixEngineController.scala` 内的 P4 注释块

### P5：调度正确性修复（4×4 上 VCD 逐项定位）

- 删 `saHeld` 保持寄存器（省 4×fp32 FF）；**EXP_S1 改锚 Δm 拍**（吃 oldMax−newMax
  beat，域内 ≤0，修复 scale=[1,4.81,...] 一类错值）；**ACC_SA 改锚实时 RowSum drain**；
  value pass ACC_SA 改锚 O drain（`oDrainBeat=2R+2C−1`）；phase-2 后移到
  `2R+2C+10` 避 d 管道影子纹波（P[1][3] 被影子乘错的观测记录在案）

### P6：PE exp2 就地 PWL ROM（根除数据依赖锁存）

- exp2 斜率/截距由操作数小数高位**组合选择**（`Left(pwlConst)`，单拍确定性），
  删除旧的流式分段匹配（CMP 的 PROP_EXP2_INTERCEPTS/exp2_counter/截距 ROM、
  FSA 的 Exp2Slopes、SpadConstIdx 缩为 2 项）——多块 lane 偏斜的根因
- exp2 控制窗口 8→1 拍
- 清理+参数化：计划常数全部通式化（`saBeatLatency=2R+2C−1` 等，4×4 实测 15 精确吻合）
- 产物：**Current 构建（9/14 14:29）**——含全部修复，但 16×16 调度未收敛（§2）

---

## 5. 未竟事项清单（截至 2026-09-24）

| # | 事项 | 状态 | 关联 |
|---|---|---|---|
| #3 | PE 关键路径复查（P6 的 PWL ROM 加在 FMA 操作数前端，可能恶化时序） | ⏸ 待对 Current 时序报告细看 | ROADMAP D0/D1 |
| #4 | **16×16 锚点波前结构式重推导**（本文 §2） | 🔴 未开始，卡住一切 RTL 重建 | ROADMAP 全部 D 档与 C1 |
| #5 | 清理前后资源/Fmax 对账（9/14 版 vs 更早基线） | 待整理（RESOURCE_MAP 已有骨架） | ROADMAP D4 综合对账 |
| #6 | 压 mxBubble（软件流水/双缓冲） | 低优先级（A1 之后再评估） | ROADMAP B 组 |
| P7 | 4×4 U280 演示配置（`FSA4X4U280Config` + DMA 宽度适配 TLWidthWidget） | 🔄 卡在 AXI4ToTL 的 TLError（elaboration 未通）；16×16 不受影响 | ROADMAP B3 同类改动 |
| P8 | DSP 流水化 / accRAM→BRAM-URAM / SLR 寄存 / 提频 | 未开始 | ROADMAP D1~D4 |

---

## 6. 快速参考：现在该用哪条 bit、信哪些数字

| 用途 | 用什么 |
|---|---|
| 正确数值的板测（任意长度） | 资源线 `U280FPGATestHarness-p1/-p2/-p3.bit` |
| RTL 回归 | 4×4 仿真（`AXI4FSA4X4Fp16Config`）+ PyEasyFloat 逐位比对 |
| 性能调优的第一步 | ROADMAP A1（持久 mmap，不动 bit） |
| 任何 RTL 改动的重建前提 | 任务 #4 修复并通过 16×16 仿真回归 |
