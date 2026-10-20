# NM37 A110 复位分发候选结果

本文保留原 A110（APB 110 MHz）的失败记录。后续将 APB 拆分至 55 MHz 的实现、验收和三组对比见 [A110 / APB55 报告](NM37_A110_APB.md)，不改写本文的历史执行结果。

## 执行身份与当前结论

A100 干净重跑实际执行提交为 `0a3a2656`，输出目录为 `/tmp/fsa-a100-final-build-20261015`。A110 候选实际执行提交为 `76ae6cd8`，输出目录为 `/tmp/fsa-a110-build-20261015`。本文与新增证据属于事后整理，不改写上述构建身份。

完整布局布线已完成，但 **A110 整板验收失败，不提升默认基线，保留 A100**。A110 的 setup/hold 通过，reset 局部复制与端点时序审计也通过；失败原因是随 DUT 升至 110 MHz 的 HBM APB PCLK 违反硬核最小周期与高/低脉宽要求。A110 代码仅保留在实验分支，不生成 bitstream，不宣称可以上板运行。生成 RTL 与六组系统仿真仍支持“PE/CMP/scratchpad/ACC/控制流水保持不变、相同工作负载的执行周期不变”。

## 实施方案与验收边界

候选在原 place 阶段之后、route 之前，针对实际驱动 fabric 负载的 reset 末级网络执行 `phys_opt_design -force_replication_on_nets`。本轮只做指定网络复制，**没有执行全局 `-slr_crossing_opt`**。时钟请求改为 110 MHz；源码默认仍为 70 MHz，PE 流水配置保持 A100 的设置。

局部结构审计保存复制前、复制后、路由后快照，检查末级 FDCE 的 D/C/CE/CLR 驱动、INIT、极性、同步属性、上游同步级以及负载集合。通过前必须确认复制确实增加有效末级实例，并验证路由后每个最终末级 D/CLR 的 max/min 四项时序均被覆盖且裕量非负。异步路径没有 timing path 不能算通过。

这些检查针对 reset 同步链及直接负载，**不是全设计网表形式等价证明**。常规算术形式证明、PE/控制测试和 RTL 系统仿真也不能代替物理复制后的结构与端点时序审计。冻结版本的通用 `physical()` 尚未单独解析 reset 审计结果；原计划的完整补充验收脚本要求整板 `physical.passed.json`，并绑定构建、阶段证明、DCP 和审计证据。本次整板已经失败，不运行该完整通过门禁，改作下文所述的独立局部结构与端点时序复核，不能混称整板通过。

## 生成 RTL 与时钟检查

对两个构建的 gen-collateral 中每个 SV 文件按文件名比较，只移除词法识别的 `// @[...]` 源码位置注释。保留代码、字符串、位宽、信号名称、语句顺序、空白、其他注释及综合指令，不通过重命名或逻辑归一化掩盖差异。逐文件原始/处理后 SHA-256 及差异见归档。

| 比较范围 | 结果 |
|---|---|
| sim16 | 224 个 SV 文件全部相同 |
| NM37 板级 | 89 个 SV 文件中 88 个相同 |
| PE、CMP、ScratchPadSRAM、Accumulator、MatrixControlFSM、Input/OutputDelayer、SystolicArray | 两构建对应模块相同，未改变这些模块的流水深度 |
| FSAConfig | 仿真与板级各自的 A100/A110 对照均逐字一致 |
| 板级唯一实际逻辑差异 | ResetWrangler 去抖终值 `14'h2710`（10000）→`14'h2AF8`（11000） |

去抖计数器仍为 14 位，其 `auto_in_0_clock` 连接 `harnessSysPLL_clk_out1`。ResetWrangler 源码按时钟周期计算去抖计数，10000/100 MHz 与 11000/110 MHz 对应相同的名义约 100 μs。这里改变的是启动去抖等待拍数，不是增加 PE 或 reset 同步器流水级，不能声称 A100/A110 的全部复位等待周期数相同。

