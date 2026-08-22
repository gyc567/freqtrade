# Cycle 14 — 4h 周期 Walk-Forward 优化方案(R:R 1.5:1 目标)

> **设计时间**: 2026-08-22
> **基础**: 用户审计修订版 6 阶段方案 + 4h 周期 + BTC/USDT 4H 主对 + R:R 1.5:1 入门门槛
> **目标策略**: TrendRider4h MR-Pro (Cycle 8, 20t/60%/validated) + NostalgiaForInfinity Cycle 12 (7t/100%/4h-only)
> **数据范围**: BTC/USDT 4h, 2023-01-01 → 2026-07-31 (43 个月, 7848 bars) + 1d 配套
> **设计文档**: `/Users/jie/.claude/plans/eventual-foraging-clarke.md`(本文件是其在仓库内的可提交副本)

---

## Context

用户决定主做 4h 周期,并将 **R:R ≥ 1.5:1** 作为单笔交易机会的入门门槛。基于这一标准,我们需要一套严格的、可执行的优化流程来验证现有策略,并对未来参数调整设防。

Cycle 13 已经证明:
- NFI Cycle 12 参数是 **4h 专用的**,1h 跨周期直接失败(31t/41.9%/-29.90 USDT)
- 单参数放宽(如 `rsi_buy_low 32→25`)对 4h 交易数无影响(6 信号只成 1 笔,瓶颈在仓位/保护机制)
- TrendRider4h MR-Pro 是唯一 3 年 OOS 验证过的策略(20t/60%/+115.34 USDT)

需要做的不是"再调一次参数",而是建立一套**结构化的、可重复的优化流程**,把所有未来调参行为约束在防过拟合的框架内。本方案对应用户修订版的 6 阶段流程,针对 4h 低频特性做了关键校准。

---

## 关键校准(针对 4h 低频特性)

用户原版方案假设 5m NFI / 1h 策略,**最低 OOS 交易数 ≥25**。但本仓库 4h 实际交易密度远低于此:

| 策略 | 36 个月总交易 | 月均交易 | 4 个月测试窗口预期交易 |
|---|---|---|---|
| TrendRider4h MR-Pro | 20 | 0.55 | 2.2 |
| NFI Cycle 12 | 7 | 0.19 | 0.76 |

按用户原版阈值,**任何 4h 策略都达不到 ≥25 OOS**。修订:

| 指标 | 用户原版(高频) | **本方案 4h 校准** | 理由 |
|---|---|---|---|
| 单窗口最低 OOS 交易 | ≥25 | **≥3** | 4h 4 月窗口预期 0.76-2.2 笔 |
| 合并 OOS 交易(5 窗口) | ≥125 | **≥15** | 累计约 5-10 笔/策略 |
| 盲测窗口交易 | ≥40 | **≥3** | 7 月盲测窗口,3-4 笔预期 |

**R:R 1.5:1 标准不受频率影响**,保持不变:
- 单笔 R:R = avg_winner / |avg_loser| ≥ 1.5
- 或 Profit Factor = sum_wins / |sum_losses| ≥ 1.5
- 期望 R = WR × 1.5R - (1-WR) × 1R > 0 → 等价于 **WR > 40%**

---

## 阶段 0:准备工作(必须,1 天)

### 数据准备
- BTC/USDT 4h: 2023-01-01 → 2026-07-31(43 个月,7848 bars),源:`/Users/jie/code/fq-data-downloader/data/binance/BTC_USDT-4h.feather`
- BTC/USDT 1d 配套(EMA50/EMA200 多周期),同上源

### 排除未来函数偏差
```bash
freqtrade lookahead-analysis --strategy TrendRider4h --timerange 20230101-20260731
freqtrade lookahead-analysis --strategy NostalgiaForInfinity --timerange 20230101-20260731
freqtrade recursive-analysis --strategy TrendRider4h --timerange 20230101-20260731
freqtrade recursive-analysis --strategy NostalgiaForInfinity --timerange 20230101-20260731
```
期望:无报错的 future function 警告。如有报错,先修复策略再继续。

