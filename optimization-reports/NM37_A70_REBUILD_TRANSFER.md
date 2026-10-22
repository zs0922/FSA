# NM37 历史 A70 重建与统一板测迁移

## 本轮范围与身份

修复历史 P2/P3 的构建顺序，从两个独立历史 checkout 重新编译、验证和实现。交付的 A70 是历史 P2/P3 优化版本；它不同于后来保留 ShiftRightJam 的 M1 A70。A100/A110 还包含中间版本的算术、资源和调度修改，不能把跨版本的资源差全部归因于提频。

本报告所在工具集成分支为 `codex/nm37-a70-rebuild`。所有工作仅在本地提交；尚未连接或测试 `10.128.157.151`，不 push。原工作区已有修改保持原样。

| 配置 | 硬件源码身份 | 构建目录 / 身份说明 |
|---|---|---|
| A70-P2 | `f7d31bb197b93eef9dc416d0508e550894531665` | `final-p2`；原 P2 `5acd5e8d` 的顺序/仿真配置修复，完整 patched-tree 源锁 |
| A70-P3 | `73b3e42f3006818d13bc059818e11cc803f78dba` | `final-p3-v2`；原 P3 `45d859c1` 的相同修复，完整 patched-tree 源锁 |
| A100 | `0f1a643a58894dc52b46d4058f39d12205405644` | `original-A100-20261013`；历史工作树构建，后来整理的源码快照，不称为该干净提交的构建 |
| A110/APB55 | `eeb1a1d0ac6f47497eb2806e412d9df9ad4b6b67` | `apb110-build04`；同一 routed DCP 经独立 `reaudit04` 验收后导出 |

本地构建根目录为 `/home/zhangsi/tmp/FSA-m1/nm37-a70-rebuild-20261022`。阶段 root commit 不包含外部子模块应用后的补丁状态，因此须同时核对源锁、补丁清单、验证 overlay、JAR/RTL 哈希，不能只看 `git rev-parse HEAD`。

## 发现和修复

历史 `p2.bit` 与 `p3.bit` 的配置 payload 完全相同：offset 137、length 84989156，SHA-256 `3f2f77a21926b6257114900759f86b403baeeda79153230e1b80c92f2d65fc45`。构建日志先生成 assembly/RTL，之后才应用 P2/P3 补丁，因此原文件名和代码提交不能证明优化进入网表。原报告资源仅作为历史证据，不再代表真正 P2/P3。详见 [历史审计](NM37_A70_STAGE_AUDIT.md)。

修复包括：

- 在 make 解析源文件列表之前，由独立 prepare 进程应用完整补丁；patch-state 同时成为 JAR/FIRRTL/annotation 的正常依赖。
- 固定历史 FSA/easyfloat/shell 提交，每个补丁先 check；shell 使用累计 p0p1→nm37 顺序，不能先打 u280 再追加累计 p0p1。取消“看到旧 marker 就吞掉 patch 错误”的行为。
- P2/P3 分开源码、缓存生成物和输出目录；全源清单锁定 175 个实际消费递归依赖。共享第三方下载缓存，不复用历史 generator、RTL、仿真器或 routed DCP。
- 修正新单端口仿真拓扑的 AXI beat 为 32 bytes；历史默认 64 bytes 超过 scratchpad row，导致 elaboration 失败。此修改只作用于新增仿真 Config，不改变 FPGA 配置。
- 形式测试 emit 增加 `disallowPackedArrays`，避免 Yosys 不支持的数组语法；仅测试表示修改，不改变算法。
- 导出前门禁发现 Vivado 实施阶段在 `obj` 外新增 141 个工程/IP 文件。原始 elaboration 封印的 128 个文件零修改、零删除；新增文件按精确路径清单、5 个 IP 的显式 Tcl/XCI 配置和 XPR 来源引用审查，并单独封印。未来入口在 implement 完成时自动记录此集合，原始 RTL/Tcl 仍须逐字节一致。未知生成布局必须重新审查，不能整目录忽略。

`final-p3-v2` 的 assembly 复用了本轮已从正确 patched source 编译的 JAR；与前一个新输出的差别仅是 Test 源 emitter 格式选项，Test 源不进入产品 assembly。所有最终 RTL、仿真和验证重新绑定到 v2 源锁。早期失败记录保留，没有改写为通过。

