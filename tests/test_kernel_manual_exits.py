"""Phase 5: a manual SL/TP must still be enforced on a kernel-managed strategy.

The kernel reconciler only manages kernel-opened trades, and manage_positions_via_kernel
short-circuits the legacy manual-exit path — so without this, an operator's manual stop
on a kernel-managed strategy would be silently ignored.
"""

from __future__ import annotations

import json

import forven.scanner as scanner
from forven.db import get_db


def _insert_open_trade(sid, trade_id, *, entry, size, leverage, signal_data, execution_type="paper", direction="long"):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO trades (id, strategy, strategy_id, asset, direction, entry_price, signal_entry_price, "
            "fill_entry_price, size, risk_pct, leverage, status, execution_type, signal_data, opened_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (trade_id, sid, sid, "BTC", direction, entry, entry, entry, size, 0.01, leverage,
             "OPEN", execution_type, json.dumps(signal_data), "2024-01-01"),
        )


def _status(trade_id):
    with get_db() as conn:
        row = conn.execute("SELECT status FROM trades WHERE id=?", (trade_id,)).fetchone()
    return str(dict(row)["status"]).upper() if row else None


def test_manual_stop_is_enforced_on_kernel_strategy(forven_db):
    sid = "PAPER-K"
    # Manual long with an absolute stop at 95.
    _insert_open_trade(sid, "M1", entry=100.0, size=1.0, leverage=1.0,
                       signal_data={"source": "manual", "stop_loss_price": 95.0})
    # Price drops to 94 → stop breached → must close.
    actions = scanner._kernel_handle_manual_exits(sid, 94.0)
    assert any("MANUAL-STOP_LOSS" in a for a in actions)
    assert _status("M1") == "CLOSED"


def test_manual_take_profit_is_enforced(forven_db):
    sid = "PAPER-K"
    _insert_open_trade(sid, "M2", entry=100.0, size=1.0, leverage=1.0,
                       signal_data={"source": "manual", "take_profit_price": 110.0})
    actions = scanner._kernel_handle_manual_exits(sid, 111.0)
    assert any("MANUAL-TAKE_PROFIT" in a for a in actions)
    assert _status("M2") == "CLOSED"


def test_kernel_trades_and_paused_are_left_alone(forven_db):
    sid = "PAPER-K"
    # A kernel-managed trade with a stop at 95 — the reconciler owns it, not this path.
    _insert_open_trade(sid, "K1", entry=100.0, size=1.0, leverage=1.0,
                       signal_data={"kernel_managed": True, "kernel_entry_time": "2024-01-01T00:00:00+00:00", "stop_loss_price": 95.0})
    # A manual but PAUSED position (short, to avoid the one-open-per-direction index) —
    # operator detached management, so it must be left alone even on a breach.
    _insert_open_trade(sid, "P1", entry=100.0, size=1.0, leverage=1.0, direction="short",
                       signal_data={"source": "manual", "manual_pause": True, "stop_loss_price": 105.0})
    actions = scanner._kernel_handle_manual_exits(sid, 90.0)  # below K1's stop, above none for the paused short
    assert actions == []
    assert _status("K1") == "OPEN"
    assert _status("P1") == "OPEN"
