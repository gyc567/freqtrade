# Cycle 14 Phase 5 — 微调与迭代规则

> **目的**: 在 dry-run 期间或之后,如果实际表现与 frozen-params backtest 偏差 > 15%,允许进行受控的微调。规则严格限制,防二次过拟合。
> **依据**: Cycle 14 计划 § 阶段 5 + Phase 2 决策(锁定 buy_params)。
> **前提**: dry-run 已运行 ≥ 30 天或 ≥ 3 笔实际交易(否则统计功效不足)。

---

## 1. 可调参数清单(每个 ±10% 单次调整)

| 参数 | 范围 | 单次调整上限 | 调整效果预期 |
|---|---|---|---|
| `min_confidence`(NFI) | 当前 48.984 | ±5(等价 ±10%) | 放宽 → 更多交易,但 WR 可能下降;收紧 → 更高 WR,但交易更少 |
| `adx_min`(NFI) | 当前 23 | ±2 | 提高 → 强制更强趋势(减少逆势交易) |
| `adx_max`(TR4h) | 当前 33 | ±3 | 提高 → 允许更多趋势期入场;收紧 → 仅震荡期 |
| `rsi_oversold_max`(TR4h) | 当前 37 | ±4 | 放宽 → 更早入场(可能接刀);收紧 → 更深超卖 |
| `volume_factor`(TR4h) / `volume_mult`(NFI) | TR4h 1.0 / NFI 1.19 | ±10% | 放宽 → 接受低量信号;收紧 → 仅高量 |
| `atr_stop_mult`(NFI) | 当前 2.384 | ±0.24 | 缩小 → 更紧止损(更快认错);放宽 → 容忍更大波动 |
| `sl_atr_mult`(TR4h) | 当前 1.754 | ±0.18 | 同上 |
| `tp_atr_mult`(TR4h) | 当前 2.864 | ±0.29 | 提高 → 更大目标(R:R 提升,WR 可能下降) |
| `rsi_exit`(NFI sell) | 当前 65 | ±5 | 提高 → 让盈利跑更久 |
| `minimal_roi` 时间节点 | 当前 {"0": 0.04, "240": 0.02} | 各 ±20% | 提前 ROI → 更早锁利 |

---

## 2. 冻结参数(任何情况都不动)

| 参数 | 理由 |
|---|---|
| `rsi_period`(NFI) | 锁定(在 populate_indicators 中使用,不能在运行时改) |
| **入场逻辑**(`populate_entry_trend` 公式) | 改这个等于重写策略,违背"稳定买参"原则 |
| **出场逻辑**(`populate_exit_trend` 公式) | 同上 |
| **multi-TF informative**(1d 配合) | 改这个破坏趋势过滤的设计 |
| **max_open_trades = 1** | 用户硬约束(单对 + 单仓) |
| **stoploss 框架**(custom_stoploss ATR-based) | 框架级,改需重新验证整套 |
| **时间框架**(4h + 1d) | 改需要重新校准所有参数 |
| **pair_whitelist** | 用户硬约束(BTC/USDT 唯一对) |

---

## 3. 调整流程(严格 6 步)

### Step 1: 基线记录
```bash
# 当前实际 dry-run 结果(从 sqlite 或 JSONL)
ft_venv/bin/python3 freqtrade-loop/run_dryrun_compare.py \
  --baseline=frozen-params-backtest \
  --strategy=TrendRider4h \
  --window=20260501-20260731
```

记录基线:
- WR (实际 vs backtest,偏差)
- R:R (实际 vs backtest,偏差)
- DD (实际 vs backtest,偏差)
- 退出原因分布(实际 vs backtest,偏差)

### Step 2: 选择 1-2 个参数
- 选择 **当前**偏差最大维度的相关参数(不能全选)
- 例:WR 偏低 → 优先调 min_confidence(adx_min 次之)
- 例:R:R 偏低 → 优先调 tp_atr_mult / rsi_exit
- 例:DD 偏高 → 优先调 sl_atr_mult / atr_stop_mult(收紧止损)

### Step 3: 单次调整
- 在 `user_data/strategies/<strategy>.py` 中修改选中的 1-2 个参数值
- 修改后**立即 commit**,commit message 注明:
  ```
  Cycle 14 Phase 5 micro-tune: <strategy> <param1> A->B (<reason>)
  ```

### Step 4: 重新回测对比
```bash
# 在 dry-run 同一窗口上重新跑 backtest(冻结新参数)
ft_venv/bin/python3 freqtrade-loop/run_backtest.py \
  --strategy=TrendRider4h \
  --timerange=20260501-20260731
```

### Step 5: 评估
- **新参数在 dry-run 窗口 backtest 必须比冻结基线**:
  - WR 改善 ≥ 3% 或 R:R 改善 ≥ 0.2(单维度)
  - DD 不能恶化 > 10 USDT
- 满足 → 保留,继续观察
- 不满足 → **回滚**(git revert),记录失败案例到 STATE.md §14.14 micro-tune log

### Step 6: 观察期
- 新参数需观察 ≥ 2 周或 ≥ 3 笔实际交易才能决定保留
- 观察期内不允许再调整
- 每次调整最多 1-2 个参数,最多 1 次/2 周

---

## 4. 对比基线(每次调整必须与之比)

