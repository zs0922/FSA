# NM37 · 16×16 @ 300MHz 提频工程方案

> **最新完成（2026-10-13）：** [一级PE流水与100 MHz验收](NM37_M1_PIPELINE.md)已完成，保留显式A100：DUT setup/hold +0.534/+0.010 ns，全设计+0.049/+0.010 ns；全部网络布线完成。LUT355,692、FF140,263、DSP288、RAMB36 60。功能形式/回归通过；最差路径转为reset分发。下面的“尚未验证100 MHz”及PE瓶颈陈述属于此前实验历史，不再描述当前A100；300 MHz仍未验证。

> **最新实测（2026-10-13）：** [ShiftRightJam 实验](NM37_M1_SHIFTRIGHTJAM.md) 已完成并保留。以复制关闭的 A 为对照，post-route LUT 从 381,532 降到 357,004，DUT WNS 从 +0.832 ns 到 +0.878 ns；形式等价与六项回归通过，流水不变。主要收益是面积，PE 仍为瓶颈，尚未验证 100/300 MHz。

> **2026-10-13 实测更新：以 [当前 M1 基线及 A/B 报告](NM37_M1_BASELINE_AB.md) 为准。** 新构建 DUT WNS：复制关闭 +0.832 ns、开启 +0.390 ns；手工 timer/lane 副本未按预期保留，写地址 MAX_FANOUT 选择器失效。当前 top100 瓶颈为 PE、CMP 和 scratchpad→PE。本文以下保留历史规划，不能把其中的阶段预测、meshSlope/fmaStages 描述或“复制已生效”当作当前实现事实。


> 更新日期：2026-10-08（v3：M0 全部落地 + M1-§4.0 RTL 完成并验证，新增 §11 实施日志；
> v2：p3 NM37 全套报告 × optimization-reports 交叉验证，新增 §1.5/§1.6/§5.5，M1/M3 方案落定）
> 适用代码状态：`main` @ `45d859c1`（tag `p3`，资源/时序优化线终点，**数值正确**）
> 目标平台：ICT NM37（VU37P，on-die HBM），Vivado 2020.2
> 本文档自包含：在全新克隆目录中按 §8 环境搭建后即可开工，无需回溯其他文档。

---

## 0. 文档定位与代码基线

### 0.1 起点代码身份（重要，避免拿错线）

本工程存在两条互不相同的 "P" 编号线，本文档基于**资源/时序线**（数值正确）：

| 线 | 内容 | 状态 | 位置 |
|---|---|---|---|
| **资源线**（本文档基线） | P0 报告基建 → P1 工具级 → P2 控制切割 → P3 累加器流水 | ✅ 数值正确（16×16 多块板上逐位验证通过） | tag `p0`/`p1`/`p2`/`p3` |
| 数据线（未推送，仅存在于原开发机本地 stash） | P3b–P6：meshSlope=2、锚点重构、就地 exp2 PWL | 🔴 16×16 调度 bug 未修复 | 不在远端，新克隆不含此线 |

克隆本仓库（`https://github.com/zs0922/FSA`）+ 更新子模块后得到的就是资源线 p3 状态：
主仓 `45d859c1`，子模块指针 `generators/fsa=f4051d9`、`generators/easyfloat=44e1c36`、
`fpga/fpga-shells=d7c3532`（四阶段 commit 指针完全一致，阶段差异只在工作区 patch）。

### 0.2 本线与数据线的锚点差异（做提频前必读）

资源线的 ExecutionPlan 锚点是"每跳 1 拍"时代的形式（mesh 未流水）；数据线把它改成了
meshSlope=2 并重构锚点（但引入 16×16 bug）。**提频会再次改变每跳拍数**，因此 §3 M0
的"锚点结构化"是全工程第一前置：所有锚点必须写成波前几何的函数，而不是手调常数。

---

## 1. 现状基线（2026-09 实测）

### 1.1 NM37 四阶段物理实现（同一 RTL，同 die 结果与 U280 一致）

| 阶段 | WNS @70MHz | 说明 |
|---|---:|---|
| baseline (p0) | +0.001 ns | 压线通过 |
| p1 | +0.023 ns | 工具级优化 |
| p2 | +0.021 ns | 控制输出加寄存器 |
| p3 | +0.021 ns | 累加器 FMA 3 拍流水 |

**读数口径（2026-09-29 复核澄清）**：表内为全局 WNS。p3 的全局 +0.021 落在 PCIe 500M
域（XDMA IP 内部 BRAM 写地址路径，不可操作）；**dut 域（`clk_out1_harnessSysPLL`）真实
WNS = +0.054ns / WHS = +0.010ns**，且最差路径**仍是 mxControl→accumulator 的 60 级
控制族**——与 U280 P3 报告"已转移到 PE MAC 51 级"不一致，见 §1.5 冲突项。

bit + 完整报告：`nm37-stage-results/<stage>/`（本仓库已提交；.bit 文件因体积未入库，
由 `nm37-stage-results/build_nm37_stages.sh` 一键重产）。

### 1.2 70MHz 到顶的病理画像（NM37 p3 报告实测，U280 文档交叉印证）

