# NM37 · 16×16 @ 300MHz 提频工程方案

> 更新日期：2026-09-29
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

bit + 完整报告：`nm37-stage-results/<stage>/`（本仓库已提交；.bit 文件因体积未入库，
由 `nm37-stage-results/build_nm37_stages.sh` 一键重产）。

### 1.2 70MHz 到顶的病理画像（U280/NM37 同源，报告实锤）

- **60 级组合长链**：FSM 定时器 → 计划窗口比较/Mux1H → 命令广播（fanout **1007**，
  `acc_ctrl_cmd[1]`，LUT6 组合驱动）→ 累加器未流水的 50 级 FMA（对齐移位→78 位
  加减→LZC 规格化→舍入）→ accSRAM 写口。P2/P3 已分别切在控制出口和数据通路内部
- **DSP 全裸奔**：DRC DPIP-2/DPOP-3/4 合计 ~1,100 条，320(→288) 个 DSP48E2 的
  AREG/BREG/CREG/MREG/PREG 全关；PE mesh 内 MAC 51 级组合是切完后的新瓶颈
- **SLR 穿越 ~2,000 SLL 无寄存补偿**；SLR1/2 局部 CLB ~52%
- **资源结构**：LUT 384K（**77% 在 256 个 PE 里**，每个 PE = 1 DSP + ~1.16K LUT），
  DSP 3.1%，URAM 0/960

### 1.3 性能计数器基线（为什么提频前必须先修主机）

seq=1024 板上实测：execTime 234.2ms，**bubble 97.3%**，mxActive 3.5%（141.6 拍/j迭代），
DMA active 2.4%。瓶颈是**主机逐字 MMIO 喂指令（3.32µs/字 × 70,529 字）**，与频率无关。
**不先做 §3 A1，提频收益为 0**（阵列在等指令，不在等时钟）。

### 1.4 目标的物理含义

300MHz = 3.33ns 周期 = **每段组合逻辑只剩 ~10–12 级 LUT 余量**（-2 速度级）。
DSP48 内部流水路径本身可跑 400MHz+，所以墙永远在"fabric 逻辑 + 控制 + SRAM 端口"。
这决定了里程碑的切法：先消灭 fabric 长链（M1/M2），再消灭全局控制（M3）。

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

### 4.1 DSP 内部流水全开

- 位置：`generators/easyfloat/src/main/scala/easyfloat/FMA.scala`（`RawFloat_FMA`，
  目前 `require(nStages >= 0 && nStages <= 2)`，需扩展到 3–4）+
  `generators/fsa/src/main/scala/fsa/arithmetic/FPArithmeticImpl.scala`
  （`FPMacUnit` fmaStages=1 / `FPAccUnit` fmaStages=2）
- 目标：让 Vivado 推断 AREG/BREG/CREG/MREG/PREG（必要时 use_dsp 属性/DSP48 原语），
  DRC DPIP-2/DPOP-3/4 从 ~1,100 → 个位数
- **连锁反应（必须同步做）**：PE MAC 延迟 1→3–4 拍 ⇒ `FSA.scala:19` 的
  `MeshParams.meshSlope` 2→4 ⇒ §3.1 的锚点公式按新 slope 全体重代入；
  累加器 accLatency 上限（当前=3，受 score 计划 EXP_S1→ACC_SA 波前窗约束）需重推

### 4.2 约束与物理

- 频率目标：`fpga/src/main/scala/nm37/TestHarness.scala` 的 `dutFreqMHz`（70→100）
- `phys_opt_design`（历史构建从未用过）+ pblock：
  mesh 整体进单 SLR（256 PE = 256 DSP + ~300K LUT，单 die 放得下）、
  accumulator/mxControl 邻接、DMA/前端靠 HBM 出口（详见 OPTIMIZATION_ROADMAP §5.1）

### 4.3 验收与预期代价

- 验收：WNS>0 @100MHz + 三档仿真逐位回归 + 板上计数器
- 代价：波前填充变长（score 计划 ~157→~250 拍量级），active/iter 上升——需要
  用双 FSM conflictFree 重叠（跨迭代）把利用率拉回；这是 M1 里真正的调度工作量

---

## 5. M2 目标 150–180MHz（深度重构）

| 项 | 内容 | 量化预期 |
|---|---|---|
| DSP 级联重写 | 乘积分段进 PCOUT/PCIN 级联、C 进 48 位 C 口：PE ~54 位、acc ~78 位的 fabric 加减器退出关键路径 | DSP 288→~560（芯片 6%），LUT 预计 −8~13 万（须综合对账） |
| SLR 跨界寄存 | ~2K SLL 插 TX/RX_REG，按 meshSlope 节奏做延迟对齐 | >100MHz 必要条件 |
| accRAM LUTRAM→BRAM/URAM | 消 LUTRAM 时序路径族 + 释放 SLR 占用（URAM 0/960 全闲） | 布线余量 |
| 控制分发树化 | fanout-1007 的 `acc_ctrl` 广播改 per-lane/逐行本地寄存器（ControlGen ROM 本已逐行错拍，物理上贴行摆放） | 拆掉最危险组合节点 |
| reciprocal 流水化 | NR 每步迭代 = 一次 FMA，按新周期预算重排（`Reciprocal.nCycles` 公式） | 消掉 recip 类残余长路径 |
| acc RMW 环重定时 | 读 accSRAM→FMA(3–4 拍)→写回，环深 ~8 拍，bank 化 + 执行计划窗口重推 | 环内时序解耦 |

---

## 6. M3 目标 250–300MHz（**立项选项**，未批准不启动）

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
**决策点：M2 收尾时按实测 WNS 距 3.33ns 的差距决定立项 A 还是 B（或 A+B 组合）。**

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
