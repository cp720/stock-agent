"""
tests/test_technical_analyst.py

Tests for pure logic in technical_analyst.py:
  - _detect_divergence() — no mocks needed (pure pandas/numpy)
  - Indicator interpretation thresholds (RSI, ADX, Stochastic)
  - Overall signal vote counting (8-indicator majority)
  - Reversal alert factor counting
"""
import numpy as np
import pandas as pd
import pytest

from technical_analyst import _detect_divergence


# ---------------------------------------------------------------------------
# Helpers that replicate the inline interpretation logic from
# get_technical_indicators() — used to test the threshold boundaries.
# ---------------------------------------------------------------------------

def _rsi_signal(rsi_value: float) -> str:
    if rsi_value < 30:
        return "Oversold"
    if rsi_value > 70:
        return "Overbought"
    return "Neutral"


def _adx_interpretation(adx_value: float):
    """Returns (trend_strength, signal_confidence)."""
    if adx_value > 25:
        return "Strong Trend", "High"
    if adx_value >= 20:
        return "Moderate", "Moderate"
    return "Ranging", "Low"


def _stoch_signal(stoch_k: float) -> str:
    if stoch_k > 80:
        return "Overbought"
    if stoch_k < 20:
        return "Oversold"
    return "Neutral"


def _overall_signal(
    rsi_signal: str,
    momentum_pct: float,
    macd_line: float,
    macd_signal_line: float,
    price: float,
    sma_20: float,
    sma_50: float,
    vwap_20: float,
    bb_percent_b: float,
    di_plus: float,
    di_minus: float,
) -> str:
    bullish_votes = sum([
        rsi_signal != "Overbought",
        momentum_pct > 0,
        macd_line > macd_signal_line,
        price > sma_20,
        price > sma_50,
        price > vwap_20,
        bb_percent_b > 0.50,
        di_plus > di_minus,
    ])
    if bullish_votes >= 5:
        return "Bullish"
    if (8 - bullish_votes) >= 5:
        return "Bearish"
    return "Neutral"


def _reversal_alert(bearish_factors: list, bullish_factors: list) -> str:
    if len(bearish_factors) >= 2:
        return "Potential Bearish Reversal"
    if len(bullish_factors) >= 2:
        return "Potential Bullish Reversal"
    return "None"


# ---------------------------------------------------------------------------
# Helpers to build deterministic series with known peaks / troughs
# ---------------------------------------------------------------------------

def _series_with_peaks(peak1_val, peak2_val, indicator1_val, indicator2_val, n=30):
    """
    Build a price series with exactly two local maxima at indices 8 and 22
    (verified with peak_window=5):

        index: 0    1    2    3    4    5    6    7   [8]   9   10   11   12   13   14
        shape: rising ramp → peak at 8 → falling ramp → valley → rising ramp → peak at 22 → falling ramp

    The ramps are strictly monotone so no intermediate index can be a local max.
    Returns (price_series, indicator_series), both pd.Series of length n.
    """
    price_vals = [
        40, 45, 50, 55, 60, 65, 70, 75,          # 0-7: rising to peak
        peak1_val,                                 # 8:   PEAK 1
        90, 80, 70, 60, 50, 40, 30,               # 9-15: falling
        40, 50, 60, 70, 80, 90,                   # 16-21: rising to peak
        peak2_val,                                 # 22:  PEAK 2
        100, 90, 80, 70, 60, 50, 40,              # 23-29: falling
    ]
    ind_vals = [
        35, 40, 45, 48, 50, 52, 54, 56,
        indicator1_val,                            # 8
        55, 50, 45, 42, 40, 38, 36,
        38, 42, 46, 50, 54, 58,
        indicator2_val,                            # 22
        58, 55, 50, 45, 42, 40, 38,
    ]
    return pd.Series(price_vals, dtype=float), pd.Series(ind_vals, dtype=float)