## 功能与等价验证

P3 连续 ACC 测试覆盖 24394 个样本，逐拍切换输入和 MAC/EXP2；与独立历史 oracle 对比固定三拍输出。525 次 reciprocal 闭环事务通过，实测旧/新完成延迟为 19/76 个 presented cycles。历史过时的 `ControlSignalTest.scala` 未运行，未以 API 补丁伪装旧测试通过。

P3 的实际 emit miter 通过三上升沿精确展开及无约束 SAT 证明，同时验证未被 valid 屏蔽的数据比较为零。输出锥无初始状态依赖，故结论适用于流水填充后任意三拍窗口；输入、复位、初态无假设。12 项展开器语义/拒绝用例通过。该证明覆盖 MAC/EXP2 三拍算术等价，不覆盖 reciprocal、scale 寄存器或完整调度；后者由定向和系统测试提供证据。

原归纳 SAT 尝试长时间未完成，替代证明通过后中止，保留 exit 130 和原日志。最终构建冻结 helper 不变，独立形式验收更新记录在 `formal-method-update.json`，绑定同一源锁/JAR/RTL。交付的新 `run.sh formal` 默认使用已通过的窗口证明入口。

P2/P3 分别完成新编译 4×4 seq4/8 与 16×16 单端口 seq16/32 系统回归，均逐位匹配 PyEasyFloat。统一板测软件又在两套独立 RTL 仿真器上完成 seq16/32/64，输入、指令和输出哈希一致、零错误。这是软件与 RTL 兼容性回归，不能称为 PCIe/HBM 实板验证。

| 16×16 / seed0 | P2 exec cycles | P3 exec cycles | P2 mxBubble | P3 mxBubble | P2 mxActive | P3 mxActive |
|---|---:|---:|---:|---:|---:|---:|
| seq16 | 394 | 442 | 167 | 111 | 179 | 249 |
| seq32 | 1109 | 1157 | 550 | 421 | 610 | 754 |
| seq64 | 3661 | 3709 | 1890 | 1599 | 2228 | 2532 |

以上同工作负载 P3 均多 48 拍；bubble 减少不代表运行更快。`mxBubble` 是矩阵指令接口 ready 且无 valid 的周期，不能等同阵列空闲，且不能将 active/bubble/DMA 直接相加当作总时长。实际板测按相同 seq/seed/输入/指令/软件哈希分组，`exec_us=execTime/clock_mhz`；主机 `FPGA.execute` 耗时另列。本轮又使用同一板测 Runtime 在保留的 A100/A110 仿真器上重跑 seq16/32/64，逐位通过，输入/指令/输出与上表一致；A100 与 A110 的八项计数器逐项一致。A100 历史 simulator 和全部 RTL 哈希重新核对原 build-metadata，通过。rerun identity 中的 hardware_source_commit 只标识提供 DRAMSim ini 的当前目录，实际旧 simulator 构建身份由原 metadata 与源码快照审计建立。

| seq | A100/A110 exec cycles | A100/A110 mxBubble | 100 MHz exec_us 换算（非实测） | 110 MHz exec_us 换算（非实测） |
|---|---:|---:|---:|---:|
| 16 | 437 | 103 | 4.370 μs | 3.973 μs |
| 32 | 1152 | 295 | 11.520 μs | 10.473 μs |
| 64 | 3704 | 997 | 37.040 μs | 33.673 μs |

仍须注意：实际 RTL 核对显示：历史 A70 新仿真是 1×256-bit DMA 接口，A100/A110 既有仿真是 8×64-bit DMA 接口，内存拓扑不同。即使工作负载相同，上述周期差也不能单独归因于 PE 流水，更不能作为真实 NM37 板上速度结论。

## 物理实现与 bitstream

本次两套历史源码均完成完整 synthesis/place/route/post-route phys_opt。P3 通过 70MHz 约束时序验收；P2 未过 setup，不导出 bit、不加入板测包。P2 并非构建没有执行，其实际失败结果保留，不能沿用旧同载荷文件的通过结论。

