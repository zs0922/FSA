# FSA 优化路线图（实测数据版）

> 更新日期：2026-09-24
> 数据来源：
> - 板上实测（2026-09，资源线 P1 bitstream，`U280FPGATestHarness-p1.bit`，seq=256/1024）
> - Current 构建报告（2026-09-14，`fpga/generated-src/.../EmptyU280Config/obj/report/`）
> - baseline/P1/P2/P3 历史报告（本目录）
> - RTL 源码（`generators/fsa/`、`generators/easyfloat/`）
> 阅读约定：所有"拍"均为 dut 域 70MHz 时钟拍；file:line 以 2026-09 主线源码为准。

---

## 1. 性能基线（板上实测，权威数字）

### 1.1 原始计数器

| 指标 | seq=256 | seq=1024 |
|---|---:|---:|
| 指令字数（Raw） | 4,577 | 70,529 |
| MX / DMA / Fence 指令 | 800 / 544 / 1 | 12,416 / 8,320 / 1 |
| Execution time | 1,079,414 拍（15.42 ms） | 16,394,771 拍（234.2 ms） |
| Mx bubble | 1,050,321（97.3%） | 15,951,180（97.3%） |
| Mx active | 36,704（3.4%） | 579,968（8.28 ms，3.5%） |
| DMA active | 25,495（2.4%） | 393,568（5.6 ms，2.4%） |
| 每字供给成本 | **3.37 µs** | **3.32 µs**（恒定！） |

两次运行的 overlap（bubble 与 active 同拍叠加，见 §1.3）分别为 7,611 / 136,377 拍。

### 1.2 派生结论

1. **执行时间严格正比于指令字数**（15.19× 时间 vs 15.41× 字数），与计算量无关
   → 当前第一瓶颈 = 主机指令供给（`xdma_mmio.py:163-172` 每字 mmap+写+munmap）
2. 稳态每 j-迭代 MX active = **141.6 拍**（两次运行一致，双 FSM 重叠已在起作用）
3. 总 MAC = 2×seq²×16（seq1024 为 33.55M）：
   - 阵列 active 利用率 = 33.55M / (579,968×256) = **22.6%**
   - 墙钟利用率 = **0.8%**；墙钟吞吐 0.29 GFLOP/s vs 70MHz×256PE 峰值 35.8 GFLOP/s
4. DMA 每 j-迭代仅 ~96 拍 active，**HBM 带宽/延迟当前不是短板**（利用率 <1%，32 个
   pseudo-channel 只用了 1 个）
5. 修完指令供给后的理论下限 ≈ MX 限制 ≈ 4096×(130~160) ≈ 0.55~0.66M 拍 ≈ **8~9.5 ms**
   （seq1024，对现状 234ms 约 25×）

### 1.3 计数器语义备忘（容易误读）

| 计数器 | 定义（AXI4FSA.scala:176-201） | 备注 |
|---|---|---|
| execTime | state==active 的每拍（SET_ACTIVE → fence stop） | 含主机喂字等待 |
| mxBubble | 首条 MX 入核后，核输入饥饿拍（ready 且无 valid） | "Max"为 Mx 笔误；**与 active 可同拍叠加**；成因含"等指令字"与"等信号量(数据)"两种 |
| mxActive | 任一 FSM 执行中（fsa.io.busy） | |
| dmaActive | Load/Store 队列头过完信号量、正在搬数的拍 | 等信号量的拍**不计** |
| Enqueue/Dequeue | 指令队列入/出队字数 | **上电累计，不随 SET_ACTIVE 复位**（AXI4FSA.scala:52-60 在 perfCounters 之外）；perf 计数器在 done→active 时清零 |

### 1.4 单迭代调度基线（ExecutionPlan.scala 锚点推导，rows=cols=16）

