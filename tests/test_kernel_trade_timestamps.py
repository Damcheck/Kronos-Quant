"""Kernel-managed paper trades must record opened_at / closed_at as the kernel's actual
ENTRY/EXIT bar times — not the wall-clock scan moment.

Regression for the backfill bug where a trade the kernel opened+closed between scans was
stamped opened_at == closed_at == the scan time, making it look like an instantaneous
"same bar" trade at prices that couldn't occur on that bar. The recording time is kept
separately on created_at.
"""

from __future__ import annotations

import forven.scanner as sc
from forven.db import get_db


def test_open_trade_db_stamps_provided_bar_open_time(forven_db):
    tid = sc._open_trade_db(
        "S-TS", "ETH", "short", 1545.15, 1.0, 0.01, 1.0,
        {"kernel_managed": True, "kernel_entry_time": "2026-06-26 12:00:00+00:00"},
        execution_type="paper",
        opened_at="2026-06-26 12:00:00+00:00",  # kernel entry-bar time
    )
    with get_db() as c:
        row = dict(c.execute("SELECT opened_at, created_at FROM trades WHERE id=?", (tid,)).fetchone())
    # opened_at is the entry-BAR time (space normalized to 'T'), NOT the scan time.
    assert row["opened_at"] == "2026-06-26T12:00:00+00:00"
    # created_at (the recording time) is preserved separately and differs from the bar time.
    assert row["created_at"] != row["opened_at"]


def test_open_trade_db_defaults_open_time_to_now(forven_db):
    # When no bar time is supplied (non-kernel callers), opened_at falls back to "now".
    tid = sc._open_trade_db(
        "S-TS", "ETH", "long", 1500.0, 1.0, 0.01, 1.0, {"source": "x"},
        execution_type="paper",
    )
    with get_db() as c:
        row = dict(c.execute("SELECT opened_at FROM trades WHERE id=?", (tid,)).fetchone())
    assert row["opened_at"] and row["opened_at"] != "2026-06-26T12:00:00+00:00"


def test_kernel_close_stamps_exit_bar_time(forven_db):
    tid = sc._open_trade_db(
        "S-TS", "ETH", "short", 1545.15, 4.974922, 0.01, 1.0,
        {"kernel_managed": True, "kernel_entry_time": "2026-06-26 12:00:00+00:00",
         "kernel_equity_at_entry": 10000.0},
        execution_type="paper",
        opened_at="2026-06-26 12:00:00+00:00",
    )
    with get_db() as c:
        row = dict(c.execute("SELECT * FROM trades WHERE id=?", (tid,)).fetchone())

    kernel_trade = {
        "exit_price": 1580.13, "pnl_pct": -0.01831, "exit_reason": "signal",
        "exit_time": "2026-06-26 16:00:00+00:00",  # kernel exit-bar time (one bar later)
    }
    sc._kernel_close_recorded("S-TS", {"asset": "ETH"}, row, kernel_trade, "short")

    with get_db() as c:
        out = dict(c.execute("SELECT status, opened_at, closed_at FROM trades WHERE id=?", (tid,)).fetchone())
    assert out["status"] == "CLOSED"
    assert out["opened_at"] == "2026-06-26T12:00:00+00:00"   # entry bar
    assert out["closed_at"] == "2026-06-26T16:00:00+00:00"   # exit bar — NOT the scan moment
    assert out["opened_at"] != out["closed_at"]              # the bug made both equal
