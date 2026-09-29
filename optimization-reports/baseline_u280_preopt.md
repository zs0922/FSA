# U280 Pre-Optimization Baseline (FSA 16x16, HBM)

> 采集自 2026-09-03 Vivado 2020.2 完整流（含 P0 报告补全）的 U280 bitstream，
> 作为优化前基线。优化路线图见文末。NM37 与本设计同 RTL 同 die，除
> part/shell 外数据基本一致，可参照对比。

## Build Info
- Board: Alveo U280
- Part: xcu280-fsvh2892-2L-e
- Vivado version: 2020.2_AR75986 (Build 3064766, lin64)
- Design: U280FPGATestHarness / EmptyU280Config（FSA 16×16, HBM AXI_00 256-bit）
- Git commit: FSA.git（fpga-shells-u280 / fsa-u280 补丁；P0 报告补丁已应用）
- Build date: **2026-09-03**（synth 04:21–04:26，place 04:26–04:51，route 04:51–05:27，report 05:31–05:38）
- Build seed: **Vivado 2020.2 不支持 `set_param place.Meu`（Common 17-153），无法固定种子**；单线程 + 固定 directive 实现可复现
- Clock constraints:
  - sys_clock 300 MHz（BJ43/BJ44，LVDS，HBM refclk 同源）
  - HBM ref 100 MHz（BH42/BJ42，NM37 同 BH42/BJ42 双用作 sys + HBM ref）
  - xdma_ref_clk 100 MHz（AR15/AR14）
  - **dut 域 70 MHz（14.286 ns，clk_out1_harnessSysPLL）**
- Synthesis strategy: `synth_design -flatten_hierarchy rebuilt`（默认策略，4 线程）
- Implementation strategy: `place_design -directive Explore`；route 默认；**无 phys_opt_design**

## Timing Baseline（2026-09-03 构建，P0 报告已采集）
- WNS: **+0.003 ns**（零裕量压线通过，`clk_out1_harnessSysPLL` 域）
- TNS: 0.000，failing endpoints 0 / 190,654
- WHS: +0.010 ns（0 failing / 190,638）；PW +24.468
- Clock period: 14.286 ns → **achieved Fmax ≈ 70.0 MHz**（1/(14.286−0.003)）
- 各域 WNS：
  - `clk_out1_harnessSysPLL`（dut 70M）：0.003 ns（106,201 endpoints）
  - `dbg_hub/INTERNAL_TCK`：12.405 ns
  - `xdma_ref_clk`：7.439 ns
  - `sys_clock`（300M）：1.216 ns
  - `HBM_REF_CLK_0`：3.890 ns
- **Worst path hierarchy**（dut 70MHz 域）：
  ```
  Source:      dutDomain/fsa/fsa/mxControl/fsm_list_1/accumTimer_reg[0]/C
  Destination: dutDomain/fsa/fsa/accumulator/accUnit_1/reciprocal/reg_r_exp_reg[8]/D
  Path Group:  clk_out1_harnessSysPLL
  Data Path:   13.830 ns = logic 4.731 (34%) + route 9.099 (66%)
  Logic Levels: 60 (CARRY8×19, LUT2×7, LUT3×6, LUT4×3, LUT5×5, LUT6×20)
  Clock Uncertainty: 0.284 ns (TSJ 0.071 + DJ 0.563)
  ```
  → 根因画像：① mxControl→accumulator 控制到倒数单元的 **60 级组合逻辑**；
  ② route 占 66%（控制广播跨越 mesh/acc 区域，跨 SLR0→SLR1）
  ③ mxControl cmd[1] **fanout=1007** 是最危险组合节点（LUT6 驱动，slack 0.003ns）

