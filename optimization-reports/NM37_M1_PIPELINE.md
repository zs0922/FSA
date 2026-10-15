# NM37 M1：PE/CMP 与 scratchpad 入口流水候选

> 主仓迁移后的复现入口、可随 clone 获取的证据与后续板测记录见 [NM37 A100 基线](NM37_A100_BASELINE.md)。本文的 `nm37-m1-*` 路径指外部冻结实验档案，普通 clone 不包含全部档案。

本实验以 [A + ShiftRightJam](NM37_M1_SHIFTRIGHTJAM.md) 为测量对照，在 PE 运算链增加一级寄存器，同时调整 CMP 和 scratchpad 入口及逐拍调度。**三个模块级形式证明、单元与调度检查、五项相关控制测试和六项紧凑候选系统回归均已通过；完整布局布线已完成，实际 DUT 10 ns约束下setup / hold为+0.534 / +0.010 ns。** 主验收已完成，保留显式A100配置；源码默认仍选择旧实现，运行候选须显式启用。

候选证据在 `nm37-m1-pipeline/`；物理候选在 `A100/`，系统候选在 `A/`。当前生成器 SHA-256 为 `55d04ff753195966be9d0cec4eac68cc61c9e5907a3354814e4fa321a21794d4`。源码、生成器和冻结 shell 指纹见 [manifest.json](../nm37-m1-pipeline/metadata/manifest.json)，修正验证基础设施后的源码快照见 [sources-infrastructure-fixed.tar.gz](../nm37-m1-pipeline/metadata/sources-infrastructure-fixed.tar.gz)，初始快照也保留。实施目标和验收顺序见 [流水计划](NM37_M1_PIPELINE_PLAN.md)。

## 实现与精确延迟契约

`RawFloat_FMA.afterAdd` 在宽加减及绝对值选择之后、归一化和舍入之前建立一级寄存器，保存完整中间精度、符号、指数、sticky 和特殊值信息。EXP2 的整数部分和命中标志同步延迟。原 accumulator 的三拍实现继续使用既有切点。

PE 将纵向算术结果和旁路统一延迟，横向控制和数据仍即时输出后经既有连接寄存器传播。驻留更新的输入后下一拍产生结果，再在提交边沿写回，最早依赖运算是输入后两拍；没有跨流水级组合反馈。EXP2 扫描始终使用扫描开始时的驻留输入，保留命中值，八个有效 token 全部排空后提交；允许有效 token 之间存在气泡。

CMP 的最大值反馈保持每拍更新。候选快速比较匹配旧减法舍入结果的符号选择行为，包含 FTZ、NaN、无穷、正负零和相等值；差值、score 旁路、零值和截距的纵向输出统一增加一级。CMP 横向控制保持原时序。

| 接口 / 路径 | 旧配置 | 流水候选 | 调度含义 |
|---|---:|---:|---|
| `peLatency`，PE 内部结果延迟 | 0 | 1 | 算术与纵向旁路一致 |
| PE 纵向总 hop `d=1+peLatency` | 1 | 2 | 包含既有阵列 link 寄存器 |
| PE 横向数据 / 控制总 hop | 1 | 1 | 每列仍增加一拍 |
| `cmpLatency`，CMP 内部纵向输出延迟 | 0 | 1 | 最大值反馈不等待该流水 |
| CMP 至首行 `Dc=1+cmpLatency` | 1 | 2 | 包含既有 CMP→PE link |
| `spadLatency=Ls`，读请求至首入口 | 1 | 2 | 新数据寄存器位于 InputDelayer 之后 |
| InputDelayer 行错位 | `r` | `2r` | 可反转以服务上行 K 波前 |
| OutputDelayer 列对齐错位 | 每列一拍 | 每列一拍 | 与横向 hop 保持一致 |
| accumulator FMA 延迟 | 3 | 3 | 读 / 写回地址按原三拍对齐 |
| accRAM 同步读延迟 | 1 | 1 | ACC 指令晚于对应读一拍 |
| 驻留运算及 EXP2 尾 token 至最早依赖 | 1 | 2 | 候选等待完整提交边沿 |

