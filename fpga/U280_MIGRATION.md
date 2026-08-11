# FSA U280 迁移文档

## 概述

本文档记录了将 FSA (Flash Single-shot Attention) 加速器从 U55C FPGA 板卡迁移到 U280 板卡的完整过程，包括所有关键问题的诊断和修复。

## 迁移成果

FSA 加速器在 Alveo U280 上成功运行，数值精度与 PyTorch 参考实现一致：
- MAE: 9.4e-05
- MaxErr: 3.1e-04
- RelErr: 0.019%

## 工程文件结构

```
fpga/src/main/scala/u280/
├── Configs.scala           # U280 配置（内存基址、FSA 参数）
├── TestHarness.scala       # 测试平台（时钟、复位、AXI 互连）
├── CustomXDMA.scala        # XDMA IP 配置（PCIe、DMA、BAR 参数）
├── CustomOverlays.scala    # XDMA 引脚绑定（PCIe lane、refclk、PERST#）
├── AXIHBM.scala            # HBM 控制器（XSDB、TransferSizes）
├── AXIClockConverter.scala # AXI 时钟域转换器
├── AXIDataWidthConverter.scala # AXI 数据宽度转换器
├── HarnessBinders.scala    # 端口绑定
└── ILA.scala                # 逻辑分析仪调试接口
```

## 关键修复（4 个）

### 修复 1: PCIe4C 硬核 JTAG 编程后不重新初始化

**文件**: `CustomOverlays.scala:93`

**问题**: JTAG 编程新比特流后，PCIe4C 硬核不重新加载配置，仍保留 flash 中 golden image 的旧配置（Device ID 显示 0x500c 而非 0x9038，BAR 内存返回 0xFFFFFFFF）。

**根因**: PCIe4C 的 `sys_rst_n` 仅由外部 PERST# 引脚（BH26）驱动，JTAG 编程不触发 PERST# 变化。

**修复**: 将 power-on reset 加入 sys_rst_n，使 FPGA 启动时自动复位 PCIe4C：
```scala
// 修改前:
b.srstn := shell.pcie_rst_n

// 修改后:
b.srstn := shell.pcie_rst_n && !shell.pllReset
```

**效果**: JTAG 编程后 Device ID 正确变为 0x9038，Memory Space 启用（Mem+），xdma 驱动正常绑定。

---

### 修复 2: HBM DMA 写入超时

**文件**: `AXIHBM.scala:54`

**问题**: XDMA DMA 写入 HBM 超时（10 秒后 abort）。DMA 读取正常返回数据。

**根因**: HBM IP 的 XSDB 调试接口被禁用（`USER_XSDB_INTF_EN = FALSE`），导致 HBM 内部初始化路径不完整。

**修复**: 启用 XSDB 接口：
```scala
// 修改前:
CONFIG.USER_XSDB_INTF_EN {FALSE}

// 修改后:
CONFIG.USER_XSDB_INTF_EN {TRUE}
```

**效果**: DMA 读写均恢复正常。

---

### 修复 3: FSA mem_base 超出 HBM 可访问范围

**文件**: `Configs.scala:8-10`

**问题**: FSA 的内存基址 `mem_base=0x80000000`（2GB）超出 HBM 单 pseudo-channel 的可访问范围（256MB，地址 0x0-0x0FFFFFFF）。写入 0x80000000 导致 HBM 不响应 → AXI 总线死锁 → 所有后续 DMA 操作永久失败（只能 JTAG 重新编程恢复）。

**根因**: HBM 控制器只连接了一个 pseudo-channel（portNum=1），但 `AddressSet` 声称覆盖 16GB。实际只有 256MB 物理可访问。

**修复**: 将 ExtMem 基址改为 0：
```scala
class WithU280HBMMemBase extends Config((site, here, up) => {
  case ExtMem => up(ExtMem, site).map(x => x.copy(master = x.master.copy(base = BigInt(0))))
})
```

**效果**: FSA 数据在地址 0x0 附近，HBM 正常响应。

---

### 修复 4: XDMA IP 配置对齐 nexst 参考工程

**文件**: `CustomXDMA.scala:104-134`

**问题**: XDMA 配置与 U280 板卡和 nexst 参考工程不匹配。

**修复内容**:
| 参数 | 修改前 | 修改后 |
|---|---|---|
| `pcie_extended_tag` | false | **true** |
| `pf0_base_class_menu` | 默认(0x070001) | **Processing_accelerators (0x120000)** |
| `pf0_sub_class_interface_menu` | 未设置 | **Unknown** |
| `xdma_axi_intf_mm` | 默认 | **AXI_Memory_Mapped** |

---

## U280 vs U55C 硬件差异

| 项目 | U55C | U280 |
|---|---|---|
| FPGA | xcu55c-fsvh2892-2L-e | xcu280-fsvh2892-2L-e |
| Board Part | xilinx.com:au55c:part0:1.0 | xilinx.com:au280:part0:1.1 |
| 系统时钟 | F24/F23, 100MHz | BJ43/BJ44, **300MHz** |
| HBM 参考时钟 | BK43/BK44, 100MHz | **G31/F31**, 100MHz (sysclk3) |
| PCIe refclk | AL15/AL14 | **AR15/AR14** |
| PERST# | BF41 | **BH26** |
| PCIe GT Quad | 默认 | **GTY_Quad_227** |
| PCIe Block | PCIE4C_X1Y0 | PCIE4C_X1Y0 |

## 构建命令

```bash
cd fpga
export RISCV=/home/zhangsi/riscv
export PATH=$RISCV/bin:$PATH
source /opt/Xilinx_2020.2/Vivado/2020.2/settings64.sh
make SUB_PROJECT=u280 bitstream
```

## 比特流位置

```
fpga/generated-src/chipyard.fpga.u280.U280FPGATestHarness.EmptyU280Config/obj/U280FPGATestHarness.bit
```

## 运行步骤

1. JTAG 编程比特流（通过 Vivado Hardware Manager）
2. 等待 15 秒（HBM 校准）
3. 验证 PCIe: `lspci -k -s ab:00.0`
4. 验证 BAR: `sudo busybox devmem 0xe2000000`（应返回 0x1FC00006）
5. 运行 FSA:
```bash
cd generators/fsa/python
sudo /home/zhangsi/.local/bin/uv run main.py --seq_q 16 --seq_kv 16 --config EmptyU280Config --engine FPGA
```

## 重要注意事项

- **DMA 失败后必须重新 JTAG 编程**：AXI 总线死锁无法通过驱动重载恢复
- **不要运行 scan-pci.sh 或 PCI remove/rescan**：会改变 BAR 地址导致驱动探测失败
- **系统驱动即可使用**：内核 6.8.0-136 自带的 xdma 驱动支持 0x9038 设备 ID
- **mem_base 必须在 0x0-0x0FFFFFFF 范围内**：HBM 只有一个 pseudo-channel 可用