### 配置统一(全程不变)
```json
{
  "pair_whitelist": ["BTC/USDT"],
  "max_open_trades": 1,
  "stake_amount": "unlimited",
  "dry_run_wallet": 1000,
  "stoploss_on_exchange": false,
  "protections": [
    {"method": "CooldownPeriod", "stop_duration": 20},
    {"method": "StoplossGuard", "lookback": 720, "trade_limit": 3, "stop_duration": 60},
    {"method": "MaxDrawdown", "lookback": 1440, "max_allowed_drawdown": 0.10, "stop_duration": 300}
  ]
}
```

### 策略文件确认
- `user_data/strategies/TrendRider4h.py`(MR-Pro 已就绪,Cycle 8)
- `user_data/strategies/NostalgiaForInfinity.py`(Cycle 12 已就绪)

---

## 阶段 1:Walk-Forward 设计(核心,5 窗口 + 1 盲测)

### 窗口表

| 窗口 | 训练起 | 训练止 | 测试起 | 测试止 | Embargo | 时长 |
|---|---|---|---|---|---|---|
| **WF1** | 2023-01-01 | 2023-09-30 | 2023-10-07 | 2024-02-07 | 7d | 9mo train / 4mo test |
| **WF2** | 2023-06-01 | 2024-02-29 | 2024-03-07 | 2024-07-07 | 7d | 9mo train / 4mo test |
| **WF3** | 2023-11-01 | 2024-07-31 | 2024-08-07 | 2024-12-07 | 7d | 9mo train / 4mo test |
| **WF4** | 2024-04-01 | 2024-12-31 | 2025-01-07 | 2025-05-07 | 7d | 9mo train / 4mo test |
| **WF5** | 2024-09-01 | 2025-05-31 | 2025-06-07 | 2025-10-07 | 7d | 9mo train / 4mo test |
| **BLIND** | — | — | 2026-01-01 | 2026-07-31 | — | 7mo,完全未参与 |

注:2025-10-07 → 2026-01-01 (3 个月) 是 "部署模拟期" — 数据不参与训练也不参与测试,代表若按 WF5 参数上线,实际表现不可知,只能等 BLIND 窗口验证。

### 每个窗口执行 4 步

**步骤 A:训练期 Hyperopt**
```bash
# TrendRider4h (参数少,500 epochs)
ft_venv/bin/python3 freqtrade-loop/run_hyperopt.py \
  --strategy=TrendRider4h \
  --timerange=20230101-20230930 \
  --epochs=500 \
  --spaces=buy,roi,stoploss \
  --loss=SharpeHyperOptLossDaily

# NFI (参数多,300 epochs, 仅 buy 全量, sell 只调 rsi_exit)
ft_venv/bin/python3 freqtrade-loop/run_hyperopt.py \
  --strategy=NostalgiaForInfinity \
  --timerange=20230101-20230930 \
  --epochs=300 \
  --spaces=buy,sell \
  --loss=SharpeHyperOptLossDaily
```

**步骤 B:JSON Override 检查(必做)**
```bash
ls /Users/jie/code/freqtrade/user_data/strategies/*.json
# 若存在:自动 mv 到 /tmp/c4_results/wf{N}_{strategy}_hyperopt.json
# 避免 [[freqtrade-json-override-trap]] 静默覆盖 buy_params
```

**步骤 C:测试期 Backtest(只 backtest,不重新 hyperopt)**
```bash
ft_venv/bin/python3 freqtrade-loop/run_backtest.py \
  --strategy=TrendRider4h \
  --timerange=20231007-20240207

ft_venv/bin/python3 freqtrade-loop/run_backtest.py \
  --strategy=NostalgiaForInfinity \
  --timerange=20231007-20240207
```