`SystolicArray` 的原连接寄存器保持一拍；候选的纵向两拍来自新增内部流水与既有 link 相加，不能再次把 link 改成两拍。`ControlGen.FlowRange` 支持行步长和任意行偏移，并允许部分行不参与。非单位步长或显式偏移采用精确生成，旧单位对角线保留既有优化。

## 逐拍调度与重叠边界

本节周期均为执行计划周期，**不额外计入 MatrixEngineController 公共输出 Pipe**。实际 SP、ACC、PE、CMP 和 semaphore 事件共同晚一拍。行号 `r` 自上而下，列号 `c` 自左而右，表中 PE 事件还需加列偏移 `c`。

令阵列为 `R×C`，CMP 首个 score 更新为：

```text
U = Ls + R*d
Tload(r) = U + R - 1 + Dc + r*(d-1)
```

CMP 连续每拍接受一个 score，row r 保留 beat `R-1-r`，因此装载斜率是 `d-1`，后续纵向运算斜率才是 `d`。这也是不能仅把旧 `hopUD` 设为 2 或统一倍乘旧调度常数的原因。

16×16、八段 EXP2、accumulator 三拍时：

| 事件 | 紧凑候选 | 保守对照 |
|---|---|---|
| SP K 读请求 | 0..15 | 0..15 |
| CMP score UPDATE | 34..49 | 34..49 |
| 各行 score 装载 | `51+r`，51..66 | `51+r`，51..66 |
| 自下而上的 zeroFlow | `18+2*(15-r)` | `50+2*(15-r)` |
| 减最大值 | `52+2r` | `69+2r` |
| 缩放驻留值 | `54+2r` | `101+2r` |
| EXP2 首 / 尾 token | `56+2r` / `63+2r` | `133+2r` / `140+2r` |
| exp 求和 MAC | `65+2r` | `172+2r` |
| accumulator EXP_S1 / EXP_S2 | 101 / 105 | 148 / 152 |
| 旧 row-sum 读 / ACC_SA | 111 / 112 | 218 / 219 |
| Score accumulator 完成 | 115 | 222 |
| Score conflictFree | 64 | 222 |
| Value 首个 accRAM 读 / ACC_SA | 48 / 49 | 48 / 49 |
| Value accumulator 完成 | 67 | 67 |
| Value conflictFree | 45 | 67 |

紧凑调度沿行波前等待两拍提交，保守模式等待整阵列前一阶段完成并关闭双 FSM 重叠。每行 score 只转发下游仍需要的 beat。紧凑 zeroFlow 在 bottom 最后一个 Score MAC 后一拍开始：每行 zero 为 `48-2r`，紧随该行最后 Score MAC `47-2r`，CMP 在 UPDATE49 后才收到零50；缩放读取的 `d_input` 已清零。

EHR 可在 conflictFree 标记的同拍接收下一指令，审查不假定额外气泡：

- Score64 可接收 Value，Value timer0 最早65，首 MAC67+2r+c 晚于求和65+2r+c 两拍；Score accRAM 读111 / 命令112与 Value 读113 / 命令114不重叠。EXP_S2=105 的驻留 scale 最早109可读，早于两个 ACC_SA。
- Value45 可接收 Load，Load timer0 最早46，首 load48+c 晚于 Value bottom 最后 MAC47+c 一拍；同列控制都增加 c，后继波前不会赶超。
- Load15 可接收 Score，Score timer0 最早16，首 MAC18+2*(15-r)+c；旧 Load 最后装载17+c，提交后18+c可读。
- Score compute 尾95启动 accumulator 基准96，accumulator 到115才完成；Value 的对应基准为48、完成67。两个 FSM 的 compute / accum timer 独立，各自忙期会进一步延后后继接收。

最终窄范围验证导出的 [score-events.json](../nm37-m1-pipeline/score-events.json) 给出全部256个 PE 的 load、subtract、scale、EXP2首尾和sum周期；另有 [紧凑事件表](../nm37-m1-pipeline/score-events-compact.json)、[保守事件表](../nm37-m1-pipeline/score-events-conservative.json)。

### Scratchpad、常量和完成边沿

末次 SP 读与 `releaseSemaphore(R-1)` 同经公共 Pipe，在物理 R 拍有效，RAM 于该拍末采样。Semaphores 为普通寄存器，无 release→acquire 组合旁路；DMA 最早 R+1 拍 acquire，LSQ 的 `depReady` 再注册后 R+2 才能发 AR。因此新增数据级处理的是已采样值，SP 内容无需保留到该级排空，release 可沿用原周期。

