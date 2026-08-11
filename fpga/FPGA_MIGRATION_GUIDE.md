# FSA FPGA 板卡迁移指南

> 基于 U55C → U280 迁移实战经验整理，适用于迁移至任意 Xilinx UltraScale+ FPGA 板卡。

---

## 一、迁移流程总览

```
┌─────────────────────────────────────────────────────────────┐
│                    迁移六步法                                │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. 复制工程目录  →  2. 改板卡基础参数  →  3. 改引脚绑定     │
│                                             │                │
│  6. 运行验证   ←   5. 逐层调试   ←   4. 构建比特流          │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

每一层验证通过后再进入下一层，避免多层问题叠加：

```
PCIe 枚举 → BAR 内存访问 → AXI-Lite 寄存器 → DMA 读 → DMA 写 → FSA 计算
```

---

## 二、第 1 步：复制工程目录

### 操作

复制源板卡的 Scala 目录，修改 package 名和类名：

```bash
cp -r fpga/src/main/scala/u55c/ fpga/src/main/scala/<新板卡>/
```

需要修改的文件列表（以 U280 为例）：

| 文件 | 作用 | 必须修改 |
|---|---|---|
| `Configs.scala` | 板卡配置参数 | ✅ |
| `TestHarness.scala` | 时钟、复位、AXI 互连 | ✅ |
| `CustomXDMA.scala` | XDMA IP TCL 参数生成 | ✅ |
| `CustomOverlays.scala` | PCIe 引脚绑定 | ✅ |
| `AXIHBM.scala` | HBM/DDR 控制器 | 视内存类型 |
| `AXIClockConverter.scala` | AXI 时钟域转换 | 通常不用改 |
| `HarnessBinders.scala` | 端口绑定 | 通常不用改 |

### 在 Makefile 中注册新板卡

```makefile
ifeq ($(SUB_PROJECT),<新板卡>)
  SBT_PROJECT       ?= chipyard_fpga
  MODEL             ?= <新板卡>FPGATestHarness
  MODEL_PACKAGE     ?= chipyard.fpga.<新板卡>
  CONFIG            ?= Empty<新板卡>Config
  CONFIG_PACKAGE    ?= chipyard.fpga.<新板卡>
  BOARD             ?= <新板卡>
  FPGA_BRAND        ?= xilinx
endif
```

### 在 fpga-shells 中注册板卡定义

```
fpga/fpga-shells/xilinx/<新板卡>/
├── tcl/
│   ├── board.tcl    # set device 和 board_part
│   └── ip.tcl       # IP 相关设置（通常为空）
└── constraints/
    └── <新板卡>-config.xdc  # CONFIG_VOLTAGE, CFGBVS 等
```

`board.tcl` 示例：
```tcl
set device xcu280-fsvh2892-2L-e
set board_part xilinx.com:au280:part0:1.1
```

---

## 三、第 2 步：修改板卡基础参数

### 3.1 FPGA 器件型号

从 Vivado 板卡文件或板卡文档获取：

```tcl
# board.tcl
set device <FPGA型号>        # 如 xcu280-fsvh2892-2L-e
set board_part <板卡Part>    # 如 xilinx.com:au280:part0:1.1
```

**获取方法**：
```bash
# Vivado 板卡文件位置：
ls /opt/Xilinx_2020.2/Vivado/2020.2/data/boards/board_files/<板卡名>/

