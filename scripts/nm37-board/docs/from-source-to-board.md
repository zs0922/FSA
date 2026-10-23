# NM37 从源码到仿真、bitstream、板测的交付复现流程

本文件是给新使用者的主入口。目标是自己重新编译 RTL、运行仿真、完成综合/布局布线并生成新的 bitstream，然后测试这些新产物。现成 bit 仅作历史对照，不能作为新机器复现成功的证据。

## 0. 哪份源码、哪次 commit

当前相关提交尚未 push。不要假设从 GitHub 的默认分支 clone 后能 checkout 到这些提交；本次使用完整源码迁移包内的 Git bundle 与固定依赖。它保留 Git 历史，不是复制当前杂乱工作目录。等新机器验收通过并发布后，才能把公开仓库 clone/tag 作为正式下载入口。

| 用途 | 源码包 profile | 精确 commit | 后续动作 |
|---|---|---|---|
| 固定构建/恢复工具、统一板测软件 | integration | `d589b230b975730c108426591e552c056008e82a` | 只检出主仓，不在此编译硬件 |
| A70-P3 | historical-p3-clean | `45d859c1c1bdbd282d0c4ac1e465c0fa0b4e296b` | 安装器应用锁定补丁和验证文件，再编译 |
| A100 | a100-reproduction | `0a3a2656df66043ea513a45bab8275c1be0ba76c` | prepare-shell 后编译 |
| A110/APB55 | a110-audited | `25673838e66ddef9031748ca40f7220c65acedaf` | prepare-shell 后编译 |

A70 的历史修复提交 `73b3e42f...` 也在包中，但不能拿它再次运行只接受原始 HEAD 的 installer。A70 安装后 Git HEAD 仍是 45d859c1，工作树包含明确记录的补丁；交付身份是 **HEAD + 安装状态 + source-lock + 固定工具提交**，不是仅一个裸 commit。A100 的 0f1a 快照、A110 的 eeb1a 原实现提交保留用于追溯，新复现使用上表入口。

已有证据：A70/P3 重建的仿真/形式验证/P&R/bit 导出完成；从包恢复的 P3 全部依赖和安装后共同输入已比对。A100 曾做独立重建，A110 原 build04 完成实现并通过修订审计。**从当前源码迁移包在 151 完整跑通三配置的新构建尚未完成**；恢复、静态命令核对或旧 DCP 审计都不能替代此验收。本指南也不是工具链全自动安装器。

## 1. 迁移、解包

当前服务器：

```bash
export NM37_REMOTE=YOUR_USER@10.128.157.151
cd /home/zhangsi/tmp/FSA-m1/nm37-a70-rebuild-20261022/delivery-final
ssh "$NM37_REMOTE" 'mkdir -p ~/nm37-transfer'
scp nm37-source-transfer.tar.gz nm37-board-transfer.tar.gz SHA256SUMS.archives \
  "$NM37_REMOTE:~/nm37-transfer/"
ssh "$NM37_REMOTE"
```

151：在新的 bash 中顺序执行，任何命令失败先停止排查。路径不允许空格。输出、源码恢复目录必须不存在；不要删除失败记录来伪造干净构建。

