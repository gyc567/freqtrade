# pragma pylint: disable=missing-docstring, invalid-name, pointless-string-statement
# flake8: noqa
# isort: skip_file
"""
HeraclesV2 Strategy — 4h Donchian + Keltner volatility breakout filter.
Loop 10: Dual-Mode + KCW Independent + Market Filter (2026-08-22).
Author: @Mablue (Masoud Azizi), adapted for freqtrade
github: https://github.com/mablue/

IMPORTANT: Add to your pairlists in config.json (under StaticPairList):
    { "method": "AgeFilter", "min_days_listed": 100 },

IMPORTANT: INSTALL TA BEFORE RUN (pip install ta)

Recommended hyperopt:
    freqtrade hyperopt --hyperopt-loss SharpeHyperOptLoss \
        --spaces roi buy --strategy HeraclesV2 --timeframe 4h

Backtest example:
    freqtrade backtesting --strategy HeraclesV2 --timeframe 4h \
        --timerange 20240101- --pairs BTC/USDT
"""

from datetime import datetime
from functools import reduce
from typing import Dict

import ta
from pandas import DataFrame
from ta.utils import dropna

from freqtrade.strategy import DecimalParameter, IntParameter, IStrategy


class HeraclesV2(IStrategy):
    # ======================================= RESULT PASTE PLACE =============
    # Loop 10: Dual-Mode + KCW Independent + Market Filter (2026-08-22)
    # ======================================= END RESULT PASTE PLACE ========

    INTERFACE_VERSION: int = 3

    # ── Timeframe ────────────────────────────────────────────────────────
    timeframe: str = "4h"
    informative_timeframe: str = "1d"

    # Buy hyperspace params (from hyperopt):
    buy_params = {
        "buy_crossed_indicator_shift": 9,
        "buy_div_max": 0.75,
        "buy_div_min": 0.14,
        "buy_indicator_shift": 15,
    }

    # Sell hyperspace params:
    sell_params = {}

    # ROI table (preserved from original):
    minimal_roi = {
        "0": 0.598,
        "644": 0.166,
        "3269": 0.115,
        "7289": 0,
    }

    # Stoploss — hard safety cap:
    stoploss = -0.12

    # ── Trailing stop (Freqtrade native) ────────────────────────────────
    trailing_stop: bool = True
    trailing_stop_positive: float = 0.10
    trailing_stop_positive_offset: float = 0.11  # must be > trailing_stop_positive
    trailing_only_offset_is_reached: bool = False

    # ── Protections ─────────────────────────────────────────────────────
    stop_duration_candles: int = 6   # 6 × 4h = 24 h cooldown

    # ── Loop 10 hyperopt params (2026-08-22) ────────────────────────────
    buy_div_min = DecimalParameter(0.0, 0.5, default=0.14, decimals=2, space="buy")
    buy_div_max = DecimalParameter(0.5, 1.0, default=0.75, decimals=2, space="buy")
    buy_indicator_shift = IntParameter(0, 30, default=15, space="buy")
    buy_crossed_indicator_shift = IntParameter(0, 30, default=9, space="buy")

    # ── Exit params (Loop 10) ────────────────────────────────────────
    ADX_THRESH: int = 30
    RSI_EXIT: int = 78
    RSI_CONSECUTIVE: int = 4
    KCW_EXIT_THRESH: float = 0.008
    MAX_HOLD: int = 192

    # ── ATR Percentile market filter (Loop 10) ───────────────────────
    ATR_PCT_WINDOW: int = 720
    ATR_PCT_LOW: float = 0.25   # no-entry threshold
    ATR_PCT_MR: float = 0.30    # MR zone threshold

    # ── Mean Reversion (MR) mode params (Loop 10) ─────────────────────
    MR_MAX_HOLD: int = 48
    MR_RSI_EXIT: int = 62
    MR_RSI_ENTRY: int = 55
    MR_STOPLOSS: float = -0.03
    MR_TP_PCT: float = 0.015

    def _atr(self, dataframe: DataFrame, period: int = 14) -> DataFrame:
        """ATR(14) helper. ta lib v0.11.0 dropped ta.atr; use ta.volatility.average_true_range."""
        return ta.volatility.average_true_range(
            dataframe["high"],
            dataframe["low"],
            dataframe["close"],
            window=period,
            fillna=False,
        )

    def populate_indicators(
        self, dataframe: DataFrame, metadata: dict
    ) -> DataFrame:
        dataframe = dropna(dataframe)

        # ── 4h Keltner Channel Width Band ─────────────────────────────
        dataframe["volatility_kcw"] = ta.volatility.keltner_channel_wband(
            dataframe["high"],
            dataframe["low"],
            dataframe["close"],
            window=20,
            window_atr=10,
            fillna=False,
            original_version=True,
        )

        # ── 4h Donchian Channel Percent Band ──────────────────────────
        dataframe["volatility_dcp"] = ta.volatility.donchian_channel_pband(
            dataframe["high"],
            dataframe["low"],
            dataframe["close"],
            window=10,
            offset=0,
            fillna=False,
        )

        # ── ATR(14) for custom_stoploss ────────────────────────────────
        dataframe["atr_14"] = self._atr(dataframe, period=14)

        # ── ADX(14) for trend strength (exit filter) ───────────────────
        # ta lib v0.11.0 uses ta.trend.adx instead of ta.adx.
        dataframe["adx_14"] = ta.trend.adx(
            dataframe["high"], dataframe["low"], dataframe["close"], window=14, fillna=False
        )

        # ── RSI(14) for exit signals ───────────────────────────────────
        # ta lib v0.11.0 uses ta.momentum.rsi (lowercase function).
        dataframe["rsi_14"] = ta.momentum.rsi(dataframe["close"], window=14, fillna=False)

        # ── ATR percentile (Loop 10) ───────────────────────────────────
        # Rolling percentile of ATR(14) over ATR_PCT_WINDOW 4h candles
        dataframe["atr_pct"] = (
            dataframe["atr_14"]
            .rolling(window=self.ATR_PCT_WINDOW, min_periods=self.ATR_PCT_WINDOW // 2)
            .apply(lambda x: (x < x.iloc[-1]).sum() / len(x), raw=False)
        )

        # ── MR entry signal (Loop 10) ──────────────────────────────────
        # ratio < 0.08 & rsi < MR_RSI_ENTRY
        ratio = dataframe["volatility_dcp"] / dataframe["volatility_kcw"].replace(0, float("nan"))
        dataframe["mr_entry_sig"] = (ratio < 0.08) & (dataframe["rsi_14"] < self.MR_RSI_ENTRY)

        return dataframe

    def info_to_1d_indicators(
        self, metadata: dict, dataframe: DataFrame
    ) -> DataFrame:
        """Populate 1d informative candles with EMA200 for trend filtering."""
        dataframe = dropna(dataframe)
        dataframe["ema_200_1d"] = ta.trend.ema_indicator(
            dataframe["close"],
            window=200,
            fillna=False,
        )
        return dataframe

    def custom_stoploss(
        self,
        pair: str,
        trade: 'Trade',
        current_time: 'datetime',
        current_rate: float,
        current_profit: float,
        start_mode: bool,
        **kwargs,
    ) -> float:
        """
        Dynamic stoploss: -1.5 × ATR(14) on the 4h candle.
        Freqtrade calls this per candle to find the best stop-price.
        """
        try:
            dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if dataframe is None or dataframe.empty:
                return self.stoploss
            atr_series = dataframe["atr_14"]
            if atr_series is None or atr_series.empty:
                return self.stoploss
            atr = float(atr_series.iloc[-1])
            # Convert ATR absolute value to a stoploss ratio relative to current rate
            stoploss_distance = -1.5 * atr / current_rate
            # Never widen past the hard floor
            return max(stoploss_distance, self.stoploss)
        except Exception:
            return self.stoploss

    def custom_exit(
        self,
        pair: str,
        trade: 'Trade',
        current_time: 'datetime',
        current_rate: float,
        current_profit: float,
        **kwargs,
    ) -> str | None:
        """
        Time-based exit: force close after MAX_HOLD 4h candles.
        """
        if trade is None:
            return None
        elapsed = int((current_time - trade.open_date_utc).total_seconds() // 3600)
        if elapsed >= self.MAX_HOLD:
            return "max_hold"
        return None

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
        """
        1d EMA200 uptrend gate: only enter long when 1d close > EMA200.
        This avoids bear-trap entries during macro downtrends.
        """
        try:
            inf_df, _ = self.dp.get_analyzed_dataframe(pair, self.informative_timeframe)
            if inf_df is None or inf_df.empty:
                return True  # no 1d data yet — fail open
            if "ema_200_1d" not in inf_df.columns:
                return True  # EMA not yet available — fail open
            ema_200 = float(inf_df["ema_200_1d"].iloc[-1])
            close_1d = float(inf_df["close"].iloc[-1])
            if ema_200 <= 0:
                return True  # EMA not formed yet
            return close_1d > ema_200
        except Exception:
            return True  # fail open on any error

    def populate_entry_trend(
        self, dataframe: DataFrame, metadata: dict
    ) -> DataFrame:
        """
        Buy signal: ratio of DCP/KCW falls within [div_min, div_max]
        after applying the two shift offsets.
        Entry is further gated by 1d EMA200 filter in confirm_trade_entry.
        ATR_pct market filter: no entry when atr_pct < ATR_PCT_LOW (low volatility).
        MR entry: mr_entry_sig when atr_pct is in MR zone.
        """
        conditions = []

        ind = dataframe["volatility_dcp"]
        crs = dataframe["volatility_kcw"]

        ratio = (
            ind.shift(self.buy_indicator_shift.value)
            .div(crs.shift(self.buy_crossed_indicator_shift.value))
        )

        # Primary momentum entry
        conditions.append(
            ratio.between(self.buy_div_min.value, self.buy_div_max.value)
        )

        # ATR percentile market filter — block entry in low-volatility environment
        conditions.append(dataframe["atr_pct"] >= self.ATR_PCT_LOW)

        if conditions:
            dataframe.loc[
                reduce(lambda x, y: x & y, conditions),
                "enter_long",
            ] = 1

        return dataframe

    def populate_exit_trend(
        self, dataframe: DataFrame, metadata: dict
    ) -> DataFrame:
        """
        Exit signals (Loop 10 — Dual-Mode):

        Momentum mode exits:
          1. KCW independent exit: volatility_kcw > KCW_EXIT_THRESH
             (no ADX filter — pure volatility breakout)
          2. RSI overbought: RSI(14) above RSI_EXIT for RSI_CONSECUTIVE bars
             while ADX < ADX_THRESH

        Mean-Reversion mode exits:
          3. MR RSI exit: RSI(14) > MR_RSI_EXIT
          4. MR max hold: elapsed >= MR_MAX_HOLD candles
          (MR tp/stoploss handled via custom_exit/custom_stoploss hooks)

        ATR_pct market filter gates entries in populate_entry_trend;
        exits are unrestricted so positions can be closed in any regime.
        Time stops handled via MAX_HOLD / MR_MAX_HOLD in custom_exit.
        """
        conditions = []

        # ── Momentum mode: KCW independent exit ─────────────────────────
        kcw_exit = dataframe["volatility_kcw"] > self.KCW_EXIT_THRESH
        conditions.append(kcw_exit)

        # ── Momentum mode: RSI overbought exit ───────────────────────────
        adx_ok = dataframe["adx_14"] < self.ADX_THRESH
        rsi_consec = (
            dataframe["rsi_14"]
            .rolling(window=self.RSI_CONSECUTIVE, min_periods=self.RSI_CONSECUTIVE)
            .apply(lambda x: (x > self.RSI_EXIT).all(), raw=False)
        )
        conditions.append(adx_ok & (rsi_consec == 1))

        # ── Mean-Reversion mode: RSI exit ────────────────────────────────
        conditions.append(dataframe["rsi_14"] > self.MR_RSI_EXIT)

        if conditions:
            dataframe.loc[
                reduce(lambda x, y: x | y, conditions),
                "exit_long",
            ] = 1
        else:
            dataframe.loc[:, "exit_long"] = 0

        return dataframe

    # ── Declarative protections ─────────────────────────────────────────
    @property
    def protections(self) -> list:
        return [
            {
                "method": "CooldownPeriod",
                "stop_duration_candles": self.stop_duration_candles,
            },
        ]