**步骤 D:记录指标到 `/tmp/c4_results/wf_results.csv`**
```
window, strategy, train_range, test_range, trades, wr, profit_usdt, dd_usdt, pf, avg_winner, avg_loser, rr_ratio, sharpe_wallet, calmar, embargo_days
```

每次循环执行 5 个窗口 × 2 策略 = 10 次 hyperopt + 10 次 backtest ≈ **2-3 小时**。

---

## 阶段 2:参数锁定规则(关键防过拟合)

### 规则 1:稳定性优先
对每个超参,统计 5 窗口的最优值:
- 若 ≥3/5 窗口取值集中(差值 < 25%) → 取**中位数**
- 若仅 1-2 窗口命中 → 取**众数**(若存在)或默认值
- 若完全分散(差值 > 50%) → 该参数**敏感性过高**,固定为保守默认值

### 规则 2:OOS 兼容
最终参数必须在**≥3/5 OOS 窗口**同时满足:
- trades ≥ 1(任一窗口至少有 1 笔交易)
- profit > 0(正向)
- max_drawdown < 30 USDT

若 ≤2/5 窗口通过 → 该参数组合**不稳定**,回退到默认或上一稳定周期。

### 规则 3:写入策略,封存
最终参数写入 `buy_params` 后,**禁止再针对任何已用窗口调整**。所有未来调整必须经过 BLIND 验证或新一轮 WF。

### NFI 特殊规则
NFI 参数极多(>30 个 buy_condition + 全局阈值),全量 hyperopt 极易过拟合。仅 hyperopt:
- **全局阈值类**:`rsi_buy_low`, `rsi_buy_high`, `adx_min`, `volume_mult`, `min_confidence`, `atr_stop_mult`
- **退出阈值**:`rsi_exit`
- **不调**:`buy_condition_*`(每个 buy_condition 的内部权重/逻辑)
- **不调**:作者核心 entry signal 逻辑

保持 NFI 作者的核心逻辑完整,通常比自己大幅优化更稳健。

---

## 阶段 3:终极盲测验证(2026-01-01 → 2026-07-31)

参数锁定后,在 BLIND 窗口做**单次 backtest**(不重新优化):

```bash
ft_venv/bin/python3 freqtrade-loop/run_backtest.py \
  --strategy=TrendRider4h \
  --timerange=20260101-20260731

ft_venv/bin/python3 freqtrade-loop/run_backtest.py \
  --strategy=NostalgiaForInfinity \
  --timerange=20260101-20260731
```

### 通过条件

| 指标 | 通过阈值 | 失败动作 |
|---|---|---|
| trades | ≥3 | < 3 → 推迟决策,延长 BLIND 窗口到 2026-04-30 |
| WR 偏差 | vs WF 合并结果 ±15% 以内 | > 15% → 重新审视 WF 锁定参数 |
| avg_winner / \|avg_loser\| | ≥1.5 | < 1.5 → R:R 不达标,拒绝部署 |
| profit_factor | ≥1.5 | < 1.5 → 同上 |
| profit_total | > 0 | < 0 → 拒绝部署 |
| max_drawdown | ≤30 USDT | > 30 → 风控不达标,拒绝部署 |

### 通过/拒绝决策
- **两策略都通过** → 选 R:R 较高的进入阶段 4 Dry-Run
- **一策略通过** → 进入该策略 Dry-Run
- **都不通过** → 回阶段 2,启用规则 2 严格化(要求 ≥4/5 窗口盈利)

---

## 阶段 4:Dry-Run 真实环境验证(6 周)

### 配置
- freqtrade dry_run 模式(用户约束:dry_run 始终开启,未经允许不切 live)
- 时长:**至少 6 周**(2026-XX-XX → 2026-XX-XX,具体起止日由 BLIND 完成后定)
- 期望交易数:2-4 笔(按 4h 0.5/月 × 1.5 月)

