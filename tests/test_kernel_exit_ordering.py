"""Kernel exit-precedence + no-lookahead regressions.

Two correctness properties the kernel must hold (and did NOT before the audit fix):

1. A signal-driven exit is a market-on-open order (decided at the prior close, filled
   at THIS bar's open). The open is the first tick of the bar, so a signal-exit must
   pre-empt any intrabar stop/take-profit that would only trigger later in the bar.
2. The ATR used to SIZE and STOP an entry must read the last CLOSED bar (signal_idx),
   not the entry bar's own (not-yet-realized) high/low/close — that would be lookahead.
"""

from __future__ import annotations

import pandas as pd
import pytest

from forven.strategies import execution_kernel as ek
from forven.strategies import sizing


def _frame(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(rows), freq="1h", tz="UTC")
    return pd.DataFrame(
        {"open": [r[0] for r in rows], "high": [r[1] for r in rows],
         "low": [r[2] for r in rows], "close": [r[3] for r in rows], "volume": 1000.0},
        index=idx,
    )


def _long_signals(n: int, *, entries: list[int], exits: list[int]) -> ek.DirectionalSignals:
    e = pd.Series([i in entries for i in range(n)], dtype=bool)
    x = pd.Series([i in exits for i in range(n)], dtype=bool)
    false = pd.Series([False] * n, dtype=bool)
    return ek.DirectionalSignals(long_entries=e, long_exits=x, short_entries=false, short_exits=false)


def _simulate(df, sig, ec):
    return ek.simulate(
        df, sig, warmup=0, leverage=1.0, regimes=None, round_trip_drag=0.0,
        trade_mode="long", allowed_modes=("long",), ec=ec, initial_capital=10000.0,
    )


def test_signal_exit_preempts_same_bar_stop():
    # Enter long at bar 1 open (=100); stop_loss 5% → stop at 95. On bar 2 BOTH a
    # signal-exit is set AND the low (90) breaches the stop. The signal-exit (at the
    # open, 98) must win over the intrabar stop (95).
    df = _frame([
        (100, 100, 100, 100),  # 0 warmup
        (100, 101, 99, 100),   # 1 entry @ open 100, no breach (low 99 > 95)
        (98, 99, 90, 92),      # 2 signal-exit + stop breach (low 90 <= 95)
        (92, 93, 91, 92),      # 3
    ])
    sig = _long_signals(4, entries=[0], exits=[1])
    ec = sizing.normalize_execution_controls(
        {"sizing_mode": "fraction", "stop_loss_pct": 5.0, "risk_per_trade": 0.02}
    )
    res = _simulate(df, sig, ec)
    assert len(res.closed_trades) == 1
    t = res.closed_trades[0]
    assert t["exit_reason"] == "signal"      # NOT "stop_loss"
    assert t["exit_price"] == 98.0           # filled at the open, not the 95 stop


def test_signal_exit_preempts_same_bar_take_profit():
    # Enter long at 100; take_profit 5% → target 105. Bar 2 hits the target (high 106)
    # AND has a signal-exit. The signal-exit (open 103) must win over the TP (105).
    df = _frame([
        (100, 100, 100, 100),  # 0
        (100, 101, 99, 100),   # 1 entry @ 100
        (103, 106, 102, 104),  # 2 TP touched + signal-exit
        (104, 105, 103, 104),  # 3
    ])
    sig = _long_signals(4, entries=[0], exits=[1])
    ec = sizing.normalize_execution_controls(
        {"sizing_mode": "full", "take_profit_pct": 5.0}
    )
    res = _simulate(df, sig, ec)
    assert len(res.closed_trades) == 1
    t = res.closed_trades[0]
    assert t["exit_reason"] == "signal"      # NOT "take_profit"
    assert t["exit_price"] == 103.0


