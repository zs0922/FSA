# NM37 源码离线恢复与比特流重建

本说明供迁移到 `10.128.157.151` 后使用。路径均为目标服务器的示例，由操作者替换；本说明未执行 SSH、烧录或该服务器上的测试。板测使用上一层 [README](../README.md) 的统一 manifest 入口，不使用历史脚本中的固定设备编号。

## 交付边界与 profile

需要同时搬运 `migration-main/`、`migration-source-deps/` 和经过审核的 `source_bundle.py`；先核验外层归档及逐文件 SHA。主仓 Git bundle **不包含**子模块对象。不能复制依赖原机器 alternates 的 `.git`，也不能把 `.gitmodules` URL 当成离线源码。

最终主仓清单应提供这些 profile；`integration` 的完整提交以最终交付 `main.json` 为准，不在这里猜测：

| profile | 精确 root commit / 用途 |
|---|---|
| `integration` | 最终本地提交；提供本迁移工具和 A70 新入口 |
| `historical-p2-clean` | `5acd5e8d5e61729090fc89a7e92d68bd01abcc37`，原始 P2，供 installer 使用 |
| `historical-p3-clean` | `45d859c1c1bdbd282d0c4ac1e465c0fa0b4e296b`，原始 P3，供 installer 使用 |
| `historical-p2` | `f7d31bb197b93eef9dc416d0508e550894531665`，保留实际阶段修复提交及父链 |
| `historical-p3` | `73b3e42f3006818d13bc059818e11cc803f78dba`，保留实际阶段修复提交及父链 |
| `a100-source-snapshot` | `0f1a643a58894dc52b46d4058f39d12205405644`，现存原 A100 bit 的事后源码快照；不是该提交当时生成 bit |
| `a100-reproduction` | `0a3a2656df66043ea513a45bab8275c1be0ba76c`，修复输入身份门禁后的 A100 完整入口 |
| `a110-apb55` | `eeb1a1d0ac6f47497eb2806e412d9df9ad4b6b67`，原 build04 硬件实现身份 |
| `a110-audited` | `25673838e66ddef9031748ca40f7220c65acedaf`，包含修订后的只读审计入口 |

**不能在最新 integration checkout 上直接运行 A100/A110 历史入口**：其严格 source-lock 包含所选版本的输入集合，新加入的工具或不同硬件会被拒绝。按上表恢复各自独立 profile。A100/A110 的 FSA/easyfloat 已被主仓直接跟踪，恢复器不会覆盖为旧历史子模块。

本任务实际验证了原始 P3 主仓 + 175 个依赖的完整离线恢复，176 个仓库无 remote、无 alternates、HEAD/gitlink 正确，477,009 个版本化文件符合固定 Git tree。39 个上游测试文件按 `.gitattributes` 的 `eol=crlf` 记录显式转换。当前 installer 的 drycheck/install/verify 全通过；与 `final-p3-v2` 的 478,173 个共同输入内容全部相同，仅新增 3 个安装工具，并单列 8 个权限差异和 root commit 差异。

A100/A110 的源码 profile 和复现/审计 profile另行做了完整递归 pin 核对：使用同一包中的 173 个依赖，未使用的两项正是直接跟踪的 FSA/easyfloat。此结论是源码恢复范围，**本次迁移验收没有重跑 A100/A110 编译或 P&R**。所有 profile 都明确保留 11 个递归排除、20 个 root 范围外 gitlinks，不能称整个 Chipyard 全部子模块已初始化。

## 目标机环境

Git bundles 不带 Vivado、许可证、已安装工具链、sbt/Maven/Python 下载缓存、生成器、RTL 构建目录或布局布线结果。完全离线编译还须另备相应安装包/缓存；仅有本源码包不能承诺断网完成 FPGA 构建。

A100/A110 的 `run.sh prepare` 严格检查 Java **20.0.2**、firtool **1.75.0**、Verilator **5.022**、Vivado **2020.2_AR75986**；需要 NM37 的 `xcvu37p_CIV-fsvh2892-2L-e` 器件/IP 和许可证。Python 项目要求 **>=3.13**，历史 build04 使用 3.13.14；以下固定选择 3.13 系列，避免自动选到未验收的更新解释器。环境记录器还读取 `$RISCV/lib/libfesvr.a` 和 `libriscv.so`，记录 C/C++/ld/Yosys 的版本及二进制 SHA。原 build04 显式 C/C++ 为系统 GCC/G++ 13.3.0；PATH 中另有 13.2.0，不能仅凭 `g++ --version` 推断实际构建编译器。

在新的 shell 中先配置目标机工具，不复制原服务器的绝对路径，也不要继承另一配置的 `-Dfsa.*`：