## High Fanout Baseline（2026-09-03 构建）
| Net | Fanout | Driver | Worst Slack (ns) | 判定 |
|---|---:|---|---:|---|
| wrangler/.../io_sync_reset_chain/sync_0 | 1,609 | BUFGCE | 8.065 | 安全（复位树，低优先级） |
| sram_accRAM_fullWrite_0_addr_REG[1] | 1,216 | FDRE | 11.561 | 安全（寄存器驱动） |
| sram_accRAM_fullWrite_0_addr_REG[2] | 1,216 | FDRE | 10.280 | 安全 |
| sram_accRAM_fullWrite_0_addr_REG[3] | 1,216 | FDRE | 10.628 | 安全 |
| sram_accRAM_fullWrite_0_addr_REG[4] | 1,216 | FDRE | 11.138 | 安全 |
| **mxControl/fsm_list_0/_mxControl_io_acc_ctrl_bits_cmd[1]** | **1,007** | **LUT6** | **0.003** | **⚠️ 最危险：组合驱动+零裕量** |
| mxControl/fsm_list_0/_mxControl_io_acc_ctrl_bits_cmd[2] | 1,007 | LUT6 | 0.235 | ⚠️ 同上 |
| mxControl/fsm_list_1/accumTimer_reg[3]_0[0] | 1,006 | LUT5 | 0.016 | ⚠️ 同族 |
| ila_1_ila/.../use_probe_debug_circuit | 637 | FDRE | 10.595 | 安全（ILA debug） |
| accRAM_sram/.../sram_accRAM_fullWrite_0_valid_REG_reg | 608 | LUT2 | 10.465 | 安全 |

→ cmd[1] fanout 1007 与 16×16 mesh 吻合（256 PE × ~4 处使用 ≈ 1024），**单点组合广播**是最危险时序节点。
→ addr_REG fanout 1216 虽高但由 FDRE 驱动（有寄存器缓冲），slack 充裕。

## DRC Baseline（2026-09-03 构建）
- Violations: DPIP-2 **593**、DPOP-3 **321**、DPOP-4 **321**、REQP-1858 23、RTSTAT-10 2（合计 ~1,260）
- **DSP Pipeline（PE/FMA 模板系统性问题，youhua 点 4–7）**：
  - 代表层次：`dutDomain/fsa/fsa/accumulator/accUnit_i/mulAddExp2/fma/_addProd_T_10`（及 mesh PE 同模板）
  - A/B/C 输入无 pipeline（AREG/BREG/CREG=0）→ DPIP-2
  - P 输出未用 PREG → DPOP-3；乘法级 MREG=0 → DPOP-4
  - DSP48E2 变换：320 个全部实例化为完整 DSP_ALU + DSP_A_B_DATA + DSP_C_DATA + DSP_MULTIPLIER + DSP_M_DATA + DSP_OUTPUT + DSP_PREADD + DSP_PREADD_DATA
  - **DSP48 三级流水全部未启用，320 个 DSP 全体裸奔**

## Utilization Baseline（2026-09-03 构建）
| 资源 | 用量 | 占比（xcu280: 1.31M LUT / 9216 DSP / 2016 BRAM36 / 960 URAM） |
|---|---:|---|
| LUT | 381,056（logic 375,687 + LUTRAM 3,814 + SRL 1,555） | **29.1%** |
| FF | 72,510 | ~2.8% |
| BRAM | RAMB36×80 + RAMB18×3 | ~4.0% |
| URAM | **0** | **0%（严重浪费，accRAM 应改 URAM）** |
| DSP | 320 | 3.5% |
| CARRY8 | 8,917 | 5.5% |

