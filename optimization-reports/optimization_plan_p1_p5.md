# FSA U280 频率优化总计划（P1–P5）

> ⚠️ **本文档已过时（2026-09-05）**：P3 报告初版将等效 Fmax 误算为 101.6 MHz（实为 71.1 MHz，
> 提升系数 1.016 被当成了 MHz）。修订后的计划见 **`optimization_plan_v2.md`**。
> 以下原文保留作历史记录。
> 目标：70 MHz → 100+ MHz（吞吐线性提升），功能保持 RelErr ~1.88e-4。
> 每阶段独立构建验证、采集 report、板上测试，通过后 commit，形成可回溯的优化链。
>
> 状态标记：✅ 已完成 | 🔄 进行中 | ⏳ 待做

---

## 全局视图：瓶颈在哪

```
                     ┌─────────────── 控制路径 ~10-12 级 ───────────────┐
                     │ accumTimer → select()比较+Mux1H → 输出扇出      │
                     └──────────────────────┬───────────────────────────┘
                                            ▼ (P2 在此切断)
   ┌─────────────────── 数据路径 ~45-50 级 ─────────────────────────────┐
   │ cmd译码 → in_a/b/c Mux → fromIEEE → 乘法器 → 对齐移位 → 加法器      │
   │ → 归一化 → 舍入 → 输出Mux                                          │
   └──────────────────────┬─────────────────────────────────────────────┘
                          ▼ (P3 在此切 3 刀)
```

- Baseline WNS +0.003ns：60+ 级逻辑，logic 4.7ns + route 9.1ns
- 板上 bubble 占比 64.5%：计算核心"吃不饱"，控制开销 + 访存延迟是吞吐瓶颈
- DSP 全部裸奔（AREG/MREG/PREG=0，DRC 告警 1262 条）
- cmd[1] fanout=1007；SLR 穿越 1996 SLL 无寄存补偿

各阶段负责切断不同的路径段，**串行推进、逐级验证**。

---

## P1 ✅ 工具优化（不动 RTL）

| 项 | 内容 |
|---|---|
| **洞察** | Vivado 2020.2 的激进策略（ExtraTimingOpt/AlternateFlowWithRetiming）在零裕量设计上会过度优化导致拥塞恶化；保守 Explore + 综合级 retiming 才是稳定收益 |
| **动机** | 零成本榨取工具收益，同时移除调试基础设施（ILA/HBM XSDB）释放资源、消除 dbg_hub 依赖 |
| **修改** | synth: `PerformanceOptimized -retiming`；place/route/phys_opt: `Explore`；删 2×AXI4ILA；HBM `USER_XSDB_INTF_EN FALSE`（与 U55C 对齐）；XDC `MAX_FANOUT=64`（mxControl cmd/accumTimer） |
| **怎么做** | tcl/XDC 改 fpga-shells（patch: p0p1）；ILA/XSDB 改主仓库 TestHarness/AXIHBM.scala |
| **结果** | WNS +0.003→+0.023ns；-4378 LUT / -8047 FF / -20 BRAM；板上 RelErr 1.88e-4 ✅；commit ab0a602b |

---

## P2 ✅ 控制路径切断（mxControl 输出寄存）

| 项 | 内容 |
|---|---|
| **洞察** | mxControl 所有下游（spRAM/accRAM 地址、SA 控制、accumulator cmd）都由其输出驱动。**统一 +1 周期移位 → SRAM 读数据与控制波前的相对对齐不变**，因此无需改任何 ExecutionPlan 周期计数 |
| **动机** | 关键路径起点 accumTimer 经过 select()（timer 区间比较 + 5-way Mux1H，~10-12 级）直接组合穿透到数据路径，且 cmd fanout=1007 的扇出树也在这个组合云里 |
| **修改** | `MatrixEngineController.scala`：FSM 输出先合并进 Wire（`*_all`），再过 `Pipe()` 寄存到 `io.*`。覆盖 sp_read / acc_read / cmp_ctrl / acc_ctrl / sem_release / pe_ctrl×16。valid 位 RegInit(false) 防虚假启动 |
| **怎么做** | 单文件改动；Chisel 验证通过（SV 中确认 `io_acc_ctrl_pipe_v` 等寄存器存在）；patch: `p2/fsa-p2.patch` |
| **验证中** | 预期 WNS +1~2ns；新关键路径变为"输出寄存器→FMA"（~50 级）；板上 +32 cycles/32 指令（可忽略） |
| **风险** | 低。唯一语义变化：指令间重叠窗口对齐 +1 拍，架构上无影响 |