- **60 级组合长链（p3 仍在）**：`fsm accumTimer → 命令广播（fanout 1007，LUT6 组合
  驱动）→ accUnit mulAddExp2/shiftRightJam sticky 链（8×CARRY8 串联）→ reciprocal
  → 指数进位链 → scale_mantissa_reg`；60–62 级（CARRY8×24–25），数据路径 14.1ns =
  logic 31% + **route 69%**，且路径在 mxControl↔accUnit 之间折返 5 次以上
- **DSP 全裸奔**：DRC 1258 条（DPIP-2 592 / DPOP-3 320 / DPOP-4 320 + 26 其他），
  320 个 DSP48E2（sa 288 = PE 256 + CMP 32；accumulator 32）的
  AREG/BREG/CREG/MREG/PREG 全关
- **高扇出族（全部实测在册）**：accRAM 写地址 1216（网延迟高达 4.5ns）、
  `acc_ctrl_cmd[1]/[2]` 1007（slack 0.055/0.060，就在 WNS 路径上）、accumTimer 1006、
  accRAM valid 608、bankIdx 326–352
- **SLR 穿越 1,964 SLL（1,321 条 SLR2↔SLR1 + 643 条 SLR1↔SLR0），TX/RX_REG 全 0**；
  fsa 内部 640 条网络跨界（578 条跨 1→2）；SLR1 CLB **60.5%** / SLR2 47.7% / SLR0
  14.7%（比 U280 的 ~52% 更挤）
- **资源结构**：LUT 376K（sa 314K = 83.5%，PE≈1.13K LUT/个），**FF 仅 64K（FF/LUT
  ≈ 0.17，流水寄存器严重不足的铁证）**，DSP 3.5%，URAM 0/960，accRAM=LUTRAM 608
- **时钟结构隐患**：MMCM（X0Y1）与时钟根（CLOCK_ROOT X3Y5）都在 SLR0，dut 时钟网
  4.1 万端点却在 SLR1/2 → 时钟网单边插入 ~5.1ns 且时钟路径本身跨 SLR；uncertainty
  0.188ns（MMCM DJ 0.370 + 源端 jitter 声明 0.5ns）
- **拥塞干净 + Rent 0.11–0.14**：无 Level 5+ 窗口、Router 初始拥塞为空 → 瓶颈不在
  布通性，在结构；低 Rent 同时证明 pblock 分区友好（§5.5 的前提）

### 1.3 性能计数器基线（为什么提频前必须先修主机）

seq=1024 板上实测：execTime 234.2ms，**bubble 97.3%**，mxActive 3.5%（141.6 拍/j迭代），
DMA active 2.4%。瓶颈是**主机逐字 MMIO 喂指令（3.32µs/字 × 70,529 字）**，与频率无关。
**不先做 §3 A1，提频收益为 0**（阵列在等指令，不在等时钟）。

**300MHz 档的换算（提频目标引出的新约束）**：A1 修完后主机供给 ≈ 7–14ms；而 300MHz
下 seq1024 的 MX 限制 ≈ 0.55–0.66M 拍 ≈ **1.8–2.2ms** → 主机将再次成为瓶颈。
**A2（指令队列写口 256-bit 化）或 A3（指令 HBM 自取）必须与 M2/M3 并行排期**，
否则 300MHz 的频率收益会兑现在"等指令"上。

### 1.4 目标的物理含义

300MHz = 3.33ns 周期 = **每段组合逻辑只剩 ~10–12 级 LUT 余量**（-2 速度级）。
DSP48 内部流水路径本身可跑 400MHz+，所以墙永远在"fabric 逻辑 + 控制 + SRAM 端口"。
这决定了里程碑的切法：先消灭 fabric 长链（M1/M2），再消灭全局控制（M3）。

### 1.5 与 optimization-reports 的交叉验证（2026-09-29，p3 全套报告逐项复核）

U280 历史文档（baseline/p1/p2/p3、ROADMAP、RESOURCE_MAP、youhua）核心结论在
NM37 p3 上的复现情况：

| U280 文档结论 | NM37 p3 实测 | 判定 |
|---|---|---|
| 最差路径 = mxControl→accumulator 60 级族（baseline 画像） | p3 **仍是该族**（60–62 级，dut WNS +0.054） | ⚠️ 与 U280 P3 报告（切到 PE MAC 51 级）不一致 → M1 双管齐下（§4.0+§4.1） |
| `acc_ctrl_cmd[1]/[2]` fanout 1007、LUT6 组合驱动 | 一致（slack 0.055/0.060，已是 WNS 路径成员） | ✅ |
| accRAM addr fanout 1216（FDRE 驱动、slack 高） | 一致（slack 9.4–10.7，但网延迟 3.2–4.5ns，提频后必成患） | ✅ |
| DRC ~1260 / 320 DSP 全裸奔 | 一致：1258 条 / 320 DSP | ✅ |
| SLL ~2000、TX/RX_REG 全 0 | 1964 / 全 0 | ✅ |
| SLR1/2 局部 CLB ~52% | SLR1 **60.5%** / SLR2 47.7% | ⚠️ NM37 更紧张 |
| 拥塞干净、Rent 低、适合分区 | 一致（Rent 0.11–0.14，无 >L5 窗口） | ✅ |
| URAM 0/960 浪费 | 一致 | ✅ |
| LUT ~77% 在 PE 里 | NM37：sa 占 83.5%（256×~1.13K ≈ 290K） | ✅ |
| 功耗 ~20.2W（HBM 8.9W，vectorless） | 20.21W（置信 Medium，只看趋势） | ✅ |