| 指令 | 计划时长 | conflictFree 拍 | 备注 |
|---|---:|---:|---|
| load_stationary | ≈16 | 15 | 每个 j 迭代都要重装 Q（P 覆盖 Q） |
| attn_score | ≈157 | 77 | 关键锚：phase2=2R+2C+10=74，exp2@76，sumAnchor@78，deltaBeat=104，rowSumBeat=140 |
| attn_value | ≈80 | 30 | oDrainBeat=2R+2C−1=63 |
| reciprocal | ~15-20（阻塞） | 结束 | 倒数多拍，i 边界 |
| lse_norm | ≈19（阻塞） | 结束 | i 边界 |

---

## 2. 优化点 A：指令供给链（当前第一瓶颈）

### A1. 持久 mmap 批量写指令 【首选，纯 Python，不动 bit】

- **现状证据**：§1.1 每字 3.3µs 恒定；`xdma_mmio.py` `_access()` 对每 4 字节做一次
  mmap 系统调用 + 一次 32-bit store + 一次 munmap 系统调用（L112/L137），两个系统调用
  占成本 95% 以上
- **改法**：`MMIO` 对象持有 `dict[页基址 → 映射指针]` 缓存；`_access` 从缓存取页指针；
  `dev_queue_mmio_write` 循环外一次性 `ctypes.cast` 出 `POINTER(c_uint32)`，循环体只做
  `ptr.contents.value = data`；`close()` 统一 munmap。文件头注释警告的"双重写入"是
  历史 mmap 模块封装问题，保持 libc 原生 mmap 即可规避
- **安全性论证（不丢字）**：指令队列写口由 `RegField.w(32, Decoupled)` 实现
  （regmapper/RegField.scala:72），写仅在 `enq.ready` 时 fire、否则挂起重试
  （RegMapper.scala:155,174）→ 队满沿 AXI-Lite → XDMA → PCIe 链路级流控逐级反压，
  最终主机 store 指令自停，无损。队列 256 字 + 下游缓冲 ~40 字，主机突发不可能写穿
- **预期**：每字 ~0.1µs；seq1024 的 70,529 字 ≈ 7~14 ms；execTime 总计落到 ~10~15 ms
  （**~20×**）
- **验证协议**：
  1. seq256：execTime 1.08M → **5~15 万拍**；Enqueue−Dequeue==0；精度行与修复前逐位一致
  2. seq1024：execTime → **0.6~2M 拍**；精度不变
  3. 连跑两次：第二次 execTime 应相同（验证计数器复位、无状态残留）
  4. 兜底：若 STATE 轮询 30s 无响应（WC 映射等意外）→ 退化为"每 256 字插一次 STATE 读"
     节流版；仍不行整体回退
- **残余风险**：修复后主机供给（7~14ms）与计算（8~9.5ms）同量级 → 长序列会再次
  指令受限，引出 A2/A3

### A2. 指令队列写口加宽（256-bit = 8 字/写）

- regmap 0x00 从 32-bit 改 256-bit 写口 + 队列入端并-串转换；每次 PCIe 写搬 8 字，
  供给带宽 ×8。改 `AXI4FSA.scala` regmap + `Queue` 前端；主机侧 Python 按八字组包
- 收益：主机供给从 7~14ms → 1~2ms，彻底退出瓶颈名单。代价：小 RTL + 重新出 bit

### A3. 指令流 HBM 自取（取指通道搬家）

- 主机 h2c 一次性把指令块写入 HBM（与 QKV 同通路，走 31 个空闲 pseudo-channel 之一）；
  硬件加一个小取指引擎（或复用 DMA 引擎加一个"写指令队列"的 func）从 HBM 拉字进队列
- 收益：稳态取指与 PCIe MMIO 彻底解耦，上限变成 HBM 带宽（近乎免费）；指令队列深度
  256 变成纯缓冲。代价：中等 RTL + ISA 扩展，需重新验证
- 附带好处：主机侧从"逐字喂"变成"一次 DMA"，Python 端也更简单

### A4. loop/repeat 指令（指令压缩）

