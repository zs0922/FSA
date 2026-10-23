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
set -euo pipefail
export NM37_WORK="$HOME/nm37-git-reproduce"
export A70_SOURCE="$NM37_WORK/a70-p3-source"
export A70_OUT="$NM37_WORK/a70-p3-build"
export A70_STAGE=p3
# 原 prepare 使用的冻结驱动，继续保留：
export A70_TOOLS="$NM37_WORK/integration-tools/scripts/nm37-a70"
# 另取新版诊断入口；目录必须不存在，不覆盖旧工具目录：
export NM37_DIAG="$NM37_WORK/integration-tools-recovery-01"
git clone --branch codex/nm37-reproduction --single-branch \
  https://github.com/zs0922/FSA.git "$NM37_DIAG"
git -C "$NM37_DIAG" rev-parse HEAD | tee "$NM37_WORK/recovery-tools-commit.txt"
git -C "$NM37_DIAG" checkout --detach
```

新 `a70_build.py` 在 `scripts/nm37-board` 中运行，通过 `--driver` 调用原驱动。它不替换 drivers.json、不改原通过记录、不搬移产物、不自动清理失败目录。完整新构建才使用新版 `scripts/nm37-a70/run.sh`；已有工程继续使用与原封印完全匹配的旧版。

如果之前已经克隆过 diagnostics 工具，仍可使用上面的新目录获取这次恢复工具；不要覆盖旧目录。`a70_recover_assembly.py` 是独立的归档工具，默认预览，只有显式 `--apply` 才归档失败文件。它不执行编译，也不生成任何阶段成功记录。

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

当前151缺少 assembly.passed.json 时，按下一节处理；还要检查源码下 `.classpath_cache/a70-release.jar`，不能仅根据输出目录为空就认定没有旧JAR。

## 3. assembly 留下日志/JAR但没有通过记录：排查与恢复

### 3.1 排查：这些命令不清理产物

先按 [完整复现环境](git-reproduction.md#2-工具环境先配置一次) 恢复原 Java、Vivado、RISCV 等环境。**归档和重试期间，同一源码/输出不能有仍在执行的 make、sbt、仿真或 Vivado 作业**。先检查下面的进程列表；对可疑 PID 可用 `readlink /proc/PID/cwd` 辅助确认目录。等待相关作业结束，或在原终端正常停止；不要按进程名批量 kill。恢复工具不会替你停止或锁住旧构建作业。

```bash
# 观察当前用户的构建进程；无匹配时返回1是正常的。
ps -u "$(id -u)" -o pid,ppid,stat,etime,args | \
  rg 'make|sbt|java|verilator|simulator|vivado' || true

# 查看错误末尾和关键字，不删除日志。
if test -f "$A70_OUT/logs/assembly.log"; then
  tail -n 120 "$A70_OUT/logs/assembly.log"
  rg -n 'error|ERROR|Exception|Caused by|OutOfMemory|Killed|No space|unresolved|SSL|TLS' \
    "$A70_OUT/logs/assembly.log" | tail -n 60 || true
fi

# 检查实际环境；空间、inode不足也可能导致JAR/封印未完成。
java -version
df -h "$A70_SOURCE" "$A70_OUT" /tmp
df -i "$A70_SOURCE" "$A70_OUT" /tmp
free -h

# 列出两份JAR、日志、完成记录，以及是否已经开始elaborate。
for p in "$A70_SOURCE/.classpath_cache/a70-release.jar" \
         "$A70_OUT/generator.jar" "$A70_OUT/logs/assembly.log" \
         "$A70_OUT/assembly.passed.json" "$A70_OUT/A70" \
         "$A70_OUT/logs/elaboration.log"; do
  if test -e "$p" || test -L "$p"; then ls -ld -- "$p"; fi
done
python3 "$NM37_DIAG/scripts/nm37-board/a70_build.py" --driver "$A70_TOOLS" status
```

| 日志/现场 | 如何处理 |
|---|---|
| 依赖下载超时、TLS、unresolved dependency | 修复本机网络/代理或仓库可达性后重试；保留已下载的依赖，不改依赖版本 |
| Java版本不符、UnsupportedClassVersionError | 恢复规定的 Java 20.0.2，并检查 PATH/JAVA_TOOL_OPTIONS；不要修改源锁放宽版本 |
| `OutOfMemoryError`、`Killed`、退出137 | 检查内存/系统OOM记录，停止其他占用任务；原驱动JVM配置16GiB堆，A70_JOBS不控制assembly的JVM堆 |
| No space / inode耗尽 | 在保留本构建和依赖的前提下腾出空间，或迁移其他无关数据；不要首先清空缓存 |
| `Build drivers changed`、源锁/补丁身份不一致 | 先找回原冻结驱动/源码并通过检查；归档工具会拒绝，不能手改JSON追认当前树 |
| JAR和“success”日志存在但缺封印 | 仍可能在复制、校验或封印时失败/中断；仅凭JAR可打开不能认定成功，必须重新运行assembly |

尚未看到151的 assembly.log 内容，所以不能认定具体是哪种失败；下面两条路线解决的是**失败现场如何保全并重新建立结果链**，环境错误仍须先修复。

### 3.2 路线一：没有任何后续阶段产物时，原地恢复

适用：prepare完整、assembly没有通过记录，且 units/formal/sim4/sim16/elaborate/implement/bitstream 均没有记录、日志或产物。工具先核对原驱动、shell、完整源锁、历史补丁状态；发现任何下游产物会拒绝，并要求走路线二。

```bash
# 每次恢复使用新归档目录，放在源码、旧输出、驱动目录之外。
export A70_RECOVERY="$NM37_WORK/a70-assembly-recovery-$(date -u +%Y%m%dT%H%M%SZ)"

