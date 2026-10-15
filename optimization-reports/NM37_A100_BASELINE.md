# NM37 A100 可复现基线

本页是迁移到主仓直接跟踪 FSA/easyfloat 后的复现入口。核心源码快照提交为 `e0cd99af`；本页所在的后续提交补齐板级配置、shell 补丁、验证工具和证据。复现时检出本页所属提交，不能只检出核心迁移提交。

## 配置与验收范围

| 项目 | 固定值 |
|---|---|
| 板卡 / 器件 | NM37 / `xcvu37p_CIV-fsvh2892-2L-e` |
| 顶层 / 配置 | `NM37FPGATestHarness` / `EmptyNM37Config` |
| 阵列 / 数据 | 16×16，FP16 输入、FP32 累加 |
| DUT 时钟 | `fsa.nm37MHz=100`，PLL 输出和时钟元数据共同使用，周期 10 ns |
| PE 流水 / 保守调度 / 控制复制 | `fsa.pePipeline=true` / `fsa.pipelineConservative=false` / `fsa.fanoutSplit=false` |
| 延迟 | PE1、CMP1、scratchpad2、accumulator3；保留 ShiftRightJam |
| 源码默认 | 仍为 70 MHz、PE 流水关闭；必须使用下面的显式入口 |

原 2026-10-13 全布局布线：DUT setup/hold **+0.534/+0.010 ns**，全设计 **+0.049/+0.010 ns**；setup/hold/pulse-width 失败端点均 0，497,497 个可布线网络全部完成，bus skew 最差 +3.142 ns。资源为 LUT 355,692、FF 140,263、DSP 288、RAMB36 60。既有 CDC Critical 30、DRC Warning/Advisory 和部分约束问题仍在，不能称为完整板级签核。

三项算术模块形式证明、五项相关控制测试、算术/PE/调度验证、六项 4/8/16 阵列回归和 seq64 扩展结果已归档。原始验收记录保持原样；其时间点的 `board_signoff=false` 不因后来存在板测记录而追溯改写。

## 主仓保存什么

- [源指纹及配置](../fpga/baselines/nm37-a100/source-lock.json)、[板测配置](../fpga/baselines/nm37-a100/FSAConfig.json)。源指纹固定本基线；以后改 RTL 应建立新的基线记录和验证结果，不能复用旧通过标记。
- [shell 完整增量补丁](../fpga/baselines/nm37-a100/fpga-shells.patch)，从锁定的 `d7c353201c61f1879afdb2fddf3c52283d450147` 恢复测量使用的 55 个 shell 文件。仍保留 shell 子模块；不用提交本地子模块新 commit。
- [复现驱动](../scripts/nm37-a100/run.sh)、[检查门禁](../scripts/nm37-a100/check.py)、[形式证明](../scripts/nm37-a100/verify_equivalence.py)、[仿真检查](../scripts/nm37-a100/verify_sim.py)。实现前要求本次构建的 unit/formal/sim4/sim8/sim16/elaboration 指纹全部匹配。
- [证据目录](nm37-a100-evidence/)：保留原始报告、形式与回归摘要、工具版本、单元日志和板测输入输出；[清单](nm37-a100-evidence/inventory.json)给出每个副本的原始路径与 SHA-256。
- 大型 JAR、DCP、bitstream、完整实验归档不进入 Git；[外部产物清单](nm37-a100-evidence/external-artifacts.json)记录现有本地文件的大小与 SHA-256。它们尚未发布到远端，另一台机器需重新构建，或另行取得归档并校验。源码重建不依赖这些旧文件。

旧 `NM37_M1_*` 文档中的 `nm37-m1-*` 链接属于冻结实验档案，普通 clone 不包含完整档案。新的构建入口不依赖这些目录，也不覆盖它们。复制出来的历史 JSON 中的旧提交号、绝对路径与原始文件名是历史出处，不是新的复现命令。

## 从干净检出恢复

必须使用新的 clone/工作区与独立构建目录。sbt-assembly 曾因输出路径切换删除旧缓存 JAR；不要在保存实验的原工作区运行新的 assembly。独立复制保存的 JAR 不作为 assembly 输出。

```bash
# 将 <A100提交号> 替换为包含本页的提交；尚未 push 时可从本机仓库 clone。
git clone --no-hardlinks /path/to/FSA FSA-A100-repro
cd FSA-A100-repro
git checkout <A100提交号>
git submodule update --init --recursive
python3 scripts/nm37-a100/prepare-shell.py
```

补丁准备工具只接受固定 shell commit，已恢复则校验后退出；发现其他脏改动则停止，不会强行覆盖。不要先重放旧 U280/NM37/M1 补丁系列。普通 Makefile 的自动补丁入口可识别本基线已经应用的 shell 改动，并跳过主仓直接跟踪的核心。

### 环境前提

配置 Chipyard 构建环境、RISC-V 工具链及 `libfesvr`，保证 `RISCV`、`PATH`、编译器和动态库路径正确。仓库已有 `build-setup.sh` 和环境说明；本入口不下载 Vivado 或安装硬件驱动。已测量版本为 Vivado **2020.2_AR75986**（需 CIV 器件/IP 支持及相应许可）、OpenJDK **20.0.2**、firtool **1.75.0**、Verilator **5.022**；精确版本输出见证据目录。工具版本或布局环境变化可能改变 JAR/RTL/布局字节及裕量，必须重新验收，不保证 bitstream 字节相同。

