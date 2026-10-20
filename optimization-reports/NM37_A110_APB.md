# NM37 A110：HBM APB 独立时钟域与启动控制


## 当前结论

本轮修复的是旧 A110 的板级时钟域问题：HBM 的 APB 管理时钟此前随 FSA/AXI 一起升到 110 MHz，违反硬核的最小周期和脉宽要求。新方案保持 FSA/AXI 为 110 MHz，将 APB 独立为 55 MHz，并补齐复位释放、初始化完成跨域同步和启动前 AXI 阻断。

**编译、单元与调度测试、三项算术等价证明、HBM/PLL 启动协议验证及六组系统回归全部通过。PE 流水深度与六组工作负载的执行周期均未改变。完整布局布线及只读复审已完成：所有受约束的板级时序通过，本轮新增/变化的 36 条 CDC 已逐项审查接受。尚未做上板性能测试。**

最终处置：**保留 APB55 优化作为 A110 实验分支候选**。全局 setup/hold 为 +0.114/+0.010 ns，DUT 为 +0.802/+0.010 ns，APB 周期和脉宽均通过。该结论不等于上板运行通过或所有既有 CDC/约束问题已关闭。

## 旧 A110 为什么失败

旧候选已经改善高扇出复位分发，DUT setup/hold 为 +0.481/+0.010 ns，但整板仍被 HBM APB 的硬核检查拒绝：

| HBM APB 检查 | 硬核要求 | 旧 A110 实际 | 裕量 |
|---|---:|---:|---:|
| 最小周期 | 10.000 ns | 9.091 ns | −0.909 ns |
| 高电平脉宽 | 5.000 ns | 4.545 ns | −0.455 ns |
| 低电平脉宽 | 5.000 ns | 4.545 ns | −0.455 ns |

