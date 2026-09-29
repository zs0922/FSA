# FSA 资源映射与优化状态（实测数据版）

> 数据来源：
> - **Baseline**（2026-09-03，P1 前）：`baseline_u280_preopt.md`（已迁移至本目录）
> - **P1/P2/P3**：`p1_u280_explore.md` / `p2_u280_ctrlpipe.md` / `p3_u280_fmapipeline.md`
> - **Current**（2026-09-14 构建，含 P1–P6 全部 RTL）：`fpga/generated-src/...EmptyU280Config/obj/report/`
>   （该构建 = EmptyU280Config 16×16；P7 的 harness 宽度适配尚未包含，需下次构建）
> 配置：16×16 fp16/fp32，U280，dut 域 70 MHz

## 一、逐部件资源映射（16×16 实测，Baseline → Current）

### 顶层总览

| 资源 | Baseline (09-03) | Current (09-14) | Δ | 说明 |
|---|---:|---:|---|---|
| LUT | 381,056 (29.1%) | **384,477** | +1% | logic 379.6K + LUTRAM 3.8K + SRL 1.1K |
| FF | 72,510 | **121,450** | +67% | P3b PE D 域寄存 + P3 acc 流水（见下） |
| BRAM36 | 80（含 HBM/壳） | 60 | — | FSA 内 8 个（spad）；其余为壳/HBM |
| URAM | 0 | 0 | — | 待优化点：accRAM/spad 预取 |
| DSP | **320** | **288** | **−32** | CMP 的 FMA 被 P 系列改动后综合成 LUT 乘法 |
| WNS@70M | +0.003 ns | **+0.091 ns** | — | Fmax ≈ 70.02 → **70.45 MHz** |
| DRC(DPIP/DPOP) | 1,260 | 1,107 | — | PE 的 256 个 DSP 流水仍未开（P3b 只在 FMA 内部切 1 刀，未映射到 DSP AREG/MREG/PREG） |

### FSA 核心逐部件（层次报告提取）

| 部件 | 数量 | LUT | FF | DSP | BRAM | Baseline→Current 变化解读 |
|---|---:|---:|---:|---:|---:|---|
| **PE**（`mesh_r_c`，FPMacUnit+reg）| 256 | 1,126→**1,159** | 17→**209** | 1 | 0 | FF×12：P3b 加 lInputD/uInputD/dInputD/ctrlD + FMA 内部 1 级切割；LUT 微增=P6 就地 PWL ROM（8×16b 斜率+8×32b 截距）|
| **mesh 互连**（u/d/l/r/ctrl 链）| — | ≈4.3K | ≈23.4K→**~46K** | 0 | 0 | P3b 管道深度 1→2：fp32 u/d 链、fp16 l/r 链、2 深 ctrl 链全加倍 |
| **CMP**（`cmp_array_j`）| 16 | 1,329→1,462 | 67→64 | **2→0** | 0 | FPCmpUnit 的 FMA 现走 LUT 乘法（省 32 DSP，+0.1K LUT）|
| **Accumulator**（FPAccUnit+scale）| 16 lanes | 11,961→**23,681** | 1,760→**8,546** | 2/lane=32 | 0 | P3：fmaStages=2（总深 3）+ Reciprocal NR 状态机；P6：就地 PWL ROM |
| **mxControl**（2×FSM+9×ControlGen ROM）| 2 槽 | **18,569→6,180** | 441→335 | 0 | 0 | **−12.4K LUT**：P2 输出 Pipe + P3b 后 ControlGen 走逐周期 ROM 直通（waveSlope=2 旁路贪心优化器）|
| **accRAM**（AccumulationSRAM，2 bank）| 17 行×512b | 2,040→1,377 | 27→19 | 0 | **0（LUTRAM 608）** | 仍为 LUTRAM——**待改 BRAM/URAM**（youhua 优化点，未做） |
| **spRAM**（ScratchPadSRAM，2 bank）| 96 行×256b | 455 | 3 | 0 | **8×BRAM36** | RAM_SDP 682×36×2——已在 BRAM |
| **inputDelayer / outputDelayer** | — | 703 / 392 | 258 / 512 | 0 | 0 | outputDelayer 全 SRL 实现 |
| **DMA**（Load/StoreQueue×nPorts）| 1 口 | 2,969 | 2,562 | 0 | 0 | P4 计划加深 inflight 时会涨 |
| 前端（instQueue 256×32 + Decoder + Semaphore）| — | 余量~1.2K | — | 0 | 0 | 与 glue 合并计在 fsa 顶层 |
| **合计（fsa 顶层）** | — | 351,236→359,093 | 35,215→92,306 | 320→288 | 8 | — |

> 单 PE 内部构成（映射关系）：reg(fp16 16b)+D 域(~90b)+SplitIF(LUT 移位/LZC)+RawFloat_FMA(1×DSP48E2 11×11 尾数乘 + 对齐/加法 LUT)+PWL ROM(LUT)+Rounding×2。
> 每列 CMP：oldMax/newMax(2×32b FF)+FMA 比较(现 LUT)+downCast。

### SLR / 时序画像（Current）
- SLR crossing：SLR2↔SLR1 1,377 SLL / SLR1↔SLR0 810 SLL（无 TX/RX_REG 补偿，仍是隐患）
- dut 域 WNS +0.091ns；总 endpoint 236,845

