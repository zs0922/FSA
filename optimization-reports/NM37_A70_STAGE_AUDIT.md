# NM37 历史 A70 阶段与后续提频基线核查

核查日期：2026-10-22。依据 `/home/zhangsi/FSA/nm37-stage-results` 的原始 bitstream、Vivado 报告、构建日志、Git 提交与补丁。本次只读核查，不重新布局布线、不烧板。机器可读统计、完整文件 SHA-256 和原始行号见 [audit.json](evidence/nm37-a70-stage-audit/audit.json)。旧仓及原产物未修改。

后续已按独立源码重建 P2/P3，并获得真实 P3 的 70MHz 实现结果；构建顺序修复、验证、失败的 P2 对照及统一迁移见 [重建与板测迁移报告](NM37_A70_REBUILD_TRANSFER.md)。本审计中的旧文件测量继续保留原始身份。

## 结论

历史 baseline/P1/P2/P3 四份 70 MHz bitstream 均存在。此前“没有找到 A70 bitstream”仅适用于当前 FSA-m1 工作区，未覆盖该历史目录，不能泛化为不存在历史 A70。历史 A70 不应与后来的 M1 A70（尤其保留 ShiftRightJam 的 A 配置）合并为同一版本。

**P2 与 P3 的配置载荷逐字节完全相同。** 四个 .bit 均为 84,989,293 字节；按 .bit 标记解析后，配置载荷从偏移 137 开始，长度 84,989,156 字节。P2/P3 的载荷 SHA-256 均为 `3f2f77a21926b6257114900759f86b403baeeda79153230e1b80c92f2d65fc45`，二者只有头部时间字段的五个字节不同。文件全体 SHA 不同不能证明硬件不同。

## 提交中的改动与实际构建证据

| 阶段 | 驱动声明的提交 | 提交/补丁中的预期改动 | 本次验收判断 |
|---|---|---|---|
| baseline/P0 | `490dd747` | NM37 移植后的报告基建，保留调试逻辑 | 保留原始物理测量；不追溯认定完成功能验收 |
| P1 | `ab0a602b` | 去 ILA、关闭 HBM XSDB，启用综合 retiming、布局/物理优化及扇出约束 | 产物资源确有下降；自动 retiming 的开关不等于实际插入流水 |
| P2 | `5acd5e8d` | MatrixEngineController 的 sp/acc read、CMP/ACC/PE control、semaphore release 统一 Pipe 一拍 | 日志显示补丁晚于生成器执行，不能确认这份 bit 中包含预期控制流水 |
| P3 | `45d859c1` | accumulator FMA 总延迟 3 拍、reciprocal 等待及 accRAM 写回/调度同步调整；PE 保持组合 | 与 P2 配置载荷相同，不能作为独立 P3 硬件优化结果 |

P3 修改的是阵列外的 accumulator FMA，与后续 A100 对 PE 内部 FMA 新增一拍的改动不同。P3 commit message 中 101.6 MHz 的旧说法也不能使用，后来的历史文档已勘误；本目录实际时钟均为 70 MHz。

构建顺序问题有直接证据：`logs/p3.log:59` 已执行 chipyard.Generator，`:344` 才开始应用 P3 FSA 补丁，`:346` 才应用 easyfloat P3 补丁。P2 同样是 `:59` 生成、`:342` 应用补丁。两份日志在 `:56` 都写明 assembly jar up to date。旧 `fpga/Makefile:212` 把补丁作为 bitstream 的 order-only prerequisite，没有使生成器编译/RTL 生成依赖补丁完成。这解释了为何“已 checkout 到该提交并最终打了补丁”不足以证明生成 RTL 正确。确切缓存源码身份未被当时封印，不能仅凭日志猜测它对应另一阶段。

## 统一资源和时序统计

下表前四行来自各阶段 `report/utilization.txt:24` 的顶层行；DUT WNS 来自 timing 的同域摘要。后四行为已归档的后续实验，作用是展示演进，不能将跨阶段全部差异归因于单项优化。

| 阶段 | MHz | LUT | FF | RAMB36 / RAMB18 | DSP | DUT setup WNS(ns) | 全设计 setup WNS(ns) |
|---|---:|---:|---:|---:|---:|---:|---:|
| 历史 baseline/P0 | 70 | 380,891 | 72,500 | 80 / 3 | 320 | +0.001 | +0.001 |
| 历史 P1 | 70 | 377,252 | 64,464 | 60 / 0 | 320 | +0.023 | +0.023 |
| 历史 P2 文件 | 70 | 376,452 | 64,457 | 60 / 0 | 320 | +0.054 | +0.021 |
| 历史 P3 文件（载荷同 P2） | 70 | 376,452 | 64,457 | 60 / 0 | 320 | +0.054 | +0.021 |
| M1 A，ShiftRightJam 优化前 | 70 | 381,532 | 71,461 | 52 / 0 | 288 | +0.832 | +0.024 |
| M1 A，保留 ShiftRightJam | 70 | 357,004 | 71,324 | 52 / 0 | 288 | +0.878 | +0.036 |
| A100 | 100 | 355,692 | 140,263 | 60 / 0 | 288 | +0.534 | +0.049 |
| A110/APB55 | 110 | 355,981 | 140,324 | 60 / 0 | 288 | +0.802 | +0.114 |

历史四组 hold WNS 均为 +0.010 ns，setup/hold/pulse-width 摘要失败端点均为零；仍保留未约束端点和 input delay 等边界，不能直接等同于当前 A110 的完整验收流程。旧 `NM37_300MHz_PLAN.md` 将 P2/P3 的 +0.021 标成 WNS@70MHz，容易将全设计与 DUT 混淆；FSA 70 MHz 同域值实际为 +0.054。

后续来源：[M1 A/B](NM37_M1_BASELINE_AB.md)、[ShiftRightJam](NM37_M1_SHIFTRIGHTJAM.md)、[A100](NM37_A100_BASELINE.md)。A110 来源是本地 `codex/nm37-a110-reset` 分支 `ef269122` 的 `optimization-reports/NM37_A110_APB.md`；main 尚未合入该报告。

## bitstream 位置

历史文件全部位于 `/home/zhangsi/FSA/nm37-stage-results/`：

- `baseline/NM37FPGATestHarness-baseline.bit`
- `p1/NM37FPGATestHarness-p1.bit`
- `p2/NM37FPGATestHarness-p2.bit`
- `p3/NM37FPGATestHarness-p3.bit`

完整 SHA 和 .bit 头部内容见 audit.json。这些文件可作为有明确身份的历史样本；P2/P3 不应统计为两种不同硬件，也不能以烧录成功替代相应的数值与稳定性验证。

## 运行时间、bubble 与重建建议

该 stage archive 中未发现绑定这些 bitstream 哈希的板上输入/输出及 execTime/mxBubble 记录。旧 optimization-reports 中多个 P1/P2/P3 性能表来自 U280，不能移植成 NM37 板测数字。本轮将历史 NM37 运行时间和 bubble 标为待同机实测，不填推算的测量值。

新服务器上分别使用“历史 P0”“历史 P1”“历史 P2/P3 同载荷”“M1 A70+ShiftRightJam”“A100”“A110/APB55”身份采集结果。若要证明源码 P2/P3 的收益，需独立干净检出、先完整应用补丁再编译生成器、保存源/JAR/RTL 指纹、核对控制寄存与 accumulator 三拍结构，随后做功能验证及完整布局布线。不能原样重跑旧 driver：它还会 checkout/reset/clean 共享工作区，并且当前循环只有 p1/p2/p3，不再自动重建 baseline。