# 查看 FPGA 型号和引脚：
cat .../production/*/board.xml | grep -i "part"
cat .../production/*/part0_pins.xml | head -20
```

### 3.2 系统时钟

**关键**：必须从板卡原理图或 `part0_pins.xml` 确认时钟引脚和频率。

```scala
// TestHarness.scala 中的时钟 Overlay
val sys_clock = Overlay(ClockInputOverlayKey, new SysClockShellPlacer(
  this, ClockInputShellInput(),
  "<P_PIN>", "<N_PIN>",    // 差分时钟引脚
  freqMHz = <频率>          // 时钟频率
))
```

| 板卡 | 引脚 | 频率 | 来源 |
|---|---|---|---|
| U55C | F24/F23 | 100 MHz | 板卡手册 |
| U280 | BJ43/BJ44 | **300 MHz** | part0_pins.xml sysclk0 |
| 其他 | 查 part0_pins.xml | 查文档 | — |

> **教训**：U280 的 BJ43/BJ44 是 300MHz，不是 U55C 的 100MHz。频率错误会导致 PLL 无法锁定，整个设计不工作。

### 3.3 HBM/DDR 参考时钟

如果目标板卡使用 HBM：
```scala
val hbm_clock = Overlay(ClockInputOverlayKey, new SysClockShellPlacer(
  this, ClockInputShellInput(),
  "<HBM_REF_CLK_P>", "<HBM_REF_CLK_N>",
  freqMHz = 100  // HBM 参考时钟通常 100MHz
))
```

> **教训**：U280 的 HBM 参考时钟在 G31/F31（sysclk3），不是 U55C 的 BK43/BK44。引脚错误会导致 HBM 无法校准。

### 3.4 PCIe 参考时钟和 PERST#

```scala
// CustomOverlays.scala
val ref = Seq("<REFCLK_P>", "<REFCLK_N>")  // PCIe GT 参考时钟
// PERST# 在 TestHarness.scala 中绑定
outer.xdc.addPackagePin(prst_n, "<PERST_PIN>")
```

> **教训**：这些引脚必须从板卡文件确认。错误会导致 PCIe 链路无法训练。

---

## 四、第 3 步：修改 XDMA IP 配置

### 4.1 核心参数对照表

XDMA IP 配置在 `CustomXDMA.scala` 的 `ElaborationArtefacts.add` 中生成 TCL：

```scala
ElaborationArtefacts.add(s"${desiredName}.vivado.tcl",
  s"""create_ip -vendor xilinx.com -library ip -version 4.1 -name xdma ...
     |set_property -dict [list
     |  CONFIG.functional_mode         {DMA}
     |  CONFIG.mode_selection          {Advanced}
     |  CONFIG.en_gt_selection         {true}
     |  CONFIG.select_quad             {<GTY_Quad>}     // 板卡相关
     |  CONFIG.pcie_blk_locn           {<PCIE_Block>}   // 板卡相关
     |  CONFIG.pl_link_cap_max_link_width  {X<lanes>}   // 板卡相关
     |  ...
     |] [get_ips ${desiredName}]
  """.stripMargin)