```bash
set -euo pipefail
cd ~/nm37-transfer
sha256sum -c SHA256SUMS.archives
export NM37_WORK="$HOME/nm37-reproduce"
export NM37_SOURCE_PACKAGE="$NM37_WORK/source-package"
mkdir "$NM37_WORK"
mkdir "$NM37_SOURCE_PACKAGE"
tar -xzf ~/nm37-transfer/nm37-source-transfer.tar.gz -C "$NM37_SOURCE_PACKAGE"
cd "$NM37_SOURCE_PACKAGE"
sha256sum -c SOURCE_SHA256SUMS
export NM37_MAIN="$NM37_SOURCE_PACKAGE/migration-main"
export NM37_DEPS="$NM37_SOURCE_PACKAGE/migration-source-deps"
export NM37_RESTORE="$NM37_SOURCE_PACKAGE/source_bundle.py"
export NM37_TOOLS="$NM37_WORK/integration-tools"
git clone --no-checkout "$NM37_MAIN/main.bundle" "$NM37_TOOLS"
git -C "$NM37_TOOLS" checkout --detach d589b230b975730c108426591e552c056008e82a
git -C "$NM37_TOOLS" remote remove origin
python3 - "$NM37_MAIN/main.json" <<'CHECK'
import json,sys
m=json.load(open(sys.argv[1]))['profiles']
expected={'integration':'d589b230b975730c108426591e552c056008e82a',
'historical-p3-clean':'45d859c1c1bdbd282d0c4ac1e465c0fa0b4e296b',
'a100-reproduction':'0a3a2656df66043ea513a45bab8275c1be0ba76c',
'a110-audited':'25673838e66ddef9031748ca40f7220c65acedaf'}
for k,v in expected.items():
    assert m[k]['commit']==v, k
print('All four source profiles match')
CHECK
```

恢复器会从本地 bundle 自动检出指定 commit 和所需子模块，不需要再运行 `git submodule update`。A70 使用 175 个依赖，A100/A110 使用 173 个；11 个递归排除与20个根目录范围外gitlink有明确记录。这是 NM37 构建所需源范围，不是 Chipyard 所有应用的全量依赖。

## 2. 工具环境（先配置一次）

工具链不在源码包内。目标机需要：Java 20.0.2、firtool 1.75.0、Verilator 5.022、Vivado 2020.2_AR75986（NM37 `xcvu37p_CIV-fsvh2892-2L-e` 支持和有效许可）、RISC-V/libfesvr 与 libriscv、Git/make/C/C++、uv、Python >=3.13，以及 sbt/Maven 下载网络或已备缓存。历史板级构建显式使用 GCC/G++ 13.3.0；记录实际选择，不从 PATH 猜测。以下 `/opt/...` 是必须按151实际安装位置替换的四处路径。

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
umask 022
java -version
firtool --version
verilator --version
vivado -version
"$CC" --version
"$CXX" --version
test -s "$RISCV/lib/libfesvr.a"
test -s "$RISCV/lib/libriscv.so"
python3 -m venv "$NM37_WORK/formal-env"
"$NM37_WORK/formal-env/bin/pip" install -r "$NM37_TOOLS/scripts/nm37-a100/formal-requirements.txt"
export YOSYS="$NM37_WORK/formal-env/bin/yowasp-yosys"
"$YOSYS" -V
```

A100/A110 prepare 会检查指定的四个工具版本，不要删除检查来“跑通”。A70 的 prepare 只记录这些版本，因此也要遵守同一环境要求。共享下载缓存可以，不能复用旧 generator.jar、RTL、Verilator二进制或 DCP。先串行跑 A70→A100→A110；不要同时占用全部内存/许可。换终端后重新设置变量，JAVA_TOOL_OPTIONS 不应带上次配置的 `-Dfsa.*`。

## 3. A70-P3：源码恢复 → 仿真 → 实现

```bash
export A70_TOOLS="$NM37_TOOLS/scripts/nm37-a70"
export A70_SOURCE="$NM37_WORK/a70-p3-source"
export A70_OUT="$NM37_WORK/a70-p3-build"
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
git -C "$A70_SOURCE" rev-parse HEAD
# 下面两步编译生成器 JAR；units/formal 是定向验证。
bash "$A70_TOOLS/run.sh" prepare
bash "$A70_TOOLS/run.sh" assembly
bash "$A70_TOOLS/run.sh" units
bash "$A70_TOOLS/run.sh" formal
# 从这里开始实际编译并执行 Verilator 系统仿真。
bash "$A70_TOOLS/run.sh" sim 4
bash "$A70_TOOLS/run.sh" sim 16
# 仿真全部通过后，再生成 FPGA RTL 和运行完整综合/布局布线。
bash "$A70_TOOLS/run.sh" elaborate
bash "$A70_TOOLS/run.sh" implement
```

预期仿真：4×4 的 seq4/8、16×16 单端口的 seq16/32；每个 `sim*/seq*/result.json` 要求 bitwise_equal=true，sim4.passed.json、sim16.passed.json 绑定实际产物。P3 的 units 包含 ACC 定向回归，formal 为三拍 MAC/EXP2 窗口证明，不涵盖 reciprocal/完整调度形式证明。全部通过后才允许 implement。

### A70 CDC/约束复核及导出

实现完成后检查 `$A70_OUT/physical.passed.json`、`implement.passed.json` 和 `implementation-generated.passed.json`。实现生成物采用141项精确目录；不同工具生成额外文件会拒绝，需要复核而非删除门禁。

A70 的 bitstream 命令还要求 `$A70_OUT/cdc-constraint-review.json`。下面仅生成**未通过的待审模板**，不替你接受CDC结果：

```bash
python3 - <<'REVIEW'
import os,json,hashlib
from pathlib import Path
out=Path(os.environ['A70_OUT'])
obj,=list((out/'A70/fpga').glob('*/obj'))
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()
names=['post_route.dcp','audit/cdc.txt','audit/check_timing.txt',
       'audit/clock_interaction.txt','audit/exceptions.txt']
