# NM37 A110：独立 HBM APB 时钟域

## 基线、目标与验收范围

从实验分支 `codex/nm37-a110-reset` 的 `9ea4c5d7` 继续。上一轮 DUT setup/hold 通过，但 HBM APB PCLK 的最小周期和脉宽失败；不能据此称整板 110 MHz 已通过。本轮只改变板级 HBM 时钟、复位与启动关系，保留 PE/CMP/scratchpad/ACC 的流水和原复位分发复制方案。

执行工作区：`nm37-m1-pipeline/apb110-20261020/src`。最终候选的大产物输出：同级 `build04`（`build`、`build02`、`build03` 保留前轮记录）。工作区从本机 Git 对象检出已提交的 A110 源码，恢复依赖的固定 gitlink，不拷贝旧生成器、仿真器或物理检查点。第三方工具/缓存可复用；正式运行的输入、工具、生成器及各阶段证据须绑定指纹。主工作区已有的波形分析修改不参与本次构建。

本轮区分三件事：功能验证、全部受约束的布局布线时序验收、实际板卡运行。前两项通过也不能替代上板测试或宣布所有既有 CDC/约束问题已关闭。

## 实施方案

APB 选择 55 MHz，满足所用 HBM IP 的 50–100 MHz 范围，与 DUT 保持 2:1。110/100 MHz 相关时钟的相邻边沿最短仅约 0.909 ns；本轮在实现前根据该关系选择 55 MHz，避免给新增跨域路径施加不必要的紧边沿窗口。没有完成新 APB100 候选的布局布线，因此不能把这项选择描述成 APB100 已经实测失败。正常计算阶段不经过 APB；降低 APB 频率主要影响初始化等待。IP 参数 `USER_APB_PCLK_0` 同步设置为真实输入频率。

- `harnessSysPLL` 在同一 `dutClockGroup` 内产生 DUT/AXI 110 MHz 与 APB 55 MHz。保留两路生成时钟的真实相关关系，不添加两域整组异步时序例外；HBM reference clock 仍来自板上 100 MHz 时钟。
- `LazyXilinxHBMController.apbClockNode` 显式连接 APB 域。暴露硬核 `apb_complete_0`，APB 域寄存后经三级 DUT 域同步，首次完成后才开放 AXI 五通道握手。
- APB PRESET_N 异步断言、三个 APB 上升沿后释放；AXI ARESET_N 等待 APB release 后再经三个 AXI 上升沿释放。共享 ResetWrangler 汇总 PLL lock 丢失及 PCIe reset 原因，不支持单独 AXI warm reset。新增板上 100 MHz 域的 `HBMPLLResetControl`，PLL 失锁或外部 PERST 异步拉低 XDMA 的 fundamental reset；三级同步释放后连续计满 10000 拍（约 100 μs）才允许释放。其输入取 ResetWrangler 之前的 PLL reset，不反馈 PLL 本身，避免 XDMA 与 Wrangler 互相等待。运行中失锁会同时清空 PCIe 主设备、互连和 HBM 整栈；恢复涉及 PCIe 重训练，主机必须重新初始化设备和重载数据，不是无损 DMA 恢复。100 μs 是本设计的复位保持策略，并非引用为 IP 的最短脉宽要求。
- 启动门控同时控制每个通道两侧 valid/ready，避免单边握手。开放后组合直通，不新增数据通路寄存器或 PE 拍数。
- 已启动后完成状态意外撤销会锁定故障并阻断流量，直到共同 cold reset。该策略是停止运行等待恢复，不保证保存正在进行的事务；主机必须重新初始化任务并重载所需 HBM 数据。
- 新同步器显式标注 `ASYNC_REG` 和 `SHREG_EXTRACT=NO`；数字仿真验证协议和释放拍数，实际同步结构及跨域路径由 routed DCP 补充核查。

