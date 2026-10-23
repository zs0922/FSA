# A70 构建诊断、151 安全更新与 seq=1024

适用已使用 `79db9d9c` 工具执行过 prepare 的151工程，以及后续新构建。更新不改变算术、调度或 FPGA 配置；改善入口诊断，并增加复用仿真器的独立序列测试。

## 为什么原入口只有 sim 4 / sim 16

原 `run.sh sim N` 的 N 是阵列尺寸（4×4或16×16），不是序列长度。原固定回归用两个尺寸分别跑 seq4/8 和 seq16/32。FSA 的序列长度1024没有因此被取消；过去入口缺少明确参数说明、非法参数静默退出，是入口问题。

新前端明确区分：

```text
sim --array-size 16                 固定16×16基线回归（seq16/32）
sim --array-size 16 --seq 1024 ...  复用已封印16×16仿真器，另跑长度1024
```

1024 不意味着1024×1024个PE。额外序列应为阵列尺寸的正整数倍，仿真周期上限与主机外部超时需足够；允许传入1024不等于已经在151执行通过。

## 1. 安全更新：保留旧工具、源码和输出

不要在旧 `integration-tools` 中 pull、checkout 或替换 `scripts/nm37-a70` 文件。prepare 保存了完整 drivers.json；直接修改被冻结的驱动会失去身份匹配。

在151另开一个工具clone，**不重新初始化已有硬件源码，不再次运行 installer，不修改原输出**：

```bash
export NM37_WORK="$HOME/nm37-git-reproduce"
export A70_SOURCE="$NM37_WORK/a70-p3-source"
export A70_OUT="$NM37_WORK/a70-p3-build"
export A70_STAGE=p3
# 原 prepare 使用的冻结驱动，继续保留：
export A70_TOOLS="$NM37_WORK/integration-tools/scripts/nm37-a70"
# 另取新版诊断入口；目录必须不存在，不覆盖旧工具目录：
export NM37_DIAG="$NM37_WORK/integration-tools-diagnostics"
git clone --branch codex/nm37-reproduction --single-branch \
  https://github.com/zs0922/FSA.git "$NM37_DIAG"
git -C "$NM37_DIAG" rev-parse HEAD | tee "$NM37_WORK/diagnostics-commit.txt"
git -C "$NM37_DIAG" checkout --detach
```

新 `a70_build.py` 在 `scripts/nm37-board` 中运行，通过 `--driver` 调用原驱动。它不替换 drivers.json、不改原通过记录、不搬移产物、不自动清理失败目录。完整新构建才使用新版 `scripts/nm37-a70/run.sh`；已有工程继续使用与原封印完全匹配的旧版。

如果已经更新了旧工具目录，先保留现场，在第三个独立clone检出原工具提交（本次通常为79db9d9c），用 `--driver` 指向其 `scripts/nm37-a70`。必须先通过驱动hash核对；不要假定只要Git commit相同就覆盖掉原先可能存在的本地修改。

## 2. 先检查状态，不运行编译

```bash
python3 "$NM37_DIAG/scripts/nm37-board/a70_build.py" \
  --driver "$A70_TOOLS" status
```

含义：

- `记录存在（未重新验收）`：相应JSON存在且能解析；不等于已重新检查全部源文件或功能/物理报告。
- `未开始`：没有对应阶段记录和所检查的产物，可以在前置阶段齐全时执行。
- `不完整/已有产物` 或 `记录损坏`：保留现场，读取日志；不要直接删除日志、重命名passed文件或手工制造成功记录。
- 驱动身份不符：明确停止，恢复正确的冻结驱动，不能改 drivers.json 接受新文件。

当前151缺少 assembly.passed.json 时：若 assembly.log 或 generator.jar 已存在，先查看日志末尾确定失败点；若两者均不存在，才开始 assembly。新前端会自动拦截已有产物，指出路径和恢复原则。

## 3. 只执行尚未开始的阶段

先按原环境文档配置 Java/firtool/Verilator/RISCV/形式工具/Python；仅clone诊断工具不安装编译器。

下面以尚未开始的 assembly 为例：

```bash
python3 "$NM37_DIAG/scripts/nm37-board/a70_build.py" \
  --driver "$A70_TOOLS" assembly
```

其余阶段分别调用，**不要把已完成阶段整段重跑**：

```bash
python3 "$NM37_DIAG/scripts/nm37-board/a70_build.py" --driver "$A70_TOOLS" units
python3 "$NM37_DIAG/scripts/nm37-board/a70_build.py" --driver "$A70_TOOLS" formal
python3 "$NM37_DIAG/scripts/nm37-board/a70_build.py" --driver "$A70_TOOLS" sim --array-size 4
python3 "$NM37_DIAG/scripts/nm37-board/a70_build.py" --driver "$A70_TOOLS" sim --array-size 16
python3 "$NM37_DIAG/scripts/nm37-board/a70_build.py" --driver "$A70_TOOLS" elaborate
python3 "$NM37_DIAG/scripts/nm37-board/a70_build.py" --driver "$A70_TOOLS" implement
# 本次 CDC/约束验收后：
python3 "$NM37_DIAG/scripts/nm37-board/a70_build.py" --driver "$A70_TOOLS" bitstream
```

只有底层命令返回0且相应完成记录存在并可解析，才打印 `COMPLETE: 阶段名`。失败打印退出码、日志位置和相关日志末尾；原 source verify 的 PASS 不是阶段成功。前置记录存在也不取代底层源锁、阶段哈希和物理门禁。

若某阶段留下失败产物，新入口不会覆盖。先保留日志与目录诊断；如果无法在不改写已封印证据的前提下恢复，则使用独立源码和新 A70_OUT 重建。新驱动不能自动继承旧封印，旧成功结果仍作为原构建证据保留。当前没有通用的“删失败日志即可重试”操作。

## 4. 已完成 sim16 后单独跑序列长度1024

须存在有效 sim16.passed.json、sim16/simulator 和对应配置。新前端先用原 artifacts.py check 校验原源/驱动身份，再核对复用的仿真器和配置哈希。

```bash
export FSA_PYTHON="$A70_SOURCE/generators/fsa/python/.venv/bin/python"
# 结果目录与源码、A70_OUT、驱动目录分离；每次使用新名字。
timeout --signal=TERM --kill-after=10s 7200s \
  python3 "$NM37_DIAG/scripts/nm37-board/a70_build.py" \
  --driver "$A70_TOOLS" sim --array-size 16 --seq 1024 \
  --max-cycles 10000000 \
  --result-out "$NM37_WORK/a70-extra-seq1024-attempt01"
```

`--max-cycles` 是 Verilator 的仿真周期预算，外部7200s是主机时间预算，均不是性能测量值或保证足够的时间。可根据实际日志调整，使用新的结果目录重试。原基础回归仍保持原周期上限和原通过记录，不被额外1024替代。

额外输出包含输入、actual/expected、result.json 和 provenance.json，后者绑定原源锁/仿真封印、实际使用的新验证脚本和前端SHA。只有逐位正确且provenance已写出才打印COMPLETE；失败/超时保留已有日志，原sim16目录及封印保持不变。不要把新结果写进原 `sim16/`，否则原阶段文件清单会改变。

本次入口测试采用独立合成fixture，覆盖参数、退出状态、依赖、失败残留、驱动身份、输出不覆盖、1024参数/周期预算和逐位比较。没有把fixture结果当成真正的1024 RTL回归，也没有在151运行构建或板测。