review={'accepted_for_board_test':False,'board_tested':False,
        'source_lock_sha256':sha(out/'source-lock.json'),
        'analysis':'','known_boundaries':[],
        'evidence_sha256':{n:sha(obj/n) for n in names}}
with (out/'cdc-constraint-review.pending.json').open('x') as f:
    json.dump(review,f,indent=2)
REVIEW
```

根据本次 routed 报告审查时钟关系、复位释放、跨域结构、无约束端点和例外覆盖；记录实际 analysis 和 known_boundaries，明确审查者及证据位置。确认可进入受控板测后才把 accepted_for_board_test 设为 true，并保存为 cdc-constraint-review.json。不能照抄旧DCP的hash或只修改布尔值。历史30条Critical、30个常量时钟端点等只是核查参考，不能自动豁免新报告。

```bash
bash "$A70_TOOLS/run.sh" bitstream
sha256sum "$A70_OUT/bitstream/NM37_A70.bit"
```

## 4. A100：源码恢复 → 仿真 → 实现 → 导出

```bash
export A100_SOURCE="$NM37_WORK/a100-source"
export A100_OUT="$NM37_WORK/a100-build"
export A100_JOBS=8
python3 "$NM37_RESTORE" restore --main "$NM37_MAIN/main.json" \
  --dependencies "$NM37_DEPS/dependencies.json" \
  --profile a100-reproduction --out "$A100_SOURCE"
cd "$A100_SOURCE"
test "$(git rev-parse HEAD)" = 0a3a2656df66043ea513a45bab8275c1be0ba76c
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
```

prepare 含 generator.jar 编译；sim 4/8/16 分别编译并执行 seq=size、2×size 的 Verilator 回归；elaborate 是 FPGA RTL 生成，implement 是完整综合/布局布线。要求对应 sim*.passed.json、units/formal 和 physical.passed.json 均通过。physical 通过不等于真实板测通过；复核本次 CDC/约束报告后导出：

```bash
bash scripts/nm37-a100/run.sh bitstream
sha256sum "$A100_OUT/bitstream/NM37_A100.bit"
```

## 5. A110/APB55：源码恢复 → 仿真 → 实现 → 导出

```bash
export A110_SOURCE="$NM37_WORK/a110-source"
export A110_OUT="$NM37_WORK/a110-build"
export A110_JOBS=8
python3 "$NM37_RESTORE" restore --main "$NM37_MAIN/main.json" \
  --dependencies "$NM37_DEPS/dependencies.json" \
  --profile a110-audited --out "$A110_SOURCE"
