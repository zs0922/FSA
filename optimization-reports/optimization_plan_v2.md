# FSA U280 优化计划 v2（P3 完成后修订，2026-09-05）

> **v2 修订原因**：P3 报告初版把提升系数 1.016 误读为 101.6 MHz（实为 **71.1 MHz**）。
> 该勘误推翻了"100 MHz 已达成"的判断，P3b 从"可选"升为**最高优先级**。
> 历史各版：P1-P3 已完成并板测，见 `baseline_u280_preopt.md` / `p1..p3_*.md`。

---

## 状态总览（勘误后的真实坐标）

| 阶段 | 状态 | WNS | 等效 Fmax | 板测 |
|---|---|---|---|---|
| Baseline | ✅ | +0.003 | 70.02 MHz | RelErr 1.88e-4, 4956 cyc |
| P1 工具优化 | ✅ | +0.023 | 70.11 MHz | 同上, 4956 cyc |
| P2 控制路径切断 | ✅ | +0.062 | 70.31 MHz | 同上, 5087 cyc (+2.6%) |
| P3 acc FMA 流水 | ✅ | +0.225 | **71.12 MHz** | 同上, 5320 cyc (+4.6%) |
| **P3b PE MAC 流水** | 🔄 **下一手** | 目标 +2~4 | **目标 100-115** | — |
| P4 吞吐 | ⏳ | 中性 | — | 目标周期 -15~25% |
| P5 频率爬升/物理 | ⏳ | — | 逐级实测 | — |

**当前瓶颈（唯一）**：PE mesh 内 MAC 组合路径 51 级
（`mesh_8_15/reg_mantissa → pipe_b_1016`，13.64ns = logic 33% + route 67%）
静态估算：75MHz 需挤 0.7ns（可试）、80MHz 挤 1.6ns（勉强）、85MHz+ **必须 P3b**。

**吞吐账本（目标 = µs 下降，baseline 70.8µs）**：
| 组合 | 频率 | 周期 | 耗时 | 加速比 |
|---|---|---|---|---|
| P3 现状 | 70 | 5320 | 76.0µs | **0.93x（慢于 baseline！）** |
| P3b@100 | 100 | ~5400 | 54µs | 1.31x |
| P3b@100 + P4 | 100 | ~4600 | 46µs | **1.54x** |
| P3b@110 + P4 | 110 | ~4600 | 42µs | **1.69x** |

> 注意：P3 若不提频实际比 baseline 慢 7%（reciprocal 76 拍代价）——**P3 的投资必须靠 P3b+P5 兑现**。

---

## P3b：PE MAC 流水化（最高优先级）

### 洞察（P3 调试沉淀的三条硬约束）

1. **mesh 不能均匀平移**：PE MAC +L 拍后，部分和流（u/d，每 hop 1 拍）与 K 流（l→r，每 hop 1 拍）
   在 PE 内**错位 L 拍**——这不是 mxControl 式的全域平移，而是**波前内错位**。
2. **标准解法 = PE 内 skid 寄存器**：每个 PE 在 `in_b`（及旁路流）进入 MAC 前打 L 拍延迟，
   使 K 元素与上游部分和同拍进 MAC；K 流本身仍 1 拍/hop 畅通，**II 保持 1**，
   代价仅是波前 fill latency +L×深度（一次性 ~L×16 拍，被 bubble 吸收）。
3. **L=1 是稳妥起点**：单刀（mult 后寄存，复用 `RawFloat_FMA` 已有的 stageReg 机制）切 51→~27 级；
   L=2 理论更优但 skid/波前校验复杂度翻倍，且 P3 教训（单刀 bug）表明每加一刀验证成本陡增。

### 方案（L=1）