```bash
source /opt/your-vivado/2020.2/settings64.sh
export RISCV=/opt/your-riscv
export PATH=/opt/java-20.0.2/bin:/opt/firtool-1.75.0/bin:/opt/verilator-5.022/bin:$RISCV/bin:$PATH
export CC=/usr/bin/gcc
export CXX=/usr/bin/g++
export LD_LIBRARY_PATH="$RISCV/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export CHISEL_FIRTOOL_PATH=/opt/firtool-1.75.0/bin
export XDG_CACHE_HOME=/work/nm37/cache
export XDG_RUNTIME_DIR=/work/nm37/runtime
mkdir -p "$XDG_CACHE_HOME" "$XDG_RUNTIME_DIR"
export JAVA_TOOL_OPTIONS='-Dsbt.supershell=false -Dsbt.server.forcestart=false -Dsbt.server.autostart=false'
umask 022
```

各构建节会重新设置 `FSA_PYTHON` 和 `PYTHONPATH`，只指向对应恢复源码；不要保留另一 checkout 的 PyEasyFloat 导入路径。`verify_sim.py` 根据自身位置选择 FSA 源和 DRAMSim 配置，不使用原服务器路径。

如果需要共享下载缓存，可显式设 `COURSIER_CACHE`；不要共享生成器 JAR、`target/` 或 RTL/仿真器输出。A110 入口会把 `CHISEL_FIRTOOL_PATH` 设为当前 PATH 中 firtool 的目录，A70/A100 则应由调用者显式保持一致。所有源码、输出、审计路径使用无空格的绝对路径，输出目录必须尚不存在。

## 先取 integration 工具

下列变量后续各节共用。`git clone` 只取主仓工具；不要在此工具 checkout 启动硬件构建，也不必为它额外恢复 173 个依赖。

```bash
export NM37_MAIN=/work/transfer/migration-main
export NM37_DEPS=/work/transfer/migration-source-deps
export NM37_TOOLS=/work/nm37/integration-tools
export NM37_RESTORE=/work/transfer/source_bundle.py

git clone --no-checkout "$NM37_MAIN/main.bundle" "$NM37_TOOLS"
# EXPECTED_INTEGRATION_COMMIT 从已核验 main.json 的 profiles.integration.commit 读取。
git -C "$NM37_TOOLS" checkout --detach EXPECTED_INTEGRATION_COMMIT
git -C "$NM37_TOOLS" remote remove origin
```

创建形式验证环境（pip 命令需要网络或预备 wheel；不要把“源码离线”混为依赖安装离线）：

```bash
python3 -m venv /work/nm37/formal-env
/work/nm37/formal-env/bin/pip install -r "$NM37_TOOLS/scripts/nm37-a100/formal-requirements.txt"
export YOSYS=/work/nm37/formal-env/bin/yowasp-yosys
```

当前 A100/A110 requirements 相同，固定 `yowasp-yosys==0.70.0.0.post1259` 等版本；A70 新形式入口也使用 `$YOSYS`。

## A70 P2 / P3：先安装，再生成

下面以 P3 为例。P2 用不同新目录、`historical-p2-clean` 和 `A70_STAGE=p2`，其余步骤相同但跳过 units/formal。

```bash
export A70_TOOLS="$NM37_TOOLS/scripts/nm37-a70"
export A70_SOURCE=/work/nm37/a70-p3-source
export A70_OUT=/work/nm37/a70-p3-build
export A70_STAGE=p3
export A70_JOBS=16
python3 "$NM37_RESTORE" restore --main "$NM37_MAIN/main.json" \
  --dependencies "$NM37_DEPS/dependencies.json" \
  --profile historical-p3-clean --out "$A70_SOURCE"
python3 "$A70_TOOLS/install_historical.py" drycheck --root "$A70_SOURCE" --stage "$A70_STAGE"
python3 "$A70_TOOLS/install_historical.py" install --root "$A70_SOURCE" --stage "$A70_STAGE"
python3 "$A70_TOOLS/install_historical.py" verify --root "$A70_SOURCE" --stage "$A70_STAGE"
uv sync --frozen --python 3.13 --project "$A70_SOURCE/generators/fsa/python"
export FSA_PYTHON="$A70_SOURCE/generators/fsa/python/.venv/bin/python"
export PYTHONPATH="$A70_SOURCE/generators/easyfloat"
bash "$A70_TOOLS/run.sh" prepare
bash "$A70_TOOLS/run.sh" assembly
bash "$A70_TOOLS/run.sh" units
bash "$A70_TOOLS/run.sh" formal
bash "$A70_TOOLS/run.sh" sim 4
bash "$A70_TOOLS/run.sh" sim 16
bash "$A70_TOOLS/run.sh" elaborate
bash "$A70_TOOLS/run.sh" implement
# 核查本次 CDC、约束边界和整板时序后才导出：
bash "$A70_TOOLS/run.sh" bitstream
```