新增数据寄存器必须位于 InputDelayer **之后**。最早 Load→Score 模式切换时，旧 Load 末读出16已被末端寄存器采样，17拍切换 rev/delay 不影响 PE17消费；Value→Load同理，旧 Value底行末token在InputDelayer46采样，47切换delay=false时 PE仍消费已注册旧数据。将该级移到InputDelayer之前会失去这些边界隔离。

紧凑 R16 的 SP slopes 请求54..61和 CMP intercept 指令54..61均在首行56..63消费，行偏移2r、列偏移c一致。八次有效请求令两类 Counter 回到0。请求63切到常量ONE时，前面的各段 slope 已进入行延迟链，不会被新的常量覆盖。

Score 读111，ACC_SA112，accumulator三拍后115输出，与 `read+1+accLatency` 的RAM写周期一致。FSM115发done，公共Pipe对应实际末RAM写116；随后接收的新指令timer0最早117，其输出至少118，没有未排空写回。完整依据见 [schedule-edge-audit.txt](../nm37-m1-pipeline/schedule-edge-audit.txt)。

## 等价验证与功能回归

### 模块级形式证明

Yosys 对实际生成的独立 gold/gate miter 完成三项证明，均退出0，输入位模式无约束，初始状态约束为空。顺序证明采用时间归纳，证明范围为一次流水转换之后的持续输出关系；没有将有限深度 BMC 表述为任意周期证明。

| miter | 参数和比较关系 | 方法 | 结果 |
|---|---|---|---|
| `PipelineCmpMaxMiter` | FP32 CMP 候选 max与旧 max同拍一致 | 组合 SAT，全部输入位模式 | 通过 |
| `PipelineCmpMiter` | FP32 CMP max同拍一致，diff比旧实现晚一拍 | 无约束初始状态的时间归纳 | 通过 |
| `PipelineMacMiter` | FP16乘法 / FP32加法、八段EXP2，acc / elem / hit比旧实现晚一拍 | 无约束初始状态的时间归纳 | 通过 |

证明脚本、输入RTL副本、工具版本、源文件和日志哈希见 [formal/results.json](../nm37-m1-pipeline/formal/results.json) 及 `formal/proof-inputs/`。这些证明覆盖模块的全部输入位模式，包括特殊浮点编码；它们不构成全 FSA 指令流、SRAM 地址或任意参数阵列的形式证明。

### 单元与控制验证范围

[最终 unit.log](../nm37-m1-pipeline/logs/unit.log) 记录 MAC 延迟等价14,394例、CMP max/diff等价20,169例，PE EXP2扫描90例，包含连续 / 有气泡扫描、全部分段、EXP2→MAC和双向旁路切换。调度检查独立跟踪CMP score token到保留行，检查驻留依赖、清零、最早EHR重叠和acc完成，覆盖4×4、4×8、8×4、8×8、16×16。

控制硬件最终运行 `-z row -z overlapped -z consecutive`，**5项执行、5项通过**，覆盖两拍及任意行偏移、候选波前、基本重叠和连续控制。旧宽范围 `ControlSignalTest` 曾中断，未完整通过；事后检查发现其持续创建独立 EphemeralSimulator 编译目录，属于含64行旧测试的慢编译，不能称为已经证实的死锁。缩小范围避免与本次修改无关的大型旧实例重复编译。原日志和范围说明见 [control-test-scope.json](../nm37-m1-pipeline/metadata/control-test-scope.json)。

### 系统功能与周期

同一冻结生成器独立构建4/8/16阵列 Verilator 仿真器，断言开启，使用 `fsa.pePipeline=true`、`fsa.pipelineConservative=false`、`fsa.fanoutSplit=false`。六次 seed0 的正值随机 FP16 输入均与 PyEasyFloat 及保存的 ShiftRightJam A 输出逐位一致。周期变化被记录，不要求流水前后计数器相同。

以下数字直接取自 [equivalence-and-regression-passed.json](../nm37-m1-pipeline/A/equivalence-and-regression-passed.json)：