| 配置 | DUT MHz | 全设计 setup WNS / hold WHS(ns) | DUT setup WNS(ns) | LUT（器件占比） | FF（器件占比） | DSP（器件占比） | RAMB36（器件占比） |
|---|---:|---:|---:|---:|---:|---:|---:|
| 真正重建 A70-P2，setup失败 | 70 | −0.182 / +0.002 | −0.182 | 375,298（28.79%） | 64,683（2.48%） | 320（3.55%） | 60（2.98%） |
| 真正重建 A70-P3 | 70 | +0.047 / +0.010 | +0.419 | 378,248（29.01%） | 71,330（2.74%） | 288（3.19%） | 52（2.58%） |
| A100，历史已验收文件 | 100 | +0.049 / +0.010 | +0.534 | 355,692（27.28%） | 140,263（5.38%） | 288（3.19%） | 60（2.98%） |
| A110/APB55，历史已验收文件 | 110 | +0.114 / +0.010 | +0.802 | 355,981（27.31%） | 140,324（5.38%） | 288（3.19%） | 60（2.98%） |

机器可读实测数据见 [routed-results.json](evidence/nm37-a70-rebuild/routed-results.json)，归档原件与解压前后 SHA 见 [routed-archive-index.json](evidence/nm37-a70-rebuild/routed-archive-index.json)；目录内 `.gz` 均为无损压缩原始报告。审计脚本副本保存当时执行方法，不替代 `scripts/nm37-a70` 的可迁移构建入口。

器件为 `xcvu37p_CIV-fsvh2892-2L-e`；分母分别为 1,303,680 LUT、2,607,360 FF、9,024 DSP、2,016 RAMB36，四组 RAMB18/URAM 均为零。资源为 routed 顶层实际结果，不是综合估算。P2→P3 为 +2,950 LUT、+6,647 FF、−32 DSP、−8 RAMB36；P3→A100 为 −22,556 LUT、+68,933 FF、+8 RAMB36。后者还包括 ShiftRightJam 等中间优化，不能全部归因于 PE 插拍。

P2 的 TNS 为 −93.724ns、失败 setup 端点 1,220。旧 report.tcl 按实际失败 exit 1，故没有最终 checkpoint marker/implement pass seal；报告独立记录失败 DCP 的 SHA，不把它升级为成功封印。最差 DUT 路径是 `mxControl/io_acc_ctrl_pipe_b_cmd_reg[2]_rep__1` → `accRAM_sram/.../RAMG_D1/I`：54 级组合逻辑，数据延迟 14.344ns，其中逻辑 5.075ns、布线 9.269ns。次差还包括 scratchpad→阵列和 CMP 输出路径。该历史架构的失败保持原样，未通过修改约束或强行导出来掩盖。

P3 全设计 setup/hold/pulse-width 失败端点均为零，477,602 条可路由 net 全部完成、路由错误零，bus skew 最小余量 +2.579ns，DRC 无 Error/Critical Warning。DCP SHA-256 为 `89af2e0680081b3b9f23ecd0f583d03c85fe76b62b65ab2553b3d4a265057707`。

P2/P3 的实际 routed CDC 均为 300 条，按类型/严重性/源目标时钟/深度/例外/具体源目标端点共九字段，与历史 70MHz shell 逐项一致；32 个时钟及 45 个 clock-pair 分类、exceptions 正文一致。P3 保留 30 条 Critical（12 异步复位、2 POR 同步器前逻辑、16 vendor PCIe pipe/GT），这不是 CDC-clean 声明。实际 30 个未使用 HBM AXI1..15 BREADY/RREADY 恒定时钟端点、异步 `prst_n` 无 input delay 均逐名核对，无其它非恒定时钟未约束端点。接受范围是带已知边界的受控板测候选，真实复位和 PCIe/HBM 启动仍待板测。

P3 bit 已由上述 DCP 成功导出，大小 84,989,293 bytes。配置载荷 offset137、length84,989,156，SHA-256 `2b3f86ef8f84c6e499408bfb9f9afe0479bea52f409390c6a106f4e3f26328bb`，与旧 P2/P3 的载荷不同；差异不仅是时间戳。结构与正确性结论仍依赖源锁/JAR/RTL、独立测试和实现证据链，不单凭 payload 不同。详见 [bit 身份核对](evidence/nm37-a70-rebuild/a70-p3-bit-identity.json)。