def _series_with_troughs(trough1_val, trough2_val, indicator1_val, indicator2_val, n=30):
    """
    Build a price series with exactly two local minima at indices 8 and 22
    (verified with peak_window=5).

    All ramp values are >= 105, so any trough1/trough2 value < 105 will be
    unambiguously detected as the local minimum in its window.

    Returns (price_series, indicator_series), both pd.Series of length n.
    """
    price_vals = [
        140, 135, 130, 125, 120, 115, 110, 105,   # 0-7: descending ramp
        trough1_val,                               # 8:   TROUGH 1 (must be < 105)
        105, 110, 115, 120, 125, 130, 135,         # 9-15: ascending ramp
        130, 125, 120, 115, 110, 105,              # 16-21: descending ramp
        trough2_val,                               # 22:  TROUGH 2 (must be < 105)
        105, 110, 115, 120, 125, 130, 135,         # 23-29: ascending ramp
    ]
    ind_vals = [
        75, 72, 69, 66, 63, 60, 57, 54,
        indicator1_val,                            # 8
        52, 54, 56, 58, 60, 62, 64,
        62, 60, 58, 56, 54, 52,
        indicator2_val,                            # 22
        52, 54, 56, 58, 60, 62, 64,
    ]
    return pd.Series(price_vals, dtype=float), pd.Series(ind_vals, dtype=float)


# ===========================================================================
# _detect_divergence() tests
# ===========================================================================

class TestDetectDivergence:

    def test_insufficient_data_returns_none(self):
        """Fewer bars than lookback → 'None' (can't detect without enough history)."""
        short = pd.Series([1.0, 2.0, 3.0], dtype=float)
        assert _detect_divergence(short, short, lookback=30) == "None"

    def test_exactly_lookback_bars_no_peaks_returns_none(self):
        """Exactly lookback bars but all flat — no local peaks found → 'None'."""
        flat = pd.Series([50.0] * 30, dtype=float)
        assert _detect_divergence(flat, flat, lookback=30, peak_window=5) == "None"

    def test_bearish_divergence_price_higher_high_indicator_lower_high(self):
        """Price makes a higher second peak; indicator makes a lower second peak → Bearish."""
        price, ind = _series_with_peaks(
            peak1_val=100.0, peak2_val=110.0,   # price: second peak higher
            indicator1_val=70.0, indicator2_val=65.0,  # indicator: second peak lower
        )
        result = _detect_divergence(price, ind, lookback=30, peak_window=5)
        assert result == "Bearish Divergence"

    def test_bullish_divergence_price_lower_low_indicator_higher_low(self):
        """Price makes a lower second trough; indicator makes a higher second trough → Bullish."""
        price, ind = _series_with_troughs(
            trough1_val=80.0, trough2_val=70.0,   # price: second trough lower
            indicator1_val=30.0, indicator2_val=35.0,  # indicator: second trough higher
        )
        result = _detect_divergence(price, ind, lookback=30, peak_window=5)
        assert result == "Bullish Divergence"

    def test_aligned_peaks_no_divergence(self):
        """Both price and indicator make higher highs → no divergence → 'None'."""
        price, ind = _series_with_peaks(
            peak1_val=100.0, peak2_val=110.0,
            indicator1_val=60.0, indicator2_val=70.0,  # indicator also higher
        )
        result = _detect_divergence(price, ind, lookback=30, peak_window=5)
        assert result == "None"

    def test_aligned_troughs_no_divergence(self):
        """Both price and indicator make lower lows → no divergence → 'None'."""
        price, ind = _series_with_troughs(
            trough1_val=80.0, trough2_val=70.0,
            indicator1_val=30.0, indicator2_val=25.0,  # indicator also lower
        )
        result = _detect_divergence(price, ind, lookback=30, peak_window=5)
        assert result == "None"

    def test_all_same_price_returns_none(self):
        """Flat price series — no peaks or troughs detectable → 'None'."""
        flat = pd.Series([100.0] * 50, dtype=float)
        indicator = pd.Series(range(50), dtype=float)
        assert _detect_divergence(flat, indicator, lookback=30, peak_window=5) == "None"

    def test_only_one_peak_returns_none(self):
        """Only one peak found — need at least two to compare → 'None'."""
        price = pd.Series([80.0] * 30, dtype=float)
        ind   = pd.Series([50.0] * 30, dtype=float)
        price.iloc[15] = 100.0   # single peak
        ind.iloc[15]   = 70.0
        result = _detect_divergence(price, ind, lookback=30, peak_window=5)
        assert result == "None"

    def test_longer_series_uses_only_last_lookback_bars(self):
        """
        Provide a 50-bar series but lookback=30.
        Place a fake bearish divergence in bars 0-19 (outside lookback window)
        and no divergence in bars 20-49 (inside window).
        Should return 'None' because the old divergence is ignored.
        """
        n = 50
        price = pd.Series([80.0] * n, dtype=float)
        ind   = pd.Series([50.0] * n, dtype=float)
        # Divergence in the OLD part (bars 3 and 16 — outside lookback=30 window)
        price.iloc[3]  = 100.0; ind.iloc[3]  = 70.0
        price.iloc[16] = 110.0; ind.iloc[16] = 65.0
        # No divergence in the RECENT 30 bars (indices 20-49)
        result = _detect_divergence(price, ind, lookback=30, peak_window=5)
        assert result == "None"


