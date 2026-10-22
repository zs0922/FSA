# 10.128.157.151 板测操作单

交付板测包包含 A70-P3、A100、A110-APB55 三个配置。A70-P3 是本次修复构建顺序后重建的历史 P3，不是 M1 ShiftRightJam A70；未通过完整时序的 P2 不进入板测 manifest。以实际 `package.json` 配置列表为准。

板测无需 clone FSA；包内已有同一套 Python 软件、easyfloat、配置和脚本。源码重建另用 source archive；Vivado、许可、驱动及 Python 安装依赖不在两个包内。本操作单是待执行步骤，不能作为已烧录或已通过板测的证据。

## 1. 搬运与校验

发布端在正式归档生成后运行以下示例；`USER` 必须替换为151服务器的实际用户名：

```bash
scp nm37-board-transfer.tar.gz nm37-source-transfer.tar.gz SHA256SUMS.archives \
  USER@10.128.157.151:/work/nm37-transfer/
ssh USER@10.128.157.151
cd /work/nm37-transfer
sha256sum -c SHA256SUMS.archives
```

先通过可信渠道核对 `SHA256SUMS.archives`，不要仅信任从同一未知来源收到的清单。校验通过后可用受信任系统 tar 解包到新目录；也可使用已审查的 `package.py extract --archive ... --sha256 ... --out ...` 安全解包板测归档。

```bash
mkdir /work/nm37-board
tar -xzf nm37-board-transfer.tar.gz -C /work/nm37-board
cd /work/nm37-board
python3 board-tools/package.py verify --root .
uv sync --frozen --python 3.13 --project software/generators/fsa/python
export FSA_PYTHON=/work/nm37-board/software/generators/fsa/python/.venv/bin/python
```

Python 安装需要网络或预备 wheel/原生编译依赖。板测结果写到包外 `/work/nm37-results/`，不修改不可变清单中的文件。软件安装产生的 `.venv`/`__pycache__` 属运行环境例外，不加入源码 SHA。

## 2. 每换一个 bit 都重新确认设备

1. 在 Vivado Hardware Manager 中核对 NM37器件/JTAG序列号，手动加载所选 `bitstreams/<配置>.bit`。不要写Flash，不自动选择板卡。保存编程日志和该文件SHA。
2. 根据实际服务器情况恢复 PCIe 枚举/XDMA 驱动，再运行 `lspci -Dnn`，检查 `/sys/class/xdma` 与 `/dev/xdmaN_*`。确认 `10ee:9038` 对应的实际 NM37、BDF和XDMA编号；换bit后编号可能变化。
3. 以下示例中的 `xdmaN`、BDF必须替换为刚确认的值。`--loaded-bitstream-sha256` 是操作者对已加载文件的声明，脚本不能读回硬件证明它，也不实测时钟。

以 A70-P3 为例；之后对 A100、A110-APB55 分别手动重新加载，再改 `CFG` 和设备映射。不要用循环在未换bit时连续测试三个manifest。

```bash
cd /work/nm37-board
export CFG=A70-P3
export DEVICE=xdmaN
export BDF=0000:81:00.0
export BIT_SHA=$(sha256sum "bitstreams/$CFG.bit" | cut -d ' ' -f1)
python3 board-tools/board_test.py --manifest "$CFG.manifest.json" \
  --device "$DEVICE" --bdf "$BDF" --loaded-bitstream-sha256 "$BIT_SHA" \
  --seq 16 --seed 0 --out "/work/nm37-results/$CFG-preflight" --dry-run
```

Dry-run 不访问设备/sysfs、不生成真实工作负载，只检查输入身份和参数。通过后才执行一次小规模实测：

```bash
timeout --signal=TERM --kill-after=10s 600s "$FSA_PYTHON" board-tools/board_test.py \
  --manifest "$CFG.manifest.json" --device "$DEVICE" --bdf "$BDF" \
  --loaded-bitstream-sha256 "$BIT_SHA" --seq 16 --seed 0 --warmup 0 --repeat 1 \
  --out "/work/nm37-results/$CFG-smoke-seq16"
```

确认根目录 `result.json` 通过后，收集相同序列/seed的性能数据，三配置均执行相同范围：

```bash
for SEQ in 16 32 64 256 1024; do
  timeout --signal=TERM --kill-after=10s 3600s "$FSA_PYTHON" board-tools/board_test.py \
    --manifest "$CFG.manifest.json" --device "$DEVICE" --bdf "$BDF" \
    --loaded-bitstream-sha256 "$BIT_SHA" --seq "$SEQ" --seed 0 --warmup 1 --repeat 20 \
    --out "/work/nm37-results/$CFG-seq$SEQ-seed0" || exit $?
done
```

每次 `--out` 必须不存在，失败也不覆盖。超时/失败后停止测试并保留退出码、目录和终端日志；timeout不能证明硬件已停止，需复位/重新加载并重新核对设备后再用新目录重试。各板串行执行，避免旧engine的 `/tmp` 调试文件相互覆盖。外部timeout不可省略。

## 3. 验收与比较

每次要求实际输出与reference逐位一致、enq/deq本次增量等于指令字数，并保存根目录PASS。预热也验证，但不计入统计。比较三配置时先核对相同seq、seed、输入原始哈希、指令哈希及软件哈希；再比较 `execTime`、按70/100/110MHz分别换算的active时间、`host_execute_ms` 和十计数器。主机计时含输入写入、提交/poll和输出读回，排除reference；`mxBubble`不是PE总空闲比例。

资源/STA/CDC使用各bit绑定的构建证据，不能由板测反推。保留板卡/JTAG/BDF/驱动信息、手动加载日志、bitSHA和所有运行目录。151上的真实测试通过后再整理结果并决定push；本操作单和脚本均不执行push。

源码恢复及A70/A100/A110逐阶段编译入口见 [源码重建说明](source-reproduction.md)。源码归档单独解包到新目录并核验其文件清单；仅源包不能代替目标机工具链安装。