installer 要求原始历史 HEAD，不能改为已修复 `f7d31bb1/73b3e42f` 再执行。它在 make 解析/assembly 前应用完整补丁，安装 256-bit 单端口16×16仿真配置；P3 独立 ACC emitter 使用 `disallowPackedArrays`。`prepare` 冻结全部实际输入，后续阶段绑定源锁和产物。P3 formal 是三拍 MAC/EXP2 窗口等价证明，不声称 reciprocal 或完整调度的形式证明；旧历史 ControlSignalTest 没有执行。16×16 系统回归使用单端口配置，避免拿多端口计数替代板级接口。

输出为 `$A70_OUT/bitstream/NM37_A70.bit`；P2/P3 文件名相同但位于独立目录，必须按各自 manifest、配置和 SHA 区分。恢复 integration 工具源码后，读取 `$NM37_TOOLS/scripts/nm37-a70/README-build.md` 查看详细说明；板测包不包含该 sibling 目录。

## A100：采用修复后的历史复现入口

```bash
export A100_SOURCE=/work/nm37/a100-source
export A100_OUT=/work/nm37/a100-build
export A100_JOBS=8
python3 "$NM37_RESTORE" restore --main "$NM37_MAIN/main.json" \
  --dependencies "$NM37_DEPS/dependencies.json" \
  --profile a100-reproduction --out "$A100_SOURCE"
cd "$A100_SOURCE"
python3 scripts/nm37-a100/prepare-shell.py
uv sync --frozen --python 3.13 --project generators/fsa/python
export FSA_PYTHON="$A100_SOURCE/generators/fsa/python/.venv/bin/python"
export PYTHONPATH="$A100_SOURCE/generators/easyfloat"
bash scripts/nm37-a100/run.sh check
bash scripts/nm37-a100/run.sh prepare
bash scripts/nm37-a100/run.sh units
bash scripts/nm37-a100/run.sh formal
bash scripts/nm37-a100/run.sh sim 4
bash scripts/nm37-a100/run.sh sim 8
bash scripts/nm37-a100/run.sh sim 16
bash scripts/nm37-a100/run.sh elaborate
bash scripts/nm37-a100/run.sh implement
# physical.passed.json 仍声明 board_signoff=false；人工复核 CDC/约束。
bash scripts/nm37-a100/run.sh bitstream
```

`prepare-shell.py` 从固定 shell commit 恢复锁定的 55 文件，不重放旧 U280/NM37 核心补丁。`run.sh` 显式设 PE pipeline=true、fanoutSplit=false、pipelineConservative=false、100MHz，`prepare` 本身包含 assembly。`implement` 在完成 Vivado 后调用严格 physical gate；有 DUT/全局 setup、hold、pulse-width、route、bus-skew 或 DRC Error/Critical 失败时不能导出。

主要结果在 `$A100_OUT/A100/fpga/chipyard.fpga.nm37.NM37FPGATestHarness.EmptyNM37Config/obj/` 的 `post_route.dcp`、`report/` 和 `audit/`；bit 在 `$A100_OUT/bitstream/NM37_A100.bit`。原2026-10-13 bit采用事后快照0f1a标识，本节新构建有新的源码执行身份和 SHA，不能照抄原bit身份。历史结果、CDC边界和输入身份修复见各历史profile的 `optimization-reports/NM37_A100_BASELINE.md` 与 `NM37_A110_RESET_RESULTS.md`。

## A110/APB55：新完整构建

```bash
export A110_SOURCE=/work/nm37/a110-source
export A110_OUT=/work/nm37/a110-build
export A110_JOBS=8
python3 "$NM37_RESTORE" restore --main "$NM37_MAIN/main.json" \
  --dependencies "$NM37_DEPS/dependencies.json" \
  --profile a110-audited --out "$A110_SOURCE"
cd "$A110_SOURCE"
python3 scripts/nm37-a110/prepare-shell.py
uv sync --frozen --python 3.13 --project generators/fsa/python
export FSA_PYTHON="$A110_SOURCE/generators/fsa/python/.venv/bin/python"
export PYTHONPATH="$A110_SOURCE/generators/easyfloat"
bash scripts/nm37-a110/run.sh check
bash scripts/nm37-a110/run.sh prepare
bash scripts/nm37-a110/run.sh units
bash scripts/nm37-a110/run.sh formal
bash scripts/nm37-a110/run.sh hbm-startup
bash scripts/nm37-a110/run.sh sim 4
bash scripts/nm37-a110/run.sh sim 8
bash scripts/nm37-a110/run.sh sim 16
bash scripts/nm37-a110/run.sh elaborate
bash scripts/nm37-a110/run.sh implement
```