实际生成的 harnessSysPLL Tcl 已确认：两版本输入请求均为 100.0 MHz，输出请求分别为 100.0 与 110.0 MHz。后续生成 IP 的实际参数也已核验：实现日志分别输出 `Achieve frequency 100.0 MHz ... jitter 115.831 ps` 与 `Achieve frequency 110.0 MHz ... jitter 211.720 ps`，A110 XCI 的 `MODELPARAM_VALUE.C_CLKOUT0_ACTUAL_FREQ` 为 `110.00000`。这些是 Vivado 生成 IP 的模型参数，不是板上测频；最终时序仍须根据 routed DCP/report 验收。输出 jitter 发生变化，且布局与布线也可能改变，所以不能仅把 A100 的 setup 裕量减去周期差约 0.909 ns，就据此预测 A110 是否通过。上述 SV 比较发生在物理实现前，不覆盖随后发生的 reset 末级复制。

## 功能与周期比较

六组回归均为 seed0、FP16 输入/FP32 累加；各对应比较的指令与内存输入字节、软件配置相同，双方均与参考逐位一致，双方输出也逐位相同。`execTime`、`mxActive`、`dmaActive`、`mxBubble`、`rawInst`、`mxInst`、`dmaInst`、`fence` 的差值全部为零；testbench 总周期差也为零。

| 阵列 | seq | A100 execTime（拍） | A110 execTime（拍） | 按 100 MHz 换算（μs） | 条件性按 110 MHz 换算（μs） |
|---|---:|---:|---:|---:|---:|
| 4×4 | 4 | 368 | 368 | 3.680 | 3.345 |
| 4×4 | 8 | 1083 | 1083 | 10.830 | 9.845 |
| 8×8 | 8 | 385 | 385 | 3.850 | 3.500 |
| 8×8 | 16 | 1100 | 1100 | 11.000 | 10.000 |
| 16×16 | 16 | 437 | 437 | 4.370 | 3.973 |
| 16×16 | 32 | 1152 | 1152 | 11.520 | 10.473 |

`execTime` 是 DUT active 状态周期，包含指令、依赖、DMA 与计算等待，不能当成纯算术延迟。其余 busy/bubble 计数存在重叠，不可相加得到总时间。testbench 总周期包含复位及 harness 活动且时钟可能不同，不按 DUT 频率换算。

若最终硬件确实达到 110 MHz 且同一工作负载的周期数保持不变，以上 active 时间可下降约 9.09%，对应同条件吞吐率上升 10%。这只是条件性周期换算，**不是已经测得的板卡吞吐提升**。4×4、8×8 是功能扩展配置，不能据此声称其具有独立布局布线的 110 MHz 实现。不同阵列大小还会改变 head_dim 和工作量，不作跨行性能比较。

## 物理结果与保留决定

| 项目 | 当前状态 |
|---|---|
| A110 全局 setup / hold | +0.151 / +0.010 ns |
| A110 DUT setup / hold | +0.481 / +0.010 ns |
| A110 最差周期/脉宽裕量 | −0.909 ns；整板不通过 |
| 布线 / bus skew / DRC | 497549 / 497549 可布线网络全部完成，0 路由错误；bus skew 最差 +2.824 ns；DRC 无 Error/Critical Warning |
| CDC / 约束复核 | 原始报告已归档；check_timing 有 30 个 unconstrained internal endpoints、1 个 no_input_delay，不能据此宣称整板 CDC/约束签核通过 |
| LUT / FF / DSP / RAMB36 | 356006 / 140282 / 288 / 60；与 A100 比较见下表 |
| reset 末级数量 | 复制前 2，复制后 10，路由后 10；跨 SLR 负载明细另见审计 |
| reset 局部结构审计与严格端点时序覆盖 | 独立重比通过；10 个末级 × 4 项 = 40 项，全部有限且非负，最差 +0.130 ns |
| 本轮整板限制 | HBM APB PCLK 的 10 ns 最小周期与 5 ns 高/低脉宽要求 |
| 候选保留 / 回退 | **不提升基线，保留 A100**；A110 保留实验代码与证据，不生成 bitstream |
| 上板运行与实测性能 | 未在本页声明通过 |


### 失败端点与源码根因

`obj/report/timing.txt:15871` 的 `Pulse Width Checks` 表报告：