```bash
source /your/Xilinx/Vivado/2020.2/settings64.sh
export RISCV=/your/riscv/toolchain
export PATH=/your/java/bin:/your/firtool/bin:/your/verilator/bin:$RISCV/bin:$PATH
# 使用仓库锁文件创建 Python 环境（Python >=3.13）。
uv sync --frozen --project generators/fsa/python
python3 -m venv /your/venvs/a100-formal
/your/venvs/a100-formal/bin/pip install -r scripts/nm37-a100/formal-requirements.txt
export YOSYS=/your/venvs/a100-formal/bin/yowasp-yosys
export FSA_PYTHON="$PWD/generators/fsa/python/.venv/bin/python"
export A100_OUT=/absolute/new/a100-build
```

### 构建、验证、布局布线

```bash
bash scripts/nm37-a100/run.sh check
bash scripts/nm37-a100/run.sh prepare
bash scripts/nm37-a100/run.sh units
bash scripts/nm37-a100/run.sh formal
for size in 4 8 16; do
  bash scripts/nm37-a100/run.sh sim "$size"
done
bash scripts/nm37-a100/run.sh elaborate
bash scripts/nm37-a100/run.sh implement
bash scripts/nm37-a100/run.sh bitstream
```

`prepare` 将 assembly 缓存复制成独立 `generator.jar`，记录源码/驱动/JAR 身份；后续步骤验证同一份身份。输出目录和已执行阶段拒绝覆盖；失败重试应使用新输出目录。实际 RTL 验证依赖新生成的模块，不使用历史 DCP。形式证明对比旧算术与新增一级流水后的持续输出，系统回归对比 PyEasyFloat；历史旧生成器的 A70 比较结果在归档中，不要求取得已经丢失的旧 JAR。

完整实现生成 `A100/fpga/*/obj/post_route.dcp` 和 `report/`、`audit/`。脚本严格检查 DUT 和全局 setup/hold、脉宽、布线、bus skew、DRC Error/Critical；还需人工复核 CDC 和约束范围与已知问题，不能仅凭工具 exit 0 签核。bitstream 位于 `$A100_OUT/bitstream/NM37_A100.bit`。

### 上板验证

先在 Vivado Hardware Manager 中确认 NM37 CIV 器件与 PCIe 设备映射，加载生成的 bitstream，并完成必要的主机重启/PCIe 枚举和 XDMA 驱动加载。脚本不会自动下载、复位或选择板卡。

```bash
# xdmaN 替换为已确认的 NM37 设备；按设备权限运行，避免误选其他板卡。
timeout 600 "$FSA_PYTHON" scripts/m1-a100-board-test.py \
  --device xdmaN --bitstream "$A100_OUT/bitstream/NM37_A100.bit" \
  --seq 16 --seed 0 --out "$A100_OUT/board/seq16-seed0"
```

继续测试 seq32/64/256/1024，使用新目录；seq1024 的 Python 参考计算可能需要更长 timeout。保存输入、输出、参考值、计数器、配置和所提供 bitstream 的指纹；通过条件为逐位一致和 enq=deq。提供的 bitstream 指纹是操作者声明的下载身份，脚本不能读取硬件确认身份或测频；设备驱动轮询缺超时，因此保留外部 timeout。

## 已发现的板测记录及限制

2026-10-14 保存的 `xdma4`、seed0 记录如下；本次整理重新比较了保存的 `actual.npy` 与 `reference.npy`，没有重新向板卡发送指令。

| seq | execTime（拍） | 按 100 MHz 换算（μs） | 逐位差异 |
|---:|---:|---:|---:|
| 16 | 1,987 | 19.87 | 0 |
| 32 | 3,707 | 37.07 | 0 |
| 64 | 10,098 | 100.98 | 0 |
| 256 | 102,031 | 1,020.31 | 0 |
| 1024 | 1,552,651 | 15,526.51 | 0 |

这五组 enq=deq。记录没有下载时的 bitstream 指纹，也未独立测量 DUT 频率；虽然同目录保存了 A100 bitstream，不能单凭文件存在证明当时加载的就是该文件。换算时间不是主机端墙钟时间，也不可与多端口 RTL 仿真周期直接相减。后续板测工具新增强制 `--bitstream` 参数，改善记录可追踪性。

## 本次归档验收

本次提交不改动算术、调度或时钟源逻辑：核心和板级生成源码与原 A100 测量指纹一致，仅原补丁入口为核心迁移增加了跳过逻辑。验收包含干净 shell 重建的 55 文件哈希、暂存文件导出后的源指纹、证据 SHA-256、保存板测输出、脚本语法与门禁检查。便携形式驱动使用保存的原始 RTL 实际重跑三项证明并通过；便携仿真驱动使用保存的 A100 仿真器重新执行 seq16 并逐位通过。这两项没有重新编译生成器或仿真器。新入口的完整编译/仿真/布局布线尚未从空环境重跑；已有原始完整运行证据不能冒充新入口的端到端测试。原始结果与复现工具的验证范围分别记录。