| 阵列 | seq_q=seq_kv | execTime 基线→候选 | mxActive 基线→候选 | mxBubble 基线→候选 | dmaActive 基线→候选 |
|---|---:|---:|---:|---:|---:|
| 4×4 | 4 | 368→368 | 143→162 | 165→153 | 16→16 |
| 4×4 | 8 | 1083→1083 | 388→464 | 694→646 | 48→48 |
| 8×8 | 8 | 381→385 | 179→210 | 154→134 | 40→40 |
| 8×8 | 16 | 1096→1100 | 524→642 | 619→535 | 116→116 |
| 16×16 | 16 | 420→437 | 249→267 | 126→103 | 76→76 |
| 16×16 | 32 | 1135→1152 | 754→824 | 438→295 | 206→220 |

4×4还完成保守模式及新生成器旧配置的seq4/8逐位回归。保守模式两例execTime仍为368/1083，mxActive185/556；新生成器旧配置为368/1083，mxActive143/388。端到端周期受指令输入和DMA等等待影响，因此相同execTime不代表保守与紧凑算术阶段等长。

另用已保存的旧 / 新16×16仿真器完成seq64扩展跨块回归，没有重新编译：输出形状64×16，两者与PyEasyFloat逐位一致，`inst.bin` / `mem.elf`逐字节一致，无断言失败。execTime3687→3704，mxActive2532→2808，mxBubble1620→997，dmaActive670→712；两组dmaInst40、mxInst56、rawInst329、fence1一致。仿真器、输入、输出和日志指纹见 [seq64-comparison.json](../nm37-m1-pipeline/extended/seq64-comparison.json)。这项扩展是六项冻结门禁之外的补充。

16×16单块增加17拍（4.05%），两块增加17拍（1.50%）。**实际100 MHz物理验收已通过**，将这两个仿真计数按70/100 MHz分别换算，会由6.000→4.370 μs、16.214→11.520 μs。该换算是周期预算比较，沿用多端口RTL测试条件，不是NM37板上实测性能或全部工作负载保证。seq64同口径为52.671→37.040 μs（约1.422倍）；这些换算支持保留决定，但不是板上测量。

验证门禁把上述结果与当前源码、生成器、manifest和单元日志哈希绑定。物理实现脚本先检查门禁，源码或生成器改变必须重建相应验证证据。

独立复核发现原 `unit.log` 在门禁通过后于16:19被清空，原因尚未定位；空日志、当时metadata和原门禁已保存在 `metadata/unit-evidence-incident-*`。16:31:51至16:33:38重新实际执行算术/PE、调度和五项控制测试，全部通过，再生成门禁。使用的主模块class与冻结generator.jar逐字节一致，精确命令、classpath指纹、执行时间和退出码见 [unit-recovery-run.json](../nm37-m1-pipeline/logs/unit-recovery-run.json)。恢复日志SHA-256为 `d1c00449d489ae5f42b64b05fa913730048f7dd5b77b18ad7b24fc2458810afe`，另存 [unit-final-preserved.log](../nm37-m1-pipeline/logs/unit-final-preserved.log)。本次恢复未修改产品源码、生成器或形式证明输入。

主Agent独立比较16×16系统仿真与A100物理生成RTL，PE、CMP、FPMacUnit、FPCmpUnit、SystolicArray、InputDelayer、OutputDelayer共7个模块去注释和空白后全部一致，记录见 [simulation-physical-rtl-binding.json](../nm37-m1-pipeline/metadata/simulation-physical-rtl-binding.json)。这将被验证的主要数据通路模块与物理候选绑定，比较范围不包括完整顶层shell / DMA / 时钟包装。

## 基线证据与生成器清理事件

对照继续采用 `nm37-m1-shiftjam/A` 的已测量RTL、模拟结果、post-route报告和DCP。本次候选 JAR 构建期间，sbt-assembly 2.1.1 的 `cachedAssembly` 在缓存输出路径变化时删除了先前输出路径，导致原 `nm37-m1-shiftjam/generator.jar` 丢失。构建前已校验原JAR哈希 `5df74565dd1dfdb262c7cf53bf7236d73370f83475ee813b02512069d872479a`；字节码证据和事件记录见 [baseline-generator-incident.json](../nm37-m1-pipeline/metadata/baseline-generator-incident.json)。