**FSA 核心模块分布**：
| 模块 | LUT | FF | DSP | BRAM | 说明 |
|---|---:|---:|---:|---:|---|
| `fsa`（AXI4FSA 顶层） | 351,236 | 35,215 | 320 | 8 | 含全部 FSA 逻辑 |
| ├ `fsa`（FSA 核心） | 347,543 | 32,002 | 320 | 8 | — |
| │ ├ `sa`（SystolicArray） | **313,811** | 28,855 | **288** | 0 | 256 PE + 16 CMP |
| │ │ ├ `mesh_0_0`（PE） | ~1,126 | 17 | 1 | 0 | 单 PE：~1.1K LUT / 17 FF / 1 DSP |
| │ │ └ `cmp_array_0`（CMP） | ~1,329 | 67 | 2 | 0 | 单 CMP |
| │ ├ `mxControl`（MatrixEngineController） | **18,569** | 441 | 0 | 0 | **18.6K LUT FSM，控制广播中心** |
| │ ├ `accumulator`（Accumulator） | 11,961 | 1,760 | **32** | 0 | 16 FPAccUnit |
| │ └ `accRAM_sram`（AccumulationSRAM） | 2,040 | 27 | 0 | 0 | **LUT 实现 SRAM，应改 BRAM/URAM** |
| ├ `dma`（DMA） | 2,969 | 2,562 | 0 | 0 | — |
| ├ `dmaInst_q`（Queue2） | 73 | 3 | 0 | 0 | — |
| └ `dma`（InstructionMerger） | 13 | 106 | 0 | 0 | — |

**SLR 分布**：
| SLR | LUT | FF | BRAM | DSP | CLB |
|---|---:|---:|---:|---:|---|
| SLR0 | 33,512 | 40,493 | 73.5 | 0 | 9,025（16.4%） |
| SLR1 | 170,763 | 15,587 | 8 | 158 | 27,894（51.7%） |
| SLR2 | 176,781 | 16,430 | 0 | 162 | 28,545（52.9%） |
→ SLR1/SLR2 接近 50% 利用率，LUT 密度较高；DSP 跨 SLR1/SLR2（158/162）；BRAM 全在 SLR0（HBM 侧）
→ SLR crossing：共 1,996 SLLs（SLR2↔SLR1 1,353；SLR1↔SLR0 643）；无 TX_REG/RX_REG 补偿

## Power Baseline（2026-09-03 构建）
| 指标 | 数值 |
|---|---:|
| **Total On-Chip Power** | **20.239 W** |
| FPGA Power | 15.244 W |
| HBM Power | 4.996 W |
| Dynamic | 16.665 W |
| Device Static | 3.575 W |
| Max Ambient | 91.2°C |
| Junction Temperature | 33.8°C |
| Confidence Level | Medium |

**On-Chip Power Breakdown**：
| 组件 | Power (W) | 说明 |
|---|---:|---|
| HBM | 8.890 | **最大功耗源**（44%） |
| CLB Logic | 2.145 | LUT 逻辑 |
| Signals | 1.879 | 互连 |
| GTY | 2.406 | PCIe PHY |
| BRAM | 0.407 | 81.5 BRAM |
| Clocks | 0.425 | — |
| DSPs | 0.061 | 320 DSP |
| PCIE Hard IP | 0.433 | — |

**FSA 核心功耗**：
- `dutDomain/fsa/fsa`：3.686 W（占 FPGA 逻辑 24%）
- `dutDomain/ram_hbm`：8.896 W（HBM 子系统）
- `pcie`：3.947 W（PCIe 子系统）

**Power Supply**：
- Vccint 0.850V：8.591 A total（6.912 A dynamic + 1.679 A static）
- VCC_IO_HBM 1.200V：2.304 A
- VCC_HBM 1.200V：2.461 A
- MGTYAVcc 0.900V：0.455 A

## Route / Congestion Baseline（2026-09-03 构建）
**Route Status**：
- Total logical nets: 871,214
- Internally routed: 337,125
- No loads: 49,567
- Routable nets: 484,522 → **fully routed（0 unrouted，0 routing errors）**

**Congestion**：
- Placer final level：**No congestion windows above level 5**（无拥塞）
- Router initial：无拥塞报告

**Design Complexity**（Rent exponent + fanout）：
| Instance | Rent | Avg Fanout | Instances | LUT6% |
|---|---:|---:|---:|---:|
| (top) U280FPGATestHarness | 0.16 | 4.08 | 540,334 | 44.7% |
| dutDomain | 0.12 | 4.17 | 484,801 | 45.4% |
| pcie | **0.56** | 3.32 | 51,746 | 34.7% |
| masterClockConverter | 0.05 | 4.48 | 2,062 | 9.8% |

