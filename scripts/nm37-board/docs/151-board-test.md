# NM37：151 服务器迁移、烧录、板测及结果汇总

本流程尚未在 10.128.157.151 执行，不代表上板通过。暂时不要 push。

三配置为 A70-P3（重新构建的历史 P3，不是后来的 M1 ShiftRightJam A70）、A100、A110-APB55（DUT 110 MHz / HBM APB 55 MHz）。P2 时序失败，不参与板测。板测无需 clone FSA；源码包用于后续重建。Vivado、驱动、Python 环境和工具链不包含在归档内。

## 1. 从当前服务器传输

将 YOUR_USER 替换为实际登录用户名。正式归档保持原哈希不变；新增汇总脚本和本操作单作为补充文件传输，不覆盖包内已校验文件。

```bash
export NM37_REMOTE=YOUR_USER@10.128.157.151
export NM37_DELIVERY=/home/zhangsi/tmp/FSA-m1/nm37-a70-rebuild-20261022/delivery-final
export NM37_TOOLS=/home/zhangsi/tmp/FSA-m1/nm37-a70-rebuild-20261022/src/scripts/nm37-board
ssh "$NM37_REMOTE" 'mkdir -p ~/nm37-transfer ~/nm37-tools'
cd "$NM37_DELIVERY"
scp nm37-board-transfer.tar.gz nm37-source-transfer.tar.gz SHA256SUMS.archives \
  "$NM37_REMOTE:~/nm37-transfer/"

# 补充工具和文档；保留相对文件名生成校验单。
cd "$NM37_TOOLS"
sha256sum summarize_results.py > /tmp/nm37-summary-SHA256SUMS
scp summarize_results.py "$NM37_REMOTE:~/nm37-tools/"
scp /tmp/nm37-summary-SHA256SUMS "$NM37_REMOTE:~/nm37-tools/SHA256SUMS"
scp docs/151-board-test.md "$NM37_REMOTE:~/nm37-tools/"
ssh "$NM37_REMOTE"
```

## 2. 在 151 校验并准备环境

```bash
cd ~/nm37-transfer
sha256sum -c SHA256SUMS.archives
cd ~/nm37-tools
sha256sum -c SHA256SUMS
# 下列目录应为新目录；已有时停止并选新目录，不覆盖旧环境。
mkdir ~/nm37-board
tar -xzf ~/nm37-transfer/nm37-board-transfer.tar.gz -C ~/nm37-board
export NM37_BOARD="$HOME/nm37-board"
export NM37_RESULTS="$HOME/nm37-results/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$NM37_RESULTS"
printf '%s\n' "$NM37_RESULTS"  # 保存本次批次路径；换终端时重新 export 此路径
cd "$NM37_BOARD"
python3 board-tools/package.py verify --root .
```

必须先确认 SHA 校验和 package verify 均成功。归档 SHA 以可信的交付回执为准；不要只依赖同一未知来源的清单。

```bash
uname -a
cat /etc/os-release
command -v python3 uv vivado hw_server lspci timeout
uv --version
vivado -version
lspci -Dnn -d 10ee:
lsmod | grep xdma
modinfo xdma
ls -l /dev/xdma*
```

Vivado 尚未加入 PATH 时，加载服务器实际安装位置的 `settings64.sh`；本次构建使用 Vivado 2020.2。缺少 uv、XDMA 驱动、JTAG 连接时先补齐。不要照抄其他机器的 PCIe 解绑/驱动卸载命令。

```bash
uv sync --frozen --python 3.13 --project software/generators/fsa/python
export FSA_PYTHON="$NM37_BOARD/software/generators/fsa/python/.venv/bin/python"
"$FSA_PYTHON" --version
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
cp ~/nm37-transfer/SHA256SUMS.archives "$NM37_RESULTS/"
```

安装 Python 依赖需要网络或预备 wheels/原生编译依赖；失败后停止，不改锁文件绕过。结果放在包外；安装生成的 `.venv`/`__pycache__` 是运行环境例外。

## 3. 烧录：逐个选择真实 JTAG 器件

先停止其他访问同一板卡的程序。启动 Hardware Manager：

```bash
mkdir -p "$NM37_RESULTS/programming"
vivado -mode gui -log "$NM37_RESULTS/programming/vivado.log" \
  -journal "$NM37_RESULTS/programming/vivado.jou"
```

在 Vivado Tcl Console 执行以下命令。若已打开 Hardware Manager/已连接 hw_server，使用现有连接即可。target、器件和用户名必须替换为核对后的真实值：

```tcl
open_hw_manager
connect_hw_server
get_hw_targets
open_hw_target [get_hw_targets {实际完整target名称}]
get_hw_devices
set dev [get_hw_devices {实际器件名}]
current_hw_device $dev
set_property PROGRAM.FILE {/home/实际用户名/nm37-board/bitstreams/A70-P3.bit} $dev
program_hw_devices $dev
refresh_hw_device $dev
```

这是易失 FPGA 配置，不写 Flash。保存板卡型号/序列号、JTAG target、器件名、编程成功日志及时间。加载下一配置时重新设置 PROGRAM.FILE 并执行 program/refresh：

