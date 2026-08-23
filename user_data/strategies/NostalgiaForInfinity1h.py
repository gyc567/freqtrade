# pragma pylint: disable=missing-docstring, invalid-name, pointless-string-statement
# isort: skip_file
"""
NostalgiaForInfinity — Cycle 11 (NFI-style multi-timeframe pullback).

Inspired by NFI5MOHO's high-WR philosophy: multi-timeframe trend filter,
strict entry confluence, fast take-profit + dynamic ATR stoploss, strong
protections. Re-targeted for 4h BTC/USDT (single pair, dry-run friendly).

Architecture
------------
1. Direction: 4h regime (close > ema_200, ema_50 > ema_200, +DI > -DI)
   AND 1d regime (close > ema_200_1d). Both must agree.
2. Entry: pullback to slow EMA (low <= ema_slow, close > open) inside
   the bullish regime, with RSI in healthy range, ADX confirming trend,
   volume confirming the bounce.
3. Confidence score (0-100) from 5 signals (bull, ADX, volume, MACD, DI).
   Trades below threshold are rejected by confirm_trade_entry.
4. Exit cascade:
   - ROI ladder (hours: 0=4%, 48h=2.5%, 96h=1.5%, 168h=0.5%) — quick profit-taking
   - RSI overbought (rsi > 70) — momentum exhaustion
   - EMA death cross on the 4h (fast EMA crosses below slow EMA)
   - 24h early-loss cut if profit < -2%
   - ATR-based stoploss (entry - 1.5x ATR, relaxed to 1.1x after 3%)
   - Trend break (close < ema_200 * 0.98)

Highlights
- Main timeframe: 4h
- Informative: 1d (daily EMA200 macro filter)
- One long entry: pullback to slow EMA
- Hyperopt: 7 buy params + 1 sell param + ROI ladder (3 tiers) + stoploss
- Protections: CooldownPeriod(20) + StoplossGuard(720/3/60) + MaxDrawdown(1440/0.10/300)
- Spot-safe (leverage 1x)
- JSON override trap: ensure no <NostalgiaForInfinity>.json next to this file

Cycle 12 changelog
------------------
- 500-epoch hyperopt refines Cycle 11's 100-epoch result. Best epoch 372/500:
  7 trades / 100% WR / +42.50 USDT / SharpeDaily -1.425 (vs Cycle 11's -1.238).
- Key parameter shifts:
  * rsi_buy_low: 44 -> 32 (more permissive pullback)
  * rsi_period: 12 -> 10 (faster RSI)
  * rsi_buy_high: 57 -> 59
  * min_confidence: 42.961 -> 48.984
  * rsi_exit: 68 -> 65 (tighter exit)
  * stoploss: -0.101 -> -0.064 (tighter catastrophic backstop)
- minimal_roi re-tuned: 0/1438/2343/5823 min (0/24h/39h/97h).

Cycle 11 changelog
------------------
- FIXED minimal_roi keys: were minutes (48/96/168 = 0.8h/1.6h/2.8h), now hours
  (2880/5760/10080 = 48h/96h/168h) per freqtrade convention. See
  [[freqtrade-roi-minutes-not-hours]] for the gotcha.
- Hyperopt now sweeps buy + sell + roi + stoploss spaces (full search).

Run examples
------------
# Backtest (4h, 2023-2025)
python3 freqtrade-loop/run_backtest.py --timerange=20230101-20260101

# Hyperopt (full spaces)
python3 freqtrade-loop/run_hyperopt.py \\
    --strategy NostalgiaForInfinity --timerange=20230101-20260101 \\
    --epochs=200 --spaces=buy,sell,roi,stoploss
"""

from datetime import datetime
from functools import reduce
from typing import Any

import logging
import math

import pandas as pd
import talib.abstract as ta
from pandas import DataFrame

from freqtrade.strategy import (
    DecimalParameter,
    IntParameter,
    IStrategy,
    merge_informative_pair,
)

logger = logging.getLogger(__name__)