- 内层 j 循环 64 次迭代仅地址不同：一条 loop 指令带基地址 + 步进 + 次数，可把
  70,529 字压到 ~数百条。收益最大（指令供给问题连根拔掉），但引入新 ISA 语义与
  例外处理（fence/信号量跨迭代），风险最高，作为研究项排最后

---

## 3. 优化点 B：访存深度与通道（A1 之后按残余 bubble 决定）

前置条件：A1 落地后重测 mxBubble。若残余 bubble 高且集中在 DMA 等待（第二类），
本组才有性价比；若 MX active 已占大头则跳过去做 C/D。

### B1. O_t 双缓冲（先做，附带正确性加固）

- **证据**：accRAM 仅 17 行（1 LSE + 1 O tile）。① store(i) 读 acc 与 value(i+1,j=0)
  写 O_t **无信号量强制序**，全靠 ~160 拍 vs ~100 拍的时序余量（ISA 单 semId 表达不了
  双依赖）；② i 边界 store 串行阻塞
- **改法**：accRows 17→33（O_t ×2），`Configs.scala` 一行 + main.py 轮转；accRAM
  当前为 LUTRAM(608)，翻倍后建议顺带迁 BRAM（见 D3）
- 收益：竞态根除 + 块间 store/compute 重叠

### B2. K/V 三缓冲

- **证据**：spad 96 行 = 2Q+4KV 恰好满（Configs.scala:145），双缓冲预取窗口仅 1 个迭代
  （~150-250 拍）；HBM 抖动/单口上 O store 的 4KB 跨步干扰可能击穿窗口
- **改法**：spadRows = 2·cols+4·rows → +2·rows（96→128；log2Up 位宽不变仍 7 bit，
  ISA 不动）；新增 2 个信号量 ID（32 中余 25）；BRAM 8→~10 个 BRAM36（忽略不计）
- 收益：窗口 ×2（~300-500 拍），吸收抖动；为 B3 多口喂得动做准备

### B3. nMemPorts=2 + 第二个 HBM pseudo-channel

- **证据**：nMemPorts=1（Configs.scala:155）；harness 只接 HBM port0（TestHarness.scala:88-97）；
  O store 16×64B 步长 4KB 与 K/V load 挤同一 LoadQueue/StoreQueue/AXI master
- **改法**：`RequestPartitioner` 现成；harness 侧再接一个 HBM PC；Q/K/V/O 分通道
- 收益：load 与 store 解耦，DMA 串行消失。代价：harness 连线 + elaboration + 重验

### B4. O 布局转置

- O_t 在 HBM 按转置布局存，store 变成 16 次离散 64B；改为连续段写入（或 store 聚合）
  改善 HBM row-buffer 局部性。纯软件/布局改动，可与 B1/B3 合并验证

---

## 4. 优化点 C：调度/微结构（22.6% 利用率的分解）

每 j-迭代 ~220 拍 MX 工作中只有 ~64 拍是 QKᵀ+PV 的 MAC，其余 ~150-190 拍是 softmax
舞步（行 max、减 max、乘 scale、exp2、行求和、在线 rescale、影子排空等待）。

### C1. softmax 相位跨迭代重叠

- 把 conflictFree 锚点族重推为波前结构式，让本块 softmax 相位与下一块 MAC 重叠。
  **与任务 #4（16×16 锚点重推导）是同一套工作**——修 bug 顺带做，边际成本低
- 风险：锚点改动即调度改动，须逐位回归

### C2. Q 重装消除

- PE 内 reg 双份（Q 与 P 各持一份）或 P 写回 spad，省掉每迭代 16 拍 load_stationary
  + 一个 FSM 槽占用。RTL 改动中等，排在 C1 之后

---

## 5. 优化点 D：频率阶梯（一切指标线性乘频率）

现状：70MHz 约束、全局 WNS +0.09ns（≈70.45MHz 到顶）、DRC 1,107 条 DSP 流水未开、
SLR 穿越 ~2,000 SLL 无寄存、SLR1/2 局部 CLB ~52%。

