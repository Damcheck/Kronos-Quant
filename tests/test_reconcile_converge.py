"""Reconciler convergence: adopt a drifted open position and converge-close an orphan
the kernel has exited — so paper never holds a trade the strategy/kernel already
closed (the bug where a position opened on the old HL data was never exited)."""

from __future__ import annotations

from forven.strategies.execution_kernel import KernelResult
from forven.strategies.paper_reconcile import reconcile

WINDOW = "2026-06-01T00:00:00+00:00"


def _kr(closed=None, open_pos=None):
    return KernelResult(closed_trades=closed or [], open_positions=open_pos or {}, closed_gross=[])


def _rec(direction, entry_time, status="open", tid="T1", orphan=False):
    r = {"direction": direction, "entry_time": entry_time, "status": status, "_row": {"id": tid, "asset": "BTC"}}
    if orphan:
        r["_orphan"] = True
    return r


def test_orphan_close_when_kernel_flat():
    # Kernel holds NO position; a recorded open short (drifted/orphan) must be closed.
    actions = reconcile(_kr(), [_rec("short", "2026-06-20T00:00:00+00:00", orphan=True)], window_start=WINDOW)
    oc = [a for a in actions if a.kind == "orphan_close"]
    assert len(oc) == 1 and oc[0].direction == "short" and oc[0].recorded["_row"]["id"] == "T1"


def test_adopt_drifted_open_no_duplicate_no_orphan_close():
    # Kernel still holds the short (entry drifted to T2); the recorded short at T1 is
    # ADOPTED (refresh) — not opened again, not orphan-closed.
    open_pos = {"short": {"entry_time": "2026-06-25T00:00:00+00:00", "entry_price": 100.0, "size_fraction": 0.2}}
    actions = reconcile(_kr(open_pos=open_pos), [_rec("short", "2026-06-20T00:00:00+00:00", orphan=True)], window_start=WINDOW)
    kinds = [a.kind for a in actions]
    assert "open" not in kinds and "orphan_close" not in kinds
    rf = [a for a in actions if a.kind == "refresh"]
    assert len(rf) == 1 and rf[0].recorded["_row"]["id"] == "T1" and rf[0].position["entry_price"] == 100.0
    assert rf[0].entry_time == "2026-06-25T00:00:00+00:00"  # stamps the kernel's current entry_time


def test_exact_match_is_plain_refresh_no_converge():
    # Consistent data (entry_times match) → behaves exactly as before: a single refresh.
    open_pos = {"short": {"entry_time": "2026-06-25T00:00:00+00:00", "entry_price": 100.0}}
    actions = reconcile(_kr(open_pos=open_pos), [_rec("short", "2026-06-25T00:00:00+00:00")], window_start=WINDOW)
    assert [a.kind for a in actions] == ["refresh"]


def test_window_guard_leaves_pre_window_orphan_alone():
    # Entry predates the kernel's evaluated window → the kernel can't speak to it; keep it.
    actions = reconcile(_kr(), [_rec("short", "2026-05-01T00:00:00+00:00")], window_start=WINDOW)
    assert "orphan_close" not in [a.kind for a in actions]


def test_matched_close_still_closes_and_suppresses_orphan():
    # A kernel closed trade matching the recorded open → normal close (not orphan_close).
    closed = [{"direction": "short", "entry_time": "2026-06-20T00:00:00+00:00", "exit_price": 90.0,
               "exit_time": "2026-06-22T00:00:00+00:00", "pnl_pct": 0.1, "exit_reason": "signal"}]
    actions = reconcile(_kr(closed=closed), [_rec("short", "2026-06-20T00:00:00+00:00")], window_start=WINDOW)
    kinds = [a.kind for a in actions]
    assert "close" in kinds and "orphan_close" not in kinds


def test_format_tolerant_window_guard():
    # 'space' vs 'T' separator must not fool the window guard.
    actions = reconcile(_kr(), [_rec("long", "2026-05-15 12:00:00+00:00")], window_start="2026-06-01T00:00:00+00:00")
    assert "orphan_close" not in [a.kind for a in actions]  # May 15 < Jun 1 despite format drift
