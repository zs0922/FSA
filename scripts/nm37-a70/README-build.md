# 历史 NM37 A70 P2 / P3 构建入口

此入口重建历史 P2/P3 的真实代码优化，修复“补丁在 assembly/RTL 之后才应用”的顺序错误。它不启用后来的 M1 ShiftRightJam、控制复制或 A100 PE 流水。P2 和 P3 必须使用不同 checkout、不同输出目录，不能在已经准备好的 P2 树上追加 P3。

| 阶段 | 原始源码 HEAD | 优化 |
|---|---|---|
| P2 | `5acd5e8d5e61729090fc89a7e92d68bd01abcc37` | 控制输出统一延后一拍，ACC 无 P3 流水 |
| P3 | `45d859c1c1bdbd282d0c4ac1e465c0fa0b4e296b` | P2 + ACC 三拍流水及相应调度；PE/CMP 内部仍无流水 |

`install_historical.py` 只接受上表的原始 HEAD。首次安装要求 Git 干净、三个直接打补丁的依赖位于固定提交且干净，拒绝已有安装状态和已生成的 generator/RTL 缓存。它不下载依赖，也不修改 Git 提交或分支。保留失败现场后，安装失败应换新的 checkout。

## 恢复源码和安装

以下变量均由调用者指定，必须使用独立目录。`A70_TOOLS` 指向包含本 README 的新工具目录，可以来自迁移包中的集成源码；它与待构建的历史源码目录分开。

```bash
export A70_TOOLS=/absolute/path/integration/scripts/nm37-a70
export A70_SRC=/absolute/path/a70-p3-source
export A70_OUT=/absolute/path/a70-p3-output
export A70_STAGE=p3

# 从迁移包的主仓 bundle 恢复；离线包必须包含全部所需子模块对象。
git clone --no-checkout /absolute/path/a70-p3-root.bundle "$A70_SRC"
git -C "$A70_SRC" checkout --detach 45d859c1c1bdbd282d0c4ac1e465c0fa0b4e296b

# 使用迁移包的依赖恢复工具，按原始 gitlink 恢复依赖；不要更新到上游最新版本。
# 只有能访问并取得全部固定对象时，才可直接用普通 submodule update。
# git -C "$A70_SRC" submodule update --init --recursive

python3 "$A70_TOOLS/install_historical.py" drycheck --root "$A70_SRC" --stage "$A70_STAGE"
python3 "$A70_TOOLS/install_historical.py" install --root "$A70_SRC" --stage "$A70_STAGE"
python3 "$A70_SRC/scripts/nm37-a70-install.py" verify --root "$A70_SRC" --stage "$A70_STAGE"
```

P2 将阶段改为 `p2`，checkout `5acd5e8d5e61729090fc89a7e92d68bd01abcc37`，并换两个新目录。不能使用 A100/A110 的源码 checkout 代替此历史版本。

安装器核对原始 Makefile SHA 后进行精确转换，复制当前严格 prepare/source-lock 工具及单端口仿真 Config；P3 另外安装四个独立 oracle 测试源。补丁顺序是：

- shell：固定 `d7c3532` + **累计** `p0p1` + `nm37`；不得先打 `u280` 再追加累计补丁。
- FSA：固定 `f4051d9` + `u280` + `p2` +（仅 P3）`p3` + `nm37`。
- easyfloat：固定 `44e1c36` +（仅 P3）`p3`。

每个补丁先 `git apply --check` 再应用，冲突立即失败。`.nm37-a70/install-state.json` 记录安装文件哈希、权限、阶段及 patch-state 哈希；重复 install 只验证，不重写文件或状态。`prepare_historical.py` 不包含新增测试文件的最终源锁，因此正式执行前还必须完成下面的全源 capture。

## 完整发布入口

本目录 `run.sh` 是后续完整阶段的统一入口。安装器先准备历史源码；环境工具配置完成后，以一个**尚不存在**的输出目录执行：

```bash
export A70_SOURCE="$A70_SRC"
export A70_STAGE=p3
export A70_OUT=/absolute/path/a70-p3-release
bash "$A70_TOOLS/run.sh" prepare
bash "$A70_TOOLS/run.sh" assembly
bash "$A70_TOOLS/run.sh" units       # 仅 P3
bash "$A70_TOOLS/run.sh" formal      # 仅 P3
bash "$A70_TOOLS/run.sh" sim 4
bash "$A70_TOOLS/run.sh" sim 16
bash "$A70_TOOLS/run.sh" elaborate
bash "$A70_TOOLS/run.sh" implement
# 完成该构建实际 CDC、约束边界及整板时序验收后再运行：
bash "$A70_TOOLS/run.sh" bitstream
```

P2 跳过 units/formal 两步。以最终发布入口实际支持的验收门禁为准；不能把早期手工预跑日志复制成这个输出目录的通过记录。

`formal` 使用 `verify/window_proof/run-proof.py`：将实际 emit 的 ACC miter 精确展开三个上升沿，在确认输出锥不存在初始寄存器状态依赖后，用 SAT 同时证明原始 mismatch 和未被 valid 屏蔽的数据 mismatch 为零。输入、复位与初始状态均无约束；证明范围是 MAC/EXP2 的三拍等价，不包括 reciprocal 或完整调度。入口同时执行 12 项展开器语义/拒绝用例，保留 RTL、工具及证明产物哈希。旧 `verify_equivalence.py` 保留为历史归纳证明尝试，不作为默认入口；本次旧尝试因耗时中止，不能标为通过。

下面的底层示例用于解释源锁和 prepare→make 顺序，使用**另外一个新输出目录**，不要与上述发布输出混用。

## 编译前冻结及正确的调用顺序

