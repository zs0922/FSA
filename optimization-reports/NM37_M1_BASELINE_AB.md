# NM37 当前 M1 基线与控制复制 A/B 对比

> 主仓迁移后的复现入口、可随 clone 获取的证据与后续板测记录见 [NM37 A100 基线](NM37_A100_BASELINE.md)。本文的 `nm37-m1-*` 路径指外部冻结实验档案，普通 clone 不包含全部档案。

> **后续实验（2026-10-13）：** 以本文 A 为对照的 [ShiftRightJam 等价优化](NM37_M1_SHIFTRIGHTJAM.md) 已完成并保留：LUT −6.43%，DUT WNS +0.878 ns，流水及回归周期不变。本文继续保留优化前 A/B 的原始测量，后续优化使用 A + ShiftRightJam 作为对照。

本基线针对当前工作区的 NM37 16×16 FSA，保留 M1 调度、canEnq 修复、累加器数据通路和原有约束。A 关闭控制复制，B 开启控制复制；只比较这一开关的效果，A **不是历史 P3 的重建**。

2026-10-13 已获得两组独立 post-route 结果：在 DUT 70 MHz 约束下，A 的 DUT WNS 为 **+0.832 ns**，B 为 **+0.390 ns**；12 个 RTL 仿真用例全部逐位通过且 A/B 周期相同。B 的手工控制副本未按预期保留，未证明提频收益。后续优先处理 PE、CMP 和 scratchpad 读数据路径。

这是一份可追溯的实现和功能基线，不是整板签核证书：`prst_n` 输入约束及 CDC 审查尚有遗留项，未进行板上验证，也未验证 100 MHz。

## 实验边界与可追溯性

- 器件：`xcvu37p_CIV-fsvh2892-2L-e`；配置：`EmptyNM37Config`。
- DUT 目标频率：70 MHz；物理实现周期以 generated-clock 报告为准。
- Vivado：2020.2 AR75986，build 3064766；线程数固定为 4。
- 同一份新编译的 `generator.jar` 生成 A/B；elaboration 参数分别为 `-Dfsa.fanoutSplit=false/true`，默认仍为 true。
- 复制开关覆盖 timer banks 和 per-lane accumulator control Pipe；不改变流水深度或算术语义。
- 两组独立生成 RTL、IP、综合及实现检查点；未加载旧 DCP，未使用旧 RTL、旧 bitstream 或历史 P2/P3 报告作为新测量。
- 两组使用相同的冻结 shell/XDC。综合为 `PerformanceOptimized -retiming`，opt 为 `Explore`，place 为 `ExtraTimingOpt`，place 后与 route 后均执行 `AlternateFlowWithRetiming`，route 为 `Explore`；保留原有 `power_opt_design`。
- 本实验止于 post-route 和报告，不进行板卡烧写；功能结果来自 RTL 仿真，不能当作 HBM/PCIe 板上吞吐测量。

源码及补丁快照、Git 提交、源码哈希、JAR 哈希见 [manifest.json](../nm37-m1-baseline/metadata/manifest.json)，工具版本见同目录。完整本地快照为 `nm37-m1-baseline/metadata/sources.tar.gz`。

生成的 Verilog 仅以下两个模块不同，其余模块一致，见 [RTL 对比](../nm37-m1-baseline/metadata/rtl_comparison.json)：

1. `MatrixControlFSM.sv`
2. `MatrixEngineController.sv`

## 功能及周期对比

使用新构建的 Verilator 5.022 仿真器，断言开启；输入为 NumPy seed 0 的 FP16 随机矩阵。每个尺寸覆盖一个 tile 和多个 tile，分别验证输出 uint32 位模式与 PyEasyFloat 逐位相同，并比较 A/B 全部 FSA 性能计数器。

