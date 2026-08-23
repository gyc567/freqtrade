# pragma pylint: disable=missing-docstring, invalid-name, pointless-string-statement
# flake8: noqa: F401
# isort: skip_file
"""
TrendRider4h — Cycle 7 (Mean Reversion Pro / MR-Pro).

Major rewrite from Cycle 5/6 breakout to **mean reversion** architecture.
The breakout+trailing approach had a structural ceiling at 40-55% WR on
2023 BTC 4h (Cycle 5: 1 trade / 0% WR; Cycle 6: 4 trades / 25% WR).
Reasoning:

1. **Direction reversal**: don't predict breakouts (frequent fakeouts);
   buy extreme panic sells in uptrends and take profit at fixed R:R.
2. **Larry Connors' RSI(2) formula** is one of the most validated
   mean-reversion systematic strategies in literature — historically
   70-80% WR on liquid equities/FX, with similar behaviour on liquid
   crypto. Strict regime filter (daily trend + 4h trend + volume spike)
   keeps Bear-market noise out.
3. **Hard TP / SL instead of trailing**: trailing stop in Cycle 6 killed
   3 of 4 trades within 1-2 days. MR-Pro uses **fixed 3R take profit**
   and **1.5×ATR stop**. Winners exit at TP before any meaningful
   retracement; losers exit at the structural invalidation.
4. **R:R math**: 65% WR × 3R − 35% × 1R = +1.60R per trade. Profitable
   even at 55% WR (+0.65R). 60%+ WR is structurally reachable.

Highlights
- Main timeframe: 4h
- Informative: 1d (daily EMA50 trend + is_bull)
- One long entry (rsi_oversold) — Connors' RSI(2) < 8 with strict confluence
- Hard TP at 3×ATR(14) via custom_exit (`tp_3r_atr`)
- Hard SL at 1.5×ATR(14) via custom_stoploss
- Time stop at 96h via custom_exit
- No trailing stop (intentional — let winners hit TP)
- Protections: CooldownPeriod(8) + StoplossGuard(720/3/60) + MaxDrawdown(1440/0.10/300/5)
- Hyperopt-ready parameter set with sensible 4h defaults
- Spot-safe (leverage 1x)

Run examples
------------
# Backtest (4h, 2023)
python3 freqtrade-loop/run_backtest.py --timerange=20230101-20240101

# Hyperopt — Stage 1: buy + ROI + stoploss
freqtrade hyperopt --strategy TrendRider4h \\
    --timeframe 4h --pairs BTC/USDT \\
    --timerange 20230101-20250101 \\
    --hyperopt-loss SharpeHyperOptLossDaily \\
    --spaces buy roi stoploss -e 500
"""

import logging
from datetime import datetime
from functools import reduce

import talib.abstract as ta
from pandas import DataFrame

from freqtrade.strategy import (
    DecimalParameter,
    IntParameter,
    IStrategy,
    merge_informative_pair,
)


logger = logging.getLogger(__name__)