| 检查 | 要求 | 实际 | 裕量 |
|---|---:|---:|---:|
| Min Period | 10.000 ns | 9.091 ns | −0.909 ns |
| Low Pulse Width（Slow / Fast） | 5.000 ns | 4.545 ns | −0.455 ns |
| High Pulse Width（Fast / Slow） | 5.000 ns | 4.545 ns | −0.455 ns |

后两类见同报告 15881–15892 行。它们是同一失败端点上的多条硬核检查，不是只有一条规则失败。端点为 `dutDomain/ram_hbm/inst/ONE_STACK.u_hbm_top/ONE_STACK_HBM.hbm_one_stack_intf/HBM_SNGLBLI_INTF_APB_INST/PCLK`，primitive pin 为 `HBM_SNGLBLI_INTF_APB/PCLK`，位置为 `BLI_HBM_APB_INTF_X8Y0`。

`fpga/src/main/scala/nm37/TestHarness.scala` 把 `ram.slaveClockNodes(0)` 接到 `dutFixedClockNode`；`fpga/src/main/scala/u280/AXIHBM.scala:102` 再把 `APB_0_PCLK` 直接接到该 `slaveClocks(i)`，所以 APB 管理时钟也变成 110 MHz。该模块同时把 APB reset 直接取自 AXI slave reset。`USER_APB_EN=false` 没有消除本次报告中该硬核 PCLK 的要求，不能通过忽略脉宽检查或添加 false path 来宣布通过。HBM AXI 数据接口的 ACLK 是另一个端点，不应把 APB 的 100 MHz 限制推广成整个 HBM 数据接口只能运行 100 MHz。

通用 physical 门禁正确拒绝本轮结果。依赖 `physical.passed.json` 的完整验收脚本不适用于整板失败的运行，因此另作独立局部复核：直接解析三份 reset 快照并重比两次结构，检查 40 个唯一端点/检查组合及空失败汇总，不依赖整板通过标记。结果为 **reset 局部通过、整板失败**，记录在归档 [fsa-a110-final-review.json](evidence/nm37-a110-reset/fsa-a110-final-review.json)，并绑定本次 DCP、构建与所读取审计文件的指纹。这不是全设计等价证明。

### 下一步方案（未实施）

1. 将 HBM APB 管理时钟从 AXI/DUT 时钟解耦，增加明确的 APB ClockSinkNode，接独立且受约束的 ≤100 MHz 时钟域。可评估使用已有稳定 100 MHz 时钟或独立 PLL 输出，但须同时满足该 IP 的周期、高/低脉宽和时钟路由要求；保持 AXI 数据域 110 MHz、PE 流水不变。
2. APB reset 必须在新的 APB 时钟域内按该 IP 要求同步释放；AXI reset 留在 DUT 域。重新核对上电、PLL lock 丢失、软件/板级复位以及 HBM 初始化的顺序，不能仅把 PCLK 换线而继续直接使用 DUT 域释放的 reset。
3. 当前 `XilinxHBMIO` 没有向上层暴露初始化完成状态。先核对所用 HBM IP 的真实完成/就绪接口和有效时钟域，再决定是否需要暴露并同步到 DUT，以此保证开始 AXI 访问前初始化完成。稳定电平采用目标域同步器；若状态实际是脉冲或握手，则采用相应 CDC 协议，不能假定简单两级寄存器总能捕获。
4. 明确所有 APB↔DUT 的状态/控制边界；相关时钟保留真实生成关系，不能为消除报告而笼统 false-path 整组时钟。验证 reset/init 时序、重复复位与初始化中断情形，再重跑 CDC、全部 setup/hold/最小周期/脉宽、功能回归及完整布局布线。

该方案针对本次已确认的板级时钟域错误；尚未实施或验证，不能据此保证下一版 110 MHz 一定通过。

### 布局布线后资源对照

| 资源 | A100 | A110 | 差值 | A100 / A110 整器件占用 |
|---|---:|---:|---:|---:|
| Total LUT | 355692 | 356006 | +314 | 27.28% / 27.31% |
| Logic LUT | 350958 | 351272 | +314 | 26.92% / 26.94% |
| LUTRAM | 3774 | 3774 | 0 | — |
| SRL | 960 | 960 | 0 | — |
| LUT as Memory（LUTRAM + SRL） | 4734 | 4734 | 0 | 0.79% / 0.79% |
| FF | 140263 | 140282 | +19 | 5.38% / 5.38% |
| DSP | 288 | 288 | 0 | 3.19% / 3.19% |
| RAMB36 | 60 | 60 | 0 | 2.98% / 2.98% |