APB 加入 ResetWrangler 后，最慢时钟为 55 MHz，去抖计数预期从 11000 变为 5500，名义等待仍约 100 μs。这不是 PE 流水变化。

## 可复现入口

沿用 `scripts/nm37-a110/run.sh`，设置 `A110_OUT` 为不存在的绝对路径。工具版本与基础依赖要求沿用 A100/A110 基线；环境由 `logs/environment.json` 记录。依次执行：

```bash
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
# 仅在全部物理门禁通过后导出：
scripts/nm37-a110/run.sh bitstream
```

新增 `hbm-startup` 阶段实际实例化产品中的启动控制与 AXI gate，以独立相位/频率时钟驱动，检查上电、APB 停钟、短异步 reset、延迟/中断初始化、重复 cold reset、故障锁定以及五通道背压/透传；另检查独立监测时钟下的 PCIe 复位、短暂 PLL 失锁、PERST、稳定计数重启和释放边沿。该阶段纳入 `gate`，未通过不能执行正式实现。

布局布线需检查：真实 HBM APB primitive PCLK 的周期/波形、AXI 和 DUT 时钟、HBM reference clock、全局 setup/hold/最小周期/高低脉宽、bus skew、完整路由、DRC、新域 CDC、同步器及复位复制结构/端点时序。不能用忽略检查或放宽负裕量的方式使结果通过。

## 官方接口依据

