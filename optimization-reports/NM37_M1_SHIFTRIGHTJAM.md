# NM37 M1：以 A 为对照的 ShiftRightJam 等价优化

> 主仓迁移后的复现入口、可随 clone 获取的证据与后续板测记录见 [NM37 A100 基线](NM37_A100_BASELINE.md)。本文的 `nm37-m1-*` 路径指外部冻结实验档案，普通 clone 不包含全部档案。

> 后续结果：[100 MHz一级流水验收](NM37_M1_PIPELINE.md)已完成并保留显式A100。本报告继续保存70 MHz ShiftRightJam A的原始测量。后续sbt构建自动清理了本实验的旧generator.jar；报告、RTL、仿真器、DCP及原证据归档经重新哈希核验保持完整，详见后续报告的基线证据说明。

本实验以 [M1 基线的 A 配置](NM37_M1_BASELINE_AB.md)（控制复制关闭）为对照，改变共享 `ShiftRightJam` 的组合实现，保持所有流水延迟不变。形式等价、六项功能回归及完整布局布线均已完成，决定**保留改写**：post-route LUT 减少 24,528（6.43%），DUT WNS 从 +0.832 ns 改善为 +0.878 ns，流水和执行周期不变。主要收益是面积，小幅时序改善不等于已实现明显提频。

## ShiftRightJam 的含义及本次改动

浮点加法/FMA 对阶时需要右移较小操作数的尾数。只保留移位后的整数会丢失被截掉的低位是否非零，而正确舍入需要这个信息。`sticky` 是所有被移出位的 OR：只要其中有一个 1，它就为 1。

传统 jam 操作把 sticky 合入结果最低位。本工程的接口将二者**分开返回**：`io.out` 为逻辑右移结果，`io.sticky` 为被移出位的 OR，后续 FMA 再使用 sticky。比如六位输入 `100101` 右移 3 位，返回 `out=000100, sticky=1`；不是直接把 `io.out` 改为 `000101`。

旧实现生成 `(1 << shamt) - 1` 掩码再与输入相与取 OR；新实现使用距离 1、2、4、8……的组合移位级，各级对实际移出的位取 OR 并累计 sticky。不新增寄存器、时钟、复位或状态，不改变调用接口、FMA 流水切点、PE/CMP 延迟和调度。

- 移位 0：out 等于输入，sticky=0。
- 移位量等于位宽：out=0，sticky 为全输入 OR。
- 移位量超过位宽：用完整移位量检测，out=0、sticky 为全输入 OR，避免仅取低位导致回绕。
- 恒零输入：任何移位量都返回 0 和 false。

本次源文件改动只有 `generators/easyfloat/src/main/scala/easyfloat/Utils.scala`。源码与生成物指纹见 [manifest.json](../nm37-m1-shiftjam/metadata/manifest.json)，源文件差异与编译缓存差异分列于 [source_delta.json](../nm37-m1-shiftjam/metadata/source_delta.json)。独立候选补丁见 [shiftjam-only.patch](../nm37-m1-shiftjam/metadata/shiftjam-only.patch)。

## 等价验证与功能回归

### 对实际生成 RTL 的形式等价证明

使用 Yosys SAT，将旧 A 和新 A **实际生成的** SystemVerilog 模块接入组合 miter，对 `out`、`sticky` 是否存在不同求解。`sat -verify -prove mismatch 0` 两次均成功，所有输入均不施加限制。

| 实际模块 | 数据位宽 | 移位量位宽 | 验证范围 | 结果 |
|---|---:|---:|---|---|
| ShiftRightJam | 74 | 11 | 全部 2^85 种输入组合 | 等价 |
| ShiftRightJam_16 | 53 | 9 | 全部 2^62 种输入组合 | 等价 |

这包含所有特殊移位边界、所有被截掉位模式和所有尾数输入，而非只测试随机样本。证明范围是当前 M1 实际使用的两种参数化实例，不宣称覆盖未来任意参数。两模块均无寄存器，周边生成 RTL 去除注释/空白后完全一致，支持本次替换的逐周期等价性。

证明脚本、原始 gold/gate RTL、miter、SAT 日志及工具版本在 `nm37-m1-shiftjam/formal/`，汇总见 [results.json](../nm37-m1-shiftjam/formal/results.json)。工具 Python 包精确版本记录在同目录 `requirements.txt`。

### 系统功能回归

同一份新生成器 JAR 独立构建三个 Verilator 仿真器，均使用 `-Dfsa.fanoutSplit=false`、断言开启；输入及指令与原 A 一致。

| 阵列 | seq_q=seq_kv | execTime 旧=新 | mxActive 旧=新 | mxBubble 旧=新 | dmaActive 旧=新 |
|---|---:|---:|---:|---:|---:|
| 4×4 | 4 | 368 | 143 | 165 | 16 |
| 4×4 | 8 | 1083 | 388 | 694 | 48 |
| 8×8 | 8 | 381 | 179 | 154 | 40 |
| 8×8 | 16 | 1096 | 524 | 619 | 116 |
| 16×16 | 16 | 420 | 249 | 126 | 76 |
| 16×16 | 32 | 1135 | 754 | 438 | 206 |

