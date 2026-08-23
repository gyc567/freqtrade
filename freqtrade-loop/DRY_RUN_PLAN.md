# Cycle 14 Phase 4 — Dry-Run Plan(详细执行方案)

> **执行日期**: 2026-08-23
> **依据**: Cycle 14 Phase 1+2 锁定 buy_params(TR4h Cycle 8 / NFI Cycle 12),BLIND regime-incompatible。
> **目标**: 验证冻结参数在最近 90 天的"实时"表现,与 frozen-params WF(WF5 = 2025-06 → 2025-10)对比,作为下一步微调/live 决策依据。
> **硬约束**: dry_run 始终开启,未经用户明确批准绝不切 live(BTC/USDT 4h 唯一对,[[freqtrade-loop/STATE.md]] Human Decisions)。

---

## 1. 基础设施架构

由于 Binance API 被墙(per [[freqtrade-loop/STATE.md]] Known Issues),无法使用 freqtrade 原生 `freqtrade trade --dry-run` 命令(fetch_ohlcv 会被 ccxt 拦截)。改用**本地数据回放引擎**:

```
fq-data-downloader (feather)  →  run_dryrun.py  →  freqtrade.Backtesting (复用)
                                    ↓
                            - 逐 K 线决策日志(JSONL)
                            - 交易表(sqlite)
                            - summary CSV
                            - 与 frozen-params WF 对比
```

**核心原则**:复用 freqtrade 内部的 `Backtesting` 类(它已经处理了 populate_indicators、custom_stoploss、custom_exit、protections、ROI tiers、multi-TF informative pairs),不重新实现。差异仅在输出层:

| 维度 | 传统 backtest | dry-run replay |
|---|---|---|
| 时间跨度 | 全部数据(9-36 个月) | 最近 90 天(模拟"上个月") |
| 输出 | 聚合 metrics | 逐 K 线决策 + 聚合 metrics |
| 日志 | 1 个 backtest-result zip | JSONL 决策流 + 1 个 zip |
| 对比基线 | 无 | frozen-params WF 同窗口 |

---

## 2. 引擎文件清单

| 文件 | 用途 | 行数预算 |
|---|---|---|
| `freqtrade-loop/run_dryrun.py` | 回放引擎(包装 Backtesting,逐 K 线日志) | ~250 |
| `freqtrade-loop/run_dryrun_compare.py` | 对比分析器(dryrun vs frozen-params 同窗口) | ~150 |
| `freqtrade-loop/DRY_RUN_PLAN.md` | 本文件 | — |
| `freqtrade-loop/DRY_RUN_TUNING_RULES.md` | 微调规则 | — |
| `user_data/dryrun_TR4h.sqlite` | TR4h 交易表 | 自动 |
| `user_data/dryrun_NFI.sqlite` | NFI 交易表 | 自动 |
| `/tmp/c4_results/dryrun/` | 决策日志 + 报告 | 自动 |

---

## 3. 运行配置(2 个并行实例)

### 3.1 共享基础配置
- **对**: `BTC/USDT`
- **timeframe**: `4h`
- **informative timeframe**: `1d`
- **数据源**: `/Users/jie/code/fq-data-downloader/data/binance/BTC_USDT-{4h,1d}.feather`
- **stake_currency**: USDT
- **dry_run**: `true`(硬约束)
- **fee**: `0.001`(0.1%,与 backtest 一致)
- **stoploss_on_exchange**: `false`(策略内部 ATR 止损已覆盖)
- **max_open_trades**: `1`

### 3.2 钱包分配
| 实例 | 钱包 | stake_amount | 备注 |
|---|---|---|---|
| **TrendRider4h** | 1000 USDT | unlimited | 与 backtest 一致 |
| **NostalgiaForInfinity** | 1000 USDT | unlimited | 与 backtest 一致 |

**风险隔离**:两个策略钱包独立,任何一方的损失不影响另一方。这是 live 之前的关键隔离测试。