| 顺序 | 文件 | 终端变量 |
|---|---|---|
| 1 | `bitstreams/A70-P3.bit` | `export CFG=A70-P3` |
| 2 | `bitstreams/A100.bit` | `export CFG=A100` |
| 3 | `bitstreams/A110-APB55.bit` | `export CFG=A110-APB55` |

每次换 bit 后完整重复第 4～6 节；不能只改 CFG 而不重新烧录。A110 失锁/冷复位后重新初始化并装载数据，不续跑旧任务。

## 4. 每次烧录后重新确认 PCIe/XDMA

```bash
lspci -Dnn -d 10ee:9038
ls -l /dev/xdma*
ls -l /sys/class/xdma/
# 以下设备/BDF只是示例，替换为当前 NM37 的真实映射。
export CFG=A70-P3
export DEVICE=xdma0
export BDF=0000:81:00.0
readlink -f "/sys/bus/pci/devices/$BDF/driver"
ls -l "/dev/${DEVICE}_user" "/dev/${DEVICE}_h2c_0" "/dev/${DEVICE}_c2h_0"
```

要求驱动为 xdma，三个字符设备属于给定 BDF，当前用户有读写权限。脚本会核对 `/sys/dev/char` 映射。`10ee:9038` 本身不能唯一证明板卡型号，需结合实体板卡/JTAG。枚举异常时先按该服务器实际方式恢复 PCIe/驱动，必要时重启，再重新确认；不要猜 XDMA 编号。

```bash
cd "$NM37_BOARD"
set -o pipefail
export BIT_SHA=$(sha256sum "bitstreams/$CFG.bit" | cut -d ' ' -f1)
export CFG_RESULTS="$NM37_RESULTS/$CFG"
mkdir "$CFG_RESULTS"
{
  date -u
  echo "configuration=$CFG"
  echo "device=$DEVICE"
  echo "bdf=$BDF"
  sha256sum "bitstreams/$CFG.bit"
  lspci -Dnn -s "$BDF"
  readlink -f "/sys/bus/pci/devices/$BDF/driver"
  ls -l "/dev/${DEVICE}_user" "/dev/${DEVICE}_h2c_0" "/dev/${DEVICE}_c2h_0"
} > "$CFG_RESULTS/device-and-bit.txt" 2>&1
cp "$CFG.manifest.json" "$CFG_RESULTS/"
```

在该目录另存本次 JTAG/序列号/烧录时间。`--loaded-bitstream-sha256` 是操作员声明，不是硬件读回；频率也不是现场测量值。

## 5. Dry-run 和小规模真实验证

```bash
python3 board-tools/board_test.py --manifest "$CFG.manifest.json" \
  --device "$DEVICE" --bdf "$BDF" --loaded-bitstream-sha256 "$BIT_SHA" \
  --seq 16 --seed 0 --out "$CFG_RESULTS/preflight" --dry-run
```

Dry-run 不访问设备、不生成真实工作负载。成功后执行：

```bash
timeout --signal=TERM --kill-after=10s 600s "$FSA_PYTHON" board-tools/board_test.py \
  --manifest "$CFG.manifest.json" --device "$DEVICE" --bdf "$BDF" \
  --loaded-bitstream-sha256 "$BIT_SHA" --seq 16 --seed 0 --warmup 0 --repeat 1 \
  --out "$CFG_RESULTS/smoke-seq16" 2>&1 | tee "$CFG_RESULTS/smoke-seq16.console.log"
NM37_RC=$?
echo "$NM37_RC" > "$CFG_RESULTS/smoke-seq16.exit-code.txt"
```

退出码必须为 0，再检查根目录 PASS：

```bash
python3 - "$CFG_RESULTS/smoke-seq16/result.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
assert r['passed'] is True and r['hardware_tested'] is True
print('Hardware smoke test PASS')
PY
```

失败/超时后停止，保留全部输出。timeout 不证明硬件已经停止；先复位/重新加载并核对设备，以新目录重试，不覆盖旧记录。

## 6. 正式性能测试

所有配置使用同一软件环境、seq、seed、预热/重复次数。不同板也串行执行，避免旧 engine `/tmp` 调试文件冲突。外部 timeout 不可省略。

```bash
for SEQ in 16 32 64 256 1024; do
  timeout --signal=TERM --kill-after=10s 3600s "$FSA_PYTHON" board-tools/board_test.py \
    --manifest "$CFG.manifest.json" --device "$DEVICE" --bdf "$BDF" \
    --loaded-bitstream-sha256 "$BIT_SHA" --seq "$SEQ" --seed 0 --warmup 1 --repeat 20 \
    --out "$CFG_RESULTS/seq${SEQ}-seed0" \
    2>&1 | tee "$CFG_RESULTS/seq${SEQ}-seed0.console.log"
  NM37_RC=$?
  echo "$NM37_RC" > "$CFG_RESULTS/seq${SEQ}-seed0.exit-code.txt"
  if [ "$NM37_RC" -ne 0 ]; then
    echo '失败或超时，停止当前配置后续测试'
    break
  fi
done
```