| 阵列 | seq_q = seq_kv | execTime A = B | mxActive A = B | mxBubble A = B | dmaActive A = B | 输出验证 |
|---|---:|---:|---:|---:|---:|---|
| 4×4 | 4 | 368 | 143 | 165 | 16 | A/B/参考逐位一致 |
| 4×4 | 8 | 1083 | 388 | 694 | 48 | A/B/参考逐位一致 |
| 8×8 | 8 | 381 | 179 | 154 | 40 | A/B/参考逐位一致 |
| 8×8 | 16 | 1096 | 524 | 619 | 116 | A/B/参考逐位一致 |
| 16×16 | 16 | 420 | 249 | 126 | 76 | A/B/参考逐位一致 |
| 16×16 | 32 | 1135 | 754 | 438 | 206 | A/B/参考逐位一致 |

共 12 次实际仿真。结果、全部计数器及输出哈希见 [summary.json](../nm37-m1-baseline/summary.json)。本地每个 `A/B/sim*/seq*/` 保留 `actual.npy`、`expected.npy`、`result.json` 和运行日志。

仿真使用 `AXI4FSA4X4/8X8/16X16Fp16Config`，内存拓扑不同于单端口 NM37 板级系统。上述验证支持本次控制复制改动的功能等价和周期中性，不是全输入形式证明或板上性能证明。尤其不能将仿真的 420 cycles 除以 70 MHz 后宣称为实测板上延迟。

历史文档中的 4×4 `mxBubble=162` 未在本次复现；本次 A/B 同为 **165**，以新记录为准。

## 已确认的约束和工具问题

### accRAM 地址 MAX_FANOUT 未命中

冻结的 `nm37-config.xdc` 用 `*sram_accRAM_fullWrite_0_addr_REG*` 查找寄存器。两组综合均出现：

```text
WARNING: [Vivado 12-180] No cells matched ...
CRITICAL WARNING: [Common 17-55] 'set_property' expects at least one object.
```

因此不能声称 M1 已有效约束该写地址的扇出。本次保持原约束以完成单变量对比；修复匹配对象应作为后续独立实验。该约束是优化属性，不是时钟周期或路径例外，失效本身不意味着全部时序分析无效。

### 自动 retiming 的实际作用

两组综合日志均报告顶层 `forward move=0, backward move=0`。工具选项已启用，但不能据此宣称流水得到自动切分。

### 手工控制复制没有按预期保留

直接打开两组 `post_synth.dcp` 后导出的寄存器清单确认：

| 综合后项目 | A 关闭复制 | B 开启复制 |
|---|---:|---:|
| mxControl 的 FD 类寄存器 | 697 | 697 |
| compute timer 位数，两个 FSM 合计 | 12 | 12 |
| accum timer 位数，两个 FSM 合计 | 14 | 14 |
| 控制命令寄存器位数，含工具自动副本 | 42 | 42 |
| 控制 valid 寄存器位数 | 1 | 1 |
| 上述 mxControl 寄存器的非空 DONT_TOUCH 属性数 | 0 | 0 |

B 的 compute timer 每个 FSM 只保留 `computeTimerBank_0` 六位；accum timer 每个 FSM 只保留 `accumTimerBank_1` 七位。并未保留源码预期的 7 组 compute timer 和 8 组 accum timer。控制命令仍为 `rep_pipe_b_cmd_reg[*]` 及工具生成的 `_rep*`，没有形成源码所描述的 16 套独立 lane Pipe。

结论：Chisel `dontTouch` 在当前链路中没有生成对应的 Vivado 保留属性；手工等价副本被合并，随后工具按自身策略复制驱动。不能把 B 的任何小幅时序差异直接解释为预期控制分发结构生效。结构统计见 [synth_structure.json](../nm37-m1-baseline/metadata/synth_structure.json)，原始清单在各组 `obj/audit/synth_structure/control_registers.tsv`。

### 构建环境

首次仿真链接因 Conda 链接器与现有 `libriscv.so` 的 GLIBC 版本不兼容而失败。改用 `/usr/bin/g++`、`/usr/bin/gcc` 后成功；失败日志保留，最终脚本固定使用系统编译器。未改动 DUT RTL 以规避该问题。

### 外层脚本收尾错误及独立复核

等待 Vivado 期间修改了外层 `run_variant.sh`，导致运行中的 Bash 在返回后读取脚本尾部时出现引号解析错误。A/B 外层驱动均以 2 退出，原始 `run.txt` 未记录结束时间；不能将它们写成完整脚本成功退出。Vivado 日志已正常执行至 `exit`，但其进程退出码未单独捕获，记录为未知。

