# FSA NM37 迁移文档

## 概述

将 FSA 加速器从 Alveo U280 迁移到 ICT NM37 板卡（XCVU37P-2L fsvh2892，与 U280 同 die 同封装）。
**主存使用 VU37P 片内 HBM2（8GB 单栈），架构与 U280 完全同构。**

## HBM 参考时钟问题的调查与结论（重要）

VU37P 的 `HBM_REF_CLK_0` **不是专用键合引脚，而是 HBM IP 的普通时钟输入端口**：

1. U280 工程 HBM IP 自动生成的 `hbm_ip.xdc` 仅含 `create_clock -period 10.00 [get_ports HBM_REF_CLK_0]`，
   **没有任何 PACKAGE_PIN 约束**；
2. U280 上该端口由 `AXIHBM.scala:89` 用 **fabric 时钟**（G31/F31 引脚上 IBUFDS 的输出）驱动，FSA 实测跑通；
3. VCU128 官方原理图中 G31/F31（fsvh2892 的 bank 75 dual-purpose 引脚）被用作 **RLDRAM3 数据线**
   （`RLD3_72B_DQ11/DQ14`），VCU128 的 HBM 参考同样不走这对脚；
4. NM37 原理图（`NM37_SCH.pdf`，2022-03-31 版）中 G31/F31 悬空（NC）。

因此 NM37 上 **HBM 参考时钟取自板载 100MHz 差分振荡器** `CLK_100_DDR_P/N`（BH42/BJ42，经 U7 缓冲），
IBUFDS 后的 fabric 时钟同时驱动 harness PLL 与 `HBM_REF_CLK_0`——与 U280 官方路径
（OSC→IBUFDS→BUFG→IP 端口）结构等价，纯缓冲路径、无分频、抖动最优。
（板上另有一路 100MHz `CLK_100_P1/N1` @ AC12/AE12（bank 43）可作备选。）

唯一残留风险：非官方参考引脚路径的抖动裕量需上板验证（HBM 校准一次实验即知；路径结构与 U280 相同，风险很低）。

## 硬件资源

| 资源 | 参数 | 引脚 |
|---|---|---|
| FPGA | xcvu37p-fsvh2892-2L-e | - |
| HBM | 8GB 单栈，AXI_00 256-bit，XSDB 使能（U280 修复 2） | die 内（无引脚） |
| HBM 参考 | 100MHz，fabric 驱动（IBUFDS 路径） | 与系统时钟共用 BH42/BJ42 (DIFF_SSTL12) |
| PCIe EP | XDMA x8 Gen3, PCIE4C_X1Y0, GTY_Quad_227, 256-bit @250MHz, AXI-Lite BAR 32MB @0x0 | refclk AR15/AR14, perstn BF5 (LVCMOS18, 上拉) |
| dut 时钟 | 70MHz（100MHz PLL） | - |

## 工程文件结构

```
fpga/src/main/scala/nm37/
├── Configs.scala               # EmptyNM37Config（fsa16x16, nMemPorts=1, ExtMem base=0）
├── TestHarness.scala           # NM37FPGATestHarness：时钟树 + HBM + AXI 互连（U280 同构）
├── CustomOverlays.scala        # sys_clock(BH42/BJ42) + XDMA overlay(AR15/AR14, PCIE4C_X1Y0)
├── AXIDDR4.scala               # [备查] DDR4 SODIMM MIG 方案（未被引用）
├── AXIDataWidthConverter.scala # [备查] FSA 32B→64B 位宽转换（未被引用）
└── HarnessBinders.scala        # AXI4MemPort tie-off

fpga/fpga-shells/xilinx/nm37/   # board.tcl(part) + ip.tcl + nm37-config.xdc（经 patch 注入）
fpga/patches/nm37/              # fpga-shells-nm37.patch + fsa-nm37.patch（python 侧，增量于 u280 patch）
scripts/apply-nm37-patches.sh   # 幂等补丁脚本（Makefile 的 NM37_PATCHES 钩子）
```

## 架构（与 U280 逐点对应）

```
100MHz BH42/BJ42 ──IBUFDS──┬──► harnessSysPLL ──► dut 70MHz
                           └──► HBM IP HBM_REF_CLK_0（fabric 时钟）

XDMA x8 Gen3 (M_AXI 256b@250MHz) ──CDC──► AXI4Xbar ──Yanker──► HBM AXI_00 (dut 域, 256b)
FSA memNode (32B) ──ILA──► xbar（HBM AXI_00 恰为 32B beat，零转换）
XDMA M_AXI_LITE ──► FSA configNode（指令/性能寄存器）
```

关键点：
- **HBM AXI_00 是 256-bit（32B beat）**，与 FSA 16x16 的 spad 行宽（32B，`BankedSRAM.nSubBanks` 整除约束）
  及 XDMA 256-bit **双双天然匹配**——整条链零位宽转换、零窄突发，这就是 U280 架构的直接复用。
