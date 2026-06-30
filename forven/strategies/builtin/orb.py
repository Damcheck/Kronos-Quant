"""Opening Range Breakout strategy."""
import pandas as pd
from forven.strategies.base import BaseStrategy, Signal

TYPE_NAME = "orb"

class ORBStrategy(BaseStrategy):
    @property
    def name(self) -> str: return f"Opening Range Breakout ({self.asset})"
    @property
    def asset(self) -> str: return self.params.get("_asset", "BTC")
    @property
    def strategy_type(self) -> str: return TYPE_NAME
    @property
    def default_params(self) -> dict:
        return {"range_bars": 4, "leverage": 3.0}
    @property
    def compatible_regimes(self) -> set[str]:
        return {"VOLATILE", "TREND_UP"}
    def describe(self) -> str:
        return "Trades the breakout of the high/low established in the first N bars of the session."
    def generate_signals(self, df: pd.DataFrame):
        """Vectorized twin of generate_signal — the SINGLE source of entry/exit logic."""
        p = self.params
        close = df["close"]
        n = p["range_bars"]
        recent_high = df["high"].rolling(n).max().shift(1)
        recent_low = df["low"].rolling(n).min().shift(1)
        entry = close > recent_high
        exit_ = close < recent_low
        return entry.fillna(False), exit_.fillna(False)

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        close = df["close"]
        curr_close = float(close.iloc[-1])

        n = self.params["range_bars"]
        recent_high = float(df["high"].rolling(n).max().iloc[-2])

        entries, exits = self.generate_signals(df)

        return Signal(
            entry_signal=bool(entries.iloc[-1]), exit_signal=bool(exits.iloc[-1]),
            price=round(curr_close, 4), direction="long", confidence=1.0,
            indicators={"orb_high": round(recent_high, 4)}
        )
    def parameter_space(self) -> dict:
        return {"range_bars": (2, 10, 2)}

STRATEGY_CLASS = ORBStrategy
STRATEGIES = [("TOMB-ORB", ORBStrategy, {"_asset": "BTC"})]