### 3.3 启动命令
```bash
# TR4h 实例(后台运行,日志到 /tmp/c4_results/dryrun/TR4h_<timestamp>.log)
ft_venv/bin/python3 freqtrade-loop/run_dryrun.py \
  --strategy=TrendRider4h \
  --timerange=20260501-20260731 \
  --db-url=sqlite:///user_data/dryrun_TR4h.sqlite

# NFI 实例(并行)
ft_venv/bin/python3 freqtrade-loop/run_dryrun.py \
  --strategy=NostalgiaForInfinity \
  --timerange=20260501-20260731 \
  --db-url=sqlite:///user_data/dryrun_NFI.sqlite
```

### 3.4 回放窗口选择

| 选项 | 时间 | 数据根数 | 预期交易 | 备注 |
|---|---|---|---|---|
| 候选 A | 2026-05-01 → 2026-07-31(90 天) | ~180 | 2-4 | 与 BLIND 窗口部分重叠(regime 已恢复) |
| 候选 B | 2026-02-01 → 2026-07-31(180 天) | ~360 | 4-8 | 含 regime 切换,信号更多 |
| 候选 C | 2025-06-07 → 2025-10-07(等同 WF5) | ~180 | 3 | 与 WF5 直接对比 |

**默认**: 选择 **候选 A**(2026-05-01 → 2026-07-31)。理由:
1. 最新 90 天,最贴近"如果昨天启动会怎样"
2. 数据完整(2026-07-31 是数据集末尾)
3. BTC 从 $58k(2026 年初低点)反弹到 $97k,regime 已恢复 uptrend,TR4h 应该有信号
4. 与 BLIND(2026-01 → 2026-07)部分重叠,便于横向比较

---

## 4. 监控指标(实时 + 事后)

### 4.1 实时监控(运行期间每 4h 检查)
| 指标 | 来源 | 阈值 |
|---|---|---|
| 当前持仓状态 | sqlite `trades` 表 | — |
| 已实现 P/L | sqlite `trades` 表 | — |
| 未实现 P/L | 最近一根 K 线 close vs open_rate | — |
| 信号流 | JSONL 决策日志 | 频率与回测 ±20% |
| 保护触发 | JSONL `[protection]` 标记 | 应与回测一致 |

### 4.2 事后分析(运行完成后)
**Dry-Run 报告**(`/tmp/c4_results/dryrun/report_<strategy>.md`):
| 字段 | 内容 |
|---|---|
| 总交易 | N |
| 胜/负 | W/L |
| 胜率 | WR |
| 净 P/L | USDT |
| 最大 DD | USDT |
| 平均赢家 | USDT |
| 平均输家 | USDT |
| 真实 R:R | avg_winner / \|avg_loser\| |
| 平均持仓时长 | h |
| 退出原因分布 | ROI / custom_exit / custom_stoploss / exit_signal |
| 与 frozen-params WF5 对比 | 偏差 % |
| 决策阈值 | WR ≥ 40%, R:R ≥ 1.5, DD ≤ 30 USDT |

### 4.3 验收门槛(用户硬要求)
| 阈值 | 说明 |
|---|---|
| **trades ≥ 3** | 4h 90 天窗口最低统计功效(per Phase 1 § 14.2 校准) |
| **WR ≥ 40%** | R:R ≥ 1.5 等价条件(WP × 1.5R − (1−WP) × 1R > 0) |
| **R:R ≥ 1.5** | 用户硬门槛 |
| **profit > 0** | 否则不进入 live 评估 |
| **DD ≤ 30 USDT** | 否则调低仓位或暂停 |
| **WR 偏差 vs WF ≤ ±15%** | 否则排查配置漂移 |
| **最大连续亏损 ≤ 3** | 否则启用额外 cooldown 保护 |

---

## 5. 停止条件

### 5.1 立即停止(进入 Phase 5 微调或回 Phase 2 重锁)
- DD > 50 USDT(超 30 USDT 阈值 67%)
- 最大连续亏损 > 5
- 实际 WR < 25%(远低于 40%)
- 实际 R:R < 1.0(完全亏损边缘)
- 单笔亏损 > 50 USDT(单笔触发保护未生效)

### 5.2 软暂停(继续运行,但记录待评估)
- WR 偏差 vs WF 在 ±15-25%
- DD 在 30-50 USDT 区间
- 单日 P/L 波动 > 10 USDT