# ===========================================================================
# Indicator interpretation — threshold boundary tests
# ===========================================================================

class TestRSIInterpretation:

    def test_below_30_is_oversold(self):
        assert _rsi_signal(25.0) == "Oversold"
        assert _rsi_signal(29.9) == "Oversold"

    def test_at_30_is_neutral(self):
        assert _rsi_signal(30.0) == "Neutral"

    def test_between_30_and_70_is_neutral(self):
        assert _rsi_signal(50.0) == "Neutral"
        assert _rsi_signal(69.9) == "Neutral"

    def test_above_70_is_overbought(self):
        assert _rsi_signal(75.0) == "Overbought"
        assert _rsi_signal(70.1) == "Overbought"

    def test_at_70_is_neutral(self):
        assert _rsi_signal(70.0) == "Neutral"


class TestADXInterpretation:

    def test_above_25_is_strong_trend_high_confidence(self):
        strength, confidence = _adx_interpretation(30.0)
        assert strength == "Strong Trend"
        assert confidence == "High"

    def test_at_25_is_not_strong_trend(self):
        strength, confidence = _adx_interpretation(25.0)
        assert strength == "Moderate"
        assert confidence == "Moderate"

    def test_between_20_and_25_is_moderate(self):
        strength, confidence = _adx_interpretation(22.0)
        assert strength == "Moderate"
        assert confidence == "Moderate"

    def test_below_20_is_ranging_low_confidence(self):
        strength, confidence = _adx_interpretation(15.0)
        assert strength == "Ranging"
        assert confidence == "Low"

    def test_at_20_is_moderate(self):
        strength, confidence = _adx_interpretation(20.0)
        assert strength == "Moderate"
        assert confidence == "Moderate"


class TestStochasticInterpretation:

    def test_above_80_is_overbought(self):
        assert _stoch_signal(85.0) == "Overbought"
        assert _stoch_signal(80.1) == "Overbought"

    def test_at_80_is_neutral(self):
        assert _stoch_signal(80.0) == "Neutral"

    def test_between_20_and_80_is_neutral(self):
        assert _stoch_signal(50.0) == "Neutral"

    def test_below_20_is_oversold(self):
        assert _stoch_signal(15.0) == "Oversold"
        assert _stoch_signal(19.9) == "Oversold"

    def test_at_20_is_neutral(self):
        assert _stoch_signal(20.0) == "Neutral"


# ===========================================================================
# Overall signal vote counting
# ===========================================================================

