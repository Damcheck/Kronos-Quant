"""Propagate execution-setting edits onto an OPEN paper/live position, and the
in-trade warning summary that drives the pre-edit confirm."""

from __future__ import annotations

import json

import pytest

from forven.api_domains import paper_control as pc
from forven.trade_state import parse_trade_signal_data


def _open_paper_trade(strategy_id, *, direction="long", entry=100.0, sl=95.0, tp=110.0):
    from forven.scanner import _open_trade_db

    return _open_trade_db(
        strat_id=strategy_id, asset="BTC", direction=direction, entry=entry,
        size=0.1, risk_pct=0.01, leverage=1.0,
        signal_data={"stop_loss": sl, "stop_loss_price": sl, "take_profit": tp, "take_profit_price": tp},
        execution_type="paper",
    )


def _signal_data(trade_id):
    from forven.db import get_db

    with get_db() as conn:
        row = conn.execute("SELECT signal_data FROM trades WHERE id = ?", (trade_id,)).fetchone()
    return parse_trade_signal_data(dict(row)["signal_data"])


_FRACTION_4_8 = {"execution_profile": {"sizing_mode": "fraction", "stop_loss_pct": 4.0, "take_profit_pct": 8.0}}


def test_apply_profile_updates_open_long_position(forven_db):
    sid = "S-PROP"
    tid = _open_paper_trade(sid, direction="long", entry=100.0, sl=95.0, tp=110.0)
    out = pc.apply_execution_profile_to_open_position(sid, _FRACTION_4_8)
    assert out and out["affected"] and out["count"] == 1
    sd = _signal_data(tid)
    assert sd["stop_loss_price"] == pytest.approx(96.0)   # 100 * (1 - 0.04)
    assert sd["take_profit_price"] == pytest.approx(108.0)  # 100 * (1 + 0.08)
    assert sd["stop_loss"] == pytest.approx(96.0)
    assert sd["stop_loss_source"] == "execution_profile"


def test_apply_profile_updates_open_short_position(forven_db):
    sid = "S-PROP-SHORT"
    tid = _open_paper_trade(sid, direction="short", entry=100.0, sl=105.0, tp=90.0)
    pc.apply_execution_profile_to_open_position(sid, _FRACTION_4_8)
    sd = _signal_data(tid)
    assert sd["stop_loss_price"] == pytest.approx(104.0)  # short: 100 * (1 + 0.04)
    assert sd["take_profit_price"] == pytest.approx(92.0)   # short: 100 * (1 - 0.08)


def test_no_open_position_returns_none(forven_db):
    assert pc.apply_execution_profile_to_open_position("S-NONE", _FRACTION_4_8) is None


def test_open_position_summary(forven_db):
    sid = "S-SUM"
    assert pc.open_position_summary(sid) == {"has_open_position": False, "count": 0, "positions": []}
    _open_paper_trade(sid, direction="long", entry=100.0)
    summ = pc.open_position_summary(sid)
    assert summ["has_open_position"] is True and summ["count"] == 1
    pos = summ["positions"][0]
    assert pos["asset"] == "BTC" and pos["direction"] == "long" and pos["is_live"] is False
    assert pos["entry_price"] == pytest.approx(100.0)


# ── api_core gating: paper/live + profile-changed ────────────────────────────

def _insert_strategy(sid, stage, params):
    from forven.db import get_db

    with get_db() as conn:
        conn.execute(
            "INSERT INTO strategies (id, name, type, symbol, timeframe, params, metrics, stage, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, sid, "rsi_momentum", "BTC", "1h", json.dumps(params), "{}", stage, "active"),
        )


def _patch_certify(monkeypatch):
    """Bypass strategy certification so the test isolates the propagation gating."""
    import forven.api_core as api_core

    class _Cert:
        def __init__(self, params):
            self.canonical_params = params

        def format_error(self, context=""):
            return None

    monkeypatch.setattr(api_core, "_parse_strategy_params_blob", lambda blob: json.loads(blob) if blob else {})
    import forven.strategies.certification as cert

    monkeypatch.setattr(cert, "certify_execution_strategy", lambda stype, params: _Cert(params))


def test_update_default_params_propagates_for_paper(forven_db, monkeypatch):
    import forven.api_core as api_core

    _patch_certify(monkeypatch)
    sid = "S-API"
    _insert_strategy(sid, "paper", {"rsi_period": 14})
    tid = _open_paper_trade(sid, direction="long", entry=100.0, sl=95.0, tp=110.0)
    res = api_core.update_strategy_default_params(sid, _FRACTION_4_8)
    assert res["open_position_update"] and res["open_position_update"]["affected"]
    assert _signal_data(tid)["stop_loss_price"] == pytest.approx(96.0)


def test_no_propagation_when_execution_profile_unchanged(forven_db, monkeypatch):
    import forven.api_core as api_core

    _patch_certify(monkeypatch)
    sid = "S-API2"
    _insert_strategy(sid, "paper", {"rsi_period": 14, "execution_profile": {"sizing_mode": "fraction", "stop_loss_pct": 4.0}})
    tid = _open_paper_trade(sid, entry=100.0, sl=95.0, tp=110.0)
    # Change only an alpha param; same execution_profile → open position untouched.
    res = api_core.update_strategy_default_params(
        sid, {"rsi_period": 20, "execution_profile": {"sizing_mode": "fraction", "stop_loss_pct": 4.0}}
    )
    assert res["open_position_update"] is None
    assert _signal_data(tid)["stop_loss_price"] == pytest.approx(95.0)


def test_no_propagation_for_non_paper_stage(forven_db, monkeypatch):
    import forven.api_core as api_core

    _patch_certify(monkeypatch)
    sid = "S-API3"
    _insert_strategy(sid, "gauntlet", {"rsi_period": 14})
    tid = _open_paper_trade(sid, entry=100.0, sl=95.0, tp=110.0)
    res = api_core.update_strategy_default_params(sid, _FRACTION_4_8)
    assert res["open_position_update"] is None  # gauntlet is not an operator-owned stage
    assert _signal_data(tid)["stop_loss_price"] == pytest.approx(95.0)
