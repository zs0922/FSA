# NM37 A110：独立 HBM APB 时钟域

## 基线、目标与验收范围

从实验分支 `codex/nm37-a110-reset` 的 `9ea4c5d7` 继续。上一轮 DUT setup/hold 通过，但 HBM APB PCLK 的最小周期和脉宽失败；不能据此称整板 110 MHz 已通过。本轮只改变板级 HBM 时钟、复位与启动关系，保留 PE/CMP/scratchpad/ACC 的流水和原复位分发复制方案。

执行工作区：`nm37-m1-pipeline/apb110-20261020/src`。大产物输出：同级 `build`。工作区从本机 Git 对象检出已提交的 A110 源码，恢复依赖的固定 gitlink，不拷贝旧生成器、仿真器或物理检查点。第三方工具/缓存可复用；正式运行的输入、工具、生成器及各阶段证据须绑定指纹。主工作区已有的波形分析修改不参与本次构建。

本轮区分三件事：功能验证、全部受约束的布局布线时序验收、实际板卡运行。前两项通过也不能替代上板测试或宣布所有既有 CDC/约束问题已关闭。

## 实施方案

APB 选择 55 MHz，满足所用 HBM IP 的 50–100 MHz 范围，与 DUT 保持 2:1。110/100 MHz 相关时钟的相邻边沿最短仅约 0.909 ns；本轮在实现前根据该关系选择 55 MHz，避免给新增跨域路径施加不必要的紧边沿窗口。没有完成新 APB100 候选的布局布线，因此不能把这项选择描述成 APB100 已经实测失败。正常计算阶段不经过 APB；降低 APB 频率主要影响初始化等待。IP 参数 `USER_APB_PCLK_0` 同步设置为真实输入频率。

- `harnessSysPLL` 在同一 `dutClockGroup` 内产生 DUT/AXI 110 MHz 与 APB 55 MHz。保留两路生成时钟的真实相关关系，不添加两域整组异步时序例外；HBM reference clock 仍来自板上 100 MHz 时钟。
- `LazyXilinxHBMController.apbClockNode` 显式连接 APB 域。暴露硬核 `apb_complete_0`，APB 域寄存后经三级 DUT 域同步，首次完成后才开放 AXI 五通道握手。
- APB PRESET_N 异步断言、三个 APB 上升沿后释放；AXI ARESET_N 等待 APB release 后再经三个 AXI 上升沿释放。共享 ResetWrangler 汇总 PLL lock 丢失及 PCIe reset 原因，运行中必须同时复位主设备、互连和 HBM 整栈，不支持单独 AXI warm reset。
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

新增 `hbm-startup` 阶段实际实例化产品中的启动控制与 AXI gate，以独立相位/频率时钟驱动，检查上电、APB 停钟、短异步 reset、延迟/中断初始化、重复 cold reset、故障锁定以及五通道背压/透传。该阶段纳入 `gate`，未通过不能执行正式实现。

布局布线需检查：真实 HBM APB primitive PCLK 的周期/波形、AXI 和 DUT 时钟、HBM reference clock、全局 setup/hold/最小周期/高低脉宽、bus skew、完整路由、DRC、新域 CDC、同步器及复位复制结构/端点时序。不能用忽略检查或放宽负裕量的方式使结果通过。

## 官方接口依据

- [AMD PG276 Port Descriptions](https://docs.amd.com/r/en-US/pg276-axi-hbm/Port-Descriptions)：APB reset 复位 controller/PHY/stack；流量开始后不允许单独 AXI reset。
- [AMD PG276 Non-AXI Ports](https://docs.amd.com/r/en-US/pg276-axi-hbm/Non-AXI-Ports)：AXI 访问必须等待初始化完成。
- [AMD PG276 Performance](https://docs.amd.com/r/en-US/pg276-axi-hbm/Performance)：APB 最高 100 MHz。

## 验证入口修复

首次诊断发现两项可复现性问题，诊断产物不作为最终通过证据：测试 emitter 没有落盘 `HasBlackBoxInline` 的同步器实现，门禁正确拒绝缺少实现的 RTL；Chisel 自动解析的 firtool 为 1.62.1，与 PATH 中记录的 1.75.0 不同。已加入分文件输出和明确 firtool 路径，正式入口统一设置 `CHISEL_FIRTOOL_PATH` 为已记录工具所在目录，并记录该变量，避免测试发射与板级编译暗用不同版本。

首次正式板级生成还发现 APB ClockSink 未声明频率、广播节点未提供明确频率元数据，读取协商参数时失败。已改为 controller 必须显式接收频率、sink 与广播建立相同的频率契约，并检查一致性；NM37 为 55 MHz，U280 调用点传其 DUT 频率。失败运行 `build` 保留原始日志和身份，修复后使用新目录 `build02`，不覆盖旧通过/失败记录。SBT 运行时 socket 使用短的临时目录，并要求执行环境允许本地 IPC。

## 执行结果

待正式编译、验证及完整布局布线完成后补入绑定执行身份的结果；本节占位不表示已经通过。