入口保持 PE/CMP/scratchpad/ACC 流水深度，DUT=110MHz、HBM APB=55MHz。`hbm-startup` 是额外必需封印。若 physical gate 生成 `cdc-review.required.json` 后拒绝验收，保留失败记录，逐项审查本次 routed evidence，再创建同目录 `cdc-review.json`；不能复制另一个 DCP 的审查或屏蔽真实失败。之后：

```bash
bash scripts/nm37-a110/run.sh physical
# 必须先通过本次物理门禁和实际 CDC 审查。
bash scripts/nm37-a110/run.sh bitstream
```

输出为 `$A110_OUT/bitstream/NM37_A110.bit`。新构建的身份是所用 audited profile，而原 build04 的硬件身份仍是 eeb1a；两者不得互换通过标记。

## 已有 A110 build04 的只读 reaudit / 导出

这是复核已有 routed DCP 的独立路线，**不重新综合或 P&R**。原 eeb1a 的审计脚本对优化后网表存在假设问题，原构建目录的失败/中断状态必须保留；原 build04 最终通过来自审计提交25673838的独立 `reaudit.passed.json`，不能写成原脚本成功。

该路线需要完整原 build04：`build.json`、generator.jar、shell快照、七个验证阶段的 `.passed.json` **及它们引用的全部文件**、生成 IP XCI、DCP、原报告、reset证据、环境记录。单独复制 `.dcp` 或本源码bundle不够。使用单独恢复的 `a110-audited` 源码并准备锁定shell；不能使用加入 A70 新工具后的 integration 主仓，因为只读审计严格限制与原实现提交的差异。

```bash
cd /work/nm37/a110-auditor-source
# 此目录也由 a110-audited profile 完整恢复。
python3 scripts/nm37-a110/prepare-shell.py
export A110_OUT=/work/imported/original-build04
export A110_REAUDIT_OUT=/work/nm37/reaudit-build04-new
bash scripts/nm37-a110/run.sh reaudit
export A110_REAUDIT_ATTEMPT=initial-review
bash scripts/nm37-a110/run.sh reaudit-accept
```

首次 accept 若因新 CDC 条目失败，会保留 `acceptance/initial-review/reaudit.failed.json` 并生成 `cdc-review.required.json`。据真实报告填写 `$A110_REAUDIT_OUT/cdc-review.json`：reviewer、build/DCP/CDC SHA、每项finding、classification、理由、证据相对路径和SHA均须完整。真实CDC缺陷不允许用该审查豁免。审查完成后使用新 attempt（若首次直接通过，就使用首次实际passed文件）：

```bash
export A110_REAUDIT_ATTEMPT=cdc-reviewed
bash scripts/nm37-a110/run.sh reaudit-accept
export A110_REAUDIT_PASS="$A110_REAUDIT_OUT/acceptance/cdc-reviewed/reaudit.passed.json"
bash scripts/nm37-a110/run.sh reaudit-verify
export A110_BITSTREAM_OUT=/work/nm37/bitstream-from-reaudit-new
bash scripts/nm37-a110/run.sh bitstream-from-reaudit
```

审计输出不能与原build或源码目录重叠，输出/attempt/bitstream目录均不可复用。reaudit在执行前后核验原文件不变、原Git driver字节、source-lock、阶段封印、DCP及IP身份；Vivado版本也须匹配原记录。最终导出位于 `$A110_BITSTREAM_OUT/export/NM37_A110.bit`，`export.passed.json` 绑定修订验收与原DCP。原报告见 `optimization-reports/NM37_A110_APB.md`，执行规则见 `scripts/nm37-a110/reaudit.py` 和 `physical_audit.py`。

## 比较与后续板测

每个新构建独立保存源锁、Git身份、工具版本、阶段封印、实际时序/资源报告、config和bit SHA。工具版本、主机或布局过程变化可能改变 bit 字节和裕量；重建目标是重新证明配置/功能/时序成立，不保证与旧 bit 逐字节相同。不要拿旧通过标记替代新生成物。

生成/验收后，把各配置的bit及对应FSAConfig交给 `package.py` 生成新manifest。板测记录DUT时钟换算、输入/指令hash、逐位结果、十计数器及独立主机耗时；仍需操作者确认实际加载身份。`source_bundle.py`、构建入口和板测入口均不自动烧卡、不push。先在151完成真实板测后，再按主任务决定发布。