使用同器件报告中的 Available 容量：LUT 1303680、memory-capable LUT 600960、FF 2607360、DSP 9024、RAMB36 2016。LUTRAM/SRL 共用 memory-capable LUT，不分别假设独立器件容量。A110 数量取原始 post-route hierarchical utilization 总计行；占用按上述明确容量计算至两位小数。复制操作新增 8 个末级 FDCE，但整次实现 FF 增加 19，不能把全部资源变化都归因于该 8 个复制寄存器。

### 复位路径的实际收益与代价

以下比较使用两次 routed DCP 导出的相同类别最差路径；端点可能不同，且频率、PLL jitter 和布局同时变化，因此不把全部差值归因于复制。

| reset 路径类别 | A100 最差 slack / data delay（ns） | A110 最差 slack / data delay（ns） |
|---|---:|---:|
| 末级到 fabric 负载分发 | +0.534 / 9.082 | +2.747 / 5.807 |
| 上游到末级 D | +9.583 / 0.351 | +4.577 / 4.213 |
| 异步释放到末级 CLR | +8.106 / 1.617 | +4.138 / 4.539 |

最差分发路径 data delay 减少 3.275 ns，其中 route 部分从 8.907 ns 降至 5.642 ns。A100 的最差端点是 `cmp_array_8/io_d_output_valid_REG_reg/D`，A110 是 `cmp_array_8/newMax_mantissa_reg[12]/R`（来自 `sync_0_reg_replica_5`）。上游和 CLR 路径延迟反而增加，仍通过严格覆盖检查。复制改善分发瓶颈，但不能声称所有 reset 路径均改善或跨 SLR 已完全消除。原目标网络有 370 个负载，分布为 SLR0 208、SLR1 162；复制后仍有跨 SLR 分支。

## 证据出处

本次分析记录、两份生成 PLL XCI 与参数摘要保存在 [evidence/nm37-a110-reset/](evidence/nm37-a110-reset/)，[inventory.json](evidence/nm37-a110-reset/inventory.json) 记录每份原 `/tmp` 路径、归档路径、字节数和 SHA-256。原文件复制后已逐字节指纹核对；PLL 摘要注明原始日志行和 XCI 参数来源。这些分析记录继续引用独立构建目录中的原始产物，不能把分析 JSON 当作原始布局布线报告。

- [逐模块 RTL 对照](evidence/nm37-a110-reset/fsa-a110-rtl-comparison.json)
- [对照方法](evidence/nm37-a110-reset/fsa-a110-rtl-comparison-method.txt)
- [六组回归计数与输入/输出指纹](evidence/nm37-a110-reset/fsa-a110-metrics.json)

- [生成 PLL 参数与日志摘要](evidence/nm37-a110-reset/pll-generated-summary.json)
- [A100 PLL XCI](evidence/nm37-a110-reset/A100-harnessSysPLL.xci)、[A110 PLL XCI](evidence/nm37-a110-reset/A110-harnessSysPLL.xci)

- [明确失败的物理复核记录](evidence/nm37-a110-reset/physical.failed.review.json)、[局部 reset 独立复核](evidence/nm37-a110-reset/fsa-a110-final-review.json)
- `A110/report`、`A110/audit`、`A110/reset_replication_audit` 保存完整文本报告，大文件为可无损解压的 `.gz`；未归档 JAR、DCP 或 bitstream。`A100/reset_timing` 为同类别基线原始路径报告。
- `A100/seals` 与 `A110/seals` 保存原 build/stage 身份证明，source-lock 保留执行输入清单。A100 的 constrained STA 通过标记同样不等同于板上测试或完整 board signoff。

A110 实现入口只在本地实验分支 `codex/nm37-a110-reset`（执行提交 `76ae6cd8`）保留。主基线仅接收报告与证据，不合入 A110 的时钟、driver 或源码锁。后续 HBM 时钟域改造不属于本轮已完成的实验结果。