# 第一次只预览：完整源锁校验可能耗时数分钟。
python3 "$NM37_DIAG/scripts/nm37-board/a70_recover_assembly.py" \
  --driver "$A70_TOOLS" --archive "$A70_RECOVERY"

# 上一步返回0且计划符合现场后归档；逐文件复制、SHA校验后才移除原文件。
python3 "$NM37_DIAG/scripts/nm37-board/a70_recover_assembly.py" \
  --driver "$A70_TOOLS" --archive "$A70_RECOVERY" --apply

# 重新编译/打包生成器，由原冻结驱动产生新的assembly完成记录。
python3 "$NM37_DIAG/scripts/nm37-board/a70_build.py" --driver "$A70_TOOLS" assembly
```

归档包括实际存在的 `generator.jar`、`logs/assembly.log`、源码 `.classpath_cache/a70-release.jar`，附原路径、大小、SHA、prepare/安装状态证据和检查日志。**缓存JAR也要隔离**：Make可能认为它已是最新；移走它才能触发sbt重新打包。不会清空源码及HOME下的 `.ivy2`、`.sbt`、Coursier缓存、`.venv`、`target` 或Git子模块。这里是保留增量缓存的失败重试，不宣称从空缓存重新编译。

### 3.3 路线二：已有下游产物，保留整个旧输出，用同一源码和新输出重跑

**对这次151更稳妥的路线**：你已执行过 elaborate，可能已有 `A70/` 或 `logs/elaboration.log`。旧入口允许在assembly缺记录时运行sim/elaborate，并通过 `-o generator.jar` 禁止Make重建JAR；那些下游记录没有绑定当时JAR的SHA。因此不能后来补assembly记录就把它们算作新构建通过。

```bash
export A70_OLD_OUT="$A70_OUT"
export A70_RETRY_ID="$(date -u +%Y%m%dT%H%M%SZ)"
export A70_RETRY_OUT="$NM37_WORK/a70-p3-build-retry-$A70_RETRY_ID"
export A70_RECOVERY="$NM37_WORK/a70-assembly-recovery-$A70_RETRY_ID"

# A70_OUT此时仍指向旧输出，用于验证原构建身份。
python3 "$NM37_DIAG/scripts/nm37-board/a70_recover_assembly.py" \
  --driver "$A70_TOOLS" --archive "$A70_RECOVERY" --new-out "$A70_RETRY_OUT"
python3 "$NM37_DIAG/scripts/nm37-board/a70_recover_assembly.py" \
  --driver "$A70_TOOLS" --archive "$A70_RECOVERY" --new-out "$A70_RETRY_OUT" --apply

# 只有归档成功后才切换输出；原目录全量保留，源码路径不变。
export A70_OUT="$A70_RETRY_OUT"
export A70_JOBS=16
export FSA_PYTHON="$A70_SOURCE/generators/fsa/python/.venv/bin/python"
export PYTHONPATH="$A70_SOURCE/generators/easyfloat"

# 新输出必须重新记录身份、编译JAR；不需要重新下载/安装源码依赖。
python3 "$NM37_DIAG/scripts/nm37-board/a70_build.py" --driver "$A70_TOOLS" prepare
python3 "$NM37_DIAG/scripts/nm37-board/a70_build.py" --driver "$A70_TOOLS" assembly
```

路线二只归档源码的缓存JAR；**旧输出中的日志、JAR、已完成结果均原样保留**。新输出在 prepare 时创建。不要再次 clone A70_SOURCE、重复运行依赖初始化/历史 installer，也不要把旧 `.passed.json` 或旧仿真器/RTL/DCP复制到新输出。随后按第4节重新执行新输出的全部后续阶段；保存旧结果不等于它们可自动继承为这次结果。

两条路线都不会直接调用 `artifacts.py seal` 补记录。如果归档中断，保留 `plan.json` 与已经复制的文件，不覆盖该归档目录；核对原文件/归档SHA后再决定新尝试。若重试再次失败，保留新的日志，修复具体错误后使用新的归档目录重复相应路线。

## 4. 按顺序执行后续阶段

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

若assembly再次留下失败产物，按第3节重新诊断并归档。其他阶段没有通用的“删失败日志即可重试”操作；保留其现场，不能绕过身份和阶段门禁。

## 5. 已完成 sim16 后单独跑序列长度1024

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

本次板测工具回归共101项通过，其中新增9项恢复测试覆盖默认只读、归档字节/SHA保全、下游产物拒绝、旧输出保留、身份变化拒绝、符号链接拒绝和复制中断。入口测试采用独立合成fixture，还覆盖参数、退出状态、依赖、失败残留、驱动身份、输出不覆盖、1024参数/周期预算和逐位比较。没有把fixture结果当成真正的1024 RTL回归，也没有在151运行构建或板测。