```

### 4.2 如何确定 PCIe 参数

| 参数 | 获取方法 |
|---|---|
| `select_quad` | 板卡原理图，PCIe GTY Quad 编号 |
| `pcie_blk_locn` | 板卡 FPGA 的 PCIe 硬核位置 |
| `pl_link_cap_max_link_width` | 板卡 PCIe 插槽物理 lane 数 |
| `axi_data_width` | 根据 lane 数计算：lanes × 250 << (gen-1) / 250 × 8 |

> **重要**：`axi_data_width` 必须与下游 AXI Slave（HBM/DDR）的数据宽度匹配，否则 Chisel diplomacy 会报错。X16 需要 512-bit，X8 需要至少 256-bit。

### 4.3 推荐配置（已验证）

```scala
// U280 已验证配置
CONFIG.functional_mode           {DMA}
CONFIG.mode_selection            {Advanced}
CONFIG.en_gt_selection           {true}
CONFIG.select_quad               {GTY_Quad_227}
CONFIG.pcie_blk_locn             {PCIE4C_X1Y0}
CONFIG.pcie_extended_tag         {true}                    // 推荐开启
CONFIG.pf0_base_class_menu       {Processing_accelerators} // 推荐 0x120000
CONFIG.pf0_msi_enabled           {true}
CONFIG.pf0_msix_enabled          {false}
CONFIG.axilite_master_en         {true}
CONFIG.axilite_master_scale      {Megabytes}
CONFIG.axilite_master_size       {32}
CONFIG.pl_link_cap_max_link_width {X8}                     // 或 X16
CONFIG.pl_link_cap_max_link_speed {8.0_GT/s}               // Gen3
```

---

## 五、第 4 步：构建比特流

### 构建命令

```bash
cd fpga
export RISCV=<riscv工具链路径>
export PATH=$RISCV/bin:$PATH
source <Vivado路径>/settings64.sh
make SUB_PROJECT=<板卡> bitstream
```

### 构建时间

- Chisel/FIRRTL 编译：约 5-10 分钟
- Vivado 综合：约 30-60 分钟
- Vivado 实现+布线：约 60-120 分钟
- 比特流生成：约 5-10 分钟
- **总计：约 2-4 小时**

### 构建检查项

1. **Chisel 编译无 `requirement failed`**：检查 AXI diplomacy 参数匹配
2. **Vivado 时序 WNS ≥ 0**：时序违规会导致功能异常
3. **DRC 无错误**
4. **比特流文件已生成**

### 常见构建错误

| 错误 | 原因 | 修复 |
|---|---|---|
| `requirement failed` | AXI 数据宽度不匹配 | 检查 busBytes 计算和下游 Slave 宽度 |
| `Value '256_bit' is out of range` | X16 模式必须用 512-bit | 与 HBM 宽度协调 |
| 时序 WNS < 0 | 设计太大或频率太高 | 降频或优化设计 |
| `Failed to meet timing` | WNS < -0.1 | 降频或加约束 |

---

## 六、第 5 步：逐层调试（最关键）

### 6.1 调试原则

```
一次只测一个层，确认通过后再测下一层
失败后必须重新 JTAG 编程，不能在死锁状态上重试
```

### 6.2 第 1 层：PCIe 枚举

**验证命令**：
```bash
lspci -vv -d 10ee:
```

**正常结果**：
- Device ID = 我们的设计值（如 0x9038）
- Control: `Mem+`（Memory Space 启用）
- Kernel driver in use: `xdma`

**异常诊断**：

| 现象 | 原因 | 修复 |
|---|---|---|
| Device ID 是 golden image 值 | PCIe4C 未重新初始化 | 加入 power-on reset 到 sys_rst_n |
| Mem- (Memory Space 禁用) | 驱动未正确探测 | 检查驱动 PCI ID 表 |
| `Unknown header type 7f` | PCIe 链路异常 | 重新 JTAG 编程 |
| `[virtual]` 或 `[disabled]` | BAR 状态异常 | 重新 JTAG 编程 |
| 无设备 | PCIe 链路未训练 | 检查 GT refclk 引脚 |

### PCIe4C power-on reset 修复（通用）

**问题**：JTAG 编程后 PCIe4C 硬核不重新加载配置。

**修复**（适用于所有 Xilinx UltraScale+ 板卡）：
```scala
// CustomOverlays.scala 或等价文件中
// 将 power-on reset 加入 PCIe sys_rst_n
b.srstn := shell.pcie_rst_n && !shell.pllReset
```

**原理**：
```
JTAG 编程 → FPGA 架构重新加载 → PowerOnReset 触发
→ pllReset 拉高 → sys_rst_n 拉低 → PCIe4C 完全复位
→ pllReset 释放 → sys_rst_n 拉高 → PCIe4C 用新配置重新初始化
```

### 6.3 第 2 层：BAR 内存访问

**验证命令**：
```bash
# 获取 BAR 地址
lspci -s ab:00.0 -v | grep Region

# 读取 XDMA 标识寄存器（BAR1 offset 0x0）
sudo busybox devmem <BAR1_ADDRESS>
# 应返回 0x1FC00006
```

**异常诊断**：

| 现象 | 原因 | 修复 |
|---|---|---|
| 返回 0xFFFFFFFF | BAR 内存不可访问 | PCIe 链路问题或 PCIe4C 未初始化 |
| 返回其他值 | BAR 映射错误 | 检查 BAR 配置 |

### 6.4 第 3 层：AXI-Lite 寄存器

**验证命令**：
```bash
# 读取 FSA 状态寄存器（BAR0 offset 0x8）
sudo busybox devmem $((<BAR0_ADDRESS> + 0x8))
# 应返回 0x00000000 (idle)

# 写入 SET_ACTIVE（BAR0 offset 0x4）
sudo busybox devmem $((<BAR0_ADDRESS> + 0x4)) 32 0xffffffff