**NM37 独有新发现（U280 文档未覆盖，已全部并入本计划）**：

1. **资源按 SLR 强不对称**：LUTRAM 3.8K 中 3.2K 在 SLR0 + 608 在 SLR1（SLR2 为 0）；
   DSP 176/144 只落在 SLR1/2（SLR0 为 0）；BRAM 52 在 SLR0（HBM/PCIe 壳）+ 8 在
   SLR1（spad）。这是 Vivado 自由布局的结果而非器件硬限制——accRAM（LUTRAM）与
   mesh（DSP）被拉向不同 die，互连全靠 SLL（→ §5.5 用 pblock 反向纠正/利用）
2. **时钟根在 SLR0**：输入脚 BH42、MMCM X0Y1、CLOCK_ROOT X3Y5 都在 SLR0，而 4.1 万
   dut 时钟端点在 SLR1/2，时钟网插入 ~5.1ns 且时钟路径跨 SLR（→ §5.5 P8）
3. **全局 WNS 口径陷阱**：+0.021 在 PCIe 500M 域（IP 内部），dut 域 +0.054——对账
   必须看 dut 域数字（→ §1.1 澄清）
4. **约束卫生**：1 个输入端口无 input_delay（HIGH）、30 个 unconstrained 端点
   （debug 相关）、源端 jitter 声明 0.5ns 偏保守（→ M2 约束清理项）
5. **hold 裕量 +0.010ns**：当前贴边，但深流水化后 hold 压力自然下降，非风险项
6. masterClockConverter 的 256 条 0→1 SLL 是 PCIe↔dut 异步 FIFO 的固有跨 die（双
   时钟域隔离），**不参与时序收敛、也不必消灭**；真正要处理的是 dutDomain/fsa 的
   640 条（其中 578 条跨 1→2）

### 1.6 300MHz 分段预算表（为什么 §4–§6 每一刀都不可省）

VU37P -2L 布线后典型段延迟（经验值，用于里程碑预算而非 Fmax 承诺）：

| 段类型 | 预算 | 对策含义 |
|---|---:|---|
| fabric 逻辑段（6–8 级 LUT） | 2.4–3.0ns | 60 级长链必须切成 ≥6 段（M1/M2） |
| DSP48E2 全流水内部级（AREG/MREG/PREG 之一） | 0.6–0.9ns | DSP 三级全开（M1） |
| fabric→DSP 入口（含路由） | 1.5–2.0ns | 必须由 AREG 吸收，不能裸进 |
| URAM/BRAM 读出口（无 / 有输出寄存） | ~2.5 / ~1.2ns | accRAM 迁 URAM 必须带 OREG（M2） |
| SLR 内普通寄存器 hop | 1.2–1.8ns | mesh 逐跳寄存化的物理基础 |
| **跨 SLR 无寄存 hop** | **2.5–3.5ns** | 300MHz 下等于整个周期 → 必须寄存化或消灭（§5.5） |
| 时钟不确定性（当前 MMCM 链实测） | 0.188ns | 占 3.33ns 的 5.6%，预算内必须计入 |

**推论**：① 60 级 → ≥6 段；② DSP 全流水；③ 跨 SLR 全寄存/消灭；④ 全局组合控制
广播在 3.33ns 下无解，必须本地化（M3 以 B 路线为基座）；⑤ 任何"单段 fabric >8 级
LUT"的残留都是下一档频率的具体敌人——每档构建后用
`report_design_analysis -logic_level_distribution` 清点。

---

## 2. 总原则

1. **数值语义全程不变**：只动时序/流水/物理，不动算术语义——PyEasyFloat 金模型
   逐位比对在每一步都必须通过（§9）
2. **每个里程碑独立可验收**：WNS>0 + 逐位回归 + 板上计数器三项齐全才进下一档
3. **频率收益线性乘一切指标**（拍数不变时墙上时间=拍数/频率），但每提一档，
   调度拍数会因流水加深而变长（M1 起），利用率要靠调度重排对冲
4. 不拿估算当 Fmax：HLS 估算/综合估算 ≠ 布线后可跑频率，以 Vivado 实现报告为准

---

## 3. M0 前置（不做完后面全是返工）

### 3.1 锚点结构化（= 数据线"任务 #4"的同源工作，在本线上做）

**含义**：ExecutionPlan.scala 里所有 `cycle` 参数目前是 4×4 调出的经验族
（`2·rows+const`），必须改写为波前几何的函数，使 R/C/meshSlope 任变不影响正确性。

**方法**（已验证的部分结论）：
1. 以 VCD/仿真逐段考古：各 PE 的 Q@K 完成时刻、cmp 各列 beat 到达窗、
   load/phase-1/phase-2 的实际到达拍
2. 关键基准（结构推导，非经验）：`S 完成 ≈ 1 + 2(R−1) + (R−1) + 2(C−1)`（slope=2 时），
   其余事件以各自上游波前到达时刻为基推导；已验证正确的通式：
   `saBeatLatency = 2R+2C−1`（4×4 实测 15 精确吻合）