→ FSA 核心 LUT6 占比 44.7%（高复杂度），但 Rent 0.12（低划分复杂度）→ 适合 pblock 分区

**SLR Net Crossing**：
| Cell | Nets Crossing SLR | 0-1 Cuts | 1-2 Cuts |
|---|---:|---:|---:|
| dutDomain | 1,070 | 324 | 746 |
| dutDomain/fsa | 662 | 62 | 600 |
| dutDomain/fsa/fsa | 7 | 0 | 7 |
| masterClockConverter | 256 | 256 | 0 |

## Runtime Performance Baseline（板上实测，seq=16）
- execTime **7852 cycles** @70MHz = **112.2 µs**；RelErr 1.88e-4
- 构成：MX active 179（2.3%）、DMA active 171、**bubble 5317（67.7%）**、其余开销 ~2190
- 有效算力：4·16²·16 = 16,384 MAC / (7852×256 PE) → **PE 阵列利用率 0.81%**
- 结论：**访存延迟主导**（单 HBM AXI_00 口 + 浅 inflight + 单块 kernel 无重叠）

## CDC / Clocking（2026-09-03 构建）
- 时钟域：sys 300M / HBM ref 100M / xdma ref 100M / dut 70M / XDMA AXI 250M（pipe_clk 250M、GT 内部 500M/PCS）
- 跨域：XDMA AXI(250M) ⇄ dut(70M) 经 2× axi_clock_converter（异步，已设 group/async path）
- 未约束路径：30 个 unconstrained_internal_endpoints（`check_timing` 报告），均为 debug 相关
- 跨域时序：
  - `clk_out1_harnessSysPLL` → `INTERNAL_TCK`：WNS 13.879 ns（安全）
  - 各 GTYE4_CHANNEL_TXOUTCLK：WNS 0.710 ns（安全）

---

# 优化路线图（规划，未实施）

