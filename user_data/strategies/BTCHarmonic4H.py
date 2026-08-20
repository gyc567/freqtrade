# pragma pylint: disable=missing-docstring, invalid-name, pointless-string-statement
"""
BTC 4H Harmonic Patterns Strategy
=================================
Freqtrade strategy for BTC/USDT on 4H timeframe using Harmonic Patterns
(Gartley, Bat, Butterfly, Crab, Deep Crab, Cypher, Shark)
with directional change extrema detection and trendline confirmation.

Based on NeuroV1 by RoboticAutomations / neurotrader888.
Adapted for BTC 4H with simplified entry logic.

Patterns detected:
  - Bullish: Gartley, Bat, Butterfly, Crab, Deep Crab, Cypher, Shark
  - Bearish: same set
"""

from freqtrade.strategy import IStrategy
import numpy as np
import pandas as pd
import pandas_ta as ta
from typing import Union, List
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class XABCD:
    XA_AB: Union[float, List, None]
    AB_BC: Union[float, List, None]
    BC_CD: Union[float, List, None]
    XA_AD: Union[float, List, None]
    name: str


@dataclass
class XABCDFound:
    X: int
    A: int
    B: int
    C: int
    D: int
    error: float
    name: str
    bull: bool


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

class BTCHarmonic4H(IStrategy):
    """
    BTC 4H Harmonic Patterns Strategy.
    Detects XABCD harmonic patterns (Gartley, Bat, Butterfly, Crab,
    Deep Crab, Cypher, Shark) on BTC/USDT 4H charts.
    """
    INTERFACE_VERSION = 3

    # --- Timeframe -----------------------------------------------------------
    timeframe = "4h"
    startup_candle_count = 150   # ~25 days of 4H data for extrema detection

    # --- Risk / ROI ---------------------------------------------------------
    stoploss = -0.10
    minimal_roi = {
        "0":    0.04,
        "24":   0.02,
        "48":   0.01,
        "72":   0.00,
    }

    trailing_stop = False
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # --- Strategy parameters (tuned for 4H) ---------------------------------
    sigma = 0.02            # Directional change threshold
    harmonic_err_thresh = 0.18   # Max combined error for pattern match

    # -------------------------------------------------------------------------
    # Harmonic pattern definitions
    # -------------------------------------------------------------------------

    GARTLEY      = XABCD(0.618, [0.382, 0.886], [1.13, 1.618], 0.786, "Gartley")
    BAT          = XABCD([0.382, 0.50], [0.382, 0.886], [1.618, 2.618], 0.886, "Bat")
    BUTTERFLY    = XABCD(0.786, [0.382, 0.886], [1.618, 2.24], [1.27, 1.41], "Butterfly")
    CRAB         = XABCD([0.382, 0.618], [0.382, 0.886], [2.618, 3.618], 1.618, "Crab")
    DEEP_CRAB    = XABCD(0.886, [0.382, 0.886], [2.0, 3.618], 1.618, "Deep Crab")
    CYPHER       = XABCD([0.382, 0.618], [1.13, 1.41], [1.27, 2.00], 0.786, "Cypher")
    SHARK        = XABCD(None, [1.13, 1.618], [1.618, 2.24], [0.886, 1.13], "Shark")
    ALL_PATTERNS = [GARTLEY, BAT, BUTTERFLY, CRAB, DEEP_CRAB, CYPHER, SHARK]

    # -------------------------------------------------------------------------
    # Indicator population
    # -------------------------------------------------------------------------

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        # Log-price to make ratios scale-invariant
        dataframe["log_close"] = np.log(dataframe["close"])

        # ---- 1. Directional-change extrema ---------------------------------
        extremes = self._get_extremes(dataframe, self.sigma)

        # Mark swing tops / bottoms in dataframe
        dataframe["top"]    = 0
        dataframe["bottom"] = 0
        for idx in extremes.index:
            if extremes.loc[idx, "type"] == 1:
                dataframe.loc[dataframe.index[idx], "top"]    = 1
            else:
                dataframe.loc[dataframe.index[idx], "bottom"] = 1

        # ---- 2. Harmonic patterns (XABCD) ---------------------------------
        harmonic_output = self._find_xabcd(dataframe, extremes)
        dataframe["harmonic_bull"] = 0
        dataframe["harmonic_bear"] = 0
        for pat in self.ALL_PATTERNS:
            dataframe["harmonic_bull"] += harmonic_output[pat.name]["bull_signal"]
            dataframe["harmonic_bear"] += harmonic_output[pat.name]["bear_signal"]

        # ---- 3. RSI (confirmation filter) ----------------------------------
        dataframe["rsi"] = ta.rsi(dataframe["close"], length=14)

        # ---- 4. ADX (trend strength filter) ------------------------------
        adx_data = ta.adx(
            dataframe["high"],
            dataframe["low"],
            dataframe["close"],
            length=14
        )
        dataframe["adx"]     = adx_data["ADX_14"]
        dataframe["plus_di"]  = adx_data["DMP_14"]
        dataframe["minus_di"] = adx_data["DMN_14"]

        # ---- 5. ATR (stop / volume filter) --------------------------------
        dataframe["atr"] = ta.atr(
            dataframe["high"],
            dataframe["low"],
            dataframe["close"],
            length=14
        )

        return dataframe

    # -------------------------------------------------------------------------
    # Entry signals
    # -------------------------------------------------------------------------

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe["enter_long"] = 0
        dataframe.loc[
            (
                # Harmonic bullish pattern detected
                (dataframe["harmonic_bull"] > 0)
                # At a swing bottom
                & (dataframe["bottom"] == 1)
                # RSI not overbought (not yet)
                & (dataframe["rsi"] < 70)
                # ADX confirms trend is not weak
                & (dataframe["adx"] > 20)
                # Bullish DI above Bearish DI
                & (dataframe["plus_di"] > dataframe["minus_di"])
                # Volume present
                & (dataframe["volume"] > 0)
            ),
            "enter_long",
        ] = 1

        return dataframe

    # -------------------------------------------------------------------------
    # Exit signals
    # -------------------------------------------------------------------------

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe["exit_long"] = 0
        dataframe.loc[
            (
                # Harmonic bearish pattern = exit signal
                (dataframe["harmonic_bear"] > 0)
                # RSI overbought or bearish DI cross
                & (
                    (dataframe["rsi"] > 65)
                    | (dataframe["minus_di"] > dataframe["plus_di"])
                )
            )
            # Hard RSI ceiling
            | (dataframe["rsi"] > 80),
            "exit_long",
        ] = 1

        return dataframe

    # =========================================================================
    # Internal: directional-change extrema
    # =========================================================================

    def _directional_change(
        self,
        close: np.ndarray,
        high: np.ndarray,
        low: np.ndarray,
        sigma: float,
    ):
        up_zig = True
        tmp_max, tmp_min = high[0], low[0]
        tmp_max_i, tmp_min_i = 0, 0
        tops, bottoms = [], []

        for i in range(len(close)):
            if up_zig:
                if high[i] > tmp_max:
                    tmp_max, tmp_max_i = high[i], i
                elif close[i] < tmp_max - tmp_max * sigma:
                    tops.append([i, tmp_max_i, tmp_max])
                    up_zig = False
                    tmp_min, tmp_min_i = low[i], i
            else:
                if low[i] < tmp_min:
                    tmp_min, tmp_min_i = low[i], i
                elif close[i] > tmp_min + tmp_min * sigma:
                    bottoms.append([i, tmp_min_i, tmp_min])
                    up_zig = True
                    tmp_max, tmp_max_i = high[i], i

        return tops, bottoms

    def _get_extremes(self, ohlc: pd.DataFrame, sigma: float) -> pd.DataFrame:
        tops, bottoms = self._directional_change(
            ohlc["close"].to_numpy(),
            ohlc["high"].to_numpy(),
            ohlc["low"].to_numpy(),
            sigma,
        )
        tops_df = pd.DataFrame(tops,    columns=["conf_i", "ext_i", "ext_p"])
        bots_df = pd.DataFrame(bottoms, columns=["conf_i", "ext_i", "ext_p"])
        tops_df["type"] = 1
        bots_df["type"] = -1
        extremes = pd.concat([tops_df, bots_df]).set_index("conf_i").sort_index()
        return extremes

    # =========================================================================
    # Internal: harmonic pattern matching (XABCD)
    # =========================================================================

    @staticmethod
    def _get_error(actual_ratio: float, pattern_ratio: Union[float, List, None]) -> float:
        if pattern_ratio is None:
            return 0.0
        log_actual = np.log(actual_ratio)
        if isinstance(pattern_ratio, list):
            log_pat0 = np.log(pattern_ratio[0])
            log_pat1 = np.log(pattern_ratio[1])
            if log_pat0 <= log_actual <= log_pat1:
                return 0.0
            err = min(abs(log_actual - log_pat0), abs(log_actual - log_pat1)) * 2.0
            return err
        return abs(log_actual - np.log(pattern_ratio))

    def _find_xabcd(self, ohlc: pd.DataFrame, extremes: pd.DataFrame):
        output = {
            pat.name: {
                "bull_signal": np.zeros(len(ohlc)),
                "bear_signal": np.zeros(len(ohlc)),
            }
            for pat in self.ALL_PATTERNS
        }

        # Guard: no extremes detected → skip pattern detection
        if len(extremes) == 0:
            return output

        extremes = extremes.copy()
        extremes["seg_height"]    = (extremes["ext_p"] - extremes["ext_p"].shift(1)).abs()
        extremes["retrace_ratio"] = extremes["seg_height"] / extremes["seg_height"].shift(1)

        first_conf   = extremes.index[0]
        extreme_i   = 0
        entry_taken = 0
        pattern_used = None

        for i in range(first_conf, len(ohlc)):
            # Advance extreme pointer when confirmed extreme is reached
            if extreme_i + 1 < len(extremes) and extremes.index[extreme_i + 1] == i:
                entry_taken = 0
                extreme_i  += 1

            # Continue emitting pattern signal until next extreme
            if entry_taken != 0:
                if entry_taken == 1:
                    output[pattern_used]["bull_signal"][i] = 1
                else:
                    output[pattern_used]["bear_signal"][i] = -1
                continue

            if extreme_i + 1 >= len(extremes) or extreme_i < 3:
                continue

            ext_type    = extremes.iloc[extreme_i]["type"]
            last_conf_i = extremes.index[extreme_i]

            # D must be the lowest low (bull) or highest high (bear) since last extreme
            if ext_type > 0:
                d_price = ohlc.iloc[i]["low"]
                if ohlc.iloc[last_conf_i:i]["low"].min() < d_price:
                    continue
            else:
                d_price = ohlc.iloc[i]["high"]
                if ohlc.iloc[last_conf_i:i]["high"].max() > d_price:
                    continue

            dc_retrace    = abs(d_price - extremes.iloc[extreme_i]["ext_p"])        / extremes.iloc[extreme_i]["seg_height"]
            xa_ad_retrace = abs(d_price - extremes.iloc[extreme_i - 2]["ext_p"])    / extremes.iloc[extreme_i - 2]["seg_height"]

            best_err = 1e30
            best_pat = None

            for pat in self.ALL_PATTERNS:
                err  = self._get_error(extremes.iloc[extreme_i]["retrace_ratio"],    pat.AB_BC)
                err += self._get_error(extremes.iloc[extreme_i - 1]["retrace_ratio"], pat.XA_AB)
                err += self._get_error(dc_retrace,                                    pat.BC_CD)
                err += self._get_error(xa_ad_retrace,                                 pat.XA_AD)
                if err < best_err:
                    best_err = err
                    best_pat = pat.name

            if best_err <= self.harmonic_err_thresh:
                pattern_used = best_pat
                if ext_type > 0:
                    entry_taken = 1
                    output[best_pat]["bull_signal"][i] = 1
                else:
                    entry_taken = -1
                    output[best_pat]["bear_signal"][i] = -1

        return output
