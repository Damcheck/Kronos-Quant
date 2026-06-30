"""Williams %R + ADX strategy - HYP-013 variants."""

import pandas as pd

from forven.strategies.base import BaseStrategy, Signal

TYPE_NAME = "williams_r"


def williams_r(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Williams %R.
    
    Formula: %R = -((Highest High - Close) / (Highest High - Lowest Low)) * 100
    
    Args:
        df: DataFrame with 'high', 'low', 'close' columns
        period: Lookback period for highest high and lowest low
        
    Returns:
        Williams %R series (range: -100 to 0)
    """
    high = df["high"]
    low = df["low"]
    close = df["close"]
    
    # Calculate rolling highest high and lowest low
    highest_high = high.rolling(window=period).max()
    lowest_low = low.rolling(window=period).min()
    
    # Williams %R calculation
    wr = -100 * (highest_high - close) / (highest_high - lowest_low)
    
    return wr


class WilliamsR_ADX_Strategy(BaseStrategy):
    """Williams %R mean reversion with ADX trend filter.
    
    Entry: Williams %R crosses UP from below oversold level AND ADX > threshold
    Exit: Williams %R crosses DOWN from above overbought level
    
    This strategy looks for mean reversion opportunities when Williams %R
    indicates oversold conditions, confirmed by sufficient trend strength (ADX).
    """

    @property
    def name(self) -> str:
        return f"Williams %R+ADX ({self.asset})"

    @property
    def asset(self) -> str:
        return self.params.get("_asset", "BNB")

    @property
    def strategy_type(self) -> str:
        return TYPE_NAME

    @property
    def default_params(self) -> dict:
        return {
            # Williams %R parameters
            "williams_r_period": 14,
            "williams_r_oversold": -80,
            "williams_r_overbought": -20,
            # ADX parameters
            "adx_period": 14,
            "adx_threshold": 20,
            # Risk parameters
            "leverage": 3.0,
        }

    @property
    def compatible_regimes(self) -> set[str]:
        return {"TREND_UP", "RANGE", "VOLATILE"}

    def describe(self) -> str:
        p = self.params
        return (
            f"Williams %R mean reversion with ADX trend filter. "
            f"Buys when %R crosses up from below {p['williams_r_oversold']} "
            f"with ADX > {p['adx_threshold']}. "
            f"Sells when %R crosses down from above {p['williams_r_overbought']}."
        )

    def generate_signals(self, df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        """Vectorized twin of generate_signal - the SINGLE source of entry/exit logic."""
        p = self.params

        wr = williams_r(df, p["williams_r_period"])
        adx_val = self._adx(df, p["adx_period"])
        prev_wr = wr.shift(1)

        # Entry: Williams %R crosses UP from below oversold AND ADX >= threshold
        entry = (
            (prev_wr < p["williams_r_oversold"])
            & (wr >= p["williams_r_oversold"])
            & (adx_val >= p["adx_threshold"])
        )

        # Exit: Williams %R crosses DOWN from above overbought
        exit_ = (prev_wr > p["williams_r_overbought"]) & (wr <= p["williams_r_overbought"])

        return entry.fillna(False), exit_.fillna(False)

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        p = self.params

        # Calculate indicators
        wr = williams_r(df, p["williams_r_period"])
        adx_val = self._adx(df, p["adx_period"])

        # Get current and previous values
        curr_close = float(df["close"].iloc[-1])
        curr_wr = float(wr.iloc[-1])
        curr_adx = float(adx_val.iloc[-1])

        # Decision comes from the vectorized twin
        entries, exits = self.generate_signals(df)

        # Calculate confidence based on ADX strength
        confidence = min(1.0, curr_adx / 40) if curr_adx >= p["adx_threshold"] else 0.0

        return Signal(
            entry_signal=bool(entries.iloc[-1]),
            exit_signal=bool(exits.iloc[-1]),
            price=round(curr_close, 4),
            direction="long",
            confidence=round(confidence, 2),
            indicators={
                "wr": round(curr_wr, 1),
                "wr_oversold": p["williams_r_oversold"],
                "wr_overbought": p["williams_r_overbought"],
                "adx": round(curr_adx, 1),
                "adx_threshold": p["adx_threshold"],
            },
        )

    def _adx(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate ADX (Average Directional Index)."""
        high = df["high"]
        low = df["low"]
        close = df["close"]
        
        # Calculate +DM and -DM
        high_diff = high.diff()
        low_diff = -low.diff()
        
        plus_dm = high_diff.where((high_diff > low_diff) & (high_diff > 0), 0)
        minus_dm = low_diff.where((low_diff > high_diff) & (low_diff > 0), 0)
        
        # Calculate True Range
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        # Calculate smoothed values
        atr = tr.rolling(window=period).mean()
        plus_dm_smooth = plus_dm.rolling(window=period).mean()
        minus_dm_smooth = minus_dm.rolling(window=period).mean()
        
        # Calculate +DI and -DI
        plus_di = 100 * (plus_dm_smooth / atr)
        minus_di = 100 * (minus_dm_smooth / atr)
        
        # Calculate DX
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
        
        # Calculate ADX
        adx = dx.rolling(window=period).mean()
        
        return adx

    def parameter_space(self) -> dict:
        return {
            "williams_r_oversold": (-90, -70, 5),
            "williams_r_overbought": (-10, -30, 5),
            "adx_threshold": (15, 30, 5),
        }


# Strategy class and registry
STRATEGY_CLASS = WilliamsR_ADX_Strategy

STRATEGIES = [
    ("HYP-013-Williams-%R-BNB-1h", WilliamsR_ADX_Strategy, {"_asset": "BNB"}),
]
