"""End-to-end paper↔backtest parity: a bar-by-bar scanner replay (kernel + reconciler
applied to in-memory paper trades) must reproduce the backtest trade-for-trade.

This is the capstone of Phase 2. It simulates exactly what the live scanner will do —
each closed bar, run the shared engine over history and reconcile its view against the
recorded paper trades — but with an in-memory trade store instead of the DB, so the
parity-critical logic is proven without any live infrastructure.

Covers an every-bar replay (the normal cadence) and a sparse replay (skipping cycles,
to exercise the backfill path that keeps a scanner-downtime gap from losing trades).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from forven.strategies import backtest as bt
from forven.strategies import execution_kernel as ek
from forven.strategies.paper_reconcile import reconcile
from forven.strategies.builtin.ema_cross import EMACrossStrategy
from forven.strategies.builtin.rsi_momentum import RSIMomentumStrategy
from forven.strategies.builtin.supertrend import SuperTrendStrategy


def _frame(n: int = 400, seed: int = 4) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.0005, 0.02, size=n).cumsum()
    close = 100.0 * np.exp(steps)
    spread = np.abs(rng.normal(0.0, 0.012, size=n)) + 0.004
    high = close * (1.0 + spread)
    low = close * (1.0 - spread)
    openp = np.empty(n)
    openp[0] = close[0]
    openp[1:] = close[:-1] * (1.0 + rng.normal(0.0, 0.004, size=n - 1))
    high = np.maximum.reduce([high, openp, close])
    low = np.minimum.reduce([low, openp, close])
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame(
        {"open": openp, "high": high, "low": low, "close": close, "volume": 1000.0},
        index=idx,
    )


WARMUP = 50
LEVERAGE = 2.0
FEE_BPS = 4.5
SLIP_BPS = 2.0

CASES = [
    ("rsi_momentum", RSIMomentumStrategy,
     {"rsi_period": 14, "rsi_entry": 45, "rsi_exit": 55, "ema_fast": 10, "ema_slow": 30, "adx_period": 14, "adx_min": 0},
     {"sizing_mode": "fraction", "risk_per_trade": 0.01, "stop_loss_pct": 3.0, "take_profit_pct": 5.0}),
    ("ema_cross", EMACrossStrategy,
     {"ema_fast": 8, "ema_slow": 21, "ema_regime": 50, "adx_period": 14, "adx_min": 0},
     None),
    ("supertrend", SuperTrendStrategy,
     {"multiplier": 1.0, "atr_period": 10},
     {"sizing_mode": "atr", "atr_stop_multiplier": 2.0, "risk_per_trade": 0.01, "trailing_stop_pct": 4.0}),
]

# Fields that define a trade for parity (the scanner persists all of these).
_CMP = ("direction", "entry_time", "entry_price", "entry_bar", "exit_time",
        "exit_price", "pnl_pct", "exit_reason", "size_fraction", "bars_held")


def _apply(actions, recorded: list[dict]) -> None:
    """Apply reconcile actions to the in-memory paper-trade store (mirrors what the
    scanner persistence layer will do)."""
    by_key = {(r["direction"], r["entry_time"]): r for r in recorded}
    for a in actions:
        if a.kind == "open":
            pos = a.position
            recorded.append({
                "direction": a.direction, "entry_time": a.entry_time,
                "entry_price": float(pos["entry_price"]), "entry_bar": int(pos["entry_bar"]),
                "size_fraction": round(float(pos.get("size_fraction", 1.0)), 4),
                "stop_price": pos.get("stop_price"), "target_price": pos.get("target_price"),
                "status": "open",
            })
        elif a.kind in ("close", "backfill"):
            t = a.trade
            r = a.recorded if a.kind == "close" else by_key.get((a.direction, a.entry_time))
            if r is None:
                r = {"direction": a.direction, "entry_time": a.entry_time,
                     "entry_price": float(t["entry_price"]), "entry_bar": int(t["entry_bar"]),
                     "size_fraction": round(float(t.get("size_fraction", 1.0)), 4), "status": "open"}
                recorded.append(r)
            r.update({
                "status": "closed", "exit_time": t["exit_time"], "exit_price": float(t["exit_price"]),
                "pnl_pct": float(t["pnl_pct"]), "exit_reason": t.get("exit_reason"),
                "bars_held": int(t["bars_held"]),
            })
        elif a.kind == "refresh":
            a.recorded["stop_price"] = a.position.get("stop_price")
            a.recorded["target_price"] = a.position.get("target_price")


def _replay(df, strat, label, ec, *, step: int) -> list[dict]:
    recorded: list[dict] = []
    cycles = list(range(WARMUP + 2, len(df) + 1, step))
    if cycles[-1] != len(df):
        cycles.append(len(df))  # always finish on the full history
    for i in cycles:
        res = bt.run_strategy_execution(
            df.iloc[:i], strat, params=strat.params, warmup=WARMUP, leverage=LEVERAGE,
            fee_bps=FEE_BPS, slippage_bps=SLIP_BPS, regime_gate=False,
            trade_mode="long_only", execution_controls=ec, initial_capital=10000.0,
            strategy_type=label,
        )
        if res is None:
            continue
        _apply(reconcile(res, recorded), recorded)
    return recorded


def test_recent_cutoff_prevents_history_flood():
    """A fresh/reset book must NOT replay the strategy's entire would-be history as
    trades — that was the bug that flooded the chart. The recency cutoff suppresses
    backfill + adoption of pre-go-live activity; closes of recorded trades still fire.
    Default (no cutoff) stays full-replay for the parity tests above."""
    from forven.strategies.paper_reconcile import reconcile

    res = ek.KernelResult(
        closed_trades=[
            {"direction": "long", "entry_time": "2024-01-01 00:00:00+00:00", "exit_time": "2024-01-01 04:00:00+00:00",
             "entry_price": 100.0, "exit_price": 110.0, "pnl_pct": 0.01, "exit_reason": "signal", "size_fraction": 0.01, "entry_bar": 1, "bars_held": 1},
            {"direction": "long", "entry_time": "2024-01-05 00:00:00+00:00", "exit_time": "2024-01-05 04:00:00+00:00",
             "entry_price": 100.0, "exit_price": 110.0, "pnl_pct": 0.01, "exit_reason": "signal", "size_fraction": 0.01, "entry_bar": 9, "bars_held": 1},
        ],
        open_positions={"long": {"entry_time": "2024-01-02 00:00:00+00:00", "entry_price": 100.0,
                                 "size_fraction": 0.01, "entry_bar": 3, "stop_price": 97.0, "target_price": 105.0}},
    )

    # Fresh book, cutoff AFTER everything → record nothing (no flood).
    assert reconcile(res, [], recent_cutoff="2024-02-01 00:00:00+00:00") == []

    # No cutoff = full replay (backtest-parity semantics) → backfills + open present.
    full_kinds = {a.kind for a in reconcile(res, [])}
    assert "backfill" in full_kinds and "open" in full_kinds

    # Cutoff in the middle → only entries at/after it are recorded.
    mid = reconcile(res, [], recent_cutoff="2024-01-03 00:00:00+00:00")
    assert not any(a.kind == "open" for a in mid)  # the open entered 01-02, before cutoff → not adopted
    assert any(a.kind == "backfill" and a.entry_time.startswith("2024-01-05") for a in mid)
    assert not any(a.entry_time.startswith("2024-01-01") for a in mid)

    # A recorded OPEN trade still closes regardless of cutoff.
    closed = reconcile(res, [{"direction": "long", "entry_time": "2024-01-01 00:00:00+00:00", "status": "open"}],
                       recent_cutoff="2024-02-01 00:00:00+00:00")
    assert any(a.kind == "close" and a.entry_time.startswith("2024-01-01") for a in closed)


@pytest.mark.parametrize("label,cls,params,ec", CASES, ids=[c[0] for c in CASES])
@pytest.mark.parametrize("step", [1, 5], ids=["every-bar", "sparse"])
def test_scanner_replay_reproduces_backtest(label, cls, params, ec, step):
    df = _frame()
    strat = cls(f"{label}-test", params)

    # Backtest reference (force-closes the final open position).
    drag = ek.round_trip_drag(FEE_BPS, SLIP_BPS, LEVERAGE)
    ref_res = bt.run_strategy_execution(
        df, strat, params=strat.params, warmup=WARMUP, leverage=LEVERAGE,
        fee_bps=FEE_BPS, slippage_bps=SLIP_BPS, regime_gate=False,
        trade_mode="long_only", execution_controls=ec, initial_capital=10000.0,
        strategy_type=label,
    )
    backtest_trades = ek.force_close(ref_res, df, leverage=LEVERAGE, round_trip_drag=drag, trade_mode="long_only")
    backtest_closed = [t for t in backtest_trades if not t.get("open_at_end")]
    backtest_open = [t for t in backtest_trades if t.get("open_at_end")]

    recorded = _replay(df, strat, label, ec, step=step)
    recorded_closed = [r for r in recorded if r["status"] == "closed"]
    recorded_open = [r for r in recorded if r["status"] == "open"]

    assert len(recorded_closed) == len(backtest_closed), (
        f"[{label}/step={step}] closed-trade count: paper={len(recorded_closed)} backtest={len(backtest_closed)}"
    )
    for bt_t, rc in zip(backtest_closed, recorded_closed):
        for k in _CMP:
            assert bt_t.get(k) == rc.get(k), (
                f"[{label}/step={step}] closed trade field '{k}': backtest={bt_t.get(k)!r} paper={rc.get(k)!r}"
            )

    # The backtest force-closes the final open position; the scanner leaves it live.
    # They must agree on WHICH position is open (same entry) and its size.
    assert len(recorded_open) == len(backtest_open), (
        f"[{label}/step={step}] open-position count differs: paper={len(recorded_open)} backtest={len(backtest_open)}"
    )
    for bt_o, ro in zip(backtest_open, recorded_open):
        for k in ("direction", "entry_time", "entry_price", "entry_bar", "size_fraction"):
            assert bt_o.get(k) == ro.get(k), (
                f"[{label}/step={step}] open position field '{k}': backtest={bt_o.get(k)!r} paper={ro.get(k)!r}"
            )
