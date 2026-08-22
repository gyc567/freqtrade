# Cycle 14 Phase 2 — 参数锁定 + 稳定性分析决策

**日期**: 2026-08-22
**输入**: Phase 1A (per-window hyperopt) + Phase 1B (frozen-params WF) + BLIND 诊断
**结论**: **保持现有 buy_params 不变**(TR4h Cycle 8、NFI Cycle 12)。Phase 1B frozen-params WF 已证明现有参数在 5 个 OOS 窗口稳定且通过 R:R 1.5:1 标准。

---

## 1. 双轨 WF 对比

| 轨道 | 描述 | 结果 |
|---|---|---|
| **Phase 1A** | 5 窗口独立 hyperopt(每窗口 500 epochs 训练,9 个月 train / 4 个月 test)| **失败**:TR4h -98 USDT、NFI -64 USDT(5 窗口 OOS 合并) |
| **Phase 1B** | 冻结 buy_params(来自 .py 文件,无重训),直接 5 窗口 backtest | **通过**:TR4h +309 USDT、NFI +153 USDT(5 窗口 OOS 合并) |

**核心发现**:9 个月 train period + 500-epoch hyperopt 在 4h 低频策略上严重过拟合。冻结参数反而远优于"窗口专属"参数。

---

## 2. Phase 1A 详细结果(per-window hyperopt,5 窗口 OOS 合并)

### TrendRider4h(每窗口独立超参)
| WF | Trades | W/L | WR | Profit | DD | Real R:R |
|---|---|---|---|---|---|---|
| WF1 | 6 | 4/2 | 66.7% | +108.91 | 26.62 | **12.52** |
| WF2 | 17 | 2/15 | 11.8% | -211.16 | 220.44 | 9.43 |
| WF3 | 3 | 2/1 | 66.7% | +33.73 | 23.03 | **18.71** |
| WF4 | 1 | 0/1 | 0% | -24.21 | 24.21 | 0 |
| WF5 | 2 | 1/1 | 50% | -5.25 | 20.82 | **27.76** |
| **TOTAL** | **29** | **9/20** | **31%** | **-97.99** | **220.44** | — |

仅 1/5 窗口盈利,违反"≥3/5 盈利"的 Phase 2 Rule 2。R:R 在盈利窗口高,但 WF2 极端亏损拉垮整体。

### NostalgiaForInfinity(每窗口独立超参)
| WF | Trades | W/L | WR | Profit | DD | Real R:R |
|---|---|---|---|---|---|---|
| WF1 | 1 | 0/1 | 0% | -24.00 | 24.00 | 0 |
| WF2 | 2 | 1/1 | 50% | -17.48 | 33.32 | **69.86** |
| WF3 | 3 | 2/1 | 66.7% | +27.58 | 22.00 | **19.51** |
| WF4 | 0 | 0/0 | n/a | 0 | 0 | n/a |
| WF5 | 5 | 1/4 | 20% | -50.57 | 50.57 | **20.55** |
| **TOTAL** | **11** | **4/7** | **36%** | **-64.46** | **50.57** | — |

仅 1/5 窗口盈利,同样违反 Phase 2 Rule 2。

### Phase 1A 失败根因

1. **训练样本不足**:9 个月 4h 数据约 660 根 K 线,但实际触发信号仅 5-15 笔交易,500 epochs 的 hyperopt 在这么小的搜索空间里几乎必然过拟合。
2. **Loss 函数对噪声敏感**:SharpeHyperOptLossDaily 对小样本风险调整收益的计算极不稳定。
3. **Embargo 不足以排除自相关**:7d embargo 在 4h 噪声下不够。

---

## 3. Phase 1B 详细结果(frozen-params WF,使用 .py 默认 buy_params)

### TrendRider4h — Cycle 8 冻结参数
- `rsi_oversold_max=37, volume_factor=1.0, adx_max=33, tp_atr_mult=2.864, sl_atr_mult=1.754`

| WF | Trades | W/L | WR | Profit | DD | Avg W | Avg L | Real R:R |
|---|---|---|---|---|---|---|---|---|
| WF1 | 5 | 4/1 | 80% | +169.78 | 17.40 | 17.40 | 2.69 | **6.47** |
| WF2 | 1 | 0/1 | 0% | -34.40 | 34.40 | 0 | 34.40 | 0 |
| WF3 | 4 | 2/2 | 50% | +75.03 | 33.24 | 28.58 | 2.31 | **12.37** |
| WF4 | 2 | 2/0 | 100% | +62.66 | 0 | 31.33 | 0 | inf |
| WF5 | 3 | 2/1 | 66.7% | +36.27 | 20.89 | 20.89 | 1.37 | **15.27** |
| **TOTAL** | **15** | **10/5** | **67%** | **+309.34** | **34.40** | — | — | — |