六次新仿真均与 PyEasyFloat 及原 A 输出逐位一致，全部性能计数器一致。旧/新 `inst.bin`、`mem.elf` 也逐字节一致。输入为 seed 0 正值随机 FP16 矩阵；特殊位模式由上述模块级形式证明覆盖。仍沿用基线的多端口 RTL 仿真配置，不把周期数当作 NM37 板上性能。

完整验收记录见 [equivalence-and-regression-passed.json](../nm37-m1-shiftjam/A/equivalence-and-regression-passed.json)。物理实现脚本会重新校验该验证条件及源码/JAR 指纹，验证失败时不会继续布局布线。

## 物理实现控制条件

- 对照：`nm37-m1-baseline/A`；候选：`nm37-m1-shiftjam/A`。旧 A 的 DCP/报告保持原样，不覆盖。
- 两者均关闭控制复制，不改变该开关的源码默认值。
- 同器件 `xcvu37p_CIV-fsvh2892-2L-e`、同 `EmptyNM37Config`、同冻结 shell/XDC、同 DUT 70 MHz（14.286 ns）约束。
- 同 Vivado 2020.2 AR75986 build 3064766、4 线程，以及原 A 的综合、opt、place、phys_opt、route 策略。
- 候选重新生成 RTL/IP，独立综合及完整布局布线，不读取旧 DCP，不增加/修复其他约束以维持单变量比较。
- 比较 DUT setup/hold、全设计 setup/hold、资源和实际关键路径；不以全设计 WNS 替代 FSA 域 WNS，不把 70 MHz 的裕量换算成实测最大频率。

## 综合阶段观察

两组顶层综合 retiming 均为 forward=0、backward=0。候选综合后总 LUT 为 366,373，对照为 388,144，减少 21,771（约 5.61%）；FF 为 72,862，对照为 72,985。两组 DSP 均为 288、RAMB36 均为 52。该数字是 post-synth 口径，不能与 post-route 表格混用。

## Post-route 测量及保留决定

预先采用以提频为主的判断口径：等价与回归必须通过，受约束 setup/hold 和布线必须通过；优先要求 DUT setup 裕量优于旧 A，并结合 LUT/FF/DSP/存储资源决定是否值得保留。若 DUT 时序退化，不因代码更短或综合估计更好而直接保留。单次实现收益不表述为多次布局的统计保证。

### 同口径结果

以下均为 70 MHz、14.286 ns 约束下的 post-route 数据。

| 指标 | 原 A | A + ShiftRightJam 改写 | 变化 |
|---|---:|---:|---:|
| DUT setup WNS | +0.832 ns | +0.878 ns | +0.046 ns |
| DUT hold WHS | +0.010 ns | +0.010 ns | 0 |
| 全设计 setup WNS | +0.024 ns | +0.036 ns | +0.012 ns |
| 全设计 hold WHS | +0.010 ns | +0.010 ns | 0 |
| 全设计 TNS / THS | 0 / 0 | 0 / 0 | 无违例 |
| 完成布线 / 可布线网络 | 477,778 / 477,778 | 469,234 / 469,234 | 全部完成 |
| 布线错误 | 0 | 0 | 不变 |
| 总线偏斜最差裕量 | +2.982 ns | +2.787 ns | 两组 14 项约束均通过 |
| Total LUT | 381,532 | 357,004 | −24,528（−6.43%） |
| Logic LUT | 375,626 | 351,114 | −24,512 |
| LUTRAM | 4,958 | 4,958 | 0 |
| SRL | 948 | 932 | −16 |
| FF | 71,461 | 71,324 | −137 |
| RAMB36 / RAMB18 / URAM | 52 / 0 / 0 | 52 / 0 / 0 | 不变 |
| DSP | 288 | 288 | 0 |

综合重建层次发生变化：旧结果保留 288 个独立 `shiftRightJam` 层次，新结果不再保留这些独立层次。因此不能把新层次统计的 0 项解释为硬件成本归零，也不能直接用子层次 LUT 相减评估优化；以上采用全设计资源口径。

### 实际关键路径

DUT 最差路径仍为 PE 运算反馈路径：

| 各自实现的最差路径 | 原 A | 候选 |
|---|---|---|
| 起点（省略 dutDomain/fsa/fsa） | sa/mesh_6_8/reg_exp_reg[1] | sa/mesh_13_15/reg_exp_reg[1] |
| 终点 | sa/pipe_b_790_exp_reg[1] | sa/pipe_b_1021_exp_reg[1] |
| 数据延迟 | 13.214 ns | 13.143 ns |
| 逻辑延迟 | 4.474 ns | 3.848 ns |
| 布线延迟 | 8.740 ns | 9.295 ns |
| 逻辑级数 | 50 | 43 |
| 路径 CARRY8 数 | 14 | 11 |

