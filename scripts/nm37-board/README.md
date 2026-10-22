# NM37 统一板测与迁移 manifest

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