cd "$A110_SOURCE"
test "$(git rev-parse HEAD)" = 25673838e66ddef9031748ca40f7220c65acedaf
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

`hbm-startup` 是额外的跨时钟启动/复位仿真，必须通过。若 implement 已完成布局布线但 physical 因 CDC 审查未完成返回非零，保留日志，不重新跑 implement 覆盖现场。根据 `$A110_OUT/cdc-review.required.json` 对本次finding逐条复核，填写同目录 cdc-review.json 的 reviewer、classification、rationale、evidence，并保持 build/checkpoint/CDC SHA 绑定。门禁允许的 classification 与具体含义见当前源码 `scripts/nm37-a110/physical_audit.py`，真实CDC错误不能作为识别问题接受。

```bash
cd "$A110_SOURCE"
bash scripts/nm37-a110/run.sh physical
bash scripts/nm37-a110/run.sh bitstream
sha256sum "$A110_OUT/bitstream/NM37_A110.bit"
```

本节必须从新源码完整构建，不使用旧 DCP 的 reaudit/bitstream-from-reaudit 路线。新网表如果出现时序、审计假设或 CDC 失败，应保存并修复复现问题；当前没有证据保证新主机上上述命令无需任何修复即全部通过。

## 6. 产物位置与逐阶段验收

令 `TOP=chipyard.fpga.nm37.NM37FPGATestHarness.EmptyNM37Config`：

| 配置 | 新 bit | 该 bit 对应配置 JSON | DCP / routed 报告 |
|---|---|---|---|
| A70-P3 | `$A70_OUT/bitstream/NM37_A70.bit` | `$A70_OUT/A70/fpga/$TOP/$TOP.FSAConfig.json` | 同 FPGA 目录 `obj/post_route.dcp`、`obj/report/`、`obj/audit/` |
| A100 | `$A100_OUT/bitstream/NM37_A100.bit` | `$A100_OUT/A100/fpga/$TOP/$TOP.FSAConfig.json` | 同上 |
| A110 | `$A110_OUT/bitstream/NM37_A110.bit` | `$A110_OUT/A110/fpga/$TOP/$TOP.FSAConfig.json` | 同上 |

保存整个各自 OUT 目录：包含源锁/build.json、工具和命令日志、JAR、验证输入/输出、阶段passed文件、RTL/Tcl、DCP、STA/资源/CDC报告、审查记录和bit。保存源码 HEAD、安装后的差异和固定工具版本；A70 还保存 `.nm37-a70/` 安装/patch-state。不要只交付一个 .bit 而丢失对应的构建记录。

验收顺序：固定源/依赖正确 → 生成器编译 → 定向与形式验证 → 系统仿真逐位通过 → 全板setup/hold/pulse/route/DRC与约束/CDC审查 → 新bit导出 → 新bit板测通过。文件存在不等于阶段通过；整个命令必须返回0并产生预期成功记录。时序不能只看 DUT WNS；若 P&R 失败不能继续绕过检查导出。

## 7. 把自己新生成的 bit 制作成新的板测包

下面仅在三配置全部通过本次构建验收后运行。使用固定 integration 中同一套板测软件，并把本次 bit、配置、build/source-lock/验收元数据打包。完整大体积 OUT 目录另行归档；这里不递归把所有仿真/DCP复制一遍。打包工具验证文件身份，不替代前述时序和CDC审查。