事后 [baseline-artifact-audit.json](../nm37-m1-pipeline/metadata/baseline-artifact-audit.json) 检查27个原报告 / checkpoint文件，哈希均匹配；原证据 archive校验匹配。已有测量证据仍可用，但原JAR不能再用于重跑精确旧生成器。因此不能说“整个基线目录完全未变”，也不能以新生成器旧开关代替原JAR保存身份。新生成器旧配置的4×4回归和局部RTL比较只是兼容性补充。

## 100 MHz post-route测量与保留决定

候选使用器件 `xcvu37p_CIV-fsvh2892-2L-e`、`EmptyNM37Config`、冻结shell、Vivado2020.2 AR75986及既有实现策略，独立生成RTL/IP后综合、布局、布线，不读取旧checkpoint。`fsa.nm37MHz=100` 同时作用于PLL/实际配置和10 ns目标，控制复制关闭。运行记录 [A100/run.txt](../nm37-m1-pipeline/A100/run.txt) 的elaboration开始时间为2026-10-13 16:09:09 UTC；实现日志为 [implementation.log](../nm37-m1-pipeline/A100/implementation.log)。

| post-route指标 | 已测量ShiftRightJam A，70 MHz | 当前候选，100 MHz |
|---|---:|---|
| DUT周期 | 14.286 ns | 实际10.000 ns |
| DUT setup WNS / hold WHS | +0.878 / +0.010 ns | +0.534 / +0.010 ns |
| 全设计setup WNS / hold WHS | +0.036 / +0.010 ns | +0.049 / +0.010 ns |
| 全设计TNS / THS、失败端点 | 0 / 0、0 | 0 / 0、setup与hold均0 |
| 全设计WPWS / TPWS、失败端点 | 本表不新增旧口径比较 | 0 / 0、0 |
| 总LUT / FF | 357,004 / 71,324 | 355,692 / 140,263 |
| DSP / RAMB36 | 288 / 52 | 288 / 60 |
| 完成布线网络 / 布线错误 | 469,234 / 0 | 497,497 / 0（全部497,497个可布线网络） |
| bus skew最差裕量 | +2.787 ns | +3.142 ns |
| 实际最差路径与新路径族 | PE反馈，13.143 ns | reset catcher广播，9.082 ns |
| DRC / CDC | 详见旧报告 | 1,114项DRC，无Error / Critical；CDC Critical30 |
| DSP寄存器映射 | 详见旧报告 | 与基线一致，全部MREG=0 |
| 最终保留决定 | 已保留ShiftRightJam组合优化 | 保留显式A100流水配置 |

原70 MHz列来自 [已完成的物理比较](../nm37-m1-shiftjam/metadata/physical_comparison.json)。当前候选数值来自post-route原报告及 `obj/audit/run_properties.txt`：DUT实际周期10.000 ns；全设计setup、hold和脉宽失败端点均0。Vivado于2026-10-13 17:20:12 UTC正常退出（exit 0），[物理采集结果](../nm37-m1-pipeline/metadata/physical_comparison.json)确认受约束setup/hold、bus skew和完整布线检查通过。脉宽也由原始timing summary单独核验，WPWS/TPWS和失败端点均0。

候选总LUT减少1,312（约0.37%），FF增加68,939（约96.66%），RAMB36增加8，DSP保持288。新增流水显著增加状态资源，需在保留决定中明确代价，不能只呈现LUT减少。

### 最差路径族已改变

DUT top100全部由reset catcher发起，到阵列valid / control等寄存器；当前最差为reset广播路径，并非CMP算术路径。最差数据延迟9.082 ns，其中逻辑0.175 ns、布线8.907 ns。旧基线各自最差为PE反馈13.143 ns，两者属于不同路径族，不能逐路径相减归因于算术切点，也不能因top100没有CMP而宣称全部CMP / PE数据路径均已独立审查。补充查询已完成，结果如下。

这里改变了目标周期，不能直接用两组WNS相减宣称净收益。采集器的`top100_path_families`按路径涉及的目的层级归类为CMP91 / PE9；这不是启动源或算术类型分类，100条启动源实际均为reset catcher。独立按启动源分类及补充查询见下表。