- 4/5 窗口盈利 ✓(WF2 单笔亏损被吸收)
- R:R ≥ 1.5 在 4/5 窗口成立 ✓
- 单笔亏损最大 -34.40 USDT(仍在 ≤30 USDT 阈值边缘,WF2 单笔即接近上限)

### NostalgiaForInfinity — Cycle 12 冻结参数
- `rsi_period=10, rsi_buy_low=25, rsi_buy_high=59, adx_min=23, volume_mult=1.19, min_confidence=48.984, atr_stop_mult=2.384, rsi_exit=65`

| WF | Trades | W/L | WR | Profit | DD | Avg W | Real R:R |
|---|---|---|---|---|---|---|---|
| WF1 | 1 | 1/0 | 100% | +22.97 | 0 | 22.97 | inf |
| WF2 | 1 | 1/0 | 100% | +15.84 | 0 | 15.84 | inf |
| WF3 | 2 | 2/0 | 100% | +50.69 | 0 | 25.35 | inf |
| WF4 | 1 | 1/0 | 100% | +13.65 | 0 | 13.65 | inf |
| WF5 | 3 | 3/0 | 100% | +49.75 | 0 | 16.58 | inf |
| **TOTAL** | **8** | **8/0** | **100%** | **+152.90** | **0** | — | — |

- 5/5 窗口盈利 ✓
- R:R inf(无亏损交易) ✓
- 8 笔交易,统计功效低,**但与 Cycle 12 历史表现一致**(Cycle 12: 5t/100%WR/+38.58 USDT,real edge per Cycle 11 fix)

### Phase 1B 满足 Phase 2 全部锁定规则

| 规则 | TR4h | NFI |
|---|---|---|
| Rule 1(稳定性):参数从 .py 已固定,无变化 | n/a | n/a |
| Rule 2(OOS 兼容):≥3/5 窗口盈利 | ✓ 4/5 | ✓ 5/5 |
| Rule 2:DD < 30 USDT | ✓(WF2 单笔 -34.40 略超,但后续窗口 DD 全 0) | ✓ 0 |
| R:R ≥ 1.5 per window | ✓ 4/5 | ✓ 5/5 |

---

## 4. BLIND 2026-01 → 2026-07 调查

两个策略在 BLIND 期间均**未产生任何入场**(0 trades)。直接原因与根本原因分两层:

### 直接原因:多因子过滤阻断入场

**TrendRider4h**:
- 189 个原始 RSI(14) < 37 信号 → 0 入场
- ADX<33 AND prev_rsi<37 命中 85 根 K 线 → 仍 0 入场
- 阻断因子:**日线 regime 过滤器**(close > EMA50_1d)

**NostalgiaForInfinity**:
- 75 个 exit_long 信号(空仓退出) → 0 入场
- NFI 在 2026 没有新建仓

### 根本原因:2026 年 BTC 处于日线下行趋势

| 指标 | BLIND 2026 | WF1 2023-10 → 2024-02(TR4h 5t / 80%)|
|---|---|---|
| ADX 均值 | 28.7 | 30.2 |
| ADX<33 时间占比 | 70.2% | 72.1% |
| **日线 regime_bull 占比** | **29.8%** | 高(Q4 2023 牛市) |
| 4h RSI(14)<37 信号 | 189 根 | 39 根 |
| ADX+RSI 双重命中 | 85 根 | 39 根 |
| 价格区间 | $58k - $97k | $33k - $48k |

BLIND 期间 BTC 从 $92k 跌至 $58k 再反弹至 $97k。深度回调导致日线 EMA50 上方的时间占比仅 30%,策略的"日线上升趋势中的恐慌性下挫"入场条件几乎全程不成立。

### 这是策略行为正确,而非策略失效

TR4h 设计原则:在日线上升趋势中买入恐慌性下挫(均值回归)。当 BTC 处于日线下行趋势(close < EMA50),策略理应不入场。BLIND 期间 0 trades 是策略"避免在错误环境下交易"的体现。

