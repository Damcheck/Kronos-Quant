"""The operator-configurable default_leverage is ONE shared default across every
engine (gauntlet confirmation/robustness backtests, execution-profile selection, and
the live/paper scanner), so leverage-sensitive sizing matches — the parity invariant.
"""

from __future__ import annotations

from forven.strategies import backtest as bt


def test_resolve_leverage_precedence(monkeypatch):
    monkeypatch.setattr("forven.api_core.get_settings", lambda: {"default_leverage": 1.0})
    assert bt.resolve_leverage({}, explicit=5.0) == 5.0      # explicit arg wins
    assert bt.resolve_leverage({"leverage": 2.0}) == 2.0     # strategy's declared leverage
    assert bt.resolve_leverage({}) == 1.0                    # operator default
    assert bt.resolve_leverage({"leverage": 0}) == 1.0       # invalid declared → default
    assert bt.resolve_leverage({"leverage": "nope"}) == 1.0
    assert bt.resolve_leverage(None) == 1.0


def test_resolve_default_leverage_reads_setting(monkeypatch):
    monkeypatch.setattr("forven.api_core.get_settings", lambda: {"default_leverage": 3.0})
    assert bt.resolve_default_leverage() == 3.0
    # operator setting flows through resolve_leverage as the fallback too
    assert bt.resolve_leverage({}) == 3.0
    monkeypatch.setattr("forven.api_core.get_settings", lambda: {"default_leverage": -2})
    assert bt.resolve_default_leverage() == 1.0  # invalid → safe 1x
    monkeypatch.setattr("forven.api_core.get_settings", lambda: {})
    assert bt.resolve_default_leverage() == 1.0  # missing → 1x


def test_selection_and_backtest_share_one_default(monkeypatch):
    """execution_selection must resolve the SAME default the backtest uses."""
    from forven.strategies import execution_selection as sel

    monkeypatch.setattr("forven.api_core.get_settings", lambda: {"default_leverage": 2.5})
    # selection's internal resolution (no explicit leverage, no declared param)
    assert bt.resolve_leverage({}) == 2.5
    # objective plumbing unaffected
    assert sel.objective_score({"sharpe_ratio": 1.1}, "sharpe_ratio") == 1.1
