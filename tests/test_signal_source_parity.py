"""Single-signal-source parity — every vectorizable builtin must have ONE signal
definition shared by the backtest and the live/paper scanner.

The bug this guards against: the backtest used a hand-maintained vectorized copy
(`backtest._vectorized_signals`) that had silently drifted from the class's per-bar
`generate_signal` that the paper scanner runs, so paper traded a different signal set
than the strategy was promoted on (confirmed on rsi_momentum, bollinger, keltner,
macd, stochastic, supertrend, williams_r, orb, ichimoku, funding).

The fix: each class owns a single vectorized `generate_signals`, and its per-bar
`generate_signal` delegates to it. This test asserts, for each ported type, that

    generate_signal(df[:k+1]).entry/exit == generate_signals(df).iloc[k]

at many bars k. That one assertion proves three things at once:
  * the per-bar method delegates to the vectorized one (they can't drift),
  * the vectorized signal at bar k is reproducible from the prefix df[:k+1] alone
    (no lookahead, and the scanner-over-history will match the backtest-over-history),
  * the suite as a whole is non-vacuous (the coverage guard at the end).

NOTE: this tests the prefix-from-bar-0 reproducibility that the backtest relies on.
The scanner's *rolling-window* convergence (a truncated window vs full history) is a
separate Phase-2 concern handled with a warmup/tolerance test once the scanner drives
the kernel.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from forven.strategies.base import DirectionalSignals
from forven.strategies.builtin import (
    bollinger_s00120,
    donchian,
    ema_cross,
    funding,
    ichimoku,
    keltner,
    macd,
    orb,
    parabolic_sar,
    rsi_momentum,
    stochastic,
    supertrend,
    williams_r,
)


def _walk(n: int = 320, seed: int = 11, *, drift: float = 0.0, with_funding: bool = False) -> pd.DataFrame:
    """Deterministic volatile random walk — enough warmup and enough crossings that
    momentum/trend signals fire. ``drift`` adds an upward bias (helps regime filters)."""
    rng = np.random.default_rng(seed)
    steps = (rng.normal(drift, 0.02, size=n)).cumsum()
    close = 100.0 * np.exp(steps)
    spread = np.abs(rng.normal(0.0, 0.01, size=n)) + 0.003
    high = close * (1.0 + spread)
    low = close * (1.0 - spread)
    openp = np.empty(n)
    openp[0] = close[0]
    openp[1:] = close[:-1]
    high = np.maximum.reduce([high, openp, close])
    low = np.minimum.reduce([low, openp, close])
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    data = {"open": openp, "high": high, "low": low, "close": close, "volume": 1000.0}
    if with_funding:
        # Oscillating funding that repeatedly crosses the entry/exit thresholds.
        data["funding_rate"] = 0.0001 * np.sin(np.arange(n) / 5.0)
    return pd.DataFrame(data, index=idx)


class Case:
    def __init__(self, label, module, params, *, warmup=60, frame=None):
        self.label = label
        self.cls = module.STRATEGY_CLASS
        self.params = params
        self.warmup = warmup
        self.frame = frame or (lambda: _walk(drift=0.001))


CASES = [
    Case("rsi_momentum", rsi_momentum,
         {"rsi_period": 14, "rsi_entry": 45, "rsi_exit": 55, "ema_fast": 10, "ema_slow": 30, "adx_period": 14, "adx_min": 0}),
    Case("ema_cross", ema_cross,
         {"ema_fast": 8, "ema_slow": 21, "ema_regime": 50, "adx_period": 14, "adx_min": 0}),
    Case("donchian", donchian, {"donchian_period": 15}),
    Case("parabolic_sar", parabolic_sar, {"step": 0.02, "max_step": 0.2}),
    Case("bollinger", bollinger_s00120,
         {"bb_period": 12, "bb_std": 2.0, "rsi_entry_long": 80, "rsi_entry_short": 55, "rsi_period": 14}),
    Case("keltner", keltner, {"kc_period": 12, "kc_mult": 2.0, "adx_min": 0, "position": "long"}),
    Case("macd", macd, {"adx_min": 0}),
    Case("stochastic", stochastic, {}),
    # multiplier 1.0 (vs default 3.0) so the basic-band cross actually fires on the
    # fixture and the True path is exercised. The class's basic-band logic at mult=3
    # is near-degenerate (price must jump >3 ATR in a bar) — a real re-score finding.
    Case("supertrend", supertrend, {"multiplier": 1.0, "atr_period": 10}),
    Case("williams_r", williams_r, {"adx_threshold": 0}),
    Case("orb", orb, {}),
    Case("ichimoku", ichimoku, {}, warmup=90),
    Case("funding", funding, {}, warmup=210, frame=lambda: _walk(n=300, drift=0.002, with_funding=True)),
]

SAMPLE_STEP = 3  # sample every Nth bar to keep the O(bars²) per-bar replay affordable


def _pair(payload):
    """Normalize generate_signals output to a long (entry, exit) Series pair."""
    if isinstance(payload, DirectionalSignals):
        return payload.long_entries, payload.long_exits
    return payload[0], payload[1]


@pytest.mark.parametrize("case", CASES, ids=[c.label for c in CASES])
def test_per_bar_matches_vectorized_and_is_prefix_stable(case):
    df = case.frame()
    strat = case.cls(f"{case.label}-test", case.params)
    entries, exits = _pair(strat.generate_signals(df))
    entries = entries.reindex(df.index).fillna(False).astype(bool)
    exits = exits.reindex(df.index).fillna(False).astype(bool)

    for k in range(case.warmup, len(df), SAMPLE_STEP):
        sig = strat.generate_signal(df.iloc[: k + 1])
        assert bool(sig.entry_signal) == bool(entries.iloc[k]), (
            f"[{case.label}] entry mismatch at bar {k}: "
            f"per-bar={sig.entry_signal} vectorized={bool(entries.iloc[k])}"
        )
        assert bool(sig.exit_signal) == bool(exits.iloc[k]), (
            f"[{case.label}] exit mismatch at bar {k}: "
            f"per-bar={sig.exit_signal} vectorized={bool(exits.iloc[k])}"
        )


def test_signal_source_suite_is_non_vacuous():
    """Across all ported strategies, both entries and exits must fire somewhere —
    otherwise the parity assertions above could pass trivially on all-False series."""
    total_entries = total_exits = 0
    per_strategy: dict[str, tuple[int, int]] = {}
    for case in CASES:
        df = case.frame()
        strat = case.cls(f"{case.label}-test", case.params)
        entries, exits = _pair(strat.generate_signals(df))
        e = int(entries.fillna(False).astype(bool).sum())
        x = int(exits.fillna(False).astype(bool).sum())
        per_strategy[case.label] = (e, x)
        total_entries += e
        total_exits += x
    assert total_entries > 0, f"no entries fired across the suite: {per_strategy}"
    assert total_exits > 0, f"no exits fired across the suite: {per_strategy}"
    # Every strategy should at least produce *some* signal activity (entry or exit),
    # else its vectorization is suspect (e.g. ichimoku's old zero-trade bug).
    dead = [label for label, (e, x) in per_strategy.items() if e == 0 and x == 0]
    assert not dead, f"strategies produced no signals at all (suspect vectorization): {dead} | {per_strategy}"