3. 锚点差一拍即错：sa_in 的 beat 只有 1 拍宽（实测案例：ACC_SA 锚 140 采到累加中途
   部分和 [26.1,25.6,23.8,21.0]，终值 [11.75,11.72,11.99,12.53]）

**验收**：4×4/8×8/16×16 仿真全部与 PyEasyFloat 逐位一致（8×8 是公式普适性的中间验证档）。

### 3.2 A1 主机 mmap 修复

`generators/fsa/python/fsa/xdma_mmio.py`：`dev_queue_mmio_write` 目前对每个 32-bit
指令字做一次 mmap+写+munmap（~3.3µs/字）。改为 `MMIO` 对象缓存页映射 + 循环裸写
（预期 ~0.1µs/字，seq1024 从 234ms → ~10–15ms，**约 20×，纯 Python 不动 bit**）。
安全性已论证：指令队列写口经 `RegField.w(Decoupled)` 反压（regmapper/RegField.scala:72），
队满沿 AXI→XDMA→PCIe 流控反压回主机 store，无损。
**验收**：精度行与修复前逐位一致；Enqueue−Dequeue==0；连跑两次 execTime 相同。

### 3.3 基线建档

跑一次 p3 bit 的板上计数器（seq 256/1024）+ 保存 timing/utilization/drc/fanout 四报告，
作为此后每个里程碑的对账基准。

---

## 4. M1 目标 100–110MHz（工程级）

### 4.0 第一刀：控制出口寄存 + 高扇出复制（NM37 复核新结论，先于 DSP 动手）

NM37 p3 最差路径仍含控制族（§1.5 冲突项）→ M1 必须控制/数据双管齐下，且这一刀
成本最低、见效最快：

- 四族高扇出网（`acc_ctrl_cmd` 1007 / accumTimer 1006 / accRAM-addr 1216 /
  bankIdx 326–352）：XDC `MAX_FANOUT 64` 作用于寄存器 + 手工复制关键寄存器，
  cmd 广播改"寄存器驱动 + per-lane 复制"
- `shiftRightJam/io_sticky2_carry` 的 8 级 CARRY8 串联改树形（或切 1 刀寄存）
- 验收：fanout 报告中上述四族消失或降为双位数；dut 域 WNS 路径离开 mxControl 族

### 4.1 DSP 内部流水全开

- 位置：`generators/easyfloat/src/main/scala/easyfloat/FMA.scala`（`RawFloat_FMA`，
  目前 `require(nStages >= 0 && nStages <= 2)`，需扩展到 3–4）+
  `generators/fsa/src/main/scala/fsa/arithmetic/FPArithmeticImpl.scala`
  （`FPMacUnit` fmaStages=1 / `FPAccUnit` fmaStages=2）
- 目标：让 Vivado 推断 AREG/BREG/CREG/MREG/PREG（必要时 use_dsp 属性/DSP48 原语），
  DRC DPIP-2/DPOP-3/4 从 1232（NM37 p3 实测）→ 个位数
- **连锁反应（必须同步做）**：PE MAC 延迟 1→3–4 拍 ⇒ `FSA.scala:19` 的
  `MeshParams.meshSlope` 2→4 ⇒ §3.1 的锚点公式按新 slope 全体重代入；
  累加器 accLatency 上限（当前=3，受 score 计划 EXP_S1→ACC_SA 波前窗约束）需重推

### 4.2 约束与物理

- 频率目标：`fpga/src/main/scala/nm37/TestHarness.scala` 的 `dutFreqMHz`（70→100）
- `phys_opt_design`（历史构建从未用过）+ pblock：mesh 整体进单 SLR、
  accumulator/mxControl 邻接、DMA/前端靠 HBM 出口
  （施工单见本文 §5.5；M1 只做软约束探路——CONTAIN_ROUTING=false 的 mesh/acc
  邻接 pblock + 时钟根位置观察，不硬化）

### 4.3 验收与预期代价

- 验收：WNS>0 @100MHz + 三档仿真逐位回归 + 板上计数器
- 代价：波前填充变长（score 计划 ~157→~250 拍量级），active/iter 上升——需要
  用双 FSM conflictFree 重叠（跨迭代）把利用率拉回；这是 M1 里真正的调度工作量

---

## 5. M2 目标 150–180MHz（深度重构）