```bash
export NM37_REBUILT_BOARD="$NM37_WORK/rebuilt-board"
python3 - <<'PACKAGE'
import os,json,hashlib,subprocess
from pathlib import Path
work=Path(os.environ['NM37_WORK'])
tools=Path(os.environ['NM37_TOOLS'])
top='chipyard.fpga.nm37.NM37FPGATestHarness.EmptyNM37Config'
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()
def item(p):
    assert p.is_file(),p
    return {'path':str(p),'sha256':sha(p)}
configs=[]; attachments=[]
for cfg,prefix,srcvar,outvar in [
    ('A70-P3','A70','A70_SOURCE','A70_OUT'),
    ('A100','A100','A100_SOURCE','A100_OUT'),
    ('A110-APB55','A110','A110_SOURCE','A110_OUT')]:
    source=Path(os.environ[srcvar]); out=Path(os.environ[outvar])
    commit=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    config=out/prefix/'fpga'/top/(top+'.FSAConfig.json')
    configs.append({'configuration_id':cfg,'hardware_source_commit':commit,
        'build_id':'151-rebuild-'+cfg,'source_identity_kind':'built_commit',
        'bitstream':item(out/'bitstream'/('NM37_'+prefix+'.bit')),'config':item(config)})
    metadata=list(out.glob('*.json'))+list((out/'logs').glob('*.json'))
    for p in sorted(metadata):
        attachments.append(dict(**item(p),destination='evidence/'+cfg+'/'+p.relative_to(out).as_posix()))
    if prefix=='A70':
        for p in sorted((source/'.nm37-a70').glob('*.json')):
            attachments.append(dict(**item(p),destination='evidence/'+cfg+'/install/'+p.name))
spec={'schema_version':1,'software_root':str(tools),'configurations':configs,'attachments':attachments}
with (work/'rebuilt-board-spec.json').open('x') as f: json.dump(spec,f,indent=2)
PACKAGE
python3 "$NM37_TOOLS/scripts/nm37-board/package.py" build \
  --spec "$NM37_WORK/rebuilt-board-spec.json" --out "$NM37_REBUILT_BOARD" \
  --archive "$NM37_WORK/rebuilt-board.tar.gz"
python3 "$NM37_REBUILT_BOARD/board-tools/package.py" verify --root "$NM37_REBUILT_BOARD"
sha256sum "$NM37_WORK/rebuilt-board.tar.gz"
```

A70 的 hardware_source_commit 是本次45d859c1，metadata中安装状态和source-lock共同说明实际补丁；不宣称45d859c1裸源就是优化网表。不要填写旧73b3构建、原A100或原A110的build_id冒充新产物。再次重建应使用新 OUT、不同 build_id 和新的打包输出目录。

## 8. 用新 bit 上板，收集新结果

```bash
export NM37_BOARD="$NM37_REBUILT_BOARD"
cd "$NM37_BOARD"
uv sync --frozen --python 3.13 --project software/generators/fsa/python
export FSA_PYTHON="$NM37_BOARD/software/generators/fsa/python/.venv/bin/python"
export NM37_RESULTS="$HOME/nm37-results/rebuilt-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$NM37_RESULTS"
```

继续本次补充文档 [151操作单](151-board-test.md) 的烧录/设备检查/测试步骤。**烧录路径必须指向 `$NM37_REBUILT_BOARD/bitstreams/` 的三份新 bit**；不要使用原板测包中的旧bit或旧manifest，也不要在文档后续初始化命令中把 NM37_BOARD 改回 ~/nm37-board。依次加载 A70-P3.bit、A100.bit、A110-APB55.bit，每次重新确认BDF/XDMA。先smoke再统一seq/seed/20次测量，并使用补充包的 summarize_results.py 汇总。

## 9. 最终交付判据

对外发布前，应在151保存三条完整的新构建记录和三份新bit的真实板测PASS，再把失败/修复过程、最终固定commit、工具环境、依赖包、命令及报告收敛为一个发布版本。**当前还不能称为“接收方已从零复现通过”的交付版本**。如果目标机缺工具、缺下载缓存、编译失败、P&R/CDC门禁失败，应依据日志修复并重新验收；不能用当前服务器的旧通过记录补齐新机器未执行的步骤。用户确认发布前不 push。