# 再读状态，应变为 0x00000001 (active)
sudo busybox devmem $((<BAR0_ADDRESS> + 0x8))
```

> AXI-Lite 走 BAR0，不经过 HBM。如果这层正常但 DMA 失败，问题在 DMA/HBM 路径。

### 6.5 第 4 层：DMA 读写

**验证命令**：
```bash
# DMA 写入（512 字节到地址 0）
sudo head -c 512 /dev/urandom | sudo timeout 10 dd of=/dev/xdma0_h2c_0 bs=512 count=1

# DMA 读取（32 字节从地址 0）
sudo timeout 10 dd if=/dev/xdma0_c2h_0 of=/tmp/test.bin bs=32 count=1
```

**异常诊断**：

| 现象 | 原因 | 修复 |
|---|---|---|
| 写超时，读正常 | HBM 写路径问题 | 见下方 HBM 调试 |
| 读写都超时 | HBM 未校准或 AXI 死锁 | 重新 JTAG 编程 |
| 读写都正常但数据错误 | 地址映射错误 | 检查 AddressSet 和 mem_base |

### HBM DMA 调试

**问题 1: DMA 写超时，读正常**

可能原因：HBM IP 的 XSDB 调试接口禁用，导致内部初始化不完整。

修复：启用 XSDB 接口：
```scala
// AXIHBM.scala
CONFIG.USER_XSDB_INTF_EN {TRUE}   // 原来是 FALSE
```

**问题 2: 特定地址 DMA 超时**

可能原因：内存基址超出物理可访问范围。

> **教训**：HBM 只连接一个 pseudo-channel（256MB），但 AddressSet 声称 16GB。地址 0x80000000 超出 256MB 范围导致 AXI 死锁。

修复：将 ExtMem base 设为 0：
```scala
// Configs.scala
class WithHBMMemBase extends Config((site, here, up) => {
  case ExtMem => up(ExtMem, site).map(x => x.copy(master = x.master.copy(base = BigInt(0))))
})
```

**问题 3: DMA 失败后所有操作永久失败**

这是 AXI 总线死锁。**唯一恢复方式是重新 JTAG 编程**。驱动重载（rmmod/insmod）无法清除 AXI 死锁。

### 6.6 第 5 层：FSA 计算验证

```bash
cd generators/fsa/python
sudo <python> main.py --seq_q 16 --seq_kv 16 --config <Config> --engine FPGA
```

**正常结果**：
```
Device finished execution
Performance counters:
  Execution time: 5291 cycles
  ...