---

## P3 ✅（仿真 config 既有 bug 已记录，以板测为准） 数据路径切断（FMA 流水线化，收益最大）

| 项 | 内容 |
|---|---|
| **洞察** | P2 之后瓶颈只剩 FMA：`RawFloat_FMA` 纯组合（乘法器→对齐→加法→归一化→舍入 ~45-50 级）。DSP48E2 硬件自带 AREG/BREG/CREG/MREG/PREG 流水寄存器但全关着（DRC DPIP-2=593）。切 3 刀后每段 ~12-15 级，10ns 内轻松收敛 → 100 MHz |
| **动机** | 这是 Fmax 从 ~80 → 100+ 的唯一途径；同时 DSP 流水化降低动态功耗（DSP 内部电容更小）；寄存后的 mantissa 乘积可映射进 DSP 的 M/P 级联 |
| **修改** | ① `RawFloat_FMA.scala`：插入 3 级流水（S1: fromIEEE 后寄存操作数 → S2: 乘积+指数差后寄存 → S3: 对齐加法后寄存，归一化+舍入收尾）<br>② `Reciprocal.scala`：牛顿迭代复用同一 FMA，迭代周期 2→3/4 拍，`nCycles` 公式相应调整（软件可见的仅是延迟）<br>③ `FPMacUnit/FPAccUnit`：`HasMultiCycleIO` 握手扩展 valid 延迟位宽<br>④ `ExecutionPlan.scala`：所有 plan 的 `computeMaxCycle/accumulateMaxCycle/accStartCycle` 增加 3（SA 每行）与 3（accumulator）<br>⑤ `InputDelayer/OutputDelayer` 延迟参数 +3 对齐<br>⑥ XDC：`set_property AREG 1 BREG 1 MREG 1 PREG 1`（或让 retiming 自动映射） |
| **怎么做** | ① 先只做 accumulator 路径（16 列）不动 SA 的 PE（PE 的 MAC 是流水波前的一部分，切它影响 mesh 时序对齐更复杂）→ 验证；② 再做 PE MAC（16×16 波前每行 +3 拍，delayer 补偿）；③ patch: `p3/fsa-p3-*.patch` 分两步提交 |
| **预期** | WNS → +2~4ns @70MHz（即 ~95-110 MHz 能力）；指令周期数 +6~8 拍（占比 <1%，bubble 64.5% 完全吸收） |
| **风险** | **中高**。Reciprocal 迭代反馈环变长需重验精度（迭代次数不变则收敛阶不变，仅延迟变化）；ExecutionPlan 周期计数硬编码在 RTL+软件两侧，必须同步；建议保留 P2 结果作为回退点 |

---

## P4 ⏳ 吞吐提升（压缩 64.5% bubble）

| 项 | 内容 |
|---|---|
| **洞察** | 板上 4956 拍中 bubble 3195（64.5%）、DMA active 172 ≈ Max active 179 —— **访存与计算完全串行**。频率提升后这个比例会更糟。收益来自 overlap，不来自单点频率 |
| **动机** | P3 提频后每拍更短，若 bubble 不压缩，端到端收益打折；且 HBM 带宽（~460GB/s AGG）远未利用 |
| **修改** | ① `DMA.scala/LSQ.scala`：`dmaLoadInflight` 16→32、`dmaStoreInflight` 8→16（FSAParams 一处配置）<br>② `Configs.scala`：`nMemPorts=2`（spad 双写口，accRAM 双读口，BankedSRAM 已支持 narrowWrite×2/narrowRead×2）<br>③ 预取：spad 下一块矩阵在当前计算期间预搬（ExecutionPlan 增加 prefetch 描述或软件双缓冲）<br>④ 软件：attention 序列 seq 32→64/128，摊薄每 seq 固定开销 |
| **怎么做** | ①② 是参数级改动零风险先做；③ 需要 ISA/plan 扩展（软件编译器配合）；④ 纯软件 |
| **预期** | bubble 64.5%→30%；端到端吞吐在 P3 基础上再 ×1.5~1.8 |
| **风险** | ①② 低（SRAM 端口已存在）；③ 中（需要锁存/信号量语义扩展防 WAR 冲突） |