| 项 | 内容 | 量化预期 |
|---|---|---|
| DSP 级联重写 | 乘积分段进 PCOUT/PCIN 级联、C 进 48 位 C 口：PE ~54 位、acc ~78 位的 fabric 加减器退出关键路径；PE LUT 1.13K → 预计 ~0.5K | DSP 288→~560（芯片 6%），LUT 预计 −8~13 万（须综合对账）；**解锁 §5.5 P1（mesh 单 SLR 放得下）** |
| SLR 跨界寄存 + floorplan | 按 §5.5 施工单消灭/寄存 fsa 的 640 条跨界网（578 条跨 1→2），按 meshSlope 节奏做延迟对齐 | >100MHz 必要条件；目标残余 <100 条全寄存 |
| accRAM LUTRAM→URAM/BRAM | 消 LUTRAM 时序路径族 + 释放 SLR 占用（URAM 0/960 全闲）；**必须带 OREG**（无输出寄存 ~2.5ns 出口直接吃掉 40–75% 周期）；顺带拆掉 addr fanout 1216 的物理发散 | 布线余量 + 扇出降双位数 |
| 控制分发树化 | fanout-1007 的 `acc_ctrl` 广播改 per-lane/逐行本地寄存器（ControlGen ROM 本已逐行错拍，物理上贴行摆放） | 拆掉最危险组合节点（M3-B 的前置） |
| reciprocal 流水化 | NR 每步迭代 = 一次 FMA，按新周期预算重排（`Reciprocal.nCycles` 公式） | 消掉 recip 类残余长路径 |
| acc RMW 环重定时 | 读 accSRAM→FMA(3–4 拍)→写回，环深 ~8 拍，bank 化 + 执行计划窗口重推 | 环内时序解耦 |
| 时钟根迁移 + 约束卫生 | §5.5 P8（时钟根随负载重心进 SLR1，时钟路径不再跨 SLR）；补 1 个 input_delay、30 个 unconstrained 端点；实测板上抖动收紧 jitter 声明 | uncertainty 0.188→<0.12ns 量级 |

**M2 出口量化门槛（对账单，全过才进 M3）**：
- dut 域 WNS ≥ +0.3ns @ 6.25ns（160MHz）约束下
- SLL：fsa 跨界 <100 条且 TX/RX_REG 不再全 0；SLR1 CLB ≤55%
- DPIP/DPOP ≤ 个位数；fanout 报告无 LUT 驱动 >64 的节点
- dut 最差路径 route 占比 <50%（当前 69%）
- `report_design_analysis -logic_level_distribution` 无 >12 级段

### 5.5 物理映射约束施工单（SLR 资源不对称 × FSA 组件 pblock；M2 主体、M3 收硬）

**VU37P 资源画像**（p3 实测用量；每 die 近似容量：LUT ~434K / DSP ~3008 / URAM ~320 / BRAM ~672）：

| 资源 | SLR0 | SLR1 | SLR2 | 解读 |
|---|---:|---:|---:|---|
| LUT 用量（占容量） | 29.0K（7%） | 190.0K（44%） | 157.4K（36%） | SLR1 最挤 |
| CLB 占用 | 14.7% | **60.5%** | 47.7% | pblock 必须给 SLR1 减负 |
| DSP | 0 | 176 | 144 | mesh 被自由劈在 SLR1/2；SLR0 的 DSP 列全闲（容量上三 die 均有） |
| LUTRAM | 3,166 | 608 | 0 | accRAM/队列类被拉向 SLR0/1 |
| BRAM36 | 52（HBM/PCIe 壳） | 8（spad） | 0 | — |
| URAM | 0 | 0 | 0 | 全闲，三 die 均可用 |

不对称是自由布局的**结果**而非器件限制：恰恰说明 accRAM（LUTRAM 引擎）与 mesh（DSP
引擎）当前互拉远离、互连靠 SLL——floorplan 的任务就是反向纠正，并把资源需求"钉"在
正确的 die 上。

**组件 → SLR 映射表**（施工顺序 P1→P8；落点
`fpga/fpga-shells/xilinx/nm37/constraints/nm37-config.xdc`，走 nm37 patch 系列）：

| # | 组件（资源需求，M2 完成后口径） | 目标位置 | 要点 | 依赖 |
|---|---|---|---|---|
| P1 | mesh 16×16（256 DSP + ~150K LUT，D4 级联化后） | **整体进 SLR1**。现状 sa 290K + cmp/acc/ctrl 邻接后 SLR1 LUT 将 >75%、CLB 冲高，布线风险大；D4 后（~150K）余量充足。兜底：按行 8/8 切 SLR1/SLR2，切缝沿 u/d 部分和链，缝上寄存器化（与 meshSlope 节奏一致） | 首选消灭最大 SLL 簇（fsa 的 578 条 1→2 切） | D4 |
| P2 | cmp_array×16（~22K LUT；CMP 恢复 DSP 则 +32） | mesh 下缘同 SLR | 与 mesh 底行单跳邻接 | P1 |
| P3 | accumulator×16 + scale（32 DSP + ~12K LUT） | cmp 正下方同 SLR | 站点须含 DSP 列 | P2 |
| P4 | accRAM（608 LUTRAM → 4×URAM，带 OREG） | accumulator 旁同 SLR | 见 M2 表；URAM 三 die 均有，不受不对称限制 | M2 accRAM 项 |
| P5 | mxControl（ROM 化后 ~6K LUT）+ ControlGen ROM | mesh 上缘 SLR1；M3-B 阶段 ROM 逐行下发进行内 pblock | 与 mesh 顶行单跳 | P1、M3-B |
| P6 | spad（8×BRAM36；B1/B2 后 ~10 个） | mesh 上缘 SLR1（现状已在 SLR1，固化） | DMA 写口与 PE 读口双邻接 | P1 |
| P7 | DMA + instQueue + decoder + 信号量 + 时钟转换 | SLR0 南缘（现状天然如此，软约束固化即可） | 贴 HBM/PCIe 出口；其 256 条跨界为异步 FIFO 固有，不管 | — |
| P8 | 时钟根 | P1–P6 落位后由负载重心牵引自动迁移进 SLR1；必要时 `set_property USER_CLOCK_ROOT` 钉死 | 验收：时钟网延迟 <1.5ns、时钟路径不再跨 SLR | P1–P6 |