## 二、优化状态总账（P1 → P7）

| 阶段 | 内容 | 状态 | 实测收益 |
|---|---|---|---|
| **P0** | 报告采集基建（report.tcl/collect_baseline.tcl） | ✅ | — |
| **P1** | 工具级：去 ILA、HBM XSDB 关、`PerformanceOptimized -retiming`、MAX_FANOUT=64 | ✅ 上板 | WNS +0.003→+0.023 |
| **P2** | 控制路径切割：mxControl 输出 source Pipe(+1)（sp_read/acc_read/acc_ctrl/sem + pe_ctrl RegNext） | ✅ 仿真+板 | →+0.062；worst path 离开 mxControl |
| **P3** | 数据路径切割：accUnit FMA fmaStages=2（accLatency=3）+ Reciprocal 多拍化 + 对齐 validD/sramInD/accRAM 写回 L+1 | ✅ 仿真+板 | →+0.225（当时）；最长段 60+→~24 级 |
| **P3b** | PE MAC 单级切割 + mesh 管道深度 2（meshSlope=2），ControlGen 改逐周期 ROM | ✅ 仿真 | mxControl −12.4K LUT；PE/mesh FF 增（换时序） |
| **P4** | 切割后波前重对齐（cmp_ctrl 不带 source pipe、load_reg_ui 斜率 1 等一揽子锚点） | ✅ 仿真 | 正确性恢复 |
| **P5** | 调度正确性：删 saHeld；EXP_S1→Δm 拍；ACC_SA→实时 RowSum；value pass→O drain；phase-2 后移避影子纹波 | ✅ 仿真（4×4/16×16 与金模型逐位一致）+ 板（FPGA≡仿真） | 正确性；−128b FF |
| **P6** | PE exp2 就地 PWL ROM（单拍确定性），删除截距/斜率流基础设施（CMP counter/ROM、FSA Exp2Slopes、SpadConstIdx 缩 2 项） | ✅ 仿真 | 根除数据依赖锁存偏斜；死代码已物理删除 |
| **P7** | 4×4 U280 配置（`FSA4X4U280Config`）+ DMA 宽度适配（mbus TLWidthWidget / 直连 TL 三明治 / U280 harness 三明治 + HBM `interleavedId`）+ main.py 板卡参数 | 🔄 **4×4 比特流 elaboration 未通**（卡在 AXI4ToTL 的 TLError 前置）；16×16 不受影响 | 4×4 spad 行 8B vs 32B 口 |
| **P8（规划）** | PE DSP 流水化（use_dsp AREG/MREG/PREG，1,107 条 DRC）；accRAM→BRAM/URAM；SLR crossing 寄存；16×16 调度重推导；bubble 68% 压缩 | 未开始 | Fmax 主升空间在此 |

## 三、比特流构建与详细报告获取

### 构建（当前 RTL，16×16 主对象）
```bash
cd ~/FSA/fpga
export RISCV=/home/zhangsi/riscv          # FPGA 流程用这套（新 glibc，走系统工具链）
make SUB_PROJECT=u280 CONFIG=EmptyU280Config bitstream
# 4×4（待 TLError 修复后）：
# make SUB_PROJECT=u280 CONFIG=FSA4X4U280Config bitstream
```
流程内置报告（P0 集成）自动落盘：
`fpga/generated-src/<config>/obj/report/{timing,utilization,utilization_slr,utilization_hierarchical?,drc,fanout,power,congestion,complexity,route_status}.txt`

### 对现有 DCP 补采更细报告（无需重跑布线）
```bash
cd ~/FSA/fpga/generated-src/chipyard.fpga.u280.U280FPGATestHarness.EmptyU280Config/obj
vivado -mode batch -nojournal -nolog \
  -source ../../../../../scripts/collect_baseline.tcl \
  -tclargs post_route.dcp    # 脚本内未带 open，可在 vivado 里手动 open_checkpoint 后 source
# 或交互式补充逐层深钻：
#   report_utilization -hierarchical -hierarchical_depth 8 -file util_hier8.rpt
#   report_timing -max_paths 20 -path_type summary -file top20paths.rpt
#   report_high_fanout_nets -timing -load_types -max_nets 50 -file fanout.rpt
#   report_drc -ruledeck methodology -file drc_method.rpt
```

### 对账建议（下轮构建后）
1. **P7 三明治开销**：对比 `dutDomain` LUT/FF（当前 361K/93.6K 基线）
2. **P6 的 PE LUT**：mesh_0_0 当前 1,159 LUT——验证就地 ROM 取代流式后是否持平
3. **DSP 288 vs 320**：确认 CMP 32 个 DSP 的释放是否稳定（跨种子）
4. **DRC 1,107**：P8 DSP 流水化的直接目标清单（`drc.txt` 中 DPIP-2/DPOP-3/4 逐条带层次路径）

## 四、文件索引
- 本目录：baseline / p1 / p2 / p3 / optimization_plan_p1_p5 / optimization_plan_v2 / youhua（自 ~/chipyard-fsa 迁入，2026-09-15）
- `generators/fsa/OPTIMIZATION_PLAN.md`：P5 以后的正确性/调度主线与待办
- 原始 patch：`fpga/patches/{u280,p0p1,p2,p3}/`