class TestOverallSignalVoting:
    """
    8 indicators, each contributing one vote.
    Bullish = 5+ bullish votes; Bearish = 5+ bearish votes; else Neutral.
    """

    def _bullish_kwargs(self):
        """All 8 indicators point bullish."""
        return dict(
            rsi_signal="Neutral",        # not overbought = bullish vote
            momentum_pct=1.0,            # positive = bullish
            macd_line=1.0,
            macd_signal_line=0.5,        # macd above signal = bullish
            price=105.0,
            sma_20=100.0,                # price > sma_20 = bullish
            sma_50=95.0,                 # price > sma_50 = bullish
            vwap_20=98.0,                # price > vwap = bullish
            bb_percent_b=0.8,            # > 0.5 = bullish
            di_plus=30.0,
            di_minus=20.0,               # di+ > di- = bullish
        )

    def test_all_bullish_votes_returns_bullish(self):
        result = _overall_signal(**self._bullish_kwargs())
        assert result == "Bullish"

    def test_five_bullish_votes_returns_bullish(self):
        kwargs = self._bullish_kwargs()
        # Flip 3 to bearish (5 bullish remain)
        kwargs["momentum_pct"] = -1.0          # bearish
        kwargs["bb_percent_b"] = 0.3           # bearish
        kwargs["di_plus"] = 10.0               # di+ < di- → bearish
        kwargs["di_minus"] = 20.0
        result = _overall_signal(**kwargs)
        assert result == "Bullish"

    def test_five_bearish_votes_returns_bearish(self):
        kwargs = self._bullish_kwargs()
        # Flip 5 to bearish
        kwargs["rsi_signal"] = "Overbought"    # overbought = bearish vote
        kwargs["momentum_pct"] = -1.0
        kwargs["macd_line"] = 0.0              # macd below signal
        kwargs["macd_signal_line"] = 0.5
        kwargs["price"] = 90.0                 # below sma_20 (100) and sma_50 (95)
        result = _overall_signal(**kwargs)
        assert result == "Bearish"

    def test_four_four_split_returns_neutral(self):
        kwargs = self._bullish_kwargs()
        # Flip exactly 4 to bearish → 4 bullish, 4 bearish = Neutral
        kwargs["momentum_pct"] = -1.0          # bearish
        kwargs["macd_line"] = 0.0              # bearish (macd < signal)
        kwargs["macd_signal_line"] = 0.5
        kwargs["bb_percent_b"] = 0.3           # bearish (< 0.5)
        kwargs["di_plus"] = 10.0              # bearish (di+ < di-)
        kwargs["di_minus"] = 20.0
        result = _overall_signal(**kwargs)
        assert result == "Neutral"


# ===========================================================================
# Reversal alert factor counting
# ===========================================================================

class TestReversalAlert:

    def test_two_bearish_factors_triggers_bearish_reversal(self):
        result = _reversal_alert(
            bearish_factors=["RSI bearish divergence", "OBV distribution"],
            bullish_factors=[],
        )
        assert result == "Potential Bearish Reversal"

    def test_one_bearish_factor_no_alert(self):
        result = _reversal_alert(
            bearish_factors=["RSI bearish divergence"],
            bullish_factors=[],
        )
        assert result == "None"

    def test_two_bullish_factors_triggers_bullish_reversal(self):
        result = _reversal_alert(
            bearish_factors=[],
            bullish_factors=["RSI bullish divergence", "OBV accumulation"],
        )
        assert result == "Potential Bullish Reversal"

    def test_zero_factors_no_alert(self):
        result = _reversal_alert(bearish_factors=[], bullish_factors=[])
        assert result == "None"

    def test_bearish_wins_when_both_have_two_factors(self):
        """Bearish checked first in the source — if both ≥2, bearish wins."""
        result = _reversal_alert(
            bearish_factors=["A", "B"],
            bullish_factors=["C", "D"],
        )
        assert result == "Potential Bearish Reversal"