Comparing with Torch...
Error of FSA vs torch: {'MAE': np.float32(9.4e-05), ...}
```

**异常诊断**：

| 现象 | 原因 | 修复 |
|---|---|---|
| `Device is not idle` | 上次测试遗留状态 | 重新 JTAG 编程 |
| 轮询 state 超时 | FSA DMA 读取 HBM 卡住 | 检查 HBM 路径 |
| 数值误差大 | HBM 数据未正确写入 | 检查 mem_base 和 DMA 写入 |

---

## 七、第 6 步：驱动管理

### 系统驱动 vs 自定义驱动

| | 系统驱动 | 自定义驱动 |
|---|---|---|
| 路径 | `/lib/modules/$(uname -r)/kernel/drivers/dma/xilinx/xdma.ko.zst` | `dma_ip_drivers-master/XDMA/linux-kernel/xdma/xdma.ko` |
| 加载方式 | 自动（modprobe） | 手动（insmod） |
| PCI ID 表 | 可能需要检查 | 自定义添加 |
| 推荐场景 | 日常使用（自动加载） | 调试或系统驱动不支持时 |

### 自定义驱动 PCI ID 添加

如果系统驱动不支持你的 Device ID，需要修改自定义驱动：

```c
// xdma_mod.c 中的 pci_ids 数组
static const struct pci_device_id pci_ids[] = {
    { PCI_DEVICE(0x10ee, 0x9038), },  // 添加你的 Device ID
    { PCI_DEVICE(0x10ee, 0x500c), },
    ...
};
```

### 驱动操作禁忌

- **不要**在 DMA 失败后重载驱动期望恢复——AXI 死锁只能 JTAG 恢复
- **不要**运行 PCI remove/rescan——会改变 BAR 地址
- **不要**同时加载系统驱动和自定义驱动

---

## 八、板卡迁移参数速查表

以下是从 U55C 迁移到各板卡时需要修改的关键参数：

### 时钟引脚

| 板卡 | 系统时钟 P/N | 频率 | HBM/DDR 参考时钟 P/N | 频率 |
|---|---|---|---|---|
| U55C | F24/F23 | 100 MHz | BK43/BK44 | 100 MHz |
| U280 | BJ43/BJ44 | **300 MHz** | G31/F31 (sysclk3) | 100 MHz |
| VCU118 | E8/E7 | 300 MHz | — (DDR4) | — |
| VCU1525 | AB11/AC10 | 300 MHz | — (DDR4) | — |

> **必须从板卡文件确认**：`/opt/Xilinx/Vivado/<版本>/data/boards/board_files/<板卡>/production/*/part0_pins.xml`

### PCIe 参数

| 板卡 | refclk P/N | PERST# | GT Quad | PCIe Block | Lane Width |
|---|---|---|---|---|---|
| U55C | AL15/AL14 | BF41 | 默认 | PCIE4C_X1Y0 | X8 |
| U280 | AR15/AR14 | BH26 | GTY_Quad_227 | PCIE4C_X1Y0 | X8 |

### 内存类型

| 板卡 | 内存类型 | 容量 | AXI 数据宽度 | 注意事项 |
|---|---|---|---|---|
| U55C | HBM2 | 16GB | 256-bit | 2 个 stack |
| U280 | HBM2 | 8GB | 256-bit | **1 个 stack, 单 pseudo-channel 仅 256MB** |
| nexst U280 | DDR4 | — | 512-bit | 用 DDR4 替代 HBM |

---

## 九、调试工具箱

### 快速诊断脚本

```bash
#!/bin/bash
# board_diag.sh - FPGA 板卡快速诊断

echo "=== PCIe ==="
lspci -k -d 10ee: 2>/dev/null

echo "=== BAR ==="
BAR0=$(lspci -d 10ee: -v 2>/dev/null | grep "Region 0:" | grep -oP 'at \K[0-9a-f]+')
BAR1=$(lspci -d 10ee: -v 2>/dev/null | grep "Region 1:" | grep -oP 'at \K[0-9a-f]+')
echo "BAR0=0x$BAR0 BAR1=0x$BAR1"

echo "=== XDMA ID ==="
sudo busybox devmem 0x$BAR1 2>/dev/null

echo "=== FSA State ==="
sudo busybox devmem $((0x$BAR0 + 0x8)) 2>/dev/null

echo "=== DMA Write Test ==="
sudo head -c 512 /dev/urandom | sudo timeout 10 dd of=/dev/xdma0_h2c_0 bs=512 count=1 2>&1 | tail -1

echo "=== DMA Read Test ==="
sudo timeout 10 dd if=/dev/xdma0_c2h_0 of=/tmp/test.bin bs=32 count=1 2>&1 | tail -1

echo "=== dmesg ==="
sudo dmesg | grep -iE "xdma|hbm|error|timeout" | tail -10
```

### 关键 dmesg 消息对照

| 消息 | 含义 | 操作 |
|---|---|---|
| `xdma:probe_one: ... xdma0` | 驱动成功探测 | ✅ 正常 |
| `Failed to detect XDMA config BAR` | BAR 布局不匹配 | 检查 BAR 配置 |
| `status: BUSY` + `transfer_abort` | DMA 超时 | 检查 HBM/AXI 路径 |
| `probe ... failed with error -22` | 探测失败 | 检查 BAR 和设备 ID |

### Vivado Hardware Manager 检查

```bash
# 通过 Vivado 批量模式检查
vivado -mode batch -source - <<'EOF'
open_hw_manager
connect_hw_server -url localhost:3121 -allow_non_jtag
open_hw_target
puts [get_hw_devices]
close_hw_target
disconnect_hw_server
EOF
```

---

## 十、常见陷阱与教训

### 陷阱 1: PCIe4C JTAG 不重新初始化

**症状**：JTAG 编程后 Device ID 不变，BAR 内存返回 0xFFFFFFFF。

**根因**：JTAG 编程不触发 PERST#，PCIe4C 硬核保持旧配置。

**预防**：所有 Xilinx UltraScale+ 板卡都必须加入 power-on reset 到 sys_rst_n。

### 陷阱 2: HBM 可访问地址范围

**症状**：DMA 到低地址正常，到高地址（如 0x80000000）导致 AXI 死锁。

**根因**：HBM 只连接有限数量的 pseudo-channel，每个 256MB。AddressSet 声称的范围可能远大于实际物理可访问范围。

**预防**：确认实际可访问范围，将 mem_base 设为 0x0 或在可访问范围内。

### 陷阱 3: DMA 失败后不可恢复

**症状**：一次 DMA 超时后，所有后续 DMA 操作永久失败。

**根因**：AXI 总线死锁——未完成的 AXI 事务阻塞了后续所有事务。

**预防**：一次 DMA 失败后立即重新 JTAG 编程，不要尝试重试。

### 陷阱 4: 驱动版本冲突

**症状**：系统驱动自动加载，不支持我们的 Device ID，设备节点不创建。

**根因**：`modprobe` 加载系统驱动（`/lib/modules/...`）而非自定义驱动。

**预防**：确认系统驱动是否支持我们的 Device ID。如果不支持，用 `insmod` 加载自定义驱动。

### 陷阱 5: AXI 数据宽度不匹配

**症状**：Chisel 编译报 `requirement failed`。

**根因**：XDMA 的 AXI 数据宽度（由 lane 数计算）与下游 Slave（HBM/DDR）不匹配。

**预防**：确保 XDMA AXI 宽度 = HBM/DDR AXI 宽度。如 X16 需 512-bit，X8 需 256-bit。

### 陷阱 6: 时钟频率错误

**症状**：设计不工作或时序严重违规。

**根因**：不同板卡的系统时钟频率不同（U55C=100MHz, U280=300MHz）。

**预防**：从板卡文件确认时钟频率，不要假设。

---

## 十一、附录

### A. 参考工程

| 工程 | 路径 | 说明 |
|---|---|---|
| U55C 原始设计 | `fpga/src/main/scala/u55c/` | FSA 初始实现 |
| U280 迁移后设计 | `fpga/src/main/scala/u280/` | 成功迁移 |
| nexst 参考工程 | `/home/zhangsi/nexst/shell/u280/` | U280 BD 方式实现 |
| XDMA 驱动 | `~/dma_ip_drivers-master/XDMA/linux-kernel/xdma/` | 自定义驱动源码 |

### B. 关键文件对照

```
Configs.scala         → 板卡配置（内存基址、FSA 参数、时钟频率）
TestHarness.scala     → 测试平台（时钟 Overlay、复位链、AXI 互连拓扑）
CustomXDMA.scala      → XDMA IP 参数（PCIe、DMA、AXI、BAR）
CustomOverlays.scala  → 物理引脚绑定（PCIe lane、refclk、PERST#）
AXIHBM.scala          → HBM IP 配置（密度、通道、XSDB）
```

### C. 构建环境

```bash
# 环境变量
export RISCV=/home/zhangsi/riscv
export PATH=$RISCV/bin:$PATH
source /opt/Xilinx_2020.2/Vivado/2020.2/settings64.sh

# Vivado 版本：2020.2 (AR75986 patch)
# Java: OpenJDK 11
# Kernel: 6.8.0-136-generic
```

### D. 运行命令速查

```bash
# 构建
cd fpga && make SUB_PROJECT=u280 bitstream

# JTAG 编程（通过 Vivado Hardware Manager）
# 比特流：fpga/generated-src/.../obj/U280FPGATestHarness.bit

# 编程后验证（等 15 秒）
lspci -k -d 10ee:
sudo busybox devmem 0xe2000000   # XDMA ID
sudo busybox devmem 0xe0000008   # FSA State

# DMA 测试
sudo head -c 512 /dev/urandom | sudo timeout 10 dd of=/dev/xdma0_h2c_0 bs=512 count=1
sudo timeout 10 dd if=/dev/xdma0_c2h_0 of=/tmp/test.bin bs=32 count=1

# FSA 运行
cd generators/fsa/python
sudo /home/zhangsi/.local/bin/uv run main.py --seq_q 16 --seq_kv 16 \
  --config EmptyU280Config --engine FPGA
```