| 改动 | 文件 | 内容 |
|---|---|---|
| PE MAC 启用单刀 | `FPArithmeticImpl.scala` | `FPMacUnit` 的 `RawFloat_MulAddExp2` 传 `pipeline=true`（同 accUnit 路径）|
| FMA 单刀模式 | `easyfloat/FMA.scala` | 支持 `nStages=1`（仅 mult 后寄存；stageB2 组合直通）|
| PE 内 skid | `sa/PE.scala` | `in_b`/旁路流进 MAC 前打 1 拍；`reg`（stationary）读出侧匹配 |
| 波前对齐 | `sa/SystolicArray.scala` | 核验 `pipe_no_reset` 深度与 ctrl 逐列延迟是否需 +1 |
| 计划周期 | `ExecutionPlan.scala` | 波前相关 cycle 全体 +1（fill latency），`accStart` 联动 |
| 常量 | `Arithmetic.scala` | `peLatency`（区别于 accLatency）|

### 验证顺序（吸取 P3 教训）

1. **独立 TB**：单 PE MAC L=1 + skid 的波前正确性（Verilator，easyfloat 工程内）
2. **4x4 仿真**（唯一可信仿真回归——8x8+ 的 AXI4FSA config 有上游既有 +2 行 bug）
3. 板测 RelErr + 周期（fill +16 拍预期，bubble 吸收）
4. 构建看 WNS：预期最长段 ~27 级 → 8~9ns → **100-115 MHz 能力**
5. 若 L=1 后仍有大余量且板测稳 → 可评估 L=2（预期 120-140）

### 风险

- mesh 波前错位类 bug 仿真只能靠 4x4 抓（16x16 仿真不可信）→ 板测是最终判据
- exp2 模式（score 计划在 PE 内做 pwl）与 skid 的交互需 TB 专项覆盖
- 预算 3-5 次构建迭代（P3 实际用了 4 次）

---

## P5a：频率爬升（P3b 之后立即做）

- 时序能力到手后逐级改 `dutFreqMHz`：85 → 95 → 105 → 115，每级一次构建，WNS<0 即回退
- P3b@L=1 预计落在 100-115 区间，取实测稳定值 -5MHz 作为产品频率
- 零 RTL 风险，纯参数迭代；与 P4 可穿插（P4 改周期数，P5 改频率，互不干扰）

## P4：吞吐/bubble 压缩（P5a 后）

- 现状 bubble 60.2%（3203/5320），DMA 171 拍 ≈ active 251 拍——访存计算完全串行
- 参数级先行：`dmaLoadInflight` 16→32、`dmaStoreInflight` 8→16（FSAParams 单点）
- `nMemPorts=2`（板上 config 当前 =1，SRAM 双端口已支持）
- 软件：seq 加长摊薄固定开销（>16 时多 tile 循环本就存在）
- 深水区（需 ISA/编译器配合）：spad 预取双缓冲——放最后

## P5b：物理级（可选收官）

- SLR 穿越 2029 SLL 无 TX_REG/RX_REG 补偿 → 高频下可能浮出
- pblock（sa 固定 SLR1/2 中部，mxControl 贴 SLR0）——Rent 0.12 天然适合
- accRAM 写地址 fanout 1216 的 per-bank 寄存
- 仅当 P3b+P5a 后 WNS 卡在 -0.3ns 量级时启用

---

## 执行序列（修订版）

```
P3b(L=1) ──TB──> 4x4仿真 ──> 板测 ──> P5a频率扫描(85..实测) ──> P4(参数级)
                                    │                            │
                                    └── (可选) P3b(L=2) 评估     └── P4(深水区)/P5b 按需
```

每步固定流程：改动 → 4x4 仿真 → 板测 RelErr/周期 → 构建看 WNS → commit（子模块 patch）。

## 经验教训库（滚动积累）

1. P1：零裕量设计上激进工具策略会翻车（ExtraTimingOpt → WNS 反而 -0.357）
2. P2：全域同源控制的 +L 平移是免改计划的通用手法
3. P3：Chisel6 `RegNext` 宽度推断陷阱（用 `Reg(chiselTypeOf(x))`）；
   **报告 Fmax = 周期/(周期-WNS)×基频，系数≠MHz**（本次勘误根因）
4. 回归判据：4x4 仿真 + 板测；`AXI4FSA8X8/16X16` 仿真 config 有上游既有 bug 不可用作判据
5. reciprocal 类多周期反馈单元：晚消费"重复结果"数值不变，会掩盖流水错位——TB 必须覆盖直通流