### 监控指标
| 类别 | 指标 | 阈值 |
|---|---|---|
| 收益 | 实际 WR | ≥40%(允许比回测低 10-15%) |
| 收益 | 实际 R:R | ≥1.5 |
| 收益 | 实际盈亏 | > 0 |
| 风险 | 实际 DD | ≤30 USDT 或 ≤ 实际回测 DD × 1.5 |
| 风险 | 最大连续亏损 | ≤3 笔 |
| 执行 | 滑点 | < 0.1% 订单价值 |
| 执行 | 手续费占比 | < 0.2% 单笔 |
| 信号 | 信号频率 | 与回测一致(±20%) |

### 与回测对比
- WR 差异 > 15% → 优先排查配置一致性(timeframe / pairlist / protections 是否漂移)
- DD 显著放大(>2x) → 优先排查滑点和实际波动率,暂停并重新评估

---

## 阶段 5:Dry-Run 后调优规则(严格限制,防二次过拟合)

**只允许**(每次最多改 1-2 个参数):
- 信心阈值 ±10%(`min_confidence`)
- ADX 阈值 ±10%(`adx_min`)
- volume 倍数 ±10%(`volume_mult`)
- ATR 止损倍数 ±10%(`atr_stop_mult`, `sl_atr_mult`)
- ROI 时间节点 ±20%(`minimal_roi` 各档)

**禁止**:
- 大幅修改入场逻辑(增加/删除 buy_condition,改变 signal 公式)
- 根据单周/单月结果反复 hyperopt
- 为"弥补"某段亏损而专门优化
- 改变 max_open_trades、stoploss、position_stacking(框架级)

**验证流程**:
1. 改动前先记录当前参数基线
2. 单次最多改 1-2 个参数
3. 改动后必须观察 ≥ 2 周或 ≥ 3 笔交易才能决定保留
4. 保留的条件:实际 R:R ≥ 1.5,WR ≥ 40%,DD ≤ 30

---

## 阶段 6:上线与持续维护

### 上线
- 小资金 live 运行 1-2 个月(用户需明确批准切 live)
- 初始仓位:回测收益 / DD 比例的 1/2,逐步放大
- 关键监控:实际 WR、PF、DD、滑点、手续费占比

### 维护节奏
| 周期 | 动作 |
|---|---|
| 每 1 周 | 检查监控仪表盘:WR、PF、DD、信号质量 |
| 每 1-3 个月 | 轻量 WF:只跑最近数据(如 2026-Q2 → 2026-Q3) |
| 每 6 个月 | 完整 WF:重新跑 5 窗口 + BLIND |
| 市场 regime 切换 | 暂停或降仓优先,不要大幅改参 |

### 仪表盘最小化字段
- 实际 WR(滚动 30 天)
- 实际 R:R(滚动 30 天)
- 实际 DD(滚动 90 天最大)
- 实际信号频率(每日)
- 当前 regime 标记(ADX > 25 趋势 / < 20 震荡)

---

## 强制检查清单

执行本方案前必须全部打勾:

- [ ] 数据范围已确认(BTC/USDT 4h 2023-01-01 → 2026-07-31)
- [ ] `lookahead-analysis` 和 `recursive-analysis` 已运行且无报错
- [ ] 配置统一(pairlist, max_open_trades, protections 已固定)
- [ ] 每个 WF 窗口执行了 4 步(hyperopt → JSON 检查 → backtest → 记录)
- [ ] 5 窗口 × 2 策略 = 10 个 WF 记录全部完成
- [ ] 参数锁定遵守"稳定性优先 + OOS 兼容 + 封存"规则
- [ ] BLIND 窗口 backtest 单次执行(无任何调参)
- [ ] BLIND 通过所有阈值(trades / WR / R:R / PF / DD)
- [ ] Dry-Run 期间未频繁改参(>2 次/周)
- [ ] protections 已启用(Cooldown / MaxDD / StoplossGuard)
- [ ] 用户明确批准后才能切 live

---