DSP寄存器分布与基线逐项相同：256个PE为AREG/BREG/CREG/MREG/PREG=0/0/1/0/0；32个ACC中16个为0/1/1/0/0，16个为0/1/1/0/1。新增after-add级使用普通FF，没有自动变成DSP MREG/PREG。SA的FF由28,814增至97,474，LUT由299,178变为299,288；scratchpad由1,184 LUTRAM映射为8个RAMB36，因此不能将总LUT减少归因于PE算术简化。

### 补充数据路径与下一步

只读打开同一post-route checkpoint，排除4个`*reset_catcher*`时钟寄存器后，在其余117,530个DUT时钟顺序单元范围内查询。各组仍包含局部命令/控制输入，不能都称为纯数据算术；不包含其他时钟域或顶层I/O。完整范围见 [scope.txt](../nm37-m1-pipeline/A100/data-path-audit-v2/scope.txt)，独立复核见 [data-path-review.json](../nm37-m1-pipeline/metadata/data-path-review.json)。

| 查询范围 | 最差setup裕量 | 数据延迟（逻辑 / 布线） |
|---|---:|---:|
| 排除reset catcher的DUT路径 / PE加减后切点输入 | +1.457 ns | 8.398（2.341 / 6.057）ns |
| PE加减后切点输出 | +2.804 ns | 7.009（1.674 / 5.335）ns |
| CMP加减后切点输入 | +2.078 ns | 7.811（2.128 / 5.683）ns |
| CMP加减后切点输出 | +2.866 ns | 7.005（1.886 / 5.119）ns |
| CMP最大值反馈目标 | +4.231 ns | 5.623（0.960 / 4.663）ns |
| 穿过scratchpad RAM数据输出 | +6.401 ns | 3.274（1.198 / 2.076）ns |

最差非reset路径为`sa/pipe_b_43_exp2_reg`→`sa/mesh_1_12/macUnit/mulAddExp2/fma/adderOutAbs_reg[53]_bret`，27级；说明切点前仍含EXP2控制选择、对齐和加法，但已满足10 ns。新增scratchpad入口级将存储读边界与PE长运算链分开，CMP快速最大值反馈也有充分余量。

**决定：保留显式A100配置作为后续100 MHz实验基线，不追加第二算术切点。** 已通过功能验证、严格非负setup/hold、脉宽、bus skew和完整布线检查；代价为FF约增加96.66%、8个额外BRAM，端到端回归新增周期未抵消提频收益。源码旧默认仍保留，以显式配置保证新旧行为可选择。最终证据指纹与验收范围见 [final-acceptance.json](../nm37-m1-pipeline/metadata/final-acceptance.json)。

若以后继续提频，优先分析reset分发的高扇出与跨SLR位置（当前最差路径98.07%为布线），再考虑PE切点前EXP2控制/对齐链及DSP寄存器利用。任何reset复制/局部分发都须保持同步释放语义并重新做CDC和完整STA；不能直接把70 MHz控制复制B的手工副本方案当作已验证解法。第二算术切点将增加驻留依赖间隔和调度成本，目前没有为100 MHz采用它的必要。尚未验证更高频率或300 MHz。

### 约束、CDC和DRC审查

独立审查确认CDC Critical仍为30，按rule / clock / type / depth / exception / source / destination字段完全一致；Warning由171变为173，其中CDC-15为170→172。变化来自100 μs debounce计数随频率由7000 / 13 bit变为10000 / 14 bit，新增bit13对应两条CLR异步reset边界CDC-15 Warning。`check_timing`正文与基线一致，30个未使用HBM常量端点及`prst_n`一项缺input_delay继续保留。

DRC总数仍1,114，无Error或Critical；1,113条详细记录相同。变化项RTSTAT-10的无routable-load计数由89降为81，列出的对象数由37降为29；报告仅显示first15，不能把局部显示列表当作全部对象清单。这些Warning / Advisory仍需按既有范围解释，不能称为DRC clean；也不能笼统称所有问题都未变。独立审查证据见 [post-route-constraints-review.json](../nm37-m1-pipeline/metadata/post-route-constraints-review.json)。

## 开关、复现和限制