| 档 | 手段 | 预期 | 代价/证据 |
|---|---|---|---|
| D0 工具级 | 重定目标 75-80MHz（TestHarness.scala:73 dutFreqMHz 一行）+ `phys_opt_design`（**至今从未用过**）+ pblock（mesh 居中、mxControl/acc 邻接、DMA 靠 HBM SLR；Rent 0.12 已证适合分区，baseline 最差路径 route 占 66%） | 75~80MHz | 不动 RTL，只重跑 Vivado |
| D1 DSP 内部流水 | FMA 模板 AREG/BREG/CREG/MREG/PREG（DPIP-2/DPOP-3/4 合计 1,107 条 → 个位数）；mesh MAC 51 级组合路径（P3 报告）的正解 | 与 D0 叠加 90~110MHz | 改 easyfloat 模板一处全体受益；accLatency 不变则锚点不动；PyEasyFloat 逐位回归兜底 |
| D2 SLR 穿越寄存 | ~2K SLL 插 TX_REG/RX_REG | >100MHz 必要条件 | 物理约束 + 小 RTL |
| D3 accRAM LUTRAM→BRAM/URAM | 消 LUTRAM 时序路径族（Current 最差路径含 rawInstQueue/accRAM）+ 释放 SLR 占用；URAM 现用量 0/960 | 布线余量 | 小改动，可与 B1 合并 |
| D4 DSP 级联重写 | FMA 分段乘加进 DSP 级联（PCOUT/PCIN），fabric 宽加减法器（PE ~54-bit、acc ~78-bit ×2，FMA.scala:95-101）退出关键路径 | 冲 120MHz+ | DSP 288→~560（仍 6% 芯片），LUT 预计 −8~13 万（youhua 估 10-20 万偏乐观，须综合对账）；数值语义不变、逐位回归可验 |

**工程闸门：所有提频都要重建比特流，而主线源码躺着 16×16 调度 bug（见
BUG_16x16_and_P_history.md）——提频工作必须排在任务 #4 之后，或在正确的分支上做。**

### 5.1 FPGA-aware 布局画像与 floorplan 细案（D0/D2 的具体化）

FSA 的 RTL 是 ASIC 思维的产物（通用逻辑搭数据通路、单 die 假设、SRAM 当 macro），
上 FPGA 后付出三笔"非意识税"。本节给出实测布局画像与对策，作为 D0（pblock）与
D2（SLR 寄存）的施工细案。

**现状布局画像（baseline/P3 报告实测，U280 三 die）**：

| 区域 | 内容 | 占用 |
|---|---|---|
| SLR0 | HBM 控制器、XDMA 壳、时钟转换、BRAM（73.5 个，几乎全在此） | LUT 33.5K，轻 |
| SLR1 | FSA 核一半（mesh 一部分 + accumulator 等） | LUT 170.8K / DSP 158，CLB ~52% |
| SLR2 | FSA 核另一半 | LUT 176.8K / DSP 162，CLB ~53% |
| 穿越 | SLR2↔SLR1 1,353–1,377 SLL；SLR1↔SLR0 643–810 SLL | **TX_REG/RX_REG 全部为 0** |

即：**16×16 mesh 被 Vivado 自由劈在 SLR1/SLR2 之间，~2K 条裸奔穿越**；全设计无任何
pblock；mxControl/accumulator 与 mesh 的相对位置不受控（baseline 最差路径 route 占
66% 的一大来源）。

**ASIC 假设 → FPGA 现实 → 对策对照**：

| ASIC 假设 | FPGA 现实 | 对策（并入哪档） |
|---|---|---|
| FMA 用标准单元搭加减/规格化/舍入 | DSP48 硬核 97% 闲置，逻辑全落 LUT | D1/D4 |
| 单 die，控制广播无物理边界 | mesh 跨 SLR1/2，2K SLL 无寄存 | 本节 floorplan + D2 |
| SRAM 由编译器出 macro | 应映射 BRAM/URAM；accRAM 却落 LUTRAM | D3 |
| 移位链 = mux 树 | FPGA 有 SRL16/LUTRAM | outputDelayer 已 SRL 化；ControlGen ROM 可显式推 LUTRAM |