---

## P5 ⏳ 物理级优化（布局/SLR/频率爬升）

| 项 | 内容 |
|---|---|
| **洞察** | route delay 占 66%（9.1ns/13.8ns baseline）。FSA 核心 Rent 指数 0.12（超低划分复杂度）——天然适合 pblock 分区；SLR1/SLR2 各 ~51% LUT 但 1996 个 SLL 无一有 TX_REG/RX_REG 补偿 |
| **动机** | P2/P3 之后的剩余路径大概率是跨 SLR 长线和高扇出时钟使能；工具级放置已经到顶，需要物理约束 |
| **修改** | ① pblock：`sa`(16×16 mesh) 固定到 SLR1+SLR2 中部列，`mxControl` 紧贴 SLR0 侧，accumulator 沿 accRAM 分布<br>② SLR 穿越寄存补偿：对 top 32 个跨界 net 加 `MAX_FANOUT` + 手动 pipeline 或 XDC `SLR_REG`<br>③ 时钟树：dut 域改用 BUFGCE 分区（Vivado 自动，检查 report_clock_utilization 确认无单点高扇出）<br>④ **频率爬升**：dutFreqMHz 70→80→90→100 逐级试（TestHarness.scala 一处参数），每级看 WNS 决定回退还是进<br>⑤ 若 P3 完成：试 `Explore` vs `ExtraTimingOpt` 翻转对比（裕量变大后激进策略可能反超） |
| **怎么做** | 全部 XDC/参数级：`u280-p5.xdc` + TestHarness freq；pblock 用 `report_design_analysis -complexity` 的模块级数据指导 |
| **预期** | 在 P3 基础上再挤 5-10% WNS；最终稳定频率点 ≥100 MHz |
| **风险** | pblock 过约束会恶化布线自由度，必须逐块验证；频率爬升以 5MHz 步进，失败即回退上一稳定点 |

---

## 执行节奏与检查点

| 阶段 | 改动面 | 预期 WNS@70M | 预期 Fmax | 构建次数 | 提交物 |
|---|---|---|---|---|---|
| P1 ✅ | 工具+调试移除 | +0.023ns | 70.16 | 3 | commit ab0a602b |
| P2 ✅ | 1 个 Scala 文件 | +0.039ns | 70.3 | 1(+板测) | commit 5acd5e8d |
| P3 ✅ | FMA+计划+delayer | +0.163ns | 101.6 | 4(含调试) | commit 45d859c1 |
| P4 | 参数+软件 | 时序中性 | — | 1-2 | FSAParams + python |
| P5 | XDC+频率参数 | 视 P3 | ≥100 | 3-5(频率扫描) | u280-p5.xdc |

**每阶段固定流程**：改动 → Chisel 快速验证（make verilog）→ Vivado 构建 → report 采集（timing/fanout/DRC 对比上一阶段）→ 板上 RelErr+性能计数器 → commit（主仓库直接提交，子模块 patch 入 `fpga/patches/pN/`）。

**回退原则**：任一阶段 WNS 恶化或板上精度劣化（RelErr > 1e-3），回退到上一阶段 commit，该阶段标记失败原因后重新设计。

---

## 关键文件索引

| 文件 | 涉及阶段 |
|---|---|
| `generators/fsa/.../MatrixEngineController.scala` | P2（已改） |
| `generators/fsa/.../arithmetic/RawFloat_FMA.scala` | P3 |
| `generators/fsa/.../arithmetic/Reciprocal.scala` | P3 |
| `generators/fsa/.../sa/PE.scala` + `FPArithmeticImpl.scala` | P3 |
| `generators/fsa/.../ExecutionPlan.scala` + `InputDelayer.scala` | P3/P4 |
| `generators/fsa/.../dma/DMA.scala` + `LSQ.scala` + `Configs.scala` | P4 |
| `fpga/fpga-shells/xilinx/u280/constraints/u280-p5.xdc` | P5 |
| `fpga/src/main/scala/u280/TestHarness.scala`（dutFreqMHz） | P5 |