def test_round_trip_drag_formula():
    # 2 * (fee_bps + slip_bps) / 1e4 * leverage — the ONE cost definition used everywhere.
    assert ek.round_trip_drag(4.5, 2.0, 1.0) == pytest.approx(0.0013)   # 2*6.5/1e4
    assert ek.round_trip_drag(4.5, 2.0, 3.0) == pytest.approx(0.0039)   # *3 leverage
    assert ek.round_trip_drag(10.0, 5.0, 2.0) == pytest.approx(0.006)   # 2*15/1e4*2
    assert ek.round_trip_drag(0.0, 0.0, 5.0) == 0.0                     # no cost → no drag


def test_kernel_net_pnl_subtracts_drag_exactly_once():
    # Enter long at 100, signal-exit at 110 → +10% gross; with fee 10bps + slip 5bps at
    # 1x, round_trip_drag = 2*(10+5)/1e4*1 = 0.003, so net pnl_pct = 0.10 - 0.003 = 0.097
    # (size_fraction = 1 for full). Pins the net magnitude with NON-zero costs — the
    # parity tests only ever fed zero/identical drag to both sides.
    df = _frame([
        (100, 100, 100, 100),
        (100, 101, 99, 100),    # entry @ open 100
        (110, 111, 109, 110),   # signal-exit @ open 110
        (110, 110, 110, 110),
    ])
    sig = _long_signals(4, entries=[0], exits=[1])
    ec = {
        "sizing_mode": "full", "stop_loss_pct": None, "take_profit_pct": None,
        "trailing_stop_pct": None, "time_stop_bars": None, "risk_per_trade": 0.01,
        "fixed_size": None, "atr_stop_multiplier": 2.0, "kelly_multiplier": 0.5,
        "kelly_lookback": 100, "needs_atr": False, "atr_period": 14,
    }
    drag = ek.round_trip_drag(10.0, 5.0, 1.0)  # 0.003
    res = ek.simulate(
        df, sig, warmup=0, leverage=1.0, regimes=None, round_trip_drag=drag,
        trade_mode="long", allowed_modes=("long",), ec=ec, initial_capital=10000.0,
    )
    assert len(res.closed_trades) == 1
    t = res.closed_trades[0]
    assert t["exit_reason"] == "signal"
    assert t["pnl_pct"] == pytest.approx(0.097, abs=1e-9)  # 0.10 gross - 0.003 drag, drag applied ONCE


def test_atr_entry_sizing_has_no_lookahead():
    # Identical history through the second-to-last bar; the entry fills at the LAST bar's
    # open. The entry's ATR-based stop/size must depend only on the prior (closed) bars,
    # so widening ONLY the entry bar's own high/low must NOT change the sizing/stop.
    base = [
        (100, 102, 98, 100),
        (100, 103, 97, 101),
        (101, 104, 99, 102),
        (102, 105, 100, 103),
        (103, 106, 101, 104),
        (104, 107, 102, 105),
        (105, 108, 103, 106),
        (106, 109, 104, 107),
        (107, 110, 105, 108),  # 8 — signal here → enter at bar 9 open
    ]
    normal_last = base + [(108, 109, 107, 108)]           # tame entry bar
    wild_last = base + [(108, 250, 5, 108)]               # same OPEN, enormous range
    sig = _long_signals(10, entries=[8], exits=[])
    ec = sizing.normalize_execution_controls(
        {"sizing_mode": "atr", "risk_per_trade": 0.02, "atr_stop_multiplier": 2.0}
    )
    res_a = _simulate(_frame(normal_last), sig, ec)
    res_b = _simulate(_frame(wild_last), sig, ec)
    pos_a = res_a.open_positions["long"]
    pos_b = res_b.open_positions["long"]
    assert pos_a["entry_price"] == pos_b["entry_price"] == 108.0
    # sizing + stop must be identical — they read the prior closed bar's ATR, not bar 9's.
    assert pos_a["size_fraction"] == pos_b["size_fraction"]
    assert pos_a["stop_price"] == pos_b["stop_price"]