两组最终报告均完整存在；A 的 477,778 条、B 的 477,809 条可布线网络全部完成布线，routing errors 均为 0。另起 Vivado 进程独立打开各自 `post_route.dcp`、重新导出结构和时序报告，两次均以 0 退出。源码指纹再次校验通过，受约束 setup/hold 均通过。上述证据支持使用已完成的实现结果，无需用旧报告补齐。

现已将驱动改为启动时读取整份脚本快照，避免运行中编辑影响执行；已有目录的拒绝覆盖检查也已验证。保留原始驱动日志和未补写的 `run.txt`，复核记录见 [completion_validation.json](../nm37-m1-baseline/metadata/completion_validation.json)。Vivado 日志结束时间分别为北京时间 2026-10-13 15:46:08（A）、15:45:46（B）。

## Post-route 时序与资源

| 指标 | A 关闭控制复制 | B 开启控制复制 |
|---|---:|---:|
| DUT 周期 | 14.286 ns | 14.286 ns |
| DUT setup WNS | **+0.832 ns** | **+0.390 ns** |
| DUT hold WHS | +0.010 ns | +0.011 ns |
| 全设计 setup WNS | +0.024 ns | +0.079 ns |
| 全设计 hold WHS | +0.010 ns | +0.010 ns |
| 全设计 TNS / THS | 0 / 0 | 0 / 0 |
| setup / hold 失败端点 | 0 / 0 | 0 / 0 |
| Total LUTs | 381,532 | 378,437 |
| Logic LUTs | 375,626 | 372,531 |
| LUTRAM / SRL | 4,958 / 948 | 4,958 / 948 |
| FF | 71,461 | 71,487 |
| RAMB36 / RAMB18 / URAM | 52 / 0 / 0 | 52 / 0 / 0 |
| DSP | 288 | 288 |
| mxControl FF | 697 | 697 |

全设计最差 setup 路径在 **PCIe 内部 500 MHz TXOUTCLK 域**，不是 FSA 域。若只比较全设计 WNS，会误以为 B 更好；FSA 域实际上是 B 比 A 少 **0.442 ns** 裕量。B 的 LUT 少 3,095 个、FF 多 26 个，是本次实现结果，不能归因于预期的手工复制结构成功。

本次只有一组受控 A/B，实现之间的布局差异不能当作稳定统计收益。建议以后续 **A 配置作为提频实验对照**，同时保留 B 作为默认 M1 的测量记录。源码默认开关仍为 true，选择 A 使用 elaboration 参数，不需要再次手工改源码。

### 关键路径分布

每组按 DUT 的 100 条最差 setup 路径统计，`nworst=1`，即每个终点最多一条；按实际经过的 CMP/PE 层次分类，不把 100 个采样终点当成全部路径占比。

| 路径族 | A 条数 | A 最差 slack | B 条数 | B 最差 slack |
|---|---:|---:|---:|---:|
| PE 内部 | 71 | +0.832 ns | 33 | +0.519 ns |
| scratchpad → PE | 16 | +0.956 ns | 53 | +0.460 ns |
| CMP | 13 | +1.067 ns | 14 | +0.390 ns |

- A 最差：`sa/mesh_6_8/reg_exp_reg[1] → sa/pipe_b_790_exp_reg[1]`；13.214 ns = logic 4.474 + route 8.740 ns，50 级逻辑。
- B 最差：`sa/pipe_b_3_cmd_reg[2] → sa/cmp_out_pipe_b_4_mantissa_reg[12]`，经过 `cmp_array_4/cmpUnit/fma/rawFMA`；13.465 ns = logic 4.415 + route 9.050 ns，56 级逻辑。
- B 的 scratchpad 最差路径：读地址寄存器经 `RAMD64E` 和 PE 到 `pipe_b_552_mantissa_reg[10]`；13.552 ns = logic 4.211 + route 9.341 ns，44 级逻辑。