## 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| 4h 低频导致单窗口交易数过少,统计意义不足 | 高 | 中 | 接受合并 15 笔阈值;BLIND 延长至 7 月 |
| NFI 参数极多, hyperopt 过拟合 | 高 | 高 | 仅调全局阈值,不调 buy_condition 内部 |
| Embargo 7d 不足以排除 4h 噪声 | 中 | 中 | 启用 recursive-analysis 双重验证 |
| Regime 切换导致参数失效 | 中 | 高 | 监控 ADX,切换时优先暂停或降仓 |
| Dry-Run 改参过频,二次过拟合 | 中 | 高 | 严格执行"每次 ≤2 个参数 + 2 周观察" |
| 用户切 live 但未充分验证 | 低 | **致命** | 强制要求所有阈值通过 + 用户明确批准 |

---

## 预期执行时间

| 阶段 | 任务 | 预期耗时 |
|---|---|---|
| 0 | 数据 + lookahead-analysis + 配置 | 0.5 天 |
| 1 | 5 WF × 2 策略 = 10 hyperopt + 10 backtest + 记录 | 2-3 小时 |
| 2 | 参数锁定 + 稳定性分析 | 0.5 天 |
| 3 | BLIND 单次 backtest + 决策 | 0.1 天 |
| 4 | Dry-Run(实时等待) | 6 周 |
| 5 | Dry-Run 后微调 | 2-4 周 |
| 6 | Live + 维护 | 持续 |

**总预期**:2.5-3 个月从启动到 live-ready(无失败重试)。

---

## 关键文件清单

| 文件 | 作用 | 修改内容 |
|---|---|---|
| `freqtrade-loop/run_hyperopt.py` | WF 训练期 hyperopt 入口 | 无需改,加 `--strategy=` 即可 |
| `freqtrade-loop/run_backtest.py` | WF 测试期 + BLIND backtest 入口 | 无需改 |
| `user_data/strategies/TrendRider4h.py` | 主策略 1 | 仅在阶段 2 写入最终 buy_params |
| `user_data/strategies/NostalgiaForInfinity.py` | 主策略 2 | 仅在阶段 2 写入最终 buy_params |
| `freqtrade-loop/wf_results.csv` | WF 5 窗口记录(新建) | 每次 backtest 追加一行 |
| `freqtrade-loop/CYCLE14_PLAN.md` | 本文件(已提交) | 一次性写入 |
| `freqtrade-loop/STATE.md` | 框架总日志 | Cycle 14 阶段记录追加 |
| `freqtrade-loop/hyperopt-history.json` | 所有 hyperopt 结果 | 自动追加 |
| `freqtrade-loop/backtest-history.json` | 所有 backtest 结果 | 自动追加 |
| `freqtrade-loop/loop-ledger.json` | 运行次数 / token 消耗 | 自动追加 |

辅助文件(`/tmp/c4_results/` 下,gitignored):
- `wf{N}_{strategy}_hyperopt.json` — 每窗口 hyperopt 导出,避免 JSON override
- `cycle14_wf_results.csv` — 汇总 5 窗口结果
- `cycle14_param_lock_rationale.md` — 参数锁定决策记录
- `cycle14_data_inventory.txt` — 数据范围/质量检查记录

---

## 总结

本方案针对 4h 低频特性 + R:R 1.5:1 目标,把用户修订版的 6 阶段流程具体化为:

1. **窗口设计**:5 个 9mo train / 4mo test + 1 个 7mo BLIND,7d embargo
2. **阈值校准**:单窗口 ≥3 笔(替代 ≥25),合并 ≥15 笔,R:R ≥1.5 保持不变
3. **NFI 特殊化**:仅 hyperopt 全局阈值,保留作者核心 buy_condition 逻辑
4. **封存原则**:参数锁定后禁止再针对已用窗口调参
5. **Dry-Run 严管**:单次 ≤2 参数,2 周观察期,防二次过拟合
6. **Live 需用户明确批准**(用户原始约束)

整个流程从启动到 live-ready 预期 2.5-3 个月,严格执行可避免过去 13 个 cycle 中反复出现的"调一次崩一次"循环。