**floorplan 细案（一次构建可同时验证）**：

1. **mesh 整体进单 SLR**：单个 SLR 有 3072 个 DSP/424K LUT，256 个 PE（256 DSP +
   ~297K LUT）单 die 放得下——mesh + 16 CMP 全进 SLR1，消灭最大一簇 SLL
2. **邻接原则**：accumulator + mxControl 紧贴 mesh 边缘（它们与 mesh 的每拍交互是
   控制广播热点）；DMA/前端/spad 留在 SLR0 边缘（靠 HBM 出口）；accRAM/spad 的
   BRAM 落 SLR1（顺带消 SLR1↔SLR0 的 BRAM 侧穿越）
3. **切不开的余量加寄存**：残留跨 die 网（如 spad 读数据总线）按 D2 插 TX/RX_REG，
   并按 meshSlope=2 的既有节奏做延迟对齐（管道深度变化要过 4×4 回归）
4. **软约束起步**：pblock 先用 CONTAIN_ROUTING=false 的软约束探路，收敛后逐步收紧
   （Rent 指数 0.12 已证明本设计低划分复杂度，适合分区）
5. 同批构建顺带：重定 75–80MHz 目标 + `phys_opt_design`（历史构建从未用过）

**激进选项（仅当前述路走完仍不够时）**：用 DSP48 原生浮点 / Xilinx FP IP 替换
easyfloat 柔精度 FMA——可获得更高 Fmax，但**数值不再逐位等于 PyEasyFloat 金模型**，
验收标准必须重新定义（误差界而非逐位），属于方法论变更，需单独评审。

**预期对账项（构建后回填）**：SLR1/2 局部 CLB 占用、SLL 总数（目标 <500）、
dut 域 WNS、route 占比（最差路径 66%→<50%）。

---

## 6. 依赖关系与推荐执行序

```
A1 mmap ──► 重测计数器 ──┬─►（残余 bubble=等数据）B1+B2+B4 一批做 ──► B3
                         └─►（MX active 占大头）  C1（与任务#4同源）
任务#4（16×16 锚点）──► 解锁一切 RTL 重建 ──► D0 ──► D1 ──►（D2/D3/D4 按需）
A2/A3/A4 视 A1 之后主机是否仍是共同瓶颈再排
```

原则：先软件后硬件；先测量后施工；每步用 PyEasyFloat 逐位回归 + 计数器前后对比收口。

---

## 7. 既有文档勘误表（实测推翻/修正的结论）

| 文档说法 | 实测判定 |
|---|---|
| baseline/P1 报告："bubble 64.5~67.7% = 控制开销/访存延迟主导" | seq≥256 时 97.3% bubble 实为主机 MMIO 每字 3.3µs；文档从未识别该瓶颈（且 baseline 文 7852 拍与 P1 文 4956 拍自相矛盾，属不同批次测量） |
| P3 报告"等效 Fmax 101.6MHz" | 已被其勘误推翻，实为 71.12MHz（提升系数 1.016 被误当 MHz） |
| 路线图 P1→P3 频率 85→150MHz | 未兑现：70MHz 约束从未重定，DSP 流水未开；WNS 变好 ≠ 频率变高 |
| RESOURCE_MAP"dut 域 WNS +0.091" | 核对 timing.txt：全局 +0.091 的最差路径在 PCIe 500M 域；dut 域最差路径族是 mesh pipe / rawInstQueue LUTRAM |
| youhua"acc 加法改 DSP 级联省 10~20 万 LUT" | 方向合理、未经验证；对齐/规格化/舍入进不了 DSP，实际可省量须综合对账 |
| 功耗报告 HBM 8.9W | vectorless 估算，跨构建波动 ±4W，只看趋势 |