**实施节奏**（对应"物理映射放最后"的约定）：M1 仅软约束探路（§4.2）；M2 做 P1–P4、
P6、P8 主体并逐个收紧；M3 补 P5（随控制本地化）并全部硬化（CONTAIN_ROUTING=true）。
每步构建后跑 `report_utilization -slr` + `report_design_analysis -congestion` 对账，
pblock 引发的拥塞立即回退上一档软约束。

**验收指标**：SLL 总数 <100 且寄存化率 100%（masterClockConverter 的 256 条异步跨
die 除外）；SLR1 CLB ≤55%；dut 最差路径 route 占比 <50%；时钟网最大延迟 <1.5ns。

---

## 6. M3 目标 250–300MHz（**已立项**：最终目标 300MHz；启动闸 = §5 M2 出口门槛全过）

**核心命题：全局逐拍微码广播在 3.33ns 下不成立。** 两条候选路线：

### 方案 A：自同步 mesh（激进，≈一次论文级重构）

- PE 间改 valid/ready 握手，数据自带节拍；ExecutionPlan 退化为"SRAM 读调度 +
  信号量管理"，控制广播彻底退出关键路径
- 风险：握手开销吃利用率；调度自由度换时序自由度需要重新设计 conflictFree 语义
- 收益上限：控制时序与阵列规模解耦，250–300MHz 变为布线问题

### 方案 B：控制表本地化（保守）

- 保留全局调度语义：ControlGen 的逐行 1-bit ROM 物理下发到行内、贴 PE 摆放；
  全局只分发"行启动"脉冲（经寄存器树错拍）；fanout 问题自然消失
- 风险低、工作量中等；可能到 200–250MHz 后仍需方案 A 补刀

**数值语义两条路线都可保持不变**（只动控制分布，不动算术），PyEasyFloat 验证链继续有效。

### 6.1 决策闸门（M2 收尾时量化判定，不拍脑袋）

设 M2 在 6.25ns（160MHz）约束下的 dut 域 WNS 实测值为 w：
- **w ≥ +0.8ns**：周期余量足以再砍半 → 走 B，直接以 3.33ns 约束冲 300；
- **+0.2 ≤ w < +0.8ns**：**B 为基座 + 对 score/value 波前的跨行依赖（RowSum、cmp
  oldMax/newMax 链）局部引入 A 元素**（valid/ready 局部握手替代全局锚点）；
- **w < +0.2ns**：fabric 段本身还没切干净 → 回头补 M2 数据通路，禁止进 M3。
- 频率梯子：3.33ns 之前先过 4.0ns（250MHz）档；**产品频率 = 实测稳定档 −5~10MHz**
  （optimization_plan_v2 的教训条款）；若 250MHz 是布线物理墙则接受（对 70MHz 已
  3.6×，300MHz 目标按 250→300 两步走）。

### 6.2 300MHz 组合拳清单（B 基座 + A 元素 + M1/M2 已完成项叠加）

1. ControlGen 1-bit ROM 物理下发到行内（LUTRAM/FF，随 §5.5 P5 落位），全局只广播
   "行启动"脉冲，经 2 级寄存器树错拍——脉冲树每级 ~1.5ns、2 级 3.0ns < 3.33ns，
   预算成立（§1.6）
2. score/value 波前跨行依赖局部 A 化：RowSum drain、cmp oldMax→newMax 链改局部
   握手，消掉全局锚点对齐（ExecutionPlan 相应锚点退化为信号量/事件）
3. PE：DSP 三级全流水 + skid（M1 已就）+ K 流/部分和流逐跳寄存（slope=4 下每 hop
   2 拍 = 6.6ns/hop，预算充裕）
4. accRMW 环 16 lane 独立 bank 化 + reciprocal NR 每步全流水 FMA（M2 已就）
5. 构建验收追加：`report_design_analysis -logic_level_distribution` 无 >8 级段；
   DPIP/DPOP = 0；300MHz 板上拷机（动态功耗预估 24–26W，Junction 监控，现 33.7°C）

---

## 7. 关键文件地图

| 文件 | 角色 | 提频时要动什么 |
|---|---|---|
| `generators/fsa/src/main/scala/fsa/ExecutionPlan.scala` | 全部调度锚点 | M0：锚点结构化；M1：slope 代入 |
| `generators/fsa/src/main/scala/fsa/ControlGen.scala` | 逐拍控制 ROM 生成 | M3B：物理下发 |
| `generators/fsa/src/main/scala/fsa/FSA.scala:19` | `MeshParams.meshSlope` | M1：2→4 |
| `generators/fsa/src/main/scala/fsa/MatrixEngineController.scala` | 双 FSM + 输出 Pipe | M2：分发树化 |
| `generators/easyfloat/.../easyfloat/FMA.scala` | RawFloat_FMA（nStages≤2 硬限） | M1：扩展 + DSP 推断 |
| `generators/fsa/.../arithmetic/FPArithmeticImpl.scala` | fmaStages / accLatency=3 上限 | M1：重推上限 |
| `generators/fsa/.../fsa/Accumulator.scala` | 16 lane FMA + RMW 环 | M2：环重定时 |
| `fpga/src/main/scala/nm37/TestHarness.scala` | dutFreqMHz / HBM 连接 | 每档改频；B 组多口 |
| `fpga/fpga-shells/xilinx/nm37/constraints/nm37-config.xdc`（经 nm37 patch 系列） | NM37 XDC 唯一落点（现仅 CONFIG_VOLTAGE） | M1：MAX_FANOUT；M2/M3：§5.5 pblock + USER_CLOCK_ROOT + 约束卫生 |
| `generators/fsa/python/fsa/xdma_mmio.py` | A1 mmap 修复点 | M0 |
| `generators/fsa/python/fa_ref.py` | PyEasyFloat 金模型 | 不动（验证锚） |