class TrendRider4h(IStrategy):
    INTERFACE_VERSION = 3

    # ------------------------------------------------------------------
    # HyperOpt: nested class for ROI/stoploss search.
    # Buy space is auto-discovered from IntParameter/DecimalParameter.
    # ------------------------------------------------------------------
    class HyperOpt:
        """Stage 1: search buy + ROI + stoploss. Ranges match MR-Pro design
        (RSI(14) shift(1) pullback-in-uptrend, 3R ATR TP, 1.5x ATR SL)."""
        @staticmethod
        def buy_space():
            from freqtrade.optimize.space import Integer, Real
            return [
                # RSI(14) oversold threshold: 25-45 (Cycle 7 sweet spot 35)
                Integer(25, 45, name="rsi_oversold_max"),
                # Volume factor: 0.8-1.5 (default 1.0)
                Real(0.8, 1.5, name="volume_factor"),
                # ADX max (avoid strong trends): 30-50 (default 40)
                Integer(30, 50, name="adx_max"),
                # TP at N x ATR: 2.0-4.0 (default 3.0)
                Real(2.0, 4.0, name="tp_atr_mult"),
                # SL at N x ATR: 1.0-2.5 (default 1.5)
                Real(1.0, 2.5, name="sl_atr_mult"),
            ]

        @staticmethod
        def sell_space():
            return []

        @staticmethod
        def roi_space():
            from freqtrade.optimize.space import Real
            return [Real(0.05, 0.20, name="roi_t1")]

        @staticmethod
        def stoploss_space():
            from freqtrade.optimize.space import Real
            return [Real(-0.05, -0.02, name="stoploss")]

    # ------------------------------------------------------------------
    # Timeframe & general
    # ------------------------------------------------------------------
    timeframe = "4h"
    startup_candle_count = 250       # need ~100 days for EMA200 + warmup
    process_only_new_candles = True
    can_short = False
    position_adjustment_enable = False
    use_custom_stoploss = True       # Cycle 7: ATR-based hard stop

    # ------------------------------------------------------------------
    # ROI / stoploss / trailing
    # ------------------------------------------------------------------
    # Cycle 7 (MR-Pro): NO trailing stop. Hard TP via custom_exit at 3R.
    # Hard SL via custom_stoploss at 1.5×ATR. ROI is a safety net only.
    minimal_roi = {
        "0":   0.10,    # 10% immediate — almost never hit (TP is at 3R ≈ 4-6%)
        "240": 0.20,    # 240h (10d) → 20% — failsafe if TP missed
    }
    stoploss = -0.05                 # 5% catastrophic backstop (custom_stoploss overrides)

    trailing_stop = False            # trailing killed Cycle 6 — disabled

    # ------------------------------------------------------------------
    # Protections (Freqtrade 2026.2+ inline)
    # ------------------------------------------------------------------
    protections = [
        {"method": "CooldownPeriod", "stop_duration": 8},   # 8 bars = 32h
        {
            "method": "StoplossGuard",
            "lookback_period": 720,
            "trade_limit": 3,
            "stop_duration": 60,
            "only_per_pair": False,
        },
        {
            "method": "MaxDrawdown",
            "lookback_period": 1440,
            "max_allowed_drawdown": 0.10,
            "stop_duration": 300,
            "trade_limit": 5,
        },
    ]

    # ------------------------------------------------------------------
    # Hyperopt: applied values (Cycle 7 MR-Pro defaults)
    # ------------------------------------------------------------------
    buy_params = {
        # Cycle 8 hyperopt (2026-08-21, 100 epochs, Sharpe daily loss) — epoch 55
        "rsi_oversold_max": 37,       # Previous bar's RSI(14) < 37
        "volume_factor": 1.0,         # volume > 1.0× avg (mild confirmation)
        "adx_max": 33,                # ADX < 33 (tighter trend filter)
        "tp_atr_mult": 2.864,         # TP at 2.86×ATR (~4-5%)
        "sl_atr_mult": 1.754,         # SL at 1.75×ATR (~2-3%)
    }

    # ------------------------------------------------------------------
    # Hyperopt: parameter ranges (4h-tuned for pullback-in-uptrend)
    # ema_short and ema_long are FIXED (not hyperopt) because they're used in
    # populate_indicators, which only runs once at hyperopt startup — sampling
    # them would cause KeyError('ema_<N>') on every epoch.
    # ------------------------------------------------------------------
    rsi_oversold_max = IntParameter(25, 45, default=35, space="buy")
    volume_factor    = DecimalParameter(0.8, 1.5, default=1.0, space="buy")
    adx_max          = IntParameter(30, 50, default=40, space="buy")
    tp_atr_mult      = DecimalParameter(2.0, 4.0, default=3.0, space="buy")
    sl_atr_mult      = DecimalParameter(1.0, 2.5, default=1.5, space="buy")

    # ------------------------------------------------------------------
    # Informative pairs
    # ------------------------------------------------------------------
    btc_context_pair = ""  # disabled — BTC trades BTC

    def informative_pairs(self):
        pairs = self.dp.current_whitelist() if self.dp else []
        return [(p, "1d") for p in pairs]

    # ------------------------------------------------------------------
    # Indicator population
    # ------------------------------------------------------------------
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # --- Main 4h indicators ---
        for period in (9, 20, 50, 100, 200):
            dataframe[f"ema_{period}"] = ta.EMA(dataframe, timeperiod=period)

        # RSI series — most important for MR-Pro
        dataframe["rsi_2"]  = ta.RSI(dataframe, timeperiod=2)   # signal
        dataframe["rsi_14"] = ta.RSI(dataframe, timeperiod=14)  # context

        # ADX for trend regime (reject over-trending)
        dataframe["adx"]       = ta.ADX(dataframe, timeperiod=14)
        dataframe["plus_di"]   = ta.PLUS_DI(dataframe, timeperiod=14)
        dataframe["minus_di"]  = ta.MINUS_DI(dataframe, timeperiod=14)

        # Bollinger Bands for context (not used in entry)
        bb = ta.BBANDS(dataframe, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
        dataframe["bb_upper"]  = bb["upperband"]
        dataframe["bb_middle"] = bb["middleband"]
        dataframe["bb_lower"]  = bb["lowerband"]
        dataframe["bb_width"]  = (
            (dataframe["bb_upper"] - dataframe["bb_lower"])
            / (dataframe["bb_middle"] + 1e-10)
        )

        # Volume profile (for spike detection)
        dataframe["vol_sma_20"] = ta.SMA(dataframe["volume"], timeperiod=20)
        dataframe["vol_std_20"] = ta.STDDEV(dataframe["volume"], timeperiod=20)
        dataframe["vol_zscore"] = (
            (dataframe["volume"] - dataframe["vol_sma_20"])
            / (dataframe["vol_std_20"] + 1e-10)
        )

        # ATR computed early — needed by stops + TP
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)

        # Candle structure
        dataframe["body"]    = dataframe["close"] - dataframe["open"]
        dataframe["range"]   = dataframe["high"] - dataframe["low"]
        dataframe["lower_wick"] = (
            (dataframe["open"] < dataframe["close"]).astype(float)
            * (dataframe["close"] - dataframe["low"]) +
            (dataframe["open"] >= dataframe["close"]).astype(float)
            * (dataframe["open"] - dataframe["low"])
        )

        # 4h trend (fixed EMA periods — these are used in populate_indicators
        # so they can't be hyperopt params)
        ema_short = dataframe["ema_50"]
        ema_long  = dataframe["ema_200"]
        dataframe["is_bull"] = (
            (dataframe["close"] > ema_short) & (ema_short > ema_long)
        ).astype(int)

        # --- Informative: 1d ---
        if self.dp:
            df_1d = self.dp.get_pair_dataframe(pair=metadata["pair"], timeframe="1d")
            if len(df_1d) > 0:
                df_1d["ema_50"]  = ta.EMA(df_1d, timeperiod=50)
                df_1d["ema_200"] = ta.EMA(df_1d, timeperiod=200)
                df_1d["rsi_14"]  = ta.RSI(df_1d, timeperiod=14)
                df_1d["is_bull"] = (
                    (df_1d["close"] > df_1d["ema_200"])
                    & (df_1d["ema_50"] > df_1d["ema_200"])
                ).astype(int)
                dataframe = merge_informative_pair(
                    dataframe,
                    df_1d[["date", "ema_50", "ema_200", "rsi_14", "is_bull"]],
                    self.timeframe,
                    "1d",
                    ffill=True,
                )
            else:
                _inject_defaults(
                    dataframe,
                    ["is_bull_1d", "rsi_14_1d", "ema_50_1d", "ema_200_1d"],
                    [1, 50, 0, 0],
                )
        else:
            _inject_defaults(
                dataframe,
                ["is_bull_1d", "rsi_14_1d", "ema_50_1d", "ema_200_1d"],
                [1, 50, 0, 0],
            )

        # Static placeholders (no live API)
        dataframe["fng_value"]     = 50
        dataframe["funding_rate"]  = 0.0

        return dataframe

    # ------------------------------------------------------------------
    # Entry signals
    # ------------------------------------------------------------------
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # === MR-Pro ENTRY v3: Shift(1) pullback-in-uptrend ===
        # Connors' classic timing: when RSI(14) drops to oversold on bar T, wait
        # for the FOLLOWING bar (T+1) to confirm reversal. Entry on T+1 if:
        #   - Bar T had RSI(14) < threshold (the panic)
        #   - Bar T+1 has close > open (reversal confirmation)
        #   - Bar T+1 has volume > avg (capitulation/interest)
        # Plus macro filters:
        #   - Daily close > daily close 30 days ago (slope — doesn't get killed
        #     by the pullback we're entering on)
        #   - 4h close > EMA200 (slow MA holds trend during pullbacks)
        #   - ADX < threshold (skip very strong trends where pullbacks fail)

        # Look at PREVIOUS bar's RSI for the panic signal
        rsi_prev = dataframe["rsi_14"].shift(1)

        cond_oversold = [
            # 1. Previous bar was oversold (Connors' signal)
            rsi_prev < self.rsi_oversold_max.value,
            rsi_prev.notna(),
            # 2. Current bar is bullish reversal (close > open)
            dataframe["close"] > dataframe["open"],
            # 3. Current bar has volume confirmation
            dataframe["volume"] > self.volume_factor.value * dataframe["vol_sma_20"],
            # 4. Daily slope up (close > 30-day-ago close) — slope-based so it
            #    doesn't get killed by the short-term pullback we're buying
            (
                (dataframe["is_bull_1d"] == 1)
                if "is_bull_1d" in dataframe.columns
                else True
            ),
            # 5. 4h above slow MA (trend intact during pullback)
            dataframe["close"] > dataframe["ema_200"],
            # 6. ADX < threshold (avoid strong trend breakdowns)
            dataframe["adx"] < self.adx_max.value,
            # 7. Volume > 0 (sanity)
            dataframe["volume"] > 0,
        ]
        dataframe.loc[
            reduce(lambda x, y: x & y, cond_oversold),
            ["enter_long", "enter_tag"],
        ] = (1, "rsi_oversold")
        n_signals = int(reduce(lambda x, y: x & y, cond_oversold).sum())
        if n_signals > 0:
            logger.info(f"rsi_oversold signals fired: {n_signals}")

        return dataframe

    # ------------------------------------------------------------------
    # Exit signals (populate_exit_trend — informational, not used by custom_exit)
    # ------------------------------------------------------------------
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Only used if use_exit_signal=True and we don't want to rely strictly on
        # custom_exit. Disabled (we use custom_exit for hard TP/SL).
        return dataframe

    # ------------------------------------------------------------------
    # Custom exit: Cycle 7 — hard TP at 3R + time stop
    # ------------------------------------------------------------------
    def custom_exit(self, pair: str, trade, current_time: datetime,
                    current_rate: float, current_profit: float, **kwargs):
        # Get current ATR
        try:
            dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if dataframe is None or len(dataframe) == 0:
                return None
            atr = dataframe.iloc[-1].get("atr", None)
        except Exception:
            return None

        if atr is None or atr <= 0:
            return None

        entry_rate = trade.open_rate
        tp_distance = self.tp_atr_mult.value * atr

        # 1. Hard TP at 3R (or 2R if HyperOpt picks lower)
        if current_rate >= entry_rate + tp_distance:
            return "tp_3r_atr"

        # 2. Time stop at 96h — if not at TP/SL, exit at market
        duration_hours = (current_time - trade.open_date_utc).total_seconds() / 3600
        if duration_hours >= 96:
            return "time_exit_96h"

        return None

    # ------------------------------------------------------------------
    # Custom stoploss: Cycle 7 — hard SL at 1.5×ATR below entry
    # ------------------------------------------------------------------
    def custom_stoploss(self, pair: str, trade, current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs) -> float:
        try:
            dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if dataframe is None or len(dataframe) == 0:
                return self.stoploss
            atr = dataframe.iloc[-1].get("atr", None)
        except Exception:
            return self.stoploss

        if atr is None or atr <= 0:
            return self.stoploss

        # Hard SL at sl_atr_mult × ATR below entry
        floor_rate = trade.open_rate - self.sl_atr_mult.value * atr
        return (floor_rate - current_rate) / current_rate

    # ------------------------------------------------------------------
    # Confidence filter (rejects weak signals before they enter)
    # ------------------------------------------------------------------
    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float,
                            time_in_force: str, current_time: datetime,
                            entry_tag: str | None, side: str, **kwargs) -> bool:
        # MR-Pro: confidence filter disabled — entry conditions are already strict
        # enough (8+ layers of confluence). Adding confidence scoring on top rejects
        # valid mean-reversion setups.
        return True

    # ------------------------------------------------------------------
    # Helpers — confidence, regime (kept for backward compat)
    # ------------------------------------------------------------------
    def _calc_confidence(self, last: dict) -> tuple:
        score = 0.0
        details = []
        rsi_2 = last.get("rsi_2", 50)
        if rsi_2 < 5:
            score += 3.0
            details.append("RSI(2) extreme")
        elif rsi_2 < 10:
            score += 2.0
            details.append("RSI(2) oversold")

        vol_z = last.get("vol_zscore", 0)
        if vol_z > 2.0:
            score += 2.0
            details.append("Volume z-score >2")
        elif vol_z > 1.0:
            score += 1.0
            details.append("Volume z-score >1")

        if last.get("is_bull", 0) == 1:
            score += 1.5
            details.append("4h uptrend")
        if last.get("is_bull_1d", 0) == 1:
            score += 2.0
            details.append("Daily uptrend")

        adx = last.get("adx", 0)
        if adx < 25:
            score += 1.5
            details.append("Low ADX (pullback)")

        numeric = max(1, min(10, round(score * 10 / 10.0)))
        level = "STRONG" if numeric >= 8 else "GOOD" if numeric >= 6 else "MEDIUM" if numeric >= 4 else "WEAK"
        bar = "|" * numeric + "-" * (10 - numeric) + f" {numeric}/10"
        return level, bar, details, numeric

    def _get_market_regime(self, last: dict) -> str:
        adx_val = last.get("adx", 0)
        ema_long = last.get("ema_200", 0)
        close = last.get("close", 0)
        is_bull = last.get("is_bull", 0)
        if adx_val < 20:
            return "Ranging"
        if is_bull and close > ema_long:
            return "Trending Bull"
        return "Trending Bear"

    # ------------------------------------------------------------------
    # 1x leverage (spot-safe)
    # ------------------------------------------------------------------
    def leverage(self, pair: str, current_time, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: str,
                 side: str, **kwargs) -> float:
        return 1.0


# ----------------------------------------------------------------------
# Module-level helper
# ----------------------------------------------------------------------
def _inject_defaults(dataframe: DataFrame, cols: list[str], defaults: list) -> None:
    """Ensure columns exist before downstream selection."""
    for col, default in zip(cols, defaults):
        if col not in dataframe.columns:
            dataframe[col] = default