- [AMD PG276 Port Descriptions](https://docs.amd.com/r/en-US/pg276-axi-hbm/Port-Descriptions)：APB reset 复位 controller/PHY/stack；流量开始后不允许单独 AXI reset。
- [AMD PG276 Non-AXI Ports](https://docs.amd.com/r/en-US/pg276-axi-hbm/Non-AXI-Ports)：AXI 访问必须等待初始化完成。
- [AMD PG276 Performance](https://docs.amd.com/r/en-US/pg276-axi-hbm/Performance)：APB 最高 100 MHz。
- [AMD PG195 XDMA Global Ports](https://docs.amd.com/r/en-US/pg195-pcie-dma/XDMA-Global-Ports) 与 [PG213 Clock and Reset Interface](https://docs.amd.com/r/en-US/pg213-pcie4-ultrascale-plus/Clock-and-Reset-Interface)：XDMA 时钟/复位接口与 PCIe fundamental reset 的定义。

## 验证入口修复

首次诊断发现两项可复现性问题，诊断产物不作为最终通过证据：测试 emitter 没有落盘 `HasBlackBoxInline` 的同步器实现，门禁正确拒绝缺少实现的 RTL；Chisel 自动解析的 firtool 为 1.62.1，与 PATH 中记录的 1.75.0 不同。已加入分文件输出和明确 firtool 路径，正式入口统一设置 `CHISEL_FIRTOOL_PATH` 为已记录工具所在目录，并记录该变量，避免测试发射与板级编译暗用不同版本。

首次正式板级生成还发现 APB ClockSink 未声明频率、广播节点未提供明确频率元数据，读取协商参数时失败。已改为 controller 必须显式接收频率、sink 与广播建立相同的频率契约，并检查一致性；NM37 为 55 MHz，U280 调用点传其 DUT 频率。失败运行 `build` 保留原始日志和身份，修复后使用新目录 `build02`，不覆盖旧通过/失败记录。SBT 运行时 socket 使用短的临时目录，并要求执行环境允许本地 IPC。

第二轮诊断 `build02` 已成功生成板级 RTL，并通过原 HBM 启动测试（8 组时钟、32768 组门控向量、2963789 次检查）。RTL 复核发现审计脚本的同步器层级名未包含 Chisel 自动添加的前缀；已在最终冻结前纠正。复位复核还发现既有 A110 中 DUT PLL 失锁未反馈到 XDMA fundamental reset，本轮增加上述监测模块并扩展测试。正式验收使用随后重新冻结的源码与全新输出，不把诊断通过记录替代最终证据。

第三轮 `build03` 的 PLL 实际输出为 110/55 MHz，估算峰峰抖动分别为 195.800/214.859 ps。APB 的固定频率广播 `FixedClockBroadcast(Some(...))` 会新建默认 200 ps 的 sink，覆盖先连接的板级 230 ps 要求，故入口正确停止。修复恢复普通 `FixedClockBroadcast()`，传递既有板级 55 MHz / 230 ps 契约；控制器在第二轮已显式声明 `take=55 MHz`，因此频率元数据仍完整。该 200 ps 是通用参数默认，不是 PG276 给出的 APB 硬性极限；本轮没有调整 HBM 参考时钟或增加时序例外。最终生成 Tcl 和实际 XCI 均需核对两输出抖动不超过 230 ps，路由时序继续使用工具的实际不确定度。

保留原 Clock Wizard 输入噪声设置：`JITTER_OPTIONS=UI`、`CLKIN1_UI_JITTER=0.010`，在 100 MHz 下相当于 100 ps。虽然 XCI 同时保存 `CLKIN1_JITTER_PS=50`，当前未使用 PS 模式，不能把实际输入假设误记为 50 ps。PG276 的小于 3 ps RMS 规定针对 HBM 参考时钟源，不能与 APB 输出的峰峰值直接比较，见 [HBM Clocking](https://docs.amd.com/r/en-US/pg276-axi-hbm/Clocking)。

## 执行结果

`build03`（源码 `cadd425778028553b75036a436ed6cbbec760399`）通过了下列全部功能检查，但实现停于 PLL IP 生成的抖动预算检查，尚未运行综合、布局或布线。修复后将使用全新 `build04` 重跑全部验收；尚未给出整板 110 MHz 通过结论。

已完成编译、板级 RTL 生成、原单元/调度测试、三项形式证明、独立 HBM/PLL 启动验证和六组功能回归。新增验证实际运行 3362863 次检查：8 组 HBM 时钟、32768 组门控向量、8 次短完成状态撤销、8 次初始化中断、8 组 PLL/PERST 时钟场景和 32 次资格释放。测试用稳定计数参数为 7，生产 RTL 单独核对为 14 位计数、终值 9999，最早在 10003 个板上 100 MHz 上升沿后释放 PCIe reset。数字仿真不证明模拟亚稳态行为或 HBM PHY 初始化成功。

`cmpeq` 为所有组合输入的 SAT 证明；`cmpseq`、`macseq` 为不约束输入/初态的时序归纳，比较一拍流水填充后的算术等价。它们不替代有状态 PE 扫描及板级启动协议测试。

独立对照验证全部阶段封存 SHA、生成器身份、历史原始日志与所比较载荷。相对上一版 A110，板级 15 个关键核心模块 RTL 一致；16×16 仿真全部 224 个 SV 文件一致。规范化仅移除 CIRCT 行号注释，不删除逻辑、标识符、指令或其它注释。六组 workload 的指令、输入、输出、配置、全部 8 项计数器及仿真总周期一致：

| 阵列 / 序列长度 | 原 A110 execTime | APB55 候选 execTime | 仿真总周期（两者相同） |
|---|---:|---:|---:|
| 4×4 / 4 | 368 | 368 | 4707 |
| 4×4 / 8 | 1083 | 1083 | 11857 |
| 8×8 / 8 | 385 | 385 | 4827 |
| 8×8 / 16 | 1100 | 1100 | 11977 |
| 16×16 / 16 | 437 | 437 | 5427 |
| 16×16 / 32 | 1152 | 1152 | 12577 |

这些计数描述 FSA 仿真运行，不包含板级 PCIe/HBM 初始化等待，也不是实测主机吞吐。A100 的对应计算周期此前已与原 A110 验证相同；只有新候选通过 110 MHz 物理验收后，才可按这些周期换算相对 100 MHz 的理论计算时间。