- XDMA 保持 U280 默认（256-bit @250MHz）；不要设 `axiDataBytes=Some(64)`（那是 DDR4 MIG 512-bit 方案的需要）。
- HBM AXI 接口时钟 = dut 70MHz 域（`slaveClockNodes(0) := dutFixedClockNode`），无独立 CDC。

## 构建、烧板与运行

```bash
export RISCV=/home/zhangsi/riscv
export PATH=$HOME/circt/bin:$PATH          # firtool
source /opt/Xilinx_2020.2/Vivado/2020.2/settings64.sh

make -C fpga SUB_PROJECT=nm37 verilog      # elaboration（约 1 分钟）
make -C fpga SUB_PROJECT=nm37 bitstream    # 完整比特流（数小时）
# 产物: fpga/generated-src/chipyard.fpga.nm37.NM37FPGATestHarness.EmptyNM37Config/obj/NM37FPGATestHarness.bit
```

烧板（Vivado Hardware Manager / JTAG；NM37 未配置 flash 编程流程）后，host 侧：

```bash
cd generators/fsa/python
sudo $HOME/.local/bin/uv run --no-sync main.py \
    --seq_q 16 --seq_kv 16 --config EmptyNM37Config --engine FPGA
```

设备探测：nm37 配置会尝试 XDMA device id 0x903f（板上原 Xiangshan 镜像）与 0x9038（本工程镜像）；
若同机还插着 FSA U280（同 0x9038），需 `--fpga_dev xdmaN` 显式指定。

## 已验证（elaboration 级）

- `make -C fpga SUB_PROJECT=nm37 verilog` 通过（清理后全新构建）
- 产物与 U280 完全同构：IP tcl 仅含 hbm(8GB/单栈/XSDB=TRUE)、xdma(x8G3/256b@250/GTY227)、
  xdma 时钟转换 ×2、ila ×2、clk_wiz、shell
- 顶层端口/引脚约束：仅 5 个（BH42/BJ42 sys_clock DIFF_SSTL12、AR15/AR14 xdma_refclk、BF5 prst_n）；
  DDR4 方案的 126 个引脚约束已随方案切换移除
- `FSAConfig.json`：sa_rows=16, sa_cols=16, mem_base=0, spad_size=3072, acc_size=1088
- Verilog 中 mesh_15_15 存在（16x16 阵列）

## 上板实测结果（2026-08-31）

CIV part 重编后烧板成功，16x16 attention 实测通过：

| 指标 | NM37 / U280（三卡实测一致） | 参照（U280 历史成绩） |
|---|---|---|
| MAE | 9.41e-05 | 9.4e-05 |
| MaxErr | 3.11e-04 | 3.1e-04 |
| RelErr | 1.88e-04 | ~2e-04 |
| 指令流 | 32 raw / 4 DMA / 5 MX / 1 fence | 同 |

三卡（2×U280 + 1×NM37，均 16x16 HBM 配置）数值完全一致；python 侧 `EmptyNM37Config`
的 JSON 与 U280 bit 兼容（同为 16x16/mem_base 0/32B beat），一份配置可同时驱动三块板。
多卡场景需 `--fpga_dev xdmaN`（自动探测会报歧义并列出候选，属预期行为）。

**性能观察（待深挖）**：其中一块卡 execTime=59750 cycles（bubble 40529），另两块约
8000 cycles（bubble ~5400），慢约 7.6 倍。三卡 PCIe 均为 Gen3 x8 满速，排除链路因素；
execTime 内 bubble 为 FSA 等待 HBM 读的周期，怀疑慢的那块卡 HBM 处于降级训练状态
（若为 NM37，可能与其非官方参考时钟路径有关）。对当前规模计算影响极小（mxActive 仅
179 cycles，计算占比 ~2%），大 seq workload 前建议先确认 BDF↔物理板映射再定位。

## 待办（后续优化）

1. 确认三卡 BDF↔物理板映射（`lspci -t` / 拔插法），定位慢卡是否为 NM37
2. 若为 NM37 HBM 降速：检查 HBM 校准/温度状态（JTAG XSDB），或试备选参考时钟
   `CLK_100_P1/N1`（AC12/AE12，bank 43 与 HBM PHY 同侧）
3. 时序余量允许时可提高 dutFreqMHz（70 → 100+）或换 fsa32x32
4. flash 编程流程（当前仅 JTAG）

## 附录：DDR4 SODIMM 备选方案（保留代码）

若 HBM 校准失败，可切回 DDR4 MIG 方案（`AXIDDR4.scala` + `AXIDataWidthConverter.scala` 已实现并
通过 elaboration；MIG 512-bit，FSA 路径需 32B→64B 位宽转换，MIG ui_clk 300MHz 需异步 CDC，
126 个 DDR 引脚约束由 Scala 自动生成）。切换方法：恢复 git 历史中对应版本的 `TestHarness.scala`
（HBM 拓扑 ↔ MIG 拓扑），并在 XDMA 配置中加回 `axiDataBytes = Some(64)`。