先配置匹配的 Java、firtool、Vivado、Verilator、RISC-V/libfesvr 与 Python 环境，记录具体版本/二进制身份。共享依赖下载缓存可以复用，generator JAR、RTL、仿真器和布局布线结果必须重新生成。源码锁排除明确的 `target`、`.ivy2`、`.sbt`、`.classpath_cache` 等缓存/生成目录；所有最终输出放在源码目录之外。

```bash
# A70_OUT 必须尚不存在。
mkdir "$A70_OUT"
mkdir "$A70_OUT/logs"
python3 "$A70_SRC/scripts/nm37-a70-source-lock.py" capture \
  --source "$A70_SRC" --lock "$A70_OUT/source-lock.json"

export USE_CHISEL6=1
# 工具路径、RISCV、FSA_PYTHON、JAVA_TOOL_OPTIONS 等由本机环境提供。
# 不得沿用原服务器的绝对路径，也不得隐式注入 A100/A110 系统属性。
"$A70_SRC/scripts/nm37-a70-build.sh" make -C "$A70_SRC/fpga" \
  SUB_PROJECT=nm37 CONFIG=EmptyNM37Config \
  GENERATOR_CLASSPATH="$A70_OUT/generator.jar" "$A70_OUT/generator.jar" \
  > "$A70_OUT/logs/assembly.log" 2>&1

python3 "$A70_SRC/scripts/nm37-a70-source-lock.py" verify \
  --source "$A70_SRC" --lock "$A70_OUT/source-lock.json"
```

`nm37-a70-build.sh` 在独立 Python 进程中完成安装文件及完整 patched-tree 验证，然后才 `exec` 调用者的 make/sbt 命令；这样 make 在解析源文件列表之前已经具备完整源树。Makefile 同时将 patch-state 作为 generator JAR 与 FIRRTL/annotation 的正常依赖，以防直接 make 遗漏顺序。不要用只修改 bitstream target 的 order-only 依赖代替此流程。

后续仿真和板级 elaboration 使用相同 wrapper，显式传入已冻结的 JAR，并在每阶段前后 verify 全源清单。P3 的定向 units / emit 使用如下 Test/sources 白名单：

```bash
"$A70_SRC/scripts/nm37-a70-build.sh" java -jar "$A70_SRC/scripts/sbt-launch.jar" \
  'set fsa / Test / sources := (fsa / Test / sources).value.filter(f => Set("A70AccumulatorVerification.scala", "A70LegacyExp2.scala", "A70LegacyFMA.scala", "A70LegacyReciprocal.scala").contains(f.getName))' \
  'fsa/Test/runMain fsa.A70AccumulatorVerification'
```

历史 `ControlSignalTest.scala` 的构造 API 已过时，**没有在此流程执行，也未对它打兼容补丁**。四个新增测试源用于定向 ACC 验证；完整功能还需要独立系统 golden 回归。P2 以系统功能回归为主，不能借用 P3 的 units 结果。

安装成功不代表 FPGA 验收通过。每组必须保存独立的源锁、JAR/RTL 哈希、验证日志、实际综合布局布线报告、CDC 审查和 bitstream SHA；完整时序/功能通过后才可将 bitstream 纳入板测清单。

## 与当前历史修复提交及迁移 bundle 的关系

本次已有两份阶段修复提交：

- P2：`f7d31bb197b93eef9dc416d0508e550894531665`，在顺序修复 `6902c6d` 之后修正单端口仿真配置的 256-bit 接口宽度；父链包含原始 P2。
- P3：`73b3e42f3006818d13bc059818e11cc803f78dba`，在顺序修复 `de77b56` 之后作相同仿真配置修正；父链包含原始 P3。

这两个提交保存了最初的 Makefile 顺序修复、单端口 Config 和 prepare 工具。安装器精确复现相同 Makefile/Config 改动，并增加当前安装/源锁/调用 wrapper 与验证 overlay。它不会自动接管这两个已修复、可能已有构建状态的工作区。当前实际构建若基于上述修复提交，报告应保留该身份及后续独立 overlay 哈希，不应声称与新历史 checkout 的 Git HEAD 相同。

离开原机器前，迁移包至少需要：

1. **工具集成提交**：包含本目录、板测入口与迁移清单；必须是本地已提交版本。
2. **P2/P3 阶段根提交及历史父链**：将上面两个修复提交都纳入 bundle/ref。只打包主集成分支不一定包含这些旁支/分离 HEAD 提交。
3. **所有实际消费子模块的固定对象**：包括 FSA `f4051d9…`、easyfloat `44e1c36…`、shell `d7c3532…` 以及源锁列出的递归依赖。主仓 bundle 不包含子模块对象，`--shared` clone 的 alternates 也不是迁移交付物。
4. 验证 overlay、环境/工具版本清单、bitstream、对应 FSAConfig、时序/资源报告与哈希清单。

可分别用阶段 checkout 的 `git bundle create <output> HEAD` 保留两个修复提交及可达历史，再由迁移工具导出子模块对象。打包完成后，应在不依赖原机器 `.git/objects`、工作区绝对路径或共享缓存的目录做提取和源码恢复检查。Vivado 安装、许可和板卡驱动属于目标服务器环境，不能由 Git bundle 代替。

目前仅准备本地构建/迁移材料；不在安装器中连接 `10.128.157.151`，不烧板、不 push。

Vivado 实施阶段新增工程/IP文件的精确分类、独立封印及旧构建恢复规则见 [实施生成物门禁](implementation-generated-README.md)。本次已审目录为 P3 的 141 项；其它布局必须显式复核，原始 RTL/Tcl 变化仍拒绝。

可直接查看 [三拍窗口形式入口](verify/window_proof/run-proof.py) 与 [实施生成物分类/封印工具](implementation_generated.py)。