---

## 8. 环境与构建（全新克隆起步手册）

```bash
git clone https://github.com/zs0922/FSA.git && cd FSA
git checkout p3                        # 或直接用 main（同一 commit）
git submodule update --init generators/fsa generators/easyfloat fpga/fpga-shells rocket-chip

# 环境（NM37 构建三件套）
export RISCV=/home/zhangsi/riscv
export PATH=$HOME/circt/bin:$RISCV/bin:$PATH   # firtool 在 ~/circt/bin
source /opt/Xilinx_2020.2/Vivado/2020.2/settings64.sh

make -C fpga SUB_PROJECT=nm37 CONFIG=EmptyNM37Config bitstream
# 产物: fpga/generated-src/chipyard.fpga.nm37.NM37FPGATestHarness.EmptyNM37Config/obj/NM37FPGATestHarness.bit
```

### 8.1 Patch 机制与已知的坑（重要，别再踩）

阶段优化以 patch 形式存在于 `fpga/patches/{u280,p0p1,p2,p3,nm37}/`，由
`scripts/apply-u280-patches.sh`（含 p2/p3 段）+ `scripts/apply-nm37-patches.sh` 在
`make bitstream` 前自动应用到子模块工作区（幂等）。

**坑：`p0p1/fpga-shells-p0p1.patch` 是"u280 迁移 patch 已应用"状态下的超集 diff**。
在干净 fpga-shells 上先打 u280 再打 p0p1 必然冲突。正确姿势（已验证）：
在干净树上**先手工打 p0p1**，再让 make 跑 apply 脚本——`check_p1_applied()` 会走
skip 分支跳过 u280 shell patch，其余 patch 按序自动应用：
```bash
git -C fpga/fpga-shells apply fpga/patches/p0p1/fpga-shells-p0p1.patch
# 然后正常 make；fsa/easyfloat 子模块不要预打（会破坏 make 重放的幂等检测）
```
完整参考实现：`nm37-stage-results/build_nm37_stages.sh`（四阶段一键重建驱动，
含本坑的规避逻辑与每阶段归档）。

### 8.2 板上运行

```bash
cd generators/fsa/python
sudo $HOME/.local/bin/uv run main.py --seq_q 16 --seq_kv 16 \
    --config EmptyNM37Config --engine FPGA --diff
# 设备探测兼容 xdma 0x9038/0x903f；烧板走 Vivado Hardware Manager JTAG
```

### 8.3 仿真回归

```bash
cd sims/verilator && make debug CONFIG=AXI4FSA4X4Fp16Config
cd ../../generators/fsa/python
uv run main.py --seq_q 4  --seq_kv 4  --config AXI4FSA4X4Fp16Config --diff
uv run main.py --seq_q 16 --seq_kv 16 --config AXI4FSA4X4Fp16Config --diff
# 16×16 仿真构建需 conda Verilator 5.022（系统 5.020 对大设计 trace 有 bug）；
# 多口仿真 config 历史上存在 +2 行错位问题，回归以 4×4 + 板测为准（见 BUG 文档 §2.6）
```

---

## 9. 验证金标准（每个里程碑的出口检查单）

1. **数值**：与 PyEasyFloat 金模型**逐位一致**（误差行与基线完全相同才算过；
   精度行变了一个数都说明算术语义被动了，禁改）
2. **梯子**：4×4 仿真 → 8×8 仿真（锚点公式普适性）→ 16×16 仿真 → NM37 板测
3. **时序**：Vivado 实现报告 WNS>0、DPIP/DPOP 趋零、fanout 报告无 LUT 驱动的
   三位数扇出节点
4. **性能**：板上计数器四件套（execTime/bubble/mxActive/dmaActive）与基线对账，
   区分"调度变长"与"频率收益"（墙上时间 = 拍数/频率，两个因子分开记录）

---

## 10. 风险与开放问题