> 参照 TAPA-CS (ASPLOS'24) 三板斧——**槽位级 floorplanning、槽间互连流水化+cut-set
> 平衡、HBM 通道/位宽探索与通信计算重叠**——结合 youhua.md 优化点与上述 baseline。

## 瓶颈因果链（2026-09-03 数据实锤）
```
Fmax=70MHz ← WNS 0.003ns ← ┬ 60 级组合：mxControl FSM → accUnit reciprocal（logic 4.7ns）
                           ├ 控制广播 fanout 1007 LUT6 组合驱动（route 9.1ns, 66%）
                           ├ DSP AREG/MREG/PREG 全关（DRC 1,260 条：DPIP-2=593, DPOP-3=321, DPOP-4=321）
                           ├ cmd[1] LUT6 驱动 1007 负载，最危险组合节点（slack 0.003ns）
                           └ SLR crossing 1,996 SLLs（无 TX/RX_REG 补偿）
吞吐 0.81% PE 利用率 ← bubble 68% ← HBM 读延迟 + 单口 + 浅 inflight + 单块无重叠
功耗 20.2W ← HBM 8.9W (44%) + FPGA 15.2W（LUT 2.1W + Signals 1.9W + GTY 2.4W）
```

## P0 报告采集补全（✅ 已完成，2026-09-03）
- `report.tcl` 已集成：`report_power`、`report_utilization -slr`、`report_design_analysis -congestion`、`report_design_analysis -complexity`、`report_route_status`
- `collect_baseline.tcl`：独立脚本（含 power verbose、hierarchical utilization、long_wires、design_analysis -timing）
- P0 报告已随 baseline 构建自动采集，本文件字段已更新

## P1 工具/约束级（不动 RTL，预期 70→85–95MHz）
1. 去 ILA/dbg_hub（省 ~1.5K LUT + 2.4K FF + 4 BRAM；去 dbg_hub 时序路径与拥塞）
2. `synth_design -directive PerformanceOptimized` + **`-retiming`**（自动切分 60 级组合）
   `place_design -directive ExtraTimingOpt`；`phys_opt_design -directive AlternateFlowWithRetiming`
   → 注意：Vivado 2020.2 无 `PerformanceExploreWithRetiming`、`ExtraTimingDelay`、`NoTimingRelaxation`
3. XDC `MAX_FANOUT=64` 压 cmd/accumTimer 复制点
4. TAPA-CS §4.5 槽位级 pblock：mesh 居中、mxControl+accumulator 邻接 mesh、
   DMA/spad/HBM 胶水靠近 HBM 出口 SLR、控制不等式 CONTAIN_ROUTING=false（软约束）
   → 直接攻击 worst path 的 9.1ns route

## P2 RTL 控制分发（youhua 点1/2/3 + TAPA-CS §4.6，预期 +2~3ns WNS）
generators/fsa 补丁（走 patch 流程）：
1. `io_acc_ctrl_bits_cmd` 先寄存（Driver LUT6 → FDRE）
2. 层次广播：global cmd reg → row/tile cmd reg → PE（每级一拍）
3. mesh 边界 pipeline；reconvergent 路径按 cut-set 做延迟对齐（对齐 data/valid/cmd）
4. 顺带：accRAM 地址 per-bank 本地寄存（youhua 点9）
验收：fanout <100/net；WNS ≥ +2ns；Verilator RelErr 不变；execTime 仅增固定常数。

## P3 RTL DSP/数据通路（youhua 点4–7，收益最大，预期 120–150MHz）
1. fma 模板三级流水：`a/b/c → RegNext`（AREG/BREG/CREG）、`mul → RegNext`（MREG）、
   `out → RegNext`（PREG）；一次修模板，mesh PE 与 accUnit 全体受益
2. **reciprocal 单元拆 3–4 级流水**（60 级组合 → ~15 级/段，直接消掉 WNS 路径）
3. （后置）acc fp32 加法改 DSP48 PCOUT/PCIN 级联：省 10–20 万 LUT + 缩短进位链
验收：DPIP-2/DPOP-3/4 降到个位数；Fmax ≥120MHz；数值与 execTime（+流水常数）复核。

## P4 访存/吞吐（TAPA-CS 通信重叠 + HBM 探索，目标 bubble 68%→<40%）
1. DMA inflight 加深（LoadQueue / UserYanker cap 8→16/32）隐藏 HBM 延迟
2. `nMemPorts=2`：K/V 分走 HBM AXI_01（LazyXilinxHBMController(portNum=2) 现成支持）
3. spad 预取队列（URAM 0%→利用）解耦 DMA 与 MX
4. seq 64/128 验证 bubble 摊薄与带宽饱和度

## 阶段验收循环
每阶段：elaboration → Verilator 数值回归（AXI4FSA16X16Fp16）→ bitstream →
板上 python 回归（RelErr + execTime + perf counters）→ 三报告（timing/fanout/drc）
diff 本 baseline 文件后再进入下一阶段。

## 频率/性能路线
| 阶段 | Fmax | execTime(seq16) | 备注 |
|---|---|---|---|
| **baseline（2026-09-03）** | **70 MHz** | **112.2 µs** | WNS +0.003ns, 20.2W, P0 报告已采集 |
| +P1 | 85–95 MHz | ~83–93 µs | 纯工具（去 ILA + retiming + phys_opt + pblock） |
| +P2 | 100–110 MHz | ~72–79 µs | 控制分发（cmd 寄存 + 层次广播） |
| +P3 | 120–150 MHz | ~53–66 µs | DSP/数据通路（FMA 三级流水 + reciprocal 拆分） |
| +P4 | 同频 | bubble 减半 → <60 µs | 访存重叠（DMA inflight + nMemPorts=2 + URAM spad） |