| 板测配置 / 包内文件 | 文件 SHA-256 |
|---|---|
| A70-P3 / `bitstreams/A70-P3.bit` | `2ad3d1602f4a8b13f4ac64df03e18ff81208feb6d691bb69080558dd76ba993a` |
| A100 / `bitstreams/A100.bit` | `f698413a3379cdc2b1155fbce9dc4acdb417935f711058654e2663151659c0cf` |
| A110-APB55 / `bitstreams/A110-APB55.bit` | `f3ffe9b90b60fb9ea00594381174bbf47e1f1bb435f2e98cfd32ca292fe11f86` |

三个文件均为 84,989,293 bytes；A100/A110 使用此前实际已验收导出的文件，本轮没有重新 P&R。A70 原文件在本地构建根 `final-p3-v2/bitstream/NM37_A70.bit`；A100 在 `/home/zhangsi/tmp/FSA-m1/nm37-m1-board/bitstream/NM37_A100.bit`；A110 在 `/home/zhangsi/tmp/FSA-m1/nm37-m1-pipeline/apb110-20261020/bitstream04/export/NM37_A110.bit`。

本次原始 phase/helper 封印保持不变，首次导出被门禁阻止的失败记录保留；独立 `bitstream-method-update.json` 绑定新增生成物封印和经复核的导出入口，重新核对原源锁、所有旧阶段、CDC 与物理验收，调用原冻结 `bitstream.tcl` 导出。该修复不改变硬件设计或 DCP，不重新布局布线。

## 迁移入口与板测边界

[历史重建入口](../scripts/nm37-a70/README-build.md)规定安装、冻结、编译、验证、实现和 bitstream 门禁。[统一板测说明](../scripts/nm37-board/README.md)规定 manifest、设备身份、重复运行、计时、bubble 和离线源码恢复。

板测包仅携带验收通过的 A70-P3、A100、A110/APB55 各自的 bit/config 与同一套软件；P2 未过 70MHz setup，不导出也不混入板测包。使用板测包不要求目标服务器先下载整个 FSA 仓库。源码复现包另含主仓旁支历史与实际消费依赖的独立 Git bundles；不包含 Vivado 安装/许可、系统驱动或编译缓存。完整依赖范围为 175 个路径，11 个明确递归排除，另外 20 个非本构建 root gitlinks 不恢复；不能声称整个 Chipyard 的所有子模块均已填满。

已完成 175 依赖的全量离线恢复，477009 个版本化文件逐一核对固定 Git tree；39 个声明 CRLF 的上游文件单列转换。安装后 478173 个共同输入内容全部匹配本轮 P3 源锁，新增三个安装工具并单列八项权限变化。A100/A110 源码及复现 profile 的 173 个递归依赖 pin 均与同一依赖包兼容。恢复器补齐 submodule active 登记，且保持无 remote/alternates。最终迁移打包发现 A110 原始证据文件名超过 USTAR 的 basename 限制，已将主入口修为保留原文件名的规范化 PAX，并验证重复归档哈希一致、解包和篡改拒绝。迁移与板测工具最终 59 项测试通过；新增实施生成物门禁另有 24 项正反例及真实 CLI 集成测试通过。

交付使用 `nm37-board-transfer.tar.gz`、`nm37-source-transfer.tar.gz` 和 `SHA256SUMS.archives`。主源码 bundle 固定于包含本报告与全部工具/验证证据的集成提交，归档最终大小/SHA 与本地提交另记交付凭据，避免归档哈希自引用。目标服务器只在人工确认 JTAG 板卡及 BDF/XDMA 对应关系后手动加载，先 dry-run 与 seq16，再增加规模。保存编程日志、文件 SHA、设备信息和所有运行结果；本轮不操作远端、不 push。

最终归档、校验和及迁移入口见 [最终交付验收](NM37_A70_DELIVERY.md)。该交付回执在归档完成后单独提交，归档本身固定于 `d589b230`，不包含后续回执提交。