### 5.3 异常停止(可能是 bug)
- 决策日志出现 freqtrade 内部错误
- 持仓状态与信号不一致
- 累计 P/L 与 trades 表对不上
- 时序错乱(决策时间戳倒退)

---

## 6. 日报 / 周报流程

### 6.1 每日(自动)
- JSONL 决策流自动追加
- 任何 close 事件触发 sqlite insert

### 6.2 每周(人工 / claude review)
- 提取本周 trades + 与本周 frozen-params 偏差
- 若有新 trade,记录 decision_quality(信号强度 / 价格入场时机 / 退出原因)
- 更新 STATE.md §14.13 dry-run progress table

### 6.3 整体(运行结束)
- 生成 `report_<strategy>.md` 总结
- 决定下一步:
  - **PASS**: 申请 live(需用户明确批准)
  - **MARGINAL**: 延长 dry-run 至 180 天
  - **FAIL**: 回 Phase 5 微调或 Phase 2 重锁

---

## 7. 文件结构

```
/Users/jie/code/freqtrade/
├── freqtrade-loop/
│   ├── run_dryrun.py                  # 回放引擎(新)
│   ├── run_dryrun_compare.py          # 对比分析器(新)
│   ├── DRY_RUN_PLAN.md                # 本文件(新)
│   ├── DRY_RUN_TUNING_RULES.md        # 微调规则(新)
│   └── STATE.md §14.13                # dry-run 进展
├── user_data/
│   ├── strategies/
│   │   ├── TrendRider4h.py            # 锁定参数(不动)
│   │   └── NostalgiaForInfinity.py    # 锁定参数(不动)
│   ├── dryrun_TR4h.sqlite             # 自动生成
│   └── dryrun_NFI.sqlite              # 自动生成
└── /tmp/c4_results/dryrun/
    ├── dryrun_TR4h_<timestamp>.log    # 决策流
    ├── dryrun_NFI_<timestamp>.log
    ├── report_TR4h.md                 # 事后分析
    └── report_NFI.md
```

---

## 8. 强制检查清单

执行前必须全部打勾:

- [ ] 数据 2026-05-01 → 2026-07-31 在 `/Users/jie/code/fq-data-downloader/data/binance/BTC_USDT-4h.feather` 中存在
- [ ] `user_data/strategies/*.json` 无 stale JSON 残留(防止 [[freqtrade-json-override-trap]])
- [ ] 两个策略 `buy_params` 来自 .py 锁定版本,不在 dry-run 期间修改
- [ ] `config.binance_local.json` `dry_run: true` 已确认
- [ ] sqlite db-url 路径有写权限(`user_data/` 目录)
- [ ] `/tmp/c4_results/dryrun/` 有写权限
- [ ] 用户已明确批准运行 dry-run(本对话已隐含批准)

---

## 9. 端到端验证

完整执行链路:

```bash
# 1. 准备
mkdir -p /tmp/c4_results/dryrun

# 2. 启动两个 dry-run(可并行)
(ft_venv/bin/python3 freqtrade-loop/run_dryrun.py \
  --strategy=TrendRider4h \
  --timerange=20260501-20260731 \
  --db-url=sqlite:///user_data/dryrun_TR4h.sqlite \
  > /tmp/c4_results/dryrun/TR4h_$(date +%s).log 2>&1) &

(ft_venv/bin/python3 freqtrade-loop/run_dryrun.py \
  --strategy=NostalgiaForInfinity \
  --timerange=20260501-20260731 \
  --db-url=sqlite:///user_data/dryrun_NFI.sqlite \
  > /tmp/c4_results/dryrun/NFI_$(date +%s).log 2>&1) &

# 3. 等待完成
wait

# 4. 生成对比报告
ft_venv/bin/python3 freqtrade-loop/run_dryrun_compare.py \
  --window=20260501-20260731

# 5. 更新 STATE.md
# (人工 review 后追加 §14.13 dry-run results)
```

**成功标准**:
- 两个策略都跑完 90 天回放,日志完整
- 总交易 ≥ 3(任一策略)
- WR ≥ 40%, R:R ≥ 1.5, DD ≤ 30 USDT(至少一策略全部满足)
- 对比报告生成,偏差在 ±15% 以内
- 用户明确批准后才能切 live(继续守 dry_run 硬约束)