| # | 事项 | 状态 |
|---|---|---|
| 1 | NM37 p2 与 p3 WNS 相同（+0.021）且 bit md5 不同 | 待查：路由种子差异 or P3 收益在 NM37 布线下被吃掉；出四阶段对比表时必须搞清 |
| 2 | M1 深流水使调度变长，利用率（当前 22.6%）可能先降后升 | C1 跨迭代重叠必须与 M1 同步做 |
| 3 | accLatency=3 上限来自 score 计划波前窗（8 拍），M1 新 slope 下窗口要重推，可能反向约束流水深度 | M1 设计输入之一 |
| 4 | 数据线（P3b–P6，含 16×16 bug）只存在于原开发机本地 stash，未推送 | 本工程不依赖它；如需恢复见原机 `git stash list` |
| 5 | HLS 版估算 7.3ns/0 裕量的教训 | 一切以布线后报告为准 |
| 6 | 300MHz 下时钟 uncertainty 0.188ns 占周期 5.6%（MMCM DJ 0.370 + 源端 jitter 声明 0.5ns 偏保守） | M2 约束卫生：实测板上抖动后收紧声明；§5.5 P8 时钟根迁移 SLR1 |
| 7 | NM37 p3 最差路径族与 U280 P3 报告不一致（die 间布局方差大） | M1 双管齐下后重估；一切以 NM37 实测为准，U280 数据只定方向（§1.5） |
| 8 | 300MHz 动态功耗预估 24–26W（现 16.6W 动态 + HBM 8.9W 常量） | M3 板上拷机 + 结温监控（现 33.7°C，余量大但须复测） |
| 9 | accRAM 迁 URAM 若不带输出寄存（~2.5ns 出口）直接吃掉 40–75% 周期 | §5.5 P4 / M2 表强制 OREG，列入 M2 验收 |
| 10 | mesh 单 SLR（§5.5 P1 首选）依赖 D4 的 LUT 收缩兑现；若综合对账不达预期 | 兜底：行 8/8 切分 + 切缝寄存化（与 meshSlope 对齐），已写入 P1 |

---

## 11. 实施日志（2026-10-08，M0 全部落地 + M1-§4.0 RTL 完成）

### 11.1 已完成与验证状态

| 项 | 内容 | 验证 | 状态 |
|---|---|---|---|
| M0-A1 | 主机 mmap 页缓存（`xdma_mmio.py` 重写）+ LE 循环去掉 lambda | 板测 seq1024：execTime 234→25.2ms（**9.3×**），Enq==Deq，精度逐位不变，连跑两次 mxActive 完全相同 | ✅ |
| M0-§3.1 Phase A | `ExecutionPlan.scala` 锚点结构化：14 个具名地标（hop 项/管道 gap 分离，`hopLR/hopUD` 入口），`2R+1` 拆成"注入 R + 上行 R + gap"，value 的 `R+C` 对角型显式标注 | 4/8/16 三档 VCD 考古全部命中公式；三档仿真与基线逐位相等 | ✅（4×4 归档待确认）|
| M1-§4.0 RTL | acc_ctrl per-lane 16 份 Pipe（dontTouch 防合并）+ 定时器 7+8 份 bank + Accumulator 解码下放 lane + XDC accRAM addr MAX_FANOUT=128 | A/B 开关（`FanoutSplit`）双构建逐位相等 → 延迟中性确证；Vivado 侧 fanout/DRC 对账留 M1 统一构建 | ✅ 仿真侧 |
| **附带修复** | **`canEnq` 空队列垃圾读取**（`Mux(io.in.valid && waitPrevAcc, ...)`）：p3 既有瑕疵，`mxInst` bits 无复位初值，`ready` 随机摆动使 bubble 计数依赖构建结构（91/92/122 三种"幽灵值"的根因） | 根因定位（VCD 逐拍对比）+ 修复 + A/B 复验 | ✅ |

### 11.2 新仿真对账基线（canEnq 修复后；**bubble 语义已变，与历史数字不可比**）

| config | execTime | mxActive | mxBubble | rawInst | 精度（vs torch） |
|---|---:|---:|---:|---:|---|
| 16×16 | 420 | 249 | 126 | 32 | MAE 9.4124e-05 / RelErr 1.8823991e-04 |
| 8×8 | 381 | 179 | 154 | 32 | MAE 1.0121e-04 / RelErr 1.9875e-04 |
| 4×4 | 368 | 143 | 162 | 32 | MAE 9.6587e-04→9.6587464e-05 / RelErr 1.9886618e-04 |

板基线（A1 后，NM37，xdma4）：seq1024 execTime 1.767M 拍（25.2ms），mxActive 579,968，bubble 1.26M（71%），FSA≡PyEasyFloat 逐位。

### 11.3 持久化

- 本节改动以 `fpga/patches/m1/{fsa-m1,fpga-shells-m1}.patch` 固化（相对 p3+nm37 基线的纯增量，往返验证 4/4 文件 IDENTICAL）
- 应用顺序：`apply-u280-patches.sh` → `apply-nm37-patches.sh` → **`apply-m1-patches.sh`**（幂等）
- 全新克隆重建 = §8 流程 + 追加 m1 脚本一行

### 11.4 遗留 TODO

1. lse_scale 的 `af3−` 全尺寸恒为 planCycle 84，与 `accumulateMaxCycle(48)+accLat` 推算差 32——§4.1 动 accumLatency 时顺手查清
2. `expS1 = scorePhase1 + rows + cols + 1` 的 (R+C) 拆分为 PROVISIONAL——PE 级考古待做（§4.1 前不必）
3. 300MHz 下主机 25ms 重新成为第一瓶颈（计算仅 ~2ms）→ A2/A3 必须与 M2/M3 并行
4. Vivado 统一构建（M1 出口）：验证 §4.0 的 fanout 报告（cmd/timer 族消失、无 LUT 驱动三位数扇出）+ DRC 1232→? + dut WNS 提升