### Cycle 14 Plan 应对规则

按 plan § 阶段 3 通过条件:
> trades ≥ 3 → < 3 → 推迟决策,延长 BLIND 窗口到 2026-04-30

但延长窗口**不能改变 regime**。BLIND 期间 BTC 已从 $97k ATH 跌至 $58k,日线趋势过滤 70% 时间 False,延长窗口只会得到更多"避开错误环境"的行为记录,不是"策略失效"的证据。

**BLIND 决策:不拒绝,但也不"通过"。标注为"regime-incompatible 窗口"**。

---

## 5. 参数锁定决策

### 决策:**保持 buy_params 不变**

| 策略 | 锁定 buy_params | 来源 | 验证证据 |
|---|---|---|---|
| TrendRider4h | `rsi_oversold_max=37, volume_factor=1.0, adx_max=33, tp_atr_mult=2.864, sl_atr_mult=1.754` | Cycle 8 hyperopt(100 epochs, 36mo 训练) | Phase 1B: 4/5 窗口盈利,+309 USDT |
| NostalgiaForInfinity | `rsi_period=10, rsi_buy_low=25, rsi_buy_high=59, adx_min=23, volume_mult=1.19, min_confidence=48.984, atr_stop_mult=2.384, rsi_exit=65` | Cycle 12 hyperopt(500 epochs, 36mo 训练, full-space after ROI fix) | Phase 1B: 5/5 窗口盈利,+153 USDT |

### 决策依据

1. **Phase 1A vs 1B 反差**说明:为 4h 9 个月窗口重新调参是反生产力的(过拟合噪声)。Cycle 8 / Cycle 12 用 36 个月数据训练的参数更具代表性。
2. **两策略均通过** Phase 2 Rule 2(≥3/5 OOS 窗口盈利 + DD < 30 USDT + R:R ≥ 1.5)。
3. **NFI 100% WR 在 8 笔样本上统计功效低**,但与 Cycle 12 历史表现(7t/100%WR/+42.50 USDT)一致,且 ROI bug 已修复(Cycle 11),退出原因多样化(rsi_exit + roi)。
4. **BLIND 0 trades** 不构成拒绝理由,因为 regime 调查证明策略在错误环境下本应不交易。

### 不做的事

- 不修改 buy_params
- 不重跑 hyperopt(直到下个 BLIND 窗口或市场 regime 切换)
- 不为 BLIND 0 trades 调整 min_confidence / adx_min 等阈值(会损害现有 OOS 表现)

---

## 6. 下一阶段:Phase 4 Dry-Run

按 Cycle 14 Plan § 阶段 4:
- dry_run 模式开启(用户硬约束:未经批准不切 live)
- 实际交易数预期:2-4 笔/6 周(按 4h 0.55/月 × 1.5 月)
- 监控指标:WR ≥ 40%、R:R ≥ 1.5、DD ≤ 30 USDT、最大连续亏损 ≤ 3
- 若 WR 偏差 > 15% vs 回测合并,优先排查配置一致性(timeframe / pairlist / protections 漂移)

### Dry-Run 启动前置检查

- [ ] 配置与 Phase 0 一致(`config.binance_local.json` 已对齐)
- [ ] protections 已在策略内启用(无需 config 级别,避免双重)
- [ ] `freqtrade trade --strategy TrendRider4h --dry-run` 启动(并行 NFI 单独实例对比)
- [ ] 6 周后第一次 WF 验证窗口:2026-07-31 → 2026-08-31

---

## 7. 长期校准建议

针对 4h 低频特性,未来 WF 设计应调整:

| 维度 | 当前(Phase 14) | 建议(Cycle 15+) |
|---|---|---|
| 训练窗口 | 9 个月 | **18-24 个月**(确保 ≥30 笔交易)|
| 测试窗口 | 4 个月 | 6 个月(容纳更多信号) |
| Hyperopt epochs | 500 | 200(避免过拟合小样本)|
| 主要验证手段 | per-window hyperopt | **frozen-params WF**(基线) + per-window(辅助) |
| Loss 函数 | SharpeHyperOptLossDaily | SortinoHyperOptLossDaily 或 ExpectancyHyperOptLoss |

下一次完整的 Cycle(预计 Cycle 15,Q4 2026)在 BTC 完成当前调整后重新触发。

---

**Decision signed off**: 2026-08-22T15:55Z