两组 top100 中没有旧报告的 `mxControl → accumulator` 路径。A 最差 PE 和 B 最差 CMP 均经过 `ShiftRightJam` 相关逻辑；这是共享算术优化实验的实际依据，但不能把整条路径延迟都归因于 sticky mask。

路径统计见 [path_families.json](../nm37-m1-baseline/metadata/path_families.json)，详细路径在各组 `obj/audit/dut_setup_top100.txt`。

### 实际存储映射与扇出

当前 `spRAM_sram` 两组均为 **1,184 LUTRAM、0 BRAM**，不是旧文档中的 8 个 BRAM。`InputDelayer` 的 row0 和 `delay=false` 模式存在直通路径，scratchpad 读数据可能直接经过输入选择和 PE 算术后才到寄存器。

B 的 accRAM 写地址实际名称为 `sram_accRAM_fullWrite_0_addr_r_3_reg[0..4]`，五个寄存器的 `MAX_FANOUT` 属性均为空；旧 `_addr_REG*` 选择器已失效。写地址扇出为 1216～1218，但 slack 为 8.632～11.258 ns。

B 的 accumulator 控制译码网络仍有 1082～1115 扇出，不过 slack 为 7.103～7.515 ns。它们在本次 70 MHz 实现中不是首要瓶颈，不应仅按扇出大小排列提频优先级。

### DSP 配置与 DRC

两组 DSP 的 AREG/BREG/CREG/MREG/PREG 配置分布相同：

| 配置 | 数量 |
|---|---:|
| 0 / 0 / 1 / 0 / 0 | 256 |
| 0 / 1 / 1 / 0 / 0 | 16 |
| 0 / 0 / 1 / 0 / 1 | 16 |

配置属性不表示每个端口都参与有效算术，例如 CREG 可能对应常量输入；应结合实际连接理解。全部 DSP 的 MREG=0。两组默认 DRC 均为 1,130 项 Warning/Advisory，无 Error；其中 DPIP-2=544、DPOP-3=272、DPOP-4=288，不能写成“DRC 已清零”。

### 约束覆盖和 CDC 的边界

- 无 no_clock、无组合环。
- 30 个 constant-clock 未约束端点全部是未使用的 HBM AXI_01～AXI_15 的 BREADY/RREADY，每端口两个；**不是 debug 端点**。报告中的其他未约束最大延迟 pin 数为 0。
- `prst_n` 是唯一缺少 input_delay 且没有 false-path 声明的输入，应按 PCIe 复位同步结构和 IP 约束审查，不能随意填一个延迟数值。
- 两组 CDC 均有 30 项 Critical：CDC-7=12、CDC-10=2、CDC-11=16；另有 CDC-8=1、CDC-15=170 个 Warning。现阶段未逐项证明其为有效 IP 内部结构或完成 waiver，因此不宣称 CDC clean。
- 这些项目来自实际新报告，详细对象见 `obj/audit/check_timing.txt`、`cdc.txt`、`clock_interaction.txt`、`exceptions.txt`。

验收区分：DUT setup/hold、全设计 setup/hold、未约束路径、CDC、布线完成状态。WNS/WHS 必须非负才称为该档时序通过；不沿用旧脚本允许 WNS ≥ -0.100 ns 的宽松退出阈值。

70 MHz 单点结果不是最大频率测量。`1000/(period-WNS)` 只能作为同一时钟域、同一实现的静态参考值，不能替代目标频率下的重新布局布线。

## 下一步优化决策

### 决定

**下一项代码实验：对共享 `ShiftRightJam` 做不增加拍数的等价重写；100 MHz 主线：PE/CMP 算术流水与 scratchpad 读边界一起规划。暂停把全局控制复制、accRAM 迁 URAM、整阵列硬 pblock 当作首要提频手段。**

