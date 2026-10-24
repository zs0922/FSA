# NM37：从源码复现 A70-P3、A100、A110/APB55 到板测

本 README 是完整执行入口：获取固定源码 → 安装依赖 → 编译/验证 → 综合布局布线 → 审查本次时序/CDC → 导出新 bit → 烧录 → 正确性/性能测试。下面命令在有 NM37 的 **10.128.157.151** 执行；工具安装路径、JTAG target 和 PCIe 设备必须按该服务器实际情况填写。现有结果不能代替接收方的新构建与板测记录。

**决定从零重建时，直接从本页第1节开始，顺序执行第1–10节，不需要先执行旧工程恢复流程。**使用全新 `NM37_WORK`，新工具、新源码和新输出均在其中；本流程不引用旧失败目录。无需为了得到干净构建而删除旧工程；若自行删除旧工程，其中的日志、本地依赖缓存和已完成结果也会丢失。不要把工具链安装目录、许可证或离线依赖包一并当作构建输出删除。

只有准备继续原构建时才看 [失败诊断与安全恢复](docs/a70-build-recovery.md)，保留原冻结驱动，不在其目录中 `git pull`。两条路线不要混用。

本页已按固定提交的脚本接口核对，但尚未在151从空工作区完整执行通过，不能承诺该机无需环境调整就一次成功。按小节执行，某个阶段非零退出即停下检查；不要整篇一次性粘贴。三个需要人工处理的位置是：第2节填写实际工具安装路径，第3–5节审查本次 routed CDC/约束报告，第8–9节确认真实JTAG器件和PCIe/XDMA映射。验收通过之前不创建验收标签。