class NostalgiaForInfinity1h(IStrategy):
    INTERFACE_VERSION = 3

    # ------------------------------------------------------------------
    # Timeframe + trade direction
    # ------------------------------------------------------------------
    # Cycle 13 Option B: 1h variant for OOS validation
    # Same Cycle 12 params as 4h, just different timeframe.
    # ------------------------------------------------------------------
    timeframe = "1h"
    can_short = False

    # One position per pair at a time — this is a swing strategy, not a
    # multi-position scalper. Larger portfolio across multiple pairs is OK.
    position_adjustment_enable = False

    # ------------------------------------------------------------------
    # ROI ladder — Cycle 11 hyperopt best (epoch with loss=-1.238)
    # Generated from roi_p1/p2/p3 + roi_t1/t2/t3 via
    # IHyperOpt.generate_roi_table (4h timeframe scale).
    # Keys must be int (minutes) — hyperopt outputs floats, we round.
    # Tier interpretation:
    #   0 min       -> 38.5% target
    #   1010 min    (~16.8h) -> 16.5% target
    #   1914 min    (~31.9h) ->  7.7% target
    #   5395 min    (~89.9h) ->  0% target (effectively disabled)
    # In practice, rsi_exit (rsi > 68) fires before ROI targets.
    # ------------------------------------------------------------------
    minimal_roi = {
        "0": 0.35786,
        "1438": 0.25229,    # 23.97h (rounded from 1437.8839978025283)
        "2343": 0.07684,    # 39.05h (rounded from 2342.709160066721)
        "5823": 0,          # 97.05h (rounded from 5823.3785856780105)
    }

    # ------------------------------------------------------------------
    # Stoploss — ATR-based dynamic via custom_stoploss
    # Cycle 12 hyperopt best: -0.064 (tighter than Cycle 11's -0.101)
    # Higher confidence in entries means tighter catastrophic backstop.
    # ------------------------------------------------------------------
    stoploss = -0.06401  # custom_stoploss overrides

    # No trailing stop — NFI's structure relies on hard TP + dynamic SL
    trailing_stop = False

    use_custom_stoploss = True
    use_exit_signal = True
    exit_profit_only = False

    # ------------------------------------------------------------------
    # Startup candle needs — 200 EMA warmup + 14 ADX + 1d EMA200 merge
    # ------------------------------------------------------------------
    startup_candle_count = 250

    # ------------------------------------------------------------------
    # Protections — NFI-style strong risk controls
    # ------------------------------------------------------------------
    @property
    def protections(self):
        return [
            {"method": "CooldownPeriod", "stop_duration": 20},
            {
                "method": "StoplossGuard",
                "lookback_period": 720,
                "trade_limit": 3,
                "stop_duration": 60,
            },
            {
                "method": "MaxDrawdown",
                "lookback_period": 1440,
                "max_allowed_drawdown": 0.10,
                "stop_duration": 300,
            },
        ]

    # ------------------------------------------------------------------
    # Buy params (Cycle 12 hyperopt best — full-space search, 500 epochs)
    # Epoch 372/500: 7 trades / 100% WR / +42.50 USDT / SharpeDaily -1.425
    # Key shift vs Cycle 11: rsi_buy_low 44 -> 32 (much more permissive
    # pullback detection), rsi_period 12 -> 10 (faster RSI).
    # min_confidence 42.961 -> 48.984 (slightly tighter gating).
    # ------------------------------------------------------------------
    buy_params = {
        "rsi_period": 10,
        "rsi_buy_low": 32,
        "rsi_buy_high": 59,
        "adx_min": 23,
        "volume_mult": 1.19,
        "min_confidence": 48.984,
        "atr_stop_mult": 2.384,
    }

    # ------------------------------------------------------------------
    # Sell params (HyperOpt space)
    # ------------------------------------------------------------------
    sell_params = {
        "rsi_exit": 65,
    }

    # ------------------------------------------------------------------
    # Hyperopt parameter ranges
    # ------------------------------------------------------------------
    # Buy space (ema_fast and ema_slow removed — see buy_params docstring)
    rsi_period = IntParameter(10, 18, default=14, space="buy")
    rsi_buy_low = IntParameter(30, 50, default=38, space="buy")
    rsi_buy_high = IntParameter(50, 70, default=58, space="buy")
    adx_min = IntParameter(15, 30, default=20, space="buy")
    volume_mult = DecimalParameter(0.8, 2.0, default=1.0, space="buy")
    min_confidence = DecimalParameter(40.0, 80.0, default=60.0, space="buy")
    atr_stop_mult = DecimalParameter(1.0, 3.0, default=1.5, space="buy")

    # Sell space
    rsi_exit = IntParameter(60, 80, default=70, space="sell")

    # ------------------------------------------------------------------
    # HyperOpt class — explicitly enumerate buy/sell/roi spaces
    # (avoid the auto-discovery conflict that broke Cycle 8 hyperopt)
    # ------------------------------------------------------------------
    class HyperOpt:
        @staticmethod
        def buy_space():
            from freqtrade.optimize.space import Integer, Real

            return [
                Integer(10, 18, name="rsi_period"),
                Integer(30, 50, name="rsi_buy_low"),
                Integer(50, 70, name="rsi_buy_high"),
                Integer(15, 30, name="adx_min"),
                Real(0.8, 2.0, name="volume_mult"),
                Real(40.0, 80.0, name="min_confidence"),
                Real(1.0, 3.0, name="atr_stop_mult"),
            ]

        @staticmethod
        def sell_space():
            from freqtrade.optimize.space import Integer

            return [Integer(60, 80, name="rsi_exit")]

        @staticmethod
        def roi_space():
            # freqtrade's adaptive roi hyperspace — auto-scales by timeframe_min / 5
            # so intervals are sensible for any timeframe.
            # Expects 6 params: roi_p1/p2/p3 (profit-tier drops) + roi_t1/t2/t3
            # (time intervals in minutes between tiers).
            # See freqtrade/optimize/hyperopt/hyperopt_interface.py:67-138.
            from freqtrade.optimize.space import Real

            from freqtrade.exchange import timeframe_to_minutes

            timeframe_min = timeframe_to_minutes("4h")  # locked to 4h
            roi_t_scale = timeframe_min / 5
            roi_p_scale = math.log1p(timeframe_min) / math.log1p(5)
            return [
                Real(0.01 * roi_p_scale, 0.04 * roi_p_scale, name="roi_p1"),
                Real(0.01 * roi_p_scale, 0.07 * roi_p_scale, name="roi_p2"),
                Real(0.01 * roi_p_scale, 0.20 * roi_p_scale, name="roi_p3"),
                Real(int(10 * roi_t_scale), int(120 * roi_t_scale), name="roi_t1"),
                Real(int(10 * roi_t_scale), int(60 * roi_t_scale), name="roi_t2"),
                Real(int(10 * roi_t_scale), int(40 * roi_t_scale), name="roi_t3"),
            ]

        @staticmethod
        def stoploss_space():
            from freqtrade.optimize.space import Real

            return [Real(-0.12, -0.05, name="stoploss")]

    # ------------------------------------------------------------------
    # Informative pairs — 1d macro filter
    # ------------------------------------------------------------------
    def informative_pairs(self):
        pairs = self.dp.current_whitelist() if self.dp else []
        return [(p, "1d") for p in pairs]

    # ------------------------------------------------------------------
    # Indicator population
    # ------------------------------------------------------------------
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Multiple short EMAs (8-41) — used for both entry pullback detection
        # and exit crossover signals
        for p in range(8, 41):
            dataframe[f"ema_{p}"] = ta.EMA(dataframe, timeperiod=p)

        # Long EMAs for trend filter
        dataframe["ema_50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema_200"] = ta.EMA(dataframe, timeperiod=200)

        # Multiple RSI windows — the user-specified rsi_period is the entry
        # signal; others are computed for completeness / future iteration
        for p in range(10, 19):
            dataframe[f"rsi_{p}"] = ta.RSI(dataframe, timeperiod=p)

        # Trend strength + direction
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["plus_di"] = ta.PLUS_DI(dataframe, timeperiod=14)
        dataframe["minus_di"] = ta.MINUS_DI(dataframe, timeperiod=14)

        # MACD histogram — used in confidence scoring
        macd = ta.MACD(dataframe)
        dataframe["macdhist"] = macd["macdhist"]

        # Bollinger Bands (lower + middle used for diagnostic)
        bb = ta.BBANDS(dataframe, timeperiod=20)
        dataframe["bb_lower"] = bb["lowerband"]
        dataframe["bb_middle"] = bb["middleband"]

        # Volume confirmation
        dataframe["volume_ema"] = ta.EMA(dataframe["volume"], timeperiod=20)
        dataframe["volume_ratio"] = dataframe["volume"] / (dataframe["volume_ema"] + 1e-9)

        # ATR — used by custom_stoploss
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)

        # 4h trend state
        dataframe["is_bull"] = (
            (dataframe["close"] > dataframe["ema_200"])
            & (dataframe["ema_50"] > dataframe["ema_200"])
            & (dataframe["plus_di"] > dataframe["minus_di"])
        ).astype(int)

        # Pullback to slow EMA — low touches/crosses the slow EMA,
        # but the bar closes bullishly (close > open)
        # ema_slow locked to 26 (see buy_params docstring — would KeyError
        # on every epoch if hyperopt-sampled because populate_indicators
        # only runs once at hyperopt startup)
        dataframe["pullback"] = (
            (dataframe["low"] <= dataframe["ema_26"]) & (dataframe["close"] > dataframe["open"])
        ).astype(int)

        # 1d macro trend filter — daily EMA200
        if self.dp:
            inf = self.dp.get_pair_dataframe(pair=metadata["pair"], timeframe="1d")
            if len(inf) > 0:
                inf["ema_200"] = ta.EMA(inf, timeperiod=200)
                dataframe = merge_informative_pair(
                    dataframe, inf[["date", "ema_200"]], self.timeframe, "1d", ffill=True
                )

        return dataframe

    # ------------------------------------------------------------------
    # Confidence score — 0-100 multi-signal composite
    # ------------------------------------------------------------------
    def calc_confidence(self, row) -> float:
        score = 0.0
        rsi = row.get(f"rsi_{self.rsi_period.value}", 50)
        adx = row.get("adx", 0)
        vol = row.get("volume_ratio", 1)
        is_bull = row.get("is_bull", 0)
        macd_h = row.get("macdhist", 0)
        plus_di = row.get("plus_di", 0)
        minus_di = row.get("minus_di", 0)

        if is_bull:
            score += 25
        if adx > 25:
            score += 20
        elif adx > 20:
            score += 12
        if 38 < rsi < 50:
            score += 15
        if vol > 1.3:
            score += 15
        elif vol > 1.0:
            score += 8
        if macd_h > 0:
            score += 15
        if plus_di > minus_di:
            score += 10

        return min(score, 100.0)

    # ------------------------------------------------------------------
    # Entry signal
    # ------------------------------------------------------------------
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        del metadata  # required by IStrategy signature, unused (we filter per-pair)
        rsi = f"rsi_{self.rsi_period.value}"

        # Compute confidence first (used as filter)
        dataframe["confidence"] = dataframe.apply(self.calc_confidence, axis=1)

        conditions = [
            dataframe["is_bull"] == 1,
            dataframe["pullback"] == 1,
            dataframe[rsi] > self.rsi_buy_low.value,
            dataframe[rsi] < self.rsi_buy_high.value,
            dataframe["adx"] > self.adx_min.value,
            dataframe["volume_ratio"] > self.volume_mult.value,
            dataframe["volume"] > 0,
            dataframe["confidence"] > self.min_confidence.value,
        ]

        # 1d macro filter — only enter when daily EMA200 also confirms
        if "ema_200_1d" in dataframe.columns:
            conditions.append(dataframe["close"] > dataframe["ema_200_1d"])

        if conditions:
            dataframe.loc[
                reduce(lambda x, y: x & y, conditions),
                ["enter_long", "enter_tag"],
            ] = (1, "pullback_ema")

        return dataframe

    # ------------------------------------------------------------------
    # Exit signal — populates exit_long (informational; use_exit_signal=True)
    # ------------------------------------------------------------------
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        del metadata  # required by IStrategy signature, unused
        rsi = f"rsi_{self.rsi_period.value}"
        # ema_fast/ema_slow locked to 8/26 (see buy_params docstring)
        ema_f = "ema_8"
        ema_s = "ema_26"

        # RSI overbought
        dataframe.loc[
            dataframe[rsi] > self.rsi_exit.value,
            ["exit_long", "exit_tag"],
        ] = (1, "rsi_exit")

        # EMA death cross (fast crosses below slow)
        ema_cross = (dataframe[ema_f] < dataframe[ema_s]) & (
            dataframe[ema_f].shift(1) >= dataframe[ema_s].shift(1)
        )
        macd_turn = dataframe["macdhist"] < 0
        dataframe.loc[
            ema_cross & macd_turn,
            ["exit_long", "exit_tag"],
        ] = (1, "ema_cross")

        return dataframe

    # ------------------------------------------------------------------
    # Confidence-based entry filter (rejects weak signals)
    # ------------------------------------------------------------------
    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time: datetime,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> bool:
        # Acknowledge required-by-interface params (silence unused-arg warnings;
        # the IStrategy contract demands this exact signature)
        _ = (
            order_type,
            amount,
            rate,
            time_in_force,
            current_time,
            entry_tag,
            side,
            kwargs,
        )
        # Get the latest analyzed row
        try:
            dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if dataframe is None or len(dataframe) == 0:
                return False
            last = dataframe.iloc[-1]
        except Exception:
            return False

        # Reject if confidence is below threshold
        conf = float(last.get("confidence", 0))
        if conf < self.min_confidence.value:
            return False

        # Reject if 1d macro filter has not warmed up
        if "ema_200_1d" in dataframe.columns:
            if pd.isna(last.get("ema_200_1d")) or last["close"] < last["ema_200_1d"]:
                return False

        return True

    # ------------------------------------------------------------------
    # Custom stoploss — NFI-style dynamic ATR floor
    # ------------------------------------------------------------------
    def custom_stoploss(
        self,
        pair: str,
        trade: Any,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool = False,
        **kwargs,
    ) -> float | None:
        _ = (current_time, after_fill, kwargs)  # IStrategy signature compliance
        try:
            dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if dataframe is None or len(dataframe) == 0:
                return self.stoploss
            atr = float(dataframe.iloc[-1].get("atr", 0))
        except Exception:
            return self.stoploss

        if atr <= 0:
            return self.stoploss

        # Base floor: entry - atr_stop_mult x ATR
        floor_rate = trade.open_rate - self.atr_stop_mult.value * atr
        sl = (floor_rate - current_rate) / current_rate

        # After 3% profit, relax slightly to give winners room
        if current_profit > 0.03:
            sl = min(sl * 1.1, -0.005)  # never tighter than -0.5%

        return sl

    # ------------------------------------------------------------------
    # Custom exit — cascade of fast profit-takes + early loss cuts
    # ------------------------------------------------------------------
    def custom_exit(
        self,
        pair: str,
        trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool = False,
        **kwargs,
    ) -> str | None:
        _ = (current_rate, after_fill, kwargs)  # IStrategy signature compliance
        duration_h = (current_time - trade.open_date_utc).total_seconds() / 3600

        # Early loss cut — don't hold losers > 4h with -2% loss
        if duration_h >= 4 and current_profit <= -0.02:
            return "early_loss_cut_4h"

        # Mid-stage loss cut — don't hold losers > 12h with -3%
        if duration_h >= 12 and current_profit <= -0.03:
            return "early_loss_cut_12h"

        # Trend break — if 4h close < ema_200 * 0.98, exit any open position
        try:
            dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if dataframe is not None and len(dataframe) > 0:
                last = dataframe.iloc[-1]
                if current_profit > 0:
                    close = float(last.get("close", 0))
                    ema_200 = float(last.get("ema_200", 0))
                    if ema_200 > 0 and close < ema_200 * 0.98:
                        return "trend_broken"
        except Exception as e:
            logger.warning("custom_exit trend check failed: %s", e)

        return None

    # ------------------------------------------------------------------
    # Leverage (spot-safe)
    # ------------------------------------------------------------------
    def leverage(
        self,
        pair: str,
        current_time,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        _ = (
            pair,
            current_time,
            current_rate,
            proposed_leverage,
            max_leverage,
            entry_tag,
            side,
            kwargs,
        )
        return 1.0