每个输出目录必须不存在。整个批次采用相同测试矩阵；超时预算包含运行前 reference 生成。如果要增加预算，保留失败记录，用明确的新批次目录重试。

## 7. 自动汇总

脚本使用 Python + numpy，不需要打开设备，也不需要 Vivado、原源仓或访问网络。必须指向一个明确选定的三配置测试批次。默认 `--min-repeat 20` 排除 seq16 单次 smoke；预热也排除。排除记录列在报告中，不代表被排除测试通过。

```bash
"$FSA_PYTHON" ~/nm37-tools/summarize_results.py \
  --results "$NM37_RESULTS" --min-repeat 20 \
  --out "${NM37_RESULTS}-summary"
cat "${NM37_RESULTS}-summary/comparison.md"
```

输出目录必须是新目录。脚本递归读取 run.json；正式测试未完成/失败、缺少三配置中的任意一个、同 seq/seed/config 有重复批次、工作负载或软件/运行环境不同，会返回非零并拒绝生成比较表。不要删除失败记录制造通过；需要重试时保留原批次，明确选择独立的完整新批次再汇总。仅在明确做短测试比较时使用较小的 `--min-repeat`；这样可能选入 smoke 并触发重复拒绝。

| 文件 | 内容 |
|---|---|
| `comparison.md` | 可直接阅读的时间、加速比、bubble、活动周期表 |
| `comparison.csv` | 每配置/seq/seed 的 min/median/max 与加速比，方便表格作图 |
| `measurements.csv` | 非预热的逐次执行时间、bubble 占比和全部十计数器 |
| `summary.json` | 聚合、逐次数据、工作负载、硬件身份、来源路径/哈希及排除清单 |

脚本核对 root/逐次结果、原始输入数组哈希、指令文件、reference/actual 文件哈希及逐位 fp32 输出（包含 ±0 区别），重算计时和 enq/deq 模 2^32 增量。三配置必须有相同 seq/seed、输入/指令/reference、配置内容、软件/easyfloat清单、runtime版本、Python/hostname、计时口径及预热/重复策略；不跨主机或跨软件版本混算。各配置在不同 seq 下的硬件构建身份也必须一致。脚本验证保存的证据一致性，不证明物理加载身份或真实时钟。

## 8. 时间、加速比及 bubble 的口径

- `execTime` 是 FPGA active 周期，`exec_us = execTime / clock_mhz`，分别除以 70/100/110。不是 `mxActive / clock_mhz`。
- FPGA 加速比 = A70-P3 `exec_us` 中位数 / 当前配置中位数；主机加速比用 `host_execute_ms` 独立计算。大于 1 表示更快，不将多个 seq 或 seed 混合。
- `host_execute_ms` 包含输入传输、序列化、主机供指、poll、调试输出和结果读回，排除 reference、kernel准备与结果文件保存；不等于纯 PCIe 时间。
- `mxBubble` 是矩阵指令接口 ready && !valid 周期。表中 bubble 占比是逐次 `100*mxBubble/execTime` 的中位数，**不是 PE 总空闲率**；ready=0 的依赖等待需波形辅助分析。
- bubble 变化 = `(当前 mxBubble 中位数 / A70-P3 中位数 - 1)*100%`；负值表示减少，基线为零时 N/A。
- `mxActive`、`dmaActive` 与 bubble 不能简单相加得到执行时间。八项性能计数器为 uint32，脚本不修复也无法仅凭保存值可靠检测溢出；enq/deq 增量单独按模 2^32 核对。
- A70-P3→A100 包含流水和其他结构变化；不能把差异全部归于提频。资源/STA/CDC 使用各 bit 绑定构建报告，板测不能反推出资源或证明完整时序。

## 9. 保存与回传

保留整个结果批次：environment、python-packages、programming、device-and-bit、manifest、console日志/退出码、run.json、inputs.npz、instructions.bin、reference.npy、各 run-NNN 的 actual.npy/result.json、根 result.json 和失败记录。输入比较使用数组原始字节哈希，不用 npz 容器哈希。

所有测试结束，关闭 Vivado 以保存完整日志后执行：

```bash
cp ~/nm37-tools/summarize_results.py "${NM37_RESULTS}-summary/"
sha256sum "${NM37_RESULTS}-summary/summarize_results.py" \
  > "${NM37_RESULTS}-summary/summarizer.sha256"
tar -czf "${NM37_RESULTS}.tar.gz" -C "$(dirname "$NM37_RESULTS")" "$(basename "$NM37_RESULTS")"
tar -czf "${NM37_RESULTS}-summary.tar.gz" -C "$(dirname "$NM37_RESULTS")" "$(basename "$NM37_RESULTS")-summary"
sha256sum "${NM37_RESULTS}.tar.gz" "${NM37_RESULTS}-summary.tar.gz" \
  > "${NM37_RESULTS}-archives.sha256"
```

如汇总失败，仍先归档原始结果用于排查，不执行不存在的 summary 归档命令。将原始结果包、汇总包及校验单带回后再验收并本地提交；本流程不 push。后续重建见 [源码重建说明](source-reproduction.md)。