只有一个主仓 [`zs0922/FSA`](https://github.com/zs0922/FSA)，以下分支是同一仓库的不同版本。每个版本用独立 clone，避免切换源码时混入上一版本的补丁、JAR 或 RTL。复现分支已发布，**151 完整复现及板测通过后再打验收标签**。

| 用途 | 分支 | 固定硬件 commit |
|---|---|---|
| 当前说明、A70 外置驱动、恢复/板测工具 | `codex/nm37-reproduction` | clone 后记录并固定工具 HEAD |
| A70-P3（历史 P3 优化，70 MHz） | `codex/nm37-a70-p3-source` | `45d859c1c1bdbd282d0c4ac1e465c0fa0b4e296b` |
| A100（100 MHz） | `codex/nm37-a100-source` | `0a3a2656df66043ea513a45bab8275c1be0ba76c` |
| A110/APB55（DUT 110 MHz，HBM APB 55 MHz） | `codex/nm37-a110-apb55-source` | `25673838e66ddef9031748ca40f7220c65acedaf` |

A70-P3 需要安装器将锁定的历史优化补丁应用到上述安装前提交；实际源码身份由 HEAD、安装记录、source-lock 和工具提交共同确定。它与后来的 M1 ShiftRightJam A70 不同。A100/A110 的 FSA/easyfloat 已直接纳入主仓，其余 Chipyard 依赖仍有子模块。

## 1. 建立新工作区、固定工具版本

作用：把复现工具与三个硬件源码目录分开；后续构建期间不更新工具目录。已有同名目录时选择新路径，保留原工程。

```bash
set -euo pipefail
umask 022
export NM37_REPO=https://github.com/zs0922/FSA.git
export NM37_WORK="$HOME/nm37-git-reproduce-fresh"
mkdir "$NM37_WORK"
export NM37_TOOLS="$NM37_WORK/integration-tools"
git clone --branch codex/nm37-reproduction --single-branch "$NM37_REPO" "$NM37_TOOLS"
git -C "$NM37_TOOLS" rev-parse HEAD | tee "$NM37_WORK/tools-commit.txt"
git -C "$NM37_TOOLS" checkout --detach
```

这些变量供后续全部章节使用；换终端后恢复同一组变量。`checkout --detach <commit>` 就是切换到精确版本，不能将文档中的硬件 commit 替换成分支最新 HEAD。

## 2. 配置工具链与共享下载缓存

作用：确定编译/仿真/P&R 使用的工具版本。需要提前安装 Java 20.0.2、firtool 1.75.0、Verilator 5.022、Vivado 2020.2_AR75986（支持 NM37 `xcvu37p_CIV-fsvh2892-2L-e`，具备许可）、RISC-V/libfesvr/libriscv、Git/make、GCC/G++、uv。FSA Python 环境使用 Python 3.13；下方普通 `python3` 是脚本解释器，使用3.10及以上（151现有3.12可以）。历史构建使用 GCC/G++ 13.3.0。原构建脚本给JVM配置16GiB堆，此外仿真C++编译和Vivado还需要内存；并行度按机器资源选择，不能将16GiB误当作整机内存需求。

下面 `/opt/...` 和编译器路径须替换为实际安装位置。源码仓库不包含 Vivado、工具链安装包或许可证；缺少这些工具时先安装，再开始构建。

```bash
source /opt/your-vivado/2020.2/settings64.sh
export RISCV=/opt/your-riscv
export PATH=/opt/java-20.0.2/bin:/opt/firtool-1.75.0/bin:/opt/verilator-5.022/bin:$RISCV/bin:$PATH
export CC=/usr/bin/gcc
export CXX=/usr/bin/g++
export LD_LIBRARY_PATH="$RISCV/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export CHISEL_FIRTOOL_PATH="$(dirname "$(command -v firtool)")"
export XDG_CACHE_HOME="$NM37_WORK/cache"
export XDG_RUNTIME_DIR="$NM37_WORK/runtime"
mkdir -p "$XDG_CACHE_HOME" "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"
export JAVA_TOOL_OPTIONS='-Dsbt.supershell=false -Dsbt.server.forcestart=false -Dsbt.server.autostart=false'
unset JDK_JAVA_OPTIONS _JAVA_OPTIONS
{
  java -version
  firtool --version
  verilator --version
  vivado -version
  "$CC" --version
  "$CXX" --version
  uv --version
} > "$NM37_WORK/tool-versions.txt" 2>&1
cat "$NM37_WORK/tool-versions.txt"
# 在下载硬件依赖和创建构建输出之前检查，避免到prepare才发现版本不符。
python3 - <<'PY'
import os, subprocess, sys
from pathlib import Path
assert sys.version_info >= (3, 10), 'python3 must be >= 3.10'
versions = (Path(os.environ['NM37_WORK']) / 'tool-versions.txt').read_text()
for expected in ['2020.2_AR75986', '20.0.2', 'firtool-1.75.0', 'Verilator 5.022']:
    assert expected in versions, 'Required tool version missing: ' + expected
for key, command in [('CC', os.environ['CC']), ('CXX', os.environ['CXX']), ('PATH g++', 'g++')]:
    version = subprocess.check_output([command, '-dumpfullversion'], text=True).strip()
    assert version == '13.3.0', f'{key}: expected 13.3.0, got {version}; set the actual compiler path'
print('PASS: documented tool versions; this does not test Vivado licensing or FPGA device support')
PY
test -s "$RISCV/lib/libfesvr.a"
test -s "$RISCV/lib/libriscv.so"
python3 -m venv "$NM37_WORK/formal-env"
"$NM37_WORK/formal-env/bin/pip" install -r "$NM37_TOOLS/scripts/nm37-a100/formal-requirements.txt"
export YOSYS="$NM37_WORK/formal-env/bin/yowasp-yosys"
"$YOSYS" -V
```

A100/A110 的 prepare 会检查上述四个主要工具版本；A70 记录工具版本，也须使用同一环境。Maven/sbt、uv 和 Git 依赖首次下载需要网络或预备缓存。缓存可以保留和复用，已生成的 JAR/RTL/仿真器/DCP 必须由本次对应源码产生。

## 3. A70-P3：下载源码、应用优化、验证、生成 bit

### 3.1 下载固定源码和依赖，安装历史优化

`init_git_dependencies.py` 按锁定 commit 初始化依赖；不能用任意最新子模块替代。不要执行不加选择的递归 submodule update。A70 初始化后再运行安装器，顺序不可交换。

```bash
export A70_TOOLS="$NM37_TOOLS/scripts/nm37-a70"
export A70_SOURCE="$NM37_WORK/a70-p3-source"
export A70_OUT="$NM37_WORK/a70-p3-build"
export A70_STAGE=p3
export A70_JOBS=16
git clone --branch codex/nm37-a70-p3-source --single-branch "$NM37_REPO" "$A70_SOURCE"
git -C "$A70_SOURCE" checkout --detach 45d859c1c1bdbd282d0c4ac1e465c0fa0b4e296b
python3 "$NM37_TOOLS/scripts/nm37-board/init_git_dependencies.py" \
  --root "$A70_SOURCE" --profile A70-P3 --report "$NM37_WORK/a70-dependencies.json"
# drycheck 检查安装前提；install 应用锁定补丁；verify 校验最终树。
python3 "$A70_TOOLS/install_historical.py" drycheck --root "$A70_SOURCE" --stage p3
python3 "$A70_TOOLS/install_historical.py" install --root "$A70_SOURCE" --stage p3
python3 "$A70_TOOLS/install_historical.py" verify --root "$A70_SOURCE" --stage p3
uv sync --frozen --python 3.13 --project "$A70_SOURCE/generators/fsa/python"
export FSA_PYTHON="$A70_SOURCE/generators/fsa/python/.venv/bin/python"
export PYTHONPATH="$A70_SOURCE/generators/easyfloat"
```

上游网络不可用时，可给依赖初始化命令追加 `--bundles /实际路径/migration-source-deps`，使用已校验的离线依赖包。不要在已经安装补丁的 A70 树上重复初始化。下载失败保留现场，诊断连接/认证，不改 pin 绕过。

### 3.2 逐阶段编译与验证

新前端区分源检查与阶段完成；只有返回 0 且出现对应 `COMPLETE`/通过记录才能继续。一个阶段失败即停止，按 [恢复文档](docs/a70-build-recovery.md) 排查，不补写 passed 文件。

```bash
# prepare：冻结源码、驱动和 shell 身份；本阶段尚未编译 JAR。
python3 "$NM37_TOOLS/scripts/nm37-board/a70_build.py" --driver "$A70_TOOLS" prepare
# assembly：编译 Chisel/Scala 生成器，保存 generator.jar 和 assembly.passed.json。
python3 "$NM37_TOOLS/scripts/nm37-board/a70_build.py" --driver "$A70_TOOLS" assembly
# units：定向功能回归并生成 formal RTL；formal：针对 P3 算术窗口做等价证明。
python3 "$NM37_TOOLS/scripts/nm37-board/a70_build.py" --driver "$A70_TOOLS" units
python3 "$NM37_TOOLS/scripts/nm37-board/a70_build.py" --driver "$A70_TOOLS" formal
# sim：编译并运行 Verilator。4/16 是阵列尺寸，不是序列长度。
python3 "$NM37_TOOLS/scripts/nm37-board/a70_build.py" --driver "$A70_TOOLS" sim --array-size 4
python3 "$NM37_TOOLS/scripts/nm37-board/a70_build.py" --driver "$A70_TOOLS" sim --array-size 16
```

固定回归分别为 4×4 的 seq4/8 和 16×16 的 seq16/32，要求结果逐位一致。**序列长度 1024 仍支持**；不能写成 `run.sh sim 1024`。在 sim16 通过后，用已校验的同一仿真器额外运行1024，结果存到原构建目录以外，保留原 sim16 封印：

```bash
timeout --signal=TERM --kill-after=10s 7200s \
  python3 "$NM37_TOOLS/scripts/nm37-board/a70_build.py" --driver "$A70_TOOLS" \
  sim --array-size 16 --seq 1024 --max-cycles 10000000 \
  --result-out "$NM37_WORK/a70-extra-seq1024-attempt01"
```

`--max-cycles` 是仿真周期预算，7200s 是主机时间预算；均不代表运行性能，超时应保留输出并另取新目录重试。定向/形式验证的覆盖范围不是完整 FSA 调度证明；seq1024 的入口测试也不等于实际 RTL 已执行通过。

### 3.3 生成 FPGA RTL、完整综合布局布线、审查并导出

```bash
# elaborate：由已封印 JAR 生成 NM37 FPGA RTL、配置 JSON、IP Tcl。
python3 "$NM37_TOOLS/scripts/nm37-board/a70_build.py" --driver "$A70_TOOLS" elaborate
# implement：运行 Vivado 综合、布局、布线及物理检查，保存 routed DCP/报告。
python3 "$NM37_TOOLS/scripts/nm37-board/a70_build.py" --driver "$A70_TOOLS" implement
```

先检查 `$A70_OUT/physical.passed.json`、`implement.passed.json`、`implementation-generated.passed.json` 和本次 `A70/fpga/*/obj/{report,audit}/`。在源码复现文档的 [A70 CDC/约束复核及导出](docs/git-reproduction.md#a70-cdc约束复核及导出) 中有生成待审模板的完整命令。也可直接在此生成同一模板：

```bash
python3 - <<'PY'
import os, json, hashlib
from pathlib import Path
out = Path(os.environ['A70_OUT'])
obj, = list((out / 'A70/fpga').glob('*/obj'))
def sha(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''): h.update(chunk)
    return h.hexdigest()
names = ['post_route.dcp', 'audit/cdc.txt', 'audit/check_timing.txt',
         'audit/clock_interaction.txt', 'audit/exceptions.txt']
review = {'accepted_for_board_test': False, 'board_tested': False,
          'source_lock_sha256': sha(out / 'source-lock.json'),
          'analysis': '', 'known_boundaries': [],
          'evidence_sha256': {n: sha(obj / n) for n in names}}
with (out / 'cdc-constraint-review.pending.json').open('x') as f:
    json.dump(review, f, indent=2)
PY
```

**此处需要人工审查本次 routed 报告。**核对全板 setup/hold/pulse、DRC、无约束端点、时钟关系、复位释放和 CDC 结构，填写真实分析、边界、审查者和证据。确认可进入受控板测后，保存 `$A70_OUT/cdc-constraint-review.json`，其中 `accepted_for_board_test=true`；`board_tested` 此时仍为 false。不能只改布尔值、复制旧报告 hash 或忽略实际 CDC 错误。

```bash
# bitstream：重新检查阶段/CDC门禁，通过后由本次 routed DCP 导出新 bit。
python3 "$NM37_TOOLS/scripts/nm37-board/a70_build.py" --driver "$A70_TOOLS" bitstream
sha256sum "$A70_OUT/bitstream/NM37_A70.bit"
```

## 4. A100：独立源码、验证、完整 P&R 和 bit

作用：固定100MHz硬件版本，重新生成该版本的所有产物。A100 的 `prepare` **已经包含 assembly**，不同于 A70 的两步入口。先串行完成 A70 再执行本节，避免同时占满内存/许可。

```bash
export A100_SOURCE="$NM37_WORK/a100-source"
export A100_OUT="$NM37_WORK/a100-build"
export A100_JOBS=8
git clone --branch codex/nm37-a100-source --single-branch "$NM37_REPO" "$A100_SOURCE"
git -C "$A100_SOURCE" checkout --detach 0a3a2656df66043ea513a45bab8275c1be0ba76c
python3 "$NM37_TOOLS/scripts/nm37-board/init_git_dependencies.py" \
  --root "$A100_SOURCE" --profile A100 --report "$NM37_WORK/a100-dependencies.json"
cd "$A100_SOURCE"
# 恢复指定 shell 补丁；安装此版本的 Python 参考/调度环境。
python3 scripts/nm37-a100/prepare-shell.py
uv sync --frozen --python 3.13 --project generators/fsa/python
export FSA_PYTHON="$A100_SOURCE/generators/fsa/python/.venv/bin/python"
export PYTHONPATH="$A100_SOURCE/generators/easyfloat"
bash scripts/nm37-a100/run.sh check       # 核对硬件源码和 shell。
bash scripts/nm37-a100/run.sh prepare     # 冻结来源、记录工具并编译 JAR。
bash scripts/nm37-a100/run.sh units       # 流水/控制调度定向回归。
bash scripts/nm37-a100/run.sh formal      # 对生成的 formal RTL 运行等价证明。
bash scripts/nm37-a100/run.sh sim 4       # 4×4，seq4/8。
bash scripts/nm37-a100/run.sh sim 8       # 8×8，seq8/16。
bash scripts/nm37-a100/run.sh sim 16      # 16×16，seq16/32。
bash scripts/nm37-a100/run.sh elaborate  # 生成 NM37 FPGA RTL/IP 配置。
bash scripts/nm37-a100/run.sh implement  # 完整综合布局布线和物理验收。
```

要求 units/formal/sim 对应记录及 `physical.passed.json` 通过，并人工复核本次 `A100/fpga/*/obj/audit/` 的 CDC/约束报告，记录审查结论。物理门禁通过不等于已经板测。完成后导出：

```bash
bash "$A100_SOURCE/scripts/nm37-a100/run.sh" bitstream
sha256sum "$A100_OUT/bitstream/NM37_A100.bit"
```

## 5. A110/APB55：增加跨时钟启动验证与 CDC 验收

作用：重建 DUT110/APB55 版本，保持本版本的固定 PE 流水深度。与 A100 相同，`prepare` 包含 JAR 编译；增加 `hbm-startup` 检查复位释放和跨时钟启动。

```bash
export A110_SOURCE="$NM37_WORK/a110-source"
export A110_OUT="$NM37_WORK/a110-build"
export A110_JOBS=8
git clone --branch codex/nm37-a110-apb55-source --single-branch "$NM37_REPO" "$A110_SOURCE"
git -C "$A110_SOURCE" checkout --detach 25673838e66ddef9031748ca40f7220c65acedaf
python3 "$NM37_TOOLS/scripts/nm37-board/init_git_dependencies.py" \
  --root "$A110_SOURCE" --profile A110-APB55 --report "$NM37_WORK/a110-dependencies.json"
cd "$A110_SOURCE"
python3 scripts/nm37-a110/prepare-shell.py
uv sync --frozen --python 3.13 --project generators/fsa/python
export FSA_PYTHON="$A110_SOURCE/generators/fsa/python/.venv/bin/python"
export PYTHONPATH="$A110_SOURCE/generators/easyfloat"
bash scripts/nm37-a110/run.sh check
bash scripts/nm37-a110/run.sh prepare
bash scripts/nm37-a110/run.sh units
bash scripts/nm37-a110/run.sh formal
bash scripts/nm37-a110/run.sh hbm-startup # 验证跨时钟启动和复位关系。
bash scripts/nm37-a110/run.sh sim 4
bash scripts/nm37-a110/run.sh sim 8
bash scripts/nm37-a110/run.sh sim 16
bash scripts/nm37-a110/run.sh elaborate
bash scripts/nm37-a110/run.sh implement
```

A110 可能已完成布局布线，但因 CDC 审查未完成而让 implement 返回非零。此时保留现场，检查 `$A110_OUT/cdc-review.required.json`；不要重复 implement 覆盖 DCP。人工逐项复核本次 finding，在 `cdc-review.json` 填写 reviewer、classification、rationale、evidence，保留 build/checkpoint/CDC SHA 绑定。具体分类由该固定版本的 `scripts/nm37-a110/physical_audit.py` 定义，真实 CDC 缺陷必须修复，不能作为识别问题豁免。

```bash
# 审查完成后重跑物理验收，不重跑布局布线；通过后才导出 bit。
bash "$A110_SOURCE/scripts/nm37-a110/run.sh" physical
bash "$A110_SOURCE/scripts/nm37-a110/run.sh" bitstream
sha256sum "$A110_OUT/bitstream/NM37_A110.bit"
```

## 6. 检查和保存本次构建证据

每个阶段必须命令返回0并有预期结果，不能把“源文件校验 PASS”或存在一个 JAR/DCP 当成阶段成功。STA 要检查整板，不只 DUT WNS。此流程重建全部网表，不走旧 DCP 的 reaudit 导出路线。

令 `TOP=chipyard.fpga.nm37.NM37FPGATestHarness.EmptyNM37Config`：

| 配置 | 本次 bit | 对应配置 JSON | routed DCP/时序/资源/CDC |
|---|---|---|---|
| A70-P3 | `$A70_OUT/bitstream/NM37_A70.bit` | `$A70_OUT/A70/fpga/$TOP/$TOP.FSAConfig.json` | 同目录 `obj/post_route.dcp`、`obj/report/`、`obj/audit/` |
| A100 | `$A100_OUT/bitstream/NM37_A100.bit` | `$A100_OUT/A100/fpga/$TOP/$TOP.FSAConfig.json` | 同上 |
| A110/APB55 | `$A110_OUT/bitstream/NM37_A110.bit` | `$A110_OUT/A110/fpga/$TOP/$TOP.FSAConfig.json` | 同上 |

保留三个完整 OUT、工具 commit/版本、依赖报告、实际源码差异，以及 A70 源码中的 `.nm37-a70/` 安装记录。额外 seq1024 结果单独保存。失败日志也是证据，不删除来制造“全部通过”。重新构建使用新输出目录和新 build_id。

## 7. 将自己生成的三份新 bit 组成板测包

作用：将本次 bit、各自配置、来源和验收元数据与**同一套板测软件**绑定。仅在三条构建全部验收/导出后执行。完整大体积 OUT 另行归档，板测包不默认包含工具链和完整 DCP。

```bash
export NM37_REBUILT_BOARD="$NM37_WORK/rebuilt-board"
export NM37_BUILD_BATCH="151-rebuild-$(date -u +%Y%m%dT%H%M%SZ)"
python3 - <<'PY'
import os, json, hashlib, subprocess
from pathlib import Path
work, tools = Path(os.environ['NM37_WORK']), Path(os.environ['NM37_TOOLS'])
top = 'chipyard.fpga.nm37.NM37FPGATestHarness.EmptyNM37Config'
def item(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''): h.update(chunk)
    return {'path': str(p), 'sha256': h.hexdigest()}
configs, attachments = [], []
for cfg, prefix in [('A70-P3', 'A70'), ('A100', 'A100'), ('A110-APB55', 'A110')]:
    source, out = Path(os.environ[prefix + '_SOURCE']), Path(os.environ[prefix + '_OUT'])
    commit = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    configs.append({'configuration_id': cfg, 'hardware_source_commit': commit,
        'build_id': os.environ['NM37_BUILD_BATCH'] + '-' + cfg, 'source_identity_kind': 'built_commit',
        'bitstream': item(out / 'bitstream' / ('NM37_' + prefix + '.bit')),
        'config': item(out / prefix / 'fpga' / top / (top + '.FSAConfig.json'))})
    for p in sorted(list(out.glob('*.json')) + list((out / 'logs').glob('*.json'))):
        attachments.append(dict(**item(p), destination='evidence/' + cfg + '/' + p.relative_to(out).as_posix()))
    if prefix == 'A70':
        for p in sorted((source / '.nm37-a70').glob('*.json')):
            attachments.append(dict(**item(p), destination='evidence/' + cfg + '/install/' + p.name))
for name in ['tools-commit.txt', 'tool-versions.txt', 'a70-dependencies.json', 'a100-dependencies.json', 'a110-dependencies.json']:
    attachments.append(dict(**item(work / name), destination='evidence/reproduction/' + name))
spec = {'schema_version': 1, 'software_root': str(tools), 'configurations': configs, 'attachments': attachments}
with (work / 'rebuilt-board-spec.json').open('x') as f: json.dump(spec, f, indent=2)
PY
python3 "$NM37_TOOLS/scripts/nm37-board/package.py" build \
  --spec "$NM37_WORK/rebuilt-board-spec.json" --out "$NM37_REBUILT_BOARD" \
  --archive "$NM37_WORK/rebuilt-board.tar.gz"
python3 "$NM37_REBUILT_BOARD/board-tools/package.py" verify --root "$NM37_REBUILT_BOARD"
sha256sum "$NM37_WORK/rebuilt-board.tar.gz" > "$NM37_WORK/rebuilt-board.tar.gz.sha256"
```

打包的文件校验不代替 P&R/CDC 审查。A70 的 manifest HEAD 是安装前提交，安装记录与 source-lock 共同说明实际优化源码。不要将旧构建的 build_id/bit/配置混入新包。

## 8. 环境检查与烧录新 bit

作用：建立本次板测批次，检查 XDMA/JTAG，手动加载第7节的**新 bit**。这一节需要真实 NM37，前面的工具测试不会访问板卡。

```bash
export NM37_BOARD="$NM37_REBUILT_BOARD"
export NM37_RESULTS="$HOME/nm37-results/rebuilt-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$NM37_RESULTS/programming"
cd "$NM37_BOARD"
python3 board-tools/package.py verify --root .
uv sync --frozen --python 3.13 --project software/generators/fsa/python
export FSA_PYTHON="$NM37_BOARD/software/generators/fsa/python/.venv/bin/python"
# 防止之前某个硬件版本的 Python 模块路径混入统一板测环境。
unset PYTHONPATH
uv pip freeze --python "$FSA_PYTHON" > "$NM37_RESULTS/python-packages.txt"
{
  date -u
  hostname
  uname -a
  cat /etc/os-release
  vivado -version
  uv --version
  "$FSA_PYTHON" --version
  lspci -Dnn
  modinfo xdma
  ls -l /dev/xdma*
} > "$NM37_RESULTS/environment.txt" 2>&1
vivado -mode gui -log "$NM37_RESULTS/programming/vivado.log" \
  -journal "$NM37_RESULTS/programming/vivado.jou" &
```

GUI 在后台启动，保留当前终端的环境变量供第9节使用。先停止其他访问同一板卡的程序，在 Vivado Hardware Manager 的 Tcl Console 执行。将两个中文占位符替换成确认过的真实 target/器件；保留板卡序列号、编程时间、日志和 SHA。

```tcl
open_hw_manager
connect_hw_server
get_hw_targets
open_hw_target [get_hw_targets {实际完整target名称}]
get_hw_devices
set dev [get_hw_devices {实际器件名}]
current_hw_device $dev
set_property PROGRAM.FILE [file join $::env(NM37_BOARD) bitstreams A70-P3.bit] $dev
program_hw_devices $dev
refresh_hw_device $dev
```

依次测试 A70-P3、A100、A110-APB55。换配置时把 Tcl 中 `A70-P3.bit` 改为相应文件，重新 program/refresh，再重复第9节；不能只更改终端 CFG。以上是易失 FPGA 配置，不写 Flash。枚举异常需按151实际环境恢复 PCIe/XDMA，再确认设备；不提供猜测的解绑/卸载命令。

## 9. 每个配置：设备确认、smoke、统一性能测试

下面以已烧录 A70-P3 为例；`DEVICE`/`BDF` 是占位示例，必须按当前板卡确认。每次换 bit 重新检查，XDMA 编号可能变化。

```bash
lspci -Dnn -d 10ee:9038
ls -l /dev/xdma* /sys/class/xdma/
export CFG=A70-P3
export DEVICE=xdma0
export BDF=0000:81:00.0
readlink -f "/sys/bus/pci/devices/$BDF/driver"
cd "$NM37_BOARD"
export BIT_SHA=$(sha256sum "bitstreams/$CFG.bit" | cut -d ' ' -f1)
export CFG_RESULTS="$NM37_RESULTS/$CFG"
mkdir "$CFG_RESULTS"
{
  date -u
  echo "configuration=$CFG device=$DEVICE bdf=$BDF"
  sha256sum "bitstreams/$CFG.bit"
  lspci -Dnn -s "$BDF"
  readlink -f "/sys/bus/pci/devices/$BDF/driver"
  ls -l "/dev/${DEVICE}_user" "/dev/${DEVICE}_h2c_0" "/dev/${DEVICE}_c2h_0"
} > "$CFG_RESULTS/device-and-bit.txt" 2>&1
cp "$CFG.manifest.json" "$CFG_RESULTS/"
# dry-run 只校验身份/参数，不访问硬件，不代表通过板测。
python3 board-tools/board_test.py --manifest "$CFG.manifest.json" \
  --device "$DEVICE" --bdf "$BDF" --loaded-bitstream-sha256 "$BIT_SHA" \
  --seq 16 --seed 0 --out "$CFG_RESULTS/preflight" --dry-run
# 一次小规模真实测试；管道失败也保存退出码。
if timeout --signal=TERM --kill-after=10s 600s "$FSA_PYTHON" board-tools/board_test.py \
  --manifest "$CFG.manifest.json" --device "$DEVICE" --bdf "$BDF" \
  --loaded-bitstream-sha256 "$BIT_SHA" --seq 16 --seed 0 --warmup 0 --repeat 1 \
  --out "$CFG_RESULTS/smoke-seq16" 2>&1 | tee "$CFG_RESULTS/smoke-seq16.console.log"; then
  NM37_RC=0
else
  NM37_RC=$?
fi
printf '%s\n' "$NM37_RC" > "$CFG_RESULTS/smoke-seq16.exit-code.txt"
test "$NM37_RC" -eq 0
"$FSA_PYTHON" - "$CFG_RESULTS/smoke-seq16/result.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
assert r['passed'] is True and r['hardware_tested'] is True
print('Hardware smoke test PASS')
PY
# 相同 seq/seed/软件；预热1次、正式20次。每个输出目录必须是新目录。
for SEQ in 16 32 64 256 1024; do
  if timeout --signal=TERM --kill-after=10s 3600s "$FSA_PYTHON" board-tools/board_test.py \
    --manifest "$CFG.manifest.json" --device "$DEVICE" --bdf "$BDF" \
    --loaded-bitstream-sha256 "$BIT_SHA" --seq "$SEQ" --seed 0 --warmup 1 --repeat 20 \
    --out "$CFG_RESULTS/seq${SEQ}-seed0" \
    2>&1 | tee "$CFG_RESULTS/seq${SEQ}-seed0.console.log"; then
    NM37_RC=0
  else
    NM37_RC=$?
  fi
  printf '%s\n' "$NM37_RC" > "$CFG_RESULTS/seq${SEQ}-seed0.exit-code.txt"
  test "$NM37_RC" -eq 0 || break
done
```

要求真实三个字符设备映射到给定 BDF、驱动为 xdma、用户有访问权限；板测脚本会核对映射。`--loaded-bitstream-sha256` 是操作者对已烧录文件的声明，不是硬件读回。任何失败/超时立即停止后续测试，保存现场；timeout 不证明 FPGA 已停。复位/重新烧录并确认设备后，用新批次重试，不覆盖旧结果。A110 失锁/冷复位后重新初始化和装载数据。

## 10. 汇总时间、加速比、bubble，保存交付证据

三配置第9节全部完成后，使用包内同版本汇总器。它不访问硬件；只接受同一批次三配置的完整、可比、逐位通过记录。`--min-repeat 20` 排除单次 smoke。

```bash
"$FSA_PYTHON" "$NM37_BOARD/board-tools/summarize_results.py" \
  --results "$NM37_RESULTS" --min-repeat 20 --out "${NM37_RESULTS}-summary"
cat "${NM37_RESULTS}-summary/comparison.md"
# 关闭 Vivado 后归档完整原始结果和汇总；失败时仍保留原始结果。
cp "$NM37_BOARD/board-tools/summarize_results.py" "${NM37_RESULTS}-summary/"
tar -czf "${NM37_RESULTS}.tar.gz" -C "$(dirname "$NM37_RESULTS")" "$(basename "$NM37_RESULTS")"
tar -czf "${NM37_RESULTS}-summary.tar.gz" -C "$(dirname "$NM37_RESULTS")" "$(basename "$NM37_RESULTS")-summary"
sha256sum "${NM37_RESULTS}.tar.gz" "${NM37_RESULTS}-summary.tar.gz" \
  > "${NM37_RESULTS}-archives.sha256"
```

输出 `comparison.md/csv`、逐次 `measurements.csv` 和带证据的 `summary.json`。`exec_us=execTime/clock_mhz`，频率分别为70/100/110；FPGA加速比以 A70-P3 时间为基准，主机执行时间单独比较。`mxBubble` 是矩阵指令接口 `ready && !valid` 周期，`100*mxBubble/execTime` 不是PE总空闲率；依赖等待需要结合波形分析。资源、STA 来自各自 P&R 报告，不能从板测计数反推。

交付需保留：三版本完整构建输出/源锁/工具版本/CDC审查/bit SHA；统一板测包；环境与烧录日志；每配置设备映射、manifest、控制台/退出码；全部 run.json、输入、指令、reference、actual、逐次及根 result.json；失败记录和汇总。接收方三配置完整复现并板测通过后，再按实际构建提交和证据创建验收标签。

---

# 板测工具与迁移格式参考

以下保留各工具的格式、约束和离线迁移细节。其他入口：[Git复现详解](docs/git-reproduction.md)、[A70恢复](docs/a70-build-recovery.md)、[现成迁移包在151板测](docs/151-board-test.md)、[离线源码重建](docs/from-source-to-board.md)。现成 bit 板测不能替代上面的源码重建。

此目录用于 A70-P2、A70-P3、A100、A110-APB55 的统一输入/指令、逐位校验与计时。
不会自动连接服务器、烧录、解绑 PCIe、加载驱动或 push。只有实际板测通过后，才能把结果称为上板验证通过。
历史 `p2.bit`/`p3.bit` 同载荷，不能放进本次真正重建 P2/P3 的 manifest。A70-P3 也不能冒充后续 M1 ShiftRightJam A70。

## 迁移目录与身份

建议把以下文件放在同一个迁移根目录（manifest 路径全部相对此根目录，不允许 `..` 逃逸或绝对路径）：

```text
nm37-transfer/
  A70-P2.manifest.json        # 仅在该版本实际验收/导出后生成
  A70-P3.manifest.json
  A100.manifest.json
  A110-APB55.manifest.json
  bitstreams/...
  configs/...
  software/generators/fsa/python/  # 完整 Python 源码、uv.lock、pyproject.toml
  software/generators/easyfloat/   # pyeasyfloat 本地依赖，保持 ../../easyfloat 路径
  board-tools/               # 本目录脚本和 README
  evidence/                  # 每个 bit 的源锁、构建/时序验收和 SHA 记录
  source/                    # 复现源码 bundle/依赖包；另行验证子模块完整性
```

`create_manifest.py` 只为现有文件建立身份，**不会证明这些文件满足时序或实际已加载到 FPGA**。硬件源码提交必须是构建证据绑定的 40 位 commit；`build_id` 指向对应唯一构建，不能填写后来只改报告的提交来替代硬件身份。A100/A110 与历史 A70 的 config 必须各自来自对应生成物；不能因为形状相同就复用配置。

```bash
python3 board-tools/create_manifest.py \
  --out A100.manifest.json --configuration-id A100 \
  --hardware-source-commit FULL_40_HEX_HARDWARE_COMMIT \
  --build-id EXACT_BUILD_ID --source-identity-kind built_commit \
  --bitstream bitstreams/A100.bit \
  --config configs/A100.FSAConfig.json --software software/generators/fsa/python
```

Manifest v2 的必需键如下（不存在默认值）：

| 键 | 约束/含义 |
|---|---|
| `schema_version` | 整数 `2` |
| `configuration_id` | `A70-P2`、`A70-P3`、`A100`、`A110-APB55` |
| `clock_mhz` | 对应整数 70、70、100、110；由已加载 bit 的身份推定，非频率实测 |
| `hardware_source_commit` | 完整 40 位小写 SHA；保留对应源码锁及子模块/overlay 证据 |
| `source_identity_kind` | `built_commit` 表示从该提交构建；`reconstructed_source_snapshot` 表示历史 bit 的源码后来被整理为该提交，不能称作从此提交生成 |
| `build_id` | 非空、唯一构建标识 |
| `bitstream` / `config` | `{ "path": "相对路径", "sha256": "64位小写SHA" }` |
| `software` | `{ "path": "software/generators/fsa/python", "files": { "relative/file.py": "SHA", ... } }`；全部 `.py`、`uv.lock`、`pyproject.toml`、`README.md` 必须完全匹配；另有必需 `easyfloat: {path, files}` 绑定本地参考依赖 |

软件哈希不包含 `.venv`、`__pycache__`、`.git`。Python 项目通过 `../../easyfloat` 引用本地 pyeasyfloat，必须保留上述相对目录；外层包清单须覆盖 easyfloat 源码/原生扩展构建输入，不能只搬 Python 子目录。统一比较必须使用同一套已验证软件；manifest 保存软件源码文件身份，easyfloat 源码还由每个 manifest 单独绑定，运行时明确从该目录导入并检查模块路径；其他依赖由锁文件安装。硬件 commit 与主机软件身份分开记录。

传输后先对迁移包外层校验清单运行 `sha256sum -c ...`；外层清单也应覆盖板测脚本和验收报告。若只搬运源码 bundle，注意它不包含 submodule 仓库、bitstream、未提交文件和工具链。

## 在 10.128.157.151 上的操作顺序

以下为待执行流程，不代表已连接该服务器或已烧录。

1. 确认服务器用户名、NM37 板卡、JTAG target 与器件。停止其他访问同一 FPGA 的进程；记录 PCIe BDF、板卡序列号、Vivado/hw_server 和 XDMA 驱动版本。
2. 用 Hardware Manager 手动加载指定 bit。保存编程日志和 bit SHA。此处是易失 FPGA 配置，不是写 Flash。A110/APB55 若失锁或冷复位，需重新初始化/重新装载数据；不能续跑旧任务。
3. 必要时按服务器实际情况恢复 PCIe 枚举和 XDMA 驱动。重新检查 `lspci -Dnn`、`/sys/class/xdma`、`/dev/xdmaN_*`；XDMA 编号可能改变。入口将检查三个实际字符设备的 `/sys/dev/char` 链接均对应给定 BDF，并要求 Xilinx `10ee:9038`/`xdma` 驱动。多卡时禁止猜编号。
4. 使用迁移包的 `software/generators/fsa/python/uv.lock` 建立环境：`uv sync --frozen --project software/generators/fsa/python`。必要的 Python 原生扩展/依赖须已按迁移工具链说明准备。运行环境使用 `software/generators/fsa/python/.venv/bin/python`。
5. 先 dry-run，再 seq16 单次测试，然后 seq32/64/256/1024；每次用新目录，保持三配置相同 seed 和序列长度。大参考计算在打开设备前执行，超时应给足预算。

```bash
# 在迁移目录执行；示例设备/BDF必须替换为真实确认值。
BIT_SHA=$(sha256sum bitstreams/A100.bit | cut -d ' ' -f1)
python3 board-tools/board_test.py \
  --manifest A100.manifest.json --device xdmaN --bdf 0000:81:00.0 \
  --loaded-bitstream-sha256 "$BIT_SHA" --seq 16 --seed 0 \
  --out ../board-results/A100-seq16-preflight --dry-run

# 只在确认已手动加载该 SHA 后运行；脚本不会读取 FPGA 配置来证明身份。
timeout --signal=TERM --kill-after=10s 600s software/generators/fsa/python/.venv/bin/python \
  board-tools/board_test.py --manifest A100.manifest.json \
  --device xdmaN --bdf 0000:81:00.0 --loaded-bitstream-sha256 "$BIT_SHA" \
  --seq 16 --seed 0 --warmup 1 --repeat 20 --out ../board-results/A100-seq16-seed0
```

`--loaded-bitstream-sha256` 是**操作者关于已加载文件的声明**，并非硬件读回。入口核对文件/config/software SHA、频率与配置身份后，才会加载运行代码和打开设备；reference 生成后再次核对。dry-run 不访问设备或 sysfs、不导入 FSA、不生成实际工作负载，因而不能替代实际执行/配置兼容性检查。当前工作负载仅接受 16×16 fp16/fp32、零基地址的 NM37 配置。

`timeout` 必须保留，因为产品 FPGA engine 的 done poll 没有内部超时。若超时或失败，保留输出目录与终端日志；不能认定硬件已停止。完成板卡复位/重新加载并重新确认设备身份后，用新目录重试。脚本的 BDF 文件锁只约束同时使用本入口的进程，不能阻止其他软件操作同一板卡。原 engine 会写 `/tmp` 调试文件，因此不同板卡测试也应串行运行以免调试文件相互覆盖。

## 输出、计时与 bubble 口径

- 根目录 `run.json`：完整 manifest、SHA、设备/BDF/sysfs、主机/Python、输入 Q/K/V 原始 C-order 字节哈希、序列/seed、指令 SHA、时间口径。`inputs.npz`、`instructions.bin`、`reference.npy` 是实际工作负载证据。比较输入时使用原始数组哈希，不能用可能受 ZIP 元数据影响的 `.npz` 文件哈希。
- 每次 `run-NNN/actual.npy`、`result.json`：逐位 fp32 比较（区分 ±0/NaN payload）、10 个 uint32 计数器、前后状态/计数、单调时钟时间戳；要求零不匹配、累计 `enq == deq`，且本次 enq/deq 增量等于指令字数。
- `exec_us = execTime / clock_mhz`；这是 FPGA active 周期换算，110 MHz 对应除以 110，不允许硬编码 100。
- `host_execute_ms`：`time.monotonic_ns()` 包围 `FPGA.execute`，包括指令字序列化、输入 HBM 写入、旧 engine `/tmp` 调试写入、SET_ACTIVE、主机供指、poll/计数器打印、输出 HBM 读回。排除 Python 环境启动、输入/kernel 准备、reference 计算、结果文件写入。它不是纯 PCIe 时间，也不等于 DUT active 时间。三配置使用相同软件/日志方式才可比较。
- 每次执行前恢复原始 input tensor data（上次输出不会被误当输入上传）。在任何 HBM 写入前要求状态 idle=0 或 done=2；重复执行从 done 经 SET_ACTIVE 回 active，清零 `perfCounters` 中八项性能计数器；`enq`、`deq` 两项自冷复位累计、不会由 SET_ACTIVE 清零。报告保留十项原始值，并另存本次 `enq/deq` 模 2^32 增量，要求两增量均等于实际提交的 instruction word count（单次小于 2^32）。预热运行也校验并保留证据，但不计入中位数/min/max。
- 十个计数器：`execTime`、`mxBubble`、`mxActive`、`dmaActive`、`rawInst`、`mxInst`、`dmaInst`、`fenceInst`、`enq`、`deq`。八项 active 性能计数为 32 位且无溢出修复，测试规模必须避免单次计数溢出；累计 enq/deq 的边界回绕按上述单次增量处理。`mxBubble` 表示矩阵指令接口 ready 且没有 valid 的周期，不等于阵列总空闲周期；控制器 ready=0 的依赖等待须结合波形独立分析，活动计数也不能简单相加得到总时间。
- 汇总只在所有运行通过后写根目录 `result.json`；失败写 `failed.json`，SIGTERM/kill 的超时不保证有失败 JSON，以外部退出码和已有部分证据为准。没有根目录 PASS 就不能作为通过记录。

板测性能应先按相同 `seq/seed/输入原始哈希/指令哈希/软件哈希` 分组，再比较 cycles、换算 active 时间、host 时间及全部 counters。资源来自各自完整 P&R 报告，另列硬件 build_id/bit SHA，不能从板测推断资源。板测通过后由主任务本地提交结果，再按用户要求执行 push；本入口从不 push。

## 离线验证

```bash
python3 -m unittest discover -s scripts/nm37-board -p 'test_*.py' -v
# 使用 FSA Python 环境执行全部 numpy/runtime mock 测试，不能把 skip 算作通过。
/path/to/fsa/python/.venv/bin/python -m unittest discover \
  -s scripts/nm37-board -p 'test_*.py' -v
```

测试只使用临时文件和 mock engine。它们覆盖身份/哈希/时钟拒绝、零设备访问 dry-run、BDF 拒绝、准备后再校验、重复恢复输入、active 状态拒绝、逐位差异、十计数器和单位换算、warmup 汇总；不代表已运行 NM37 或验证真实 PCIe 设备节点布局。


## 构建与验证迁移包

`package.py` 的本地 spec 可以引用绝对输入文件；它不被放入迁移包，生成 manifest、清单和启动说明只使用包内相对路径。配置必须明确给出预期 bit/config SHA，复制后再次核对。不会自动补齐或伪造尚未通过构建的 A70；仅生成 spec 中提供且验证通过的配置。下面是 spec 格式（FULL_COMMIT、SHA 等占位符必须替换成对应真实构建证据）：

```json
{
  "schema_version": 1,
  "software_root": "/path/to/verified/FSA/source",
  "configurations": [
    {
      "configuration_id": "A110-APB55",
      "hardware_source_commit": "FULL_40_HEX_HARDWARE_COMMIT",
      "build_id": "apb110-build04",
      "source_identity_kind": "built_commit",
      "bitstream": {"path": "/path/to/NM37_A110.bit", "sha256": "EXPECTED_64_HEX_SHA"},
      "config": {"path": "/path/to/FSAConfig.json", "sha256": "EXPECTED_64_HEX_SHA"}
    }
  ],
  "attachments": [
    {"path": "/path/to/source.bundle", "sha256": "EXPECTED_64_HEX_SHA", "destination": "source/FSA.bundle"}
  ]
}
```

`attachments` 可省略；附件只允许落到 `source/`、`evidence/`、`docs/`。补齐所有配置时各自增加条目；同名配置重复会被拒绝。bitstream 的 full hardware commit 不会由脚本从现有分支猜测。工具没有检查 Git commit 对象与 bit 内网表的对应关系，这一关系必须由所附构建证据建立。若一个旧 bit 产生于未提交源码，应先补全源身份审计，使用 `reconstructed_source_snapshot` 明示后来整理的源码快照，不得称作 clean commit build。现存 A100 bit 的身份属于此类：原始工作树构建日期 2026-10-13，后来源码快照提交 `0f1a643a58894dc52b46d4058f39d12205405644`，build_id 为 `original-A100-20261013`；它不是该提交当时生成的 bit，仍须附源哈希链证据。A110/APB55 为 `built_commit`。

```bash
python3 scripts/nm37-board/package.py build --spec /path/to/package-spec.json \
  --out /path/to/new-transfer --archive /path/to/new-transfer.tar.gz
python3 /path/to/new-transfer/board-tools/package.py verify --root /path/to/new-transfer
sha256sum /path/to/new-transfer.tar.gz
```

工具自带的 `docs/*.md` 原样进入 `board-tools/docs/`，README 的相对文档链接在解包后仍有效。包内 `SHA256SUMS` 覆盖每个有效载荷和 `package.json`，后者记录逐文件 SHA/大小及配置列表。归档不带原机器目录、uid/gid/mtime，固定文件内容时可复现。仅拷贝 Python 源码、README、pyproject/uv.lock，不带 `.venv`、缓存、RTL build 产物。源代码仓库 bundle、submodule 对象、板级配置/报告需作为附件显式加入；Vivado/toolchain/wheels 默认不在包里，所以本工具不能单独保证离线完整重新编译 FPGA。

把 archive SHA 通过可信通道保存。目标服务器先比较 SHA，再使用可审查的 `package.py extract` 解包：

```bash
python3 /path/to/reviewed/package.py extract --archive new-transfer.tar.gz \
  --sha256 EXPECTED_ARCHIVE_SHA --out /path/to/new-extracted
python3 /path/to/new-extracted/board-tools/package.py verify --root /path/to/new-extracted
```

解包器要求新目录、拒绝路径逃逸、重复文件、软/硬链接及设备节点，解包后再核对完整清单和全部 manifest。也可先手动确认 archive SHA 后用受信任的系统工具解包，再执行 `verify`。不要仅根据来自同一未知包的清单认为其来源可信。

在目标服务器先照前节执行 `board_test.py --dry-run`，再手工烧录与测试。把结果放在迁移根目录之外，保持迁移包不可变。允许环境安装生成 `.venv` 和 `__pycache__`；清单不对它们验源，依赖版本由锁文件和运行记录确定，它们也不会被重新归档。

PCIe ID 证据：本次 A110 已生成 `obj/ip/nm37_xdma/nm37_xdma.xci` 的 `PARAM_VALUE.pf0_device_id=9038`，内部 `MODELPARAM_VALUE.PF0_DEVICE_ID=0x9038`。入口因此要求 `10ee:9038`；这个 ID 并不能唯一证明 NM37 板型号，必须结合物理板卡、JTAG target 和 BDF 人工确认。历史 A70/A100 的实际 XCI 也应随各自构建核对，不能用旧 Xiangshan 镜像 `903f` 代替 FSA 镜像验收。


## 完整 Git 源码迁移：主仓与递归依赖分开

只下载主仓或主仓 `.bundle` 不包含 submodule Git 对象。离线环境也不能靠 `.gitmodules` 的 remote URL 获取内容；本工作区还有 shared alternates，直接复制小 `.git` 目录会保留对原机器路径的依赖。

`source_bundle.py` 从每个精确 commit 获取完整可达对象到独立临时 bare 仓，再导出独立 bundle；在空对象库执行 `git bundle verify`，不修改原仓库/分支。相同 commit 的递归库共用一个 bundle，清单路径相对，不携带原机器 alternates。源码 Git 历史中原有 URL/文本保留，但恢复命令不使用网络；恢复仓库删除 origin、检查没有 alternates、核对 HEAD 和父仓 gitlink，并执行 connectivity fsck。恢复器仅在父仓本地配置中标记已恢复子模块 `active=true`，不写入 remote/URL，确保旧构建门禁的 `git submodule status` 能识别其已初始化状态。

依赖从实际构建 source-lock 的 `dependencies` 导出。该任务锁定 175 个依赖路径、11 个显式递归排除；主仓另外 20 个不在 NM37 构建锁内的 root gitlinks（如软件 workload 和无关工具仓库）单列为 out-of-scope，不下载、不冒充已经恢复。是否“完整”指这个构建 scope，不能声称整个 Chipyard 所有子模块已填满。源码 bundle 不包含编译缓存、安装后的工具链、Vivado 或许可证。版本化的 toolchain *源码* 若实际存在于锁内则依锁保留。

```bash
# 导出锁内依赖；输出目录首次必须不存在。
python3 scripts/nm37-board/source_bundle.py dependencies \
  --source /path/to/clean-historical-p2-checkout \
  --lock /path/to/actual-source-lock.json --out /path/to/migration-source-deps
# 中途停止后可使用相同 pins/exclusions、同一输出目录和 --resume 继续。
# --only generators/fsa 等选项用于部分验证；它会加入祖先及该 subtree 子库，
# 但会明确列出 not_exported_dependencies，不会标记为完整依赖迁移。
```

最终 integration 提交确定后，单独用如下 main spec 导出主仓。每个 profile 可来自不同本地 checkout，但必须拥有完整历史对象。历史 P2/P3 的完整提交及父链均要保留；下面占位符必须由真实提交替换，不要使用复制源码后新建的无历史仓库冒充。

```json
{
  "profiles": {
    "integration": {"repository": "/path/to/integration", "revision": "FINAL_COMMIT"},
    "historical-p2-clean": {"repository": "/path/to/stages/p2", "revision": "5acd5e8d5e61729090fc89a7e92d68bd01abcc37"},
    "historical-p3-clean": {"repository": "/path/to/stages/p3", "revision": "45d859c1c1bdbd282d0c4ac1e465c0fa0b4e296b"},
    "historical-p2": {"repository": "/path/to/stages/p2", "revision": "f7d31bb197b93eef9dc416d0508e550894531665"},
    "historical-p3": {"repository": "/path/to/stages/p3", "revision": "73b3e42f3006818d13bc059818e11cc803f78dba"},
    "a100-source-snapshot": {"repository": "/path/to/integration", "revision": "0f1a643a58894dc52b46d4058f39d12205405644"},
    "a110-apb55": {"repository": "/path/to/integration", "revision": "eeb1a1d0ac6f47497eb2806e412d9df9ad4b6b67"},
    "a100-reproduction": {"repository": "/path/to/integration", "revision": "0a3a2656df66043ea513a45bab8275c1be0ba76c"},
    "a110-audited": {"repository": "/path/to/integration", "revision": "25673838e66ddef9031748ca40f7220c65acedaf"}
  }
}
```

```bash
python3 scripts/nm37-board/source_bundle.py main \
  --spec /path/to/main-spec.json --out /path/to/migration-main
# 迁移 migration-main、migration-source-deps 的完整目录及可审查的 source_bundle.py。
# 固定新 checkout/patch 新文件的模式，便于复核权限位。
umask 022
# 通过迁移包的外层 SHA 清单/归档 SHA 验证来源后，离线恢复一个新 checkout：
python3 source_bundle.py restore --main migration-main/main.json \
  --dependencies migration-source-deps/dependencies.json \
  --profile historical-p2 --out /path/to/new-restored-p2
```

恢复会按该 profile 的真实 Git tree 遍历 gitlinks。历史 P2/P3 的 FSA/easyfloat 是子模块，因此按锁恢复；integration 已直接跟踪 FSA/easyfloat，就不会把旧子仓覆盖到这些目录。完整恢复缺少任何一个所需锁内 bundle 会失败。报告写在 checkout 的相邻 `NAME-restore.json`，列出恢复的精确 HEAD 与未填充 gitlinks；它不证明已经重新编译或通过板测。恢复仓库故意不配置 origin；目标服务器板测通过后再按主任务发布流程设置上游并 push，本工具从不 push。若构建入口还要应用已版本化 patch/overlay，应继续按照该 profile 的构建入口执行，不能把原始 submodule commit 与应用后的工作树源哈希混淆。

快速离线依赖恢复检查：

```bash
python3 source_bundle.py restore-dependencies \
  --dependencies migration-source-deps/dependencies.json \
  --only generators/fsa --only generators/easyfloat --only fpga/fpga-shells \
  --only generators/rocket-chip --out /path/to/new-dependency-smoke
```

该检查只恢复选定的依赖及递归子库，不含主仓，输出明确标为 dependency-only smoke。`test_source_bundle.py` 还用小型 Git fixtures 验证原仓被移走后仍能恢复、shared alternates 的独立导出、主仓多 profile/父链、精确 gitlinks、integration 跟踪目录不被旧子模块覆盖，以及缺失/损坏 bundle 拒绝。

历史 source-lock 可能记录了原工作区的组写权限（例如 0664），Git 则只版本化普通文件的可执行位。跨机器对比应分别列出内容 SHA、可执行位和完整权限位，不能把 umask 造成的模式差异误报为 RTL 内容变化，也不能直接声称两个完整 source-lock JSON 相同。少数上游测试文件还通过 `.gitattributes` 显式使用 `text eol=crlf`；工作树原始 SHA 应与实际源锁比较，Git blob 校验则按声明归一化并记录，不能把 CRLF 转换默默忽略。

完整历史 P3 安装入口恢复示例（`integration-tools` 指经审核的 integration 主仓代码，可由 main bundle 的 integration profile 检出；这些工具本身不需要先填满其子模块）：

```bash
python3 source_bundle.py restore --main migration-main/main.json \
  --dependencies migration-source-deps/dependencies.json \
  --profile historical-p3-clean --out /path/to/restored-p3
python3 /path/to/integration-tools/scripts/nm37-a70/install_historical.py drycheck \
  --root /path/to/restored-p3 --stage p3
python3 /path/to/integration-tools/scripts/nm37-a70/install_historical.py install \
  --root /path/to/restored-p3 --stage p3
python3 /path/to/integration-tools/scripts/nm37-a70/install_historical.py verify \
  --root /path/to/restored-p3 --stage p3
python3 /path/to/restored-p3/scripts/nm37-a70-source-lock.py capture \
  --source /path/to/restored-p3 --lock /path/to/restored-p3-source-lock.json
```

这里从原始 `45d859c1...` 安装，固定 P3 `73b3e42f...` profile 保留已验证构建的提交和父链；两者不是同一个 Git HEAD。验收比较实际产品源内容，单列 installer 新增工具、权限和 root commit 差异。P2 使用 `historical-p2-clean` 和 `--stage p2`。不能直接在已修复提交上再次执行要求原始 HEAD 的 installer。

已核对本任务的 A100 `0f1a643a...`、A110 `eeb1a1d0...` 以及工具集成 `47a123fe...`：它们的递归 gitlinks 使用此包中的相同 173 个依赖 pin，FSA/easyfloat 两个目录已直接版本化，不使用对应历史子仓 bundle；各自仍明确保留 11 个递归排除和 20 个 root 范围外 gitlinks。这只是源对象兼容性结论，完整编译、布局布线还需要各 profile 的构建入口、工具链和独立验收。

目标服务器的分阶段编译、验证、布局布线与 A110 历史 reaudit 命令见 [源码恢复与比特流重建](docs/source-reproduction.md)。A100 使用修复门禁后的 `a100-reproduction`，A110 新构建/旧DCP审计使用 `a110-audited`，不要在加入新工具后的 integration checkout 上直接运行历史锁定入口。

目标服务器的简洁操作顺序见 [151 板测操作单](docs/151-board-test.md)，包含 USER 占位、手动加载确认、三配置一致测试及结果保存要求。

## 板测结果汇总

新增 `summarize_results.py` 读取一个完整三配置板测批次（Python + numpy），核对原始证据、排除预热和低重复次数 smoke，生成 Markdown、CSV 与 JSON。默认要求三配置同工作负载/软件/环境，缺失、失败或重复记录会拒绝汇总。

```bash
/path/to/fsa/python/.venv/bin/python scripts/nm37-board/summarize_results.py \
  --results /path/to/campaign --min-repeat 20 --out /path/to/new-summary
```

完整传输、烧录、执行、补充工具传输及汇总命令见 [151 操作单](docs/151-board-test.md)。已有正式迁移包不变，新脚本单独传到目标机 `~/nm37-tools/`；不要覆盖归档清单内文件。测试均为离线模拟证据验证，不能替代板测。

A70 构建阶段诊断、保留旧结果的更新方式，以及阵列尺寸与seq1024的区分，见 [A70构建诊断与恢复](docs/a70-build-recovery.md)。