失败端点为 `HBM_SNGLBLI_INTF_APB/PCLK`。旧连接让 `APB_0_PCLK` 直接使用 AXI slave clock，因而也变成 110 MHz；`USER_APB_EN=false` 没有取消该时钟引脚的硬核要求。APB 的最高 100 MHz 限制不适用于 HBM AXI 数据接口。[旧实验完整结果](NM37_A110_RESET_RESULTS.md)、[AMD PG276 Performance](https://docs.amd.com/r/en-US/pg276-axi-hbm/Performance)

## 本轮改动及运行约束

| 部分 | 新方案 | 对 FSA 的影响 |
|---|---|---|
| DUT / AXI 时钟 | 保持 110 MHz | 计算时钟和 PE 深度不变 |
| HBM APB 时钟 | 同一 MMCM 的另一输出，55 MHz；IP 参数同步声明 55 MHz | 常规数据传输仍走 AXI；APB 变慢可能增加初始化等待 |
| HBM reference clock | 保持板上 100 MHz 来源 | 未改 PHY 参考时钟来源 |
| APB / AXI reset | 共同异步断言，分别在所属时钟域按顺序释放 | 禁止独立 AXI warm reset，cold reset 丢弃在途工作 |
| 初始化完成 | APB 域采样，再经三级 AXI 域同步；确认完成才开放流量 | 不再允许 AXI 早于 HBM 初始化完成 |
| AXI 启动门控 | 同时阻断五通道两侧握手；开放后组合直通 | 不新增数据流水拍数 |
| PLL 失锁 / 外部 PERST | 同时使 PCIe/XDMA 进入 fundamental reset | 恢复需要 PCIe 重训练、主机重新初始化和重载数据 |

110 MHz 与 55 MHz 保持 2:1，属于同一个 ClockGroup，工具继续分析真实相关时钟路径，没有将两域整体设置为异步或 false path。选择 55 MHz 是为了满足 APB 频率限制并避免 110/100 MHz 组合约 0.909 ns 的最小相邻边沿窗口；本轮没有完成 APB100 候选的布局布线，不能称其已被实测否定。

APB reset 需要三个 APB 上升沿才释放；AXI reset 等待 APB release 后，再经过三个 AXI 上升沿释放。`apb_complete` 在 APB 域寄存后进入三级 AXI 同步链，完成确认前不开放访问。这里的等待用于安全启动，不能当作 PE 流水延迟。[PG276 Port Descriptions](https://docs.amd.com/r/en-US/pg276-axi-hbm/Port-Descriptions)、[PG276 Non-AXI Ports](https://docs.amd.com/r/en-US/pg276-axi-hbm/Non-AXI-Ports)

新增的 PLL 复位资格检查由独立板上 100 MHz 时钟驱动，取 ResetWrangler **之前**的原始 PLL reset。PLL 恢复后先经三级同步，再连续计满 10000 拍才允许 PCIe reset 释放，最早需要 10003 个监测时钟上升沿。该信号不复位 PLL，也不取决于 HBM ready，避免 PCIe、Wrangler 与 HBM 相互等待。100 μs 是本设计采用的保持策略，不能称为 PCIe IP 文档规定的最短要求。[PG195 XDMA Global Ports](https://docs.amd.com/r/en-US/pg195-pcie-dma/XDMA-Global-Ports)、[PG213 Clock and Reset Interface](https://docs.amd.com/r/en-US/pg213-pcie4-ultrascale-plus/Clock-and-Reset-Interface)

PLL 失锁会触发 PCIe 冷复位流程；运行中若同步后检测到初始化完成状态意外撤销，则进入锁定故障、阻断流量，等待一次共同 cold reset。后者不会自动进行无损事务恢复。两种情况下都不能假定在途 DMA 或 HBM 数据仍可继续使用，主机必须重新初始化任务并重载所需数据。

实现入口见 [NM37 harness](../fpga/src/main/scala/nm37/TestHarness.scala)、[HBM 时钟与接口连接](../fpga/src/main/scala/u280/AXIHBM.scala)及[复位、启动和 AXI 门控](../fpga/src/main/scala/u280/HBMStartup.scala)。这些改动位于板级集成层；本轮没有修改 FSA/easyfloat 的算术流水和控制调度。

## 已完成验证

正式结果全部来自独立新目录 `build04`，未复用 `build02/build03` 的生成器、仿真器或阶段通过记录。

| 验证范围 | build04 结果 | 能支持的结论 |
|---|---|---|
| 编译、板级 RTL 生成 | 通过 | 板级配置与真实产品模块可生成 |
| 单元与调度 | 14394 个 FPMac、20169 个 CMP、90 个 PE EXP2 用例；5 项控制测试通过 | 原有算术、驻留扫描和控制调度回归通过 |
| 算术形式证明 | `cmpeq`、`cmpseq`、`macseq` 全部通过 | 组合等价或一拍流水填充后的时序等价 |
| HBM/PLL 启动协议 | 8 组 HBM 时钟、32768 组门控向量、3362863 次检查通过 | 停钟、短复位、初始化中断/撤销、背压及释放边沿符合所测协议 |
| PLL/PERST 子测试 | 8 组场景、32 次资格释放；测试计数参数为 7 | 短失锁/外部复位会重启等待，监测时钟停顿时不会释放 |
| 六组 FSA 系统回归 | 指令、输入、配置、输出、全部八项计数器和仿真总周期一致 | APB 改造未改变所测 FSA 计算与调度行为 |
| 原 A110 与本轮 RTL 对照 | 板级 15 个核心模块一致；sim16 全部 224 个 SV 一致 | PE/CMP/scratchpad/ACC/控制/对齐流水未改变 |

RTL 对照仅用词法扫描移除 CIRCT 源码位置行注释，保留代码、位宽、名称、其他注释和综合指令。生产板级 RTL 另行核对 PLL 资格计数为 10000，而不是仿真加速使用的 7。

`cmpeq` 覆盖所有组合输入位模式；`cmpseq/macseq` 使用无输入、无初态约束的时序归纳，证明一拍流水转换后的等价。它们不等于整个 FSA、HBM 启动状态机或布局布线网表的形式证明。HBM/PLL 数字测试不模拟模拟亚稳态、PCIe 链路或 HBM PHY；AXI gate 测试是控制/载荷及定向背压检查，不是完整事务级 AXI scoreboard。

验证与方法见最终归档中的 `build/*passed.json`、`build/formal`、`build/hbm-startup`、`build/sim*`、`review/candidate-comparison.json[.gz]` 和 `review/build04-verification-review.json`，摘要与原始文件的对应关系由[证据清单](evidence/nm37-a110-apb/inventory.json)记录。

## 运行周期与性能边界

以下对应比较均为相同阵列、序列长度、seed0、FP16 输入/FP32 累加以及相同指令/内存输入。八项计数器差值全部为零，不仅是 `execTime` 相同。

| 阵列 / 序列长度 | A100 / 旧 A110 / APB55 execTime | 三者仿真总周期 | 100 MHz active 时间 | 110 MHz active 时间 |
|---|---:|---:|---:|---:|
| 4×4 / 4 | 368 / 368 / 368 | 4707 | 3.680 μs | 3.345 μs |
| 4×4 / 8 | 1083 / 1083 / 1083 | 11857 | 10.830 μs | 9.845 μs |
| 8×8 / 8 | 385 / 385 / 385 | 4827 | 3.850 μs | 3.500 μs |
| 8×8 / 16 | 1100 / 1100 / 1100 | 11977 | 11.000 μs | 10.000 μs |
| 16×16 / 16 | 437 / 437 / 437 | 5427 | 4.370 μs | 3.973 μs |
| 16×16 / 32 | 1152 / 1152 / 1152 | 12577 | 11.520 μs | 10.473 μs |

本轮相对旧 A110 没有减少计算周期；目的在于使 110 MHz 配置满足板级时钟与启动要求。本轮受约束 STA 已确认 110 MHz，可将相同的 A100 周期预算换算为约 9.09% 的 active 时间下降，或同条件下 10% 的频率收益。这是计算周期换算，**不是测得的整板吞吐提升**。

`execTime` 含 DUT active 期间的计算、依赖和 DMA 等等待；busy/bubble 计数有重叠，不应相加。testbench 总周期不按 DUT 时钟换算。4×4、8×8 用例提供功能扩展覆盖，完整板级布局布线目标是 16×16，且不同阵列用例的工作量不同。

## 完整布局布线结果与保留决定

下表来自最终 routed DCP 的实际报告和物理门禁，逐项来源、原始行号与 SHA-256 见证据包中的 `review/build04-physical-summary.json`。A100 执行提交为 `0a3a2656`，旧 A110 为复位复制历史实验，APB55 为 `eeb1a1d0`。

| 时序 / 审计项 | A100 | 旧 A110（APB110） | 本轮 A110（APB55） |
|---|---:|---:|---|
| 全局 setup / hold | +0.049 / +0.010 ns | +0.151 / +0.010 ns | +0.114 / +0.010 ns |
| DUT setup / hold | +0.534 / +0.010 ns | +0.481 / +0.010 ns | +0.802 / +0.010 ns |
| HBM APB 最小周期裕量 | 0.000 ns | −0.909 ns | +8.182 ns |
| APB 高 / 低脉宽裕量 | 0.000 / 0.000 ns | −0.455 / −0.455 ns | +4.091 / +4.091 ns |
| bus skew | +3.142 ns | +2.824 ns | +2.596 ns |
| 完整路由 / DRC | 路由完成；无 Error/Critical Warning | 路由完成；无 Error/Critical Warning | 497569/497569 nets；Error/Critical Warning=0 |
| 新增 CDC / 复位端点审查 | 无本轮新增模块 | 无本轮 APB 域和 PLL 资格模块 | 36 条变化逐项接受；原有范围保留 |
| 复位复制结构及 D/CLR max/min 覆盖 | 原基线策略 | 局部通过 | 21 个副本、84 项端点检查，最差 +0.259 ns |
| 全部受约束的板级物理门禁 | 通过（既有 CDC/约束边界保留） | 失败 | 通过 |
| bitstream / 上板运行 | 见 A100 历史报告 | 未导出 / 未通过本页验收 | 已导出且摘要核验通过 / 未实测 |

| 资源 | A100 | 旧 A110 | 本轮 A110/APB55 | 相对旧 A110 变化 / 整器件占用 |
|---|---:|---:|---|---|
| Total LUT | 355692（27.28%） | 356006（27.31%） | 355981（27.31%） | -25 / 27.31% |
| Logic LUT | 350958（26.92%） | 351272（26.94%） | 351247（26.94%） | -25 / 26.94% |
| LUT as Memory | 4734（0.79%） | 4734（0.79%） | 4734（0.79%） | +0 / 0.79% |
| FF | 140263（5.38%） | 140282（5.38%） | 140324（5.38%） | +42 / 5.38% |
| DSP | 288（3.19%） | 288（3.19%） | 288（3.19%） | +0 / 3.19% |
| RAMB36 | 60（2.98%） | 60（2.98%） | 60（2.98%） | +0 / 2.98% |

各资源括号内为占整器件同类资源的比例；Logic LUT 与 Total LUT 使用相同 LUT 总容量，memory LUT 使用可配置为存储器的 LUT 容量，其占用率不能与总 LUT 相加。占用率采用同一器件容量：LUT 1303680、memory-capable LUT 600960、FF 2607360、DSP 9024、RAMB36 2016。需要同时检查 BRAM 类型或计数口径是否变化，不能只依据“PE RTL 相同”就预填整个设计资源不变。

最终保留理由：修复旧 A110 的 APB 硬核违规，保持 PE 深度及所测执行周期，LUT 比旧 A110 减少 25、FF 增加 42；相对 A100 为 LUT 增加 289、FF 增加 61，DSP/BRAM 不变。旧 A110 的失败记录继续作为历史证据保留；本轮是否可用由本轮结果独立决定。原有约束边界仍为 30 个未使用 HBM AXI1–15 BREADY/RREADY 内部端点未约束，以及 `prst_n` 一项缺少 input delay。CDC 全量仍报告 25 Critical、171 Warning、98 Info；本轮验收接受相对基线的 36 条变化，不宣称清零全部既有 CDC。`cdc.review_required=false` 表示本轮差异已完成审查；顶层 `cdc_review_required=true` 和 `board_signoff=false` 保留原有整板签核边界。HBM/PLL 启动与复位的 66 项 max/min 时序检查全部通过，最差 +0.066 ns。

## 实现身份与只读复审

本轮完整综合、布局、复位复制与布线使用冻结源码 `eeb1a1d0`。物理审计中发现两个网表表示兼容问题：Vivado 将常开参考时钟缓冲器实现为 `BUFGCE`；优化还消除了启动模块的 `io_reset` 层级引脚。因此，原审计对 `BUFG` 名称和该边界引脚的假设不适用于实际网表。此外，原执行在 routed DCP 已写出后、最终 utilization 报告生成期间执行中断，原最终报告集不完整。原执行日志、审计失败和中断记录保留，不改写成原流程通过。复审不能引用原目录中未完成的报告作为通过证据。

修订审计检查真实单元：`BUFGCE` 的 I/CE 引脚必须明确不反相、CE 必须唯一连接 VCC；公共复位必须为两域复位释放末级 Q 的精确 NAND，并核对完整三级链的类型、初值、时钟、使能、极性和连接顺序。未知属性、错误逻辑或未覆盖的时序端点仍然拒绝。修订只影响检查代码，不改变 RTL、约束、IP 参数或实现策略。

单独的 `reaudit` 入口从同一个 `post_route.dcp`（SHA-256 `44195817d21dfdc147800868637505f1ce89303fd3aa98f1c6e044f4b3ccf236`）重新生成报告，在新目录记录修订后的验收身份。它核对原构建清单、七个验证阶段全部封印、实际生成器与 IP XCI、原驱动 Git 对象、原报告和 DCP 的摘要；执行前后均检查原产物不变。新增 CDC 必须绑定本次 DCP 和本次报告逐项审查。审计、报告和证据提交不意味着再次综合或布局布线，也不替代任何真实失败的时序检查。最终复审为 `passed`：硬件实现提交 `eeb1a1d0`，只读审计提交 `25673838`；报告目录为 `reaudit04/obj`，验收记录为 `reaudit04/acceptance/cdc-reviewed/reaudit.passed.json`。原目录保持失败/中断历史，本次通过来自上述独立复审，不是改写原流程。

物理门禁通过后已从同一 DCP 导出 [NM37_A110.bit](/home/zhangsi/tmp/FSA-m1/nm37-m1-pipeline/apb110-20261020/bitstream04/export/NM37_A110.bit)，大小为 84989293 bytes，SHA-256 为 `f3ffe9b90b60fb9ea00594381174bbf47e1f1bb435f2e98cfd32ca292fe11f86`。`bitstream04/export.passed.json` 绑定原 DCP、构建身份和最终验收记录，导出退出码为 0。仅完成文件导出，未配置板卡或执行实机运行。

## 启动耗时与上板范围

板上完整启动耗时尚未测量。PLL 资格保持约 100 μs、ResetWrangler 的名义约 100 μs 去抖、PCIe 链路训练、HBM 校准及主机初始化共同决定可用时间，不能把其中一个计数直接当成“整板启动只需 100 μs”。APB 降到 55 MHz 也可能延长其管理操作；本次 FSA 仿真不包含这些板级等待。

后续上板验收至少覆盖：冷启动/重新枚举、HBM 初始化完成前无 AXI 访问、初始化后正确读写与 FSA 输出、连续执行和不同负载、PERST 后恢复；具备受控注入条件时验证 PLL 失锁后的冷复位和重训练。每次冷复位后重新初始化设备、装载程序及数据。主机侧实际 DMA/计算耗时和稳定性需单独测量，才能报告整板吞吐；这些硬件结果目前均未宣称完成。

## 复现入口与源码身份

正式执行源码为 `eeb1a1d0ac6f47497eb2806e412d9df9ad4b6b67`，来自本地实验分支 `codex/nm37-a110-reset`；输出目录为 `nm37-m1-pipeline/apb110-20261020/build04`。报告和证据的后续提交不改变该执行身份。未 push。

完整重跑应从包含审计修复的本报告最终提交干净检出（至少包含 `25673838`），恢复固定依赖 gitlink 和锁定 shell，使用 `scripts/nm37-a110/run.sh`。`eeb1a1d0` 是本次硬件实现身份，其旧审计对优化后网表的假设会导致失败，不应直接作为新完整流程的入口版本。设置 `A110_OUT` 为**尚不存在**的绝对路径；原构建目录只读保留，不能覆盖后复用其通过标记。

```bash
export A110_OUT=/absolute/path/to/a-new-nm37-a110-build
python3 scripts/nm37-a110/prepare-shell.py
scripts/nm37-a110/run.sh check
scripts/nm37-a110/run.sh prepare
scripts/nm37-a110/run.sh units
scripts/nm37-a110/run.sh formal
scripts/nm37-a110/run.sh hbm-startup
scripts/nm37-a110/run.sh sim 4
scripts/nm37-a110/run.sh sim 8
scripts/nm37-a110/run.sh sim 16
scripts/nm37-a110/run.sh elaborate
scripts/nm37-a110/run.sh implement
# 首次遇到新增 CDC 时，物理门禁会停止。
# 按该新构建的 cdc-review.required.json 完成带证据的 cdc-review.json。
scripts/nm37-a110/run.sh physical
# 仅在该构建的物理门禁通过后：
scripts/nm37-a110/run.sh bitstream
```

本次已完成的硬件沿用 `build04`，只读复审及导出使用审计提交 `25673838` 的下列入口。各输出路径均须是新的绝对路径；报告生成后，必须对实际 `cdc-review.required.json` 中的差异逐项补充带证据摘要的 `cdc-review.json`，然后执行验收，不可复制另一个 DCP 的接受记录。

```bash
export A110_OUT=/absolute/path/to/original-build04
export A110_REAUDIT_OUT=/absolute/path/to/new-reaudit
scripts/nm37-a110/run.sh reaudit
# 按本次报告完成 cdc-review.json 后：
export A110_REAUDIT_ATTEMPT=cdc-reviewed
scripts/nm37-a110/run.sh reaudit-accept
export A110_REAUDIT_PASS="$A110_REAUDIT_OUT/acceptance/cdc-reviewed/reaudit.passed.json"
scripts/nm37-a110/run.sh reaudit-verify
export A110_BITSTREAM_OUT=/absolute/path/to/new-bitstream-export
scripts/nm37-a110/run.sh bitstream-from-reaudit
```

入口设置 `pePipeline=true`、`fanoutSplit=false`、`pipelineConservative=false` 和 `nm37MHz=110`；APB55 由板级 harness 声明。Java 20.0.2、firtool 1.75.0、Verilator 5.022、Vivado 2020.2_AR75986 等环境由本次 `logs/environment.json` 和工具记录确定，源码/依赖与阶段身份分别保存在 source-lock、`build.json` 和阶段封印中。正式入口明确设置 `CHISEL_FIRTOOL_PATH`，测试也落盘实际 inline 同步器实现，防止只记录 PATH 版本或遗漏 BlackBox 源码。

`build03` 的可复现性修复只有一个板级参数传播要点：`FixedClockBroadcast(Some(...))` 重建了默认 200 ps 的时钟 sink，覆盖先连接的板级 230 ps 预算，导致正确的 110/55 MHz IP 在生成阶段被拒绝。恢复普通广播后传递已有的 55 MHz / 230 ps 契约；控制器自身已声明 `take=55 MHz`，频率协商仍明确。**200 ps 是通用 diplomacy 默认值，不是 HBM 硬核的 APB 极限。** 该失败发生在 IP 生成阶段，build03 没有综合或布局布线结果，不用于本轮物理结论。

Clock Wizard 保留原输入噪声假设：`JITTER_OPTIONS=UI`、`CLKIN1_UI_JITTER=0.010`，在 100 MHz 下为 100 ps；XCI 虽同时存有 `CLKIN1_JITTER_PS=50`，但 PS 模式未启用，不能将其记成 50 ps 的有效输入假设。本轮未采用独立诊断中的低抖动模式，也没有为通过而减小输入噪声。PG276 的小于 3 ps RMS 要求针对 HBM reference clock 源，不能与 APB 输出的峰峰值直接比较。[PG276 Clocking](https://docs.amd.com/r/en-US/pg276-axi-hbm/Clocking)

[最终证据目录](evidence/nm37-a110-apb/)与[清单](evidence/nm37-a110-apb/inventory.json)保存所需原始报告、实际回归输入输出、日志、生成 IP 配置及独立复核。它是精选证据包，不是完整自包含构建：大 JAR、仿真器、DCP 和 bitstream 仅保留本机位置、大小及摘要。证据归档使用清单核对 Git 跟踪内容，避免 `.log` 受忽略规则影响而漏提交。