| 顺序 | 工作与具体位置 | 验收依据 |
|---|---|---|
| 0 | 修正失效 XDC 的对象匹配，添加命中数量断言和属性读回；单独审查 prst_n 与 CDC。保持独立变更记录 | 对象存在且属性生效；签核遗留项有逐项结论。该项不预设提频收益 |
| 1 | `easyfloat/Utils.scala`：把 `(1 << shamt) - 1` 掩码实现改为分级右移时累计被移出位的 OR，保持输入输出和延迟 | 覆盖各实际位宽、shift=0、等于/超过位宽、随机数据；与原逻辑逐位等价；12 个回归用例及计数器不变；用独立 post-route 报告决定保留或回退 |
| 2 | `BankedSRAM.scala` 和内存生成模板：试验明确的同步读 BRAM 推断；核对现有一拍读语义、读写同址行为。若加入额外输出寄存，则同步处理 bank/subbank 索引、valid 和 `InputDelayer` 控制 | scratchpad→PE 不再把存储读出与完整 MAC 串在同一长段；实际 RAM 类型及输出寄存从网表确认 |
| 3 | `FMA.scala`、`Exp2.scala`、`FPArithmeticImpl.scala`、`PE.scala`：参数化 PE 运算延迟，按对阶/乘法、加减、归一化/舍入的实测延迟选流水切点 | II=1；MAC 与 EXP2 的命令、分段索引、命中标志、valid、旁路和驻留寄存器写回全部对齐；不能直接把 accumulator 的三级 `pipeline=true` 塞进 PE |
| 4 | `FPCmpUnit` 与 `CMP.scala`：把每拍 newMax 更新的比较反馈与 oldMax−newMax 的差值输出分开设计，必要时只对差值路径流水；同时评估专用浮点减法替代通用 `a*1+b` 实现 | 保持 max 更新吞吐；±0、NaN/Inf、相等数及舍入规则与旧实现匹配；CMP 的新延迟纳入 score 各阶段 |
| 5 | `SystolicArray.scala`、`ControlGen.scala`、`ExecutionPlan.scala`：统一登记 SRAM/PE/CMP/上下行及旁路延迟，按生产和消费时间重新推导波前、oldMax/newMax/EXP2/RowSum、conflictFree 和信号量释放 | 4/8/16 阵列、多 tile 逐位回归；不能仅修改当前的 `hopUD` 常量或把所有锚点统一加一 |

第 2～5 项是同一个提频里程碑的联动设计，不表示每一项单独完成即可达到 100 MHz。B 的 CMP 与 scratchpad 最差 slack 只差 0.070 ns，只改 CMP 会很快转移瓶颈；A 中 PE 本身也已是最差路径。

100 MHz 仅作为下一里程碑的**目标**：需在实际 10 ns 约束下重新综合、布局布线，验证 DUT 和其他时钟域 setup/hold，并处理约束/CDC 遗留项，再开展板测。对每个工作负载同时记录 cycles 与时间；流水增加的周期数必须与频率收益一起核算。当前结果不支持宣称已达到 100 MHz，更不支持直接承诺 300 MHz。

本次止于基线重建、A/B 比较和方案决定，未实施上述算术或存储优化。

## 复现

在具备依赖的全新工作区应用当前 u280、nm37、m1 补丁，确认源码与快照对应后，创建仅包含本次 scripts 的实验目录（不要复制已有 metadata、JAR 或运行目录）。仿真还需要工程的 MacroCompiler/tapeout JAR 等依赖缓存就绪：

```bash
bash nm37-m1-baseline/scripts/prepare.sh
bash nm37-m1-baseline/scripts/run_variant.sh A
bash nm37-m1-baseline/scripts/run_variant.sh B
for variant in A B; do
  for size in 4 8 16; do
    bash nm37-m1-baseline/scripts/run_sim.sh "$variant" "$size"
  done
done
python3 nm37-m1-baseline/scripts/summarize.py
```

`prepare.sh` 拒绝覆盖已有基线；`run_variant.sh` 校验源码/JAR 指纹，并拒绝复用已有实现目录。环境脚本中工具安装位置为本机路径，异机需配置相同版本。当前运行的目录已经存在，不应直接重放覆盖。

## 历史数据纠正

历史 `nm37-stage-results/p2` 与 `p3` 的 timing、utilization、fanout、DRC 报告除日期外完全相同，不能用来证明 P3 的独立实现效果。历史 P3 全设计 WNS 为 0.021 ns，而 DUT 域为 0.054 ns。当前基线使用全新的源码指纹和独立实现结果，不延续上述混用口径。