源码默认 `fsa.pePipeline=false`，即pe/cmp/spad延迟0/0/1；候选需显式选择 `true`。候选内部默认 `fsa.pipelineConservative=false`，选择紧凑波前和双FSM重叠；`true`提供整阵列屏障及串行调试对照。本实验显式 `fsa.fanoutSplit=false`，没有将控制复制开关的源码默认值作为A配置。时钟目标通过 `fsa.nm37MHz=100`显式指定。

复现需在新的独立工作区恢复记录的源码 / 依赖、冻结shell和基线证据，按本机 `environment.sh` 提供Vivado、Java、firtool、Verilator和Python工具。现有候选目录已冻结，prepare / elaborate / implement会拒绝覆盖；不要直接在完成的证据目录重复创建阶段。重建前应先独立备份任何需保存的旧JAR，避免sbt缓存输出清理再次影响它。

```bash
bash nm37-m1-pipeline/scripts/prepare.sh
bash nm37-m1-pipeline/scripts/run_units.sh
python3 nm37-m1-pipeline/scripts/verify_equivalence.py
bash nm37-m1-pipeline/scripts/run_sim.sh 4 legacy
bash nm37-m1-pipeline/scripts/run_sim.sh 4 conservative
for size in 4 8 16; do
  bash nm37-m1-pipeline/scripts/run_sim.sh "$size" A
done
python3 nm37-m1-pipeline/scripts/check_verification_gate.py
bash nm37-m1-pipeline/scripts/run_physical.sh elaborate
bash nm37-m1-pipeline/scripts/run_physical.sh implement
# 仅在实现完整结束且所有所需审计报告存在后执行：
python3 nm37-m1-pipeline/scripts/collect_physical.py
```

补充审计使用 [audit_data_paths.tcl](../nm37-m1-pipeline/scripts/audit_data_paths.tcl)，传入已完成的`post_route.dcp`和一个全新的输出目录；必须使用`open_checkpoint`建立当前设计。初次`read_checkpoint`尝试因没有打开design而失败，原脚本和日志已保留；成功结果位于`A100/data-path-audit-v2`。查询只读，不修改或重新保存checkpoint。此脚本及最终验收脚本是在物理运行完成后新增，单独由`final-acceptance.json`和归档清单记录哈希，不追溯改写冻结构建manifest。

完成独立审查后运行 [final_acceptance.py](../nm37-m1-pipeline/scripts/final_acceptance.py)，再用 [archive_evidence.py](../nm37-m1-pipeline/scripts/archive_evidence.py)生成 [完整证据归档](../nm37-m1-pipeline/NM37_M1_PIPELINE_evidence.tar.gz)和 [SHA-256](../nm37-m1-pipeline/NM37_M1_PIPELINE_evidence.sha256)。归档包括本次精确generator.jar、源码快照、生成RTL、验证证据、物理报告和checkpoint；独立的`metadata/generator-preserved.jar`副本不重复打包。

形式脚本使用 `nm37-m1-shiftjam/formal-env/bin/yowasp-yosys`，环境版本依据 [ShiftRightJam工具依赖](../nm37-m1-shiftjam/formal/requirements.txt) 和当前 [Yosys版本](../nm37-m1-pipeline/formal/yosys-version.txt) 恢复。脚本阶段与源码指纹保持一致；门禁或采集失败不能跳过后写入“通过”。

当前限制包括：全FSA指令调度未做形式证明；系统回归为六个冻结案例加seq64扩展，均seed0正值随机输入，特殊编码主要由模块证明和单元样本覆盖；保守模式只做4×4端到端回归；任意行偏移支持已验证但不意味着所有未来流水深度自动正确；宽64行旧控制suite未完整执行；未进行板上验证。实际100 MHz受约束STA已有正裕量，仍不能将其视为板级签核；既有CDC、复位input_delay、未使用HBM常量端点和约束选择器问题继续保留。主验收已完成，结论限于上述功能覆盖与受约束STA范围。

若未来更高频率目标需要，可另行评估独立的两级PE算术方案：PE内部延迟2、CMP1 / SP2 / ACC3，驻留提交间隔3，并同步延迟所有路由 / EXP标签及检查完整pending队列。该方案仅为备选，尚未实现、验证或进行物理测量；不能把当前结果直接推广过去，启用后必须重新完整验收。