| 基线 | 来源 | 用途 |
|---|---|---|
| **frozen-params WF5** | `wf_cycle14_phase1B_frozen_params.csv` WF5 行 | 同期对比(2025-06 → 2025-10) |
| **frozen-params WF 合计** | `wf_cycle14_phase1B_frozen_params.csv` 5 行加总 | 长期对比(2023-10 → 2025-10) |
| **Cycle 8 / Cycle 12** | `user_data/strategies/<strategy>.py` 锁定 buy_params 的原始 backtest | 训练期对比 |
| **上一次 dry-run** | `/tmp/c4_results/dryrun/report_<strategy>.md` 上次结果 | 调参前后对比 |

---

## 5. 迭代节奏

| 时间节点 | 触发条件 | 动作 |
|---|---|---|
| T+0 | 启动 dry-run | 锁定 buy_params(已完成 per Phase 2) |
| T+30 天 OR ≥ 3 笔交易 | 首次评估窗口 | 与 frozen-params WF5 对比,记录偏差 |
| T+60 天 OR ≥ 6 笔交易 | 第二次评估 | 若偏差 > 15% → 启用 Step 1-6 微调流程 |
| T+90 天 | dry-run 结束 | 生成最终报告,决定 PASS / MARGINAL / FAIL |
| T+90+30 天(延长)| 若 MARGINAL | 再跑 30 天 |
| T+180 天(上限) | 强制决策点 | 必须 PASS → 申请 live,或 FAIL → 重锁 |

**单次 dry-run 总时长上限**:180 天(防止无限循环)。

---

## 6. 多实例交互规则

### 6.1 仓位独立性
- TR4h 和 NFI 钱包完全独立(各 1000 USDT)
- 一方的损失不影响另一方
- 两方调参独立,可分别优化

### 6.2 调参同步
- **不同步**:两策略的调参应独立进行
- **绝对禁止**:为一个策略的 dry-run 结果去修改另一个策略(无信号关联)

### 6.3 共享参数修改
- `protections`(Cooldown / StoplossGuard / MaxDD)是两策略共用的**框架级**配置
- 修改前需要**双策略** dry-run 同时验证
- 不允许只在一个策略上修改 protections

---

## 7. 异常情况处理

### 7.1 实际表现远好于 backtest(WR > 80%, R:R > 5)
- **怀疑**: regime 切换、信号过拟合、数据漂移
- **动作**: 暂停 dry-run,审查最近 30 天信号质量
- **不可立即进入 live**(异常好通常意味着过拟合)

### 7.2 实际表现远差于 backtest(WR < 30%, R:R < 1.0)
- **怀疑**: 配置漂移(timeframe / pairlist / data 不同)
- **动作**: 立即排查配置一致性(对比 `config.binance_local.json` 与 Cycle 14 Phase 0 配置)
- **优先**:重新跑 frozen-params 同窗口 backtest,确认 backtest 仍然如预期
- 确认非配置漂移 → 进入 Step 1-6 微调流程

### 7.3 没有交易
- **原因**: regime 不匹配(per BLIND 调查) 或 数据问题
- **动作**: 验证数据完整性 + 检查 regime 过滤器
- **不可调参**(无信号 = 无可学习样本)

### 7.4 单笔大亏(> 50 USDT)
- **立即停止该策略**
- 审查:是否 custom_stoploss 失效 / protection 未触发 / 数据异常
- 修复前不重启

---

## 8. dry-run → live 转换条件(用户硬约束)

| 条件 | 说明 |
|---|---|
| **dry-run 全部验收门槛通过** | WR ≥ 40%, R:R ≥ 1.5, DD ≤ 30 USDT |
| **持续运行 ≥ 90 天** | 不能 1 周就切 |
| **≥ 5 笔实际交易** | 最低统计功效 |
| **无未解决的异常事件** | 没有大亏 / 配置漂移 / regime 异常 |
| **用户明确批准** | **硬约束**(任何自动切换 live 都被禁止) |
| **仓位减半**(初始 live) | 回测收益 / DD 比例的 1/2,逐步放大 |

---

## 9. 文档化要求

每次调参 / 评估都必须在 STATE.md §14.14 micro-tune log 记录:

```markdown
### 14.14.X — Micro-tune attempt Y (date)

**Trigger**: WR deviated -18% vs frozen-params WF5 (42% actual vs 60% baseline)
**Action**: 
- TR4h: rsi_oversold_max 37 -> 35 (-5%, in allowed range)
- TR4h: tp_atr_mult 2.864 -> 3.150 (+10%, in allowed range)

**Backtest on dry-run window (20260501-20260731)**:
- WR: 42% -> 51% (+9pp)
- R:R: 1.2 -> 1.8 (+0.6)
- DD: 35 USDT -> 28 USDT (-7)

**Decision**: KEEP changes. Continue dry-run. Next review: T+15 days.
```

---

## 10. 总结

本规则的核心理念:**微调是微调,不是重训**。

- 单次最多 1-2 个参数
- 单次最多 ±10% 调整
- 每次必须有 backtest 验证
- 观察期 ≥ 2 周 / 3 笔交易
- 失败回滚并记录

这保证 dry-run 期间的所有调参行为都是**结构化的、可审计的、防过拟合的**,为最终 live 决策提供坚实基础。