比较的是两个实现各自的最差路径，端点不同，不是固定 PE 的同一路径对照。这里观察到逻辑延迟减少 0.626 ns、布线延迟增加 0.555 ns；连线抵消了大部分逻辑收益，加上时钟偏斜差异，最终 setup 裕量仅改善 46 ps。不能把逻辑级数减少直接换算为同等幅度的提频。

按每个终点最多一条路径统计 DUT top100：

| 路径族 | 原 A 条数 / 最差 slack | 候选条数 / 最差 slack |
|---|---|---|
| PE | 71 / +0.832 ns | 89 / +0.878 ns |
| scratchpad → PE | 16 / +0.956 ns | 7 / +1.102 ns |
| CMP | 13 / +1.067 ns | 4 / +1.115 ns |

上述采样不代表全部路径占比。PE 仍是首要瓶颈，CMP 与 scratchpad 路径仍接近，不支持只靠本次局部组合改写达到 100 MHz。

### DRC、DSP 映射和验收边界

- DRC 共 1,114 项 Warning/Advisory，无 Error；对照为 1,130 项。DPIP-2 从 544 降为 528，其他规则计数不变，不能表述为 DRC clean。
- DSP 的 AREG/BREG/CREG/MREG/PREG：256 个仍为 0/0/1/0/0，16 个仍为 0/1/1/0/0；另 16 个从 0/0/1/0/1 变为 0/1/1/0/1。全部 MREG 仍为 0。属性反映工具映射，不表示源码新增一拍；本次未新增流水寄存器，形式证明和系统周期回归保持一致。
- CDC 统计与原 A 相同：CDC-7=12、CDC-10=2、CDC-11=16 个 Critical，CDC-8=1、CDC-15=170 个 Warning，仍需逐项审查。
- `prst_n` 缺少 input_delay、未使用 HBM 端口的 30 个未约束常量端点以及 accRAM 地址 MAX_FANOUT 选择器失效，仍按基线记录保留；本次没有修改约束来获取时序收益。
- 尚未进行板上验证，亦未在 100 MHz 约束下实现。70 MHz 单次布局的小幅裕量改善不能当作可重复的最大频率增益。

### 保留决定与后续基线

**保留本次改写，以 A + ShiftRightJam 作为下一次优化的对照。** 两个实际参数实例全输入等价，六项功能回归逐位且逐周期一致；布线完整、受约束 setup/hold 与补充总线偏斜检查通过；LUT 减少 6.43%，本次未观察到 DUT 时序退化。46 ps 的增益较小，不能据单次布局证明稳定的提频幅度，保留的主要依据是面积收益及无新增流水代价。

候选 Vivado 和外层脚本均以 0 退出，结束于 2026-10-13 10:55:57 UTC（北京时间 18:55:57）。独立打开旧/新 `post_route.dcp` 的两个补充检查进程也均以 0 退出。14 项总线偏斜约束逐项通过，结果见 [bus_skew_comparison.json](../nm37-m1-shiftjam/metadata/bus_skew_comparison.json)。

最差路径仍在 PE，下一步应围绕 PE 内部运算链的流水切点设计，并同时校核 CMP、scratchpad 读边界和控制波前；不建议把本次 46 ps 收益作为继续只改 sticky 逻辑就能达到 100 MHz 的依据。本次未开始这些后续改动。

机器可读比较见 [physical_comparison.json](../nm37-m1-shiftjam/metadata/physical_comparison.json)，最终保留记录见 [decision.json](../nm37-m1-shiftjam/metadata/decision.json)。原 A 保持只读对照；本次的源码快照、单独补丁、形式证明、回归输出和新旧物理报告已归档到 [证据包](../nm37-m1-shiftjam/NM37_M1_SHIFTRIGHTJAM_evidence.tar.gz)，并提供 [SHA-256](../nm37-m1-shiftjam/NM37_M1_SHIFTRIGHTJAM_evidence.sha256)。大型 JAR、仿真器和 DCP 保留在本地运行目录，证据包记录其相应指纹，未将这些大文件全部打包。

## 复现

新工作区需先恢复已记录的 M1 A 基线，再应用独立 `shiftjam-only.patch`；实验目录仅复制 scripts 和 formal/requirements.txt，不能复制已有运行目录/JAR/metadata。工具路径按本机 `environment.sh` 配置，MacroCompiler/tapeout 等工程依赖需预先就绪。

```bash
bash nm37-m1-shiftjam/scripts/prepare.sh
bash nm37-m1-shiftjam/scripts/run_variant.sh A elaborate
python3 nm37-m1-shiftjam/scripts/verify_equivalence.py
for size in 4 8 16; do
  bash nm37-m1-shiftjam/scripts/run_sim.sh A "$size"
done
python3 nm37-m1-shiftjam/scripts/check_verification_gate.py
bash nm37-m1-shiftjam/scripts/run_variant.sh A implement
python3 nm37-m1-shiftjam/scripts/compare_physical.py
```

形式验证先按 `formal/requirements.txt` 在 `formal-env` 安装工具。比较脚本还需只读访问原基线 A 的 RTL、仿真结果及 summary；原始证据不得用候选覆盖。现有本机目录已经完成相应阶段，不能直接重复执行创建阶段。
