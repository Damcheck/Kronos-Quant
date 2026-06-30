"""Tests for scanner signal/execution split flow."""

from __future__ import annotations

import forven.scanner as scanner_mod
import forven.strategies.registry as registry_mod
from forven.db import kv_get, kv_set


def _stub_signals():
    strat = {
        "name": "Stub Strategy",
        "asset": "BTC",
        "type": "ema_cross",
        "params": {"risk_pct": 0.01, "leverage": 2.0},
    }
    signal = {"price": 100.0, "adx": 15.0, "entry_signal": True, "exit_signal": False}
    return {"S-STUB": signal}, [{"strategy_id": "S-STUB", "strategy": strat, "signal": signal}]


def test_run_scan_signal_only_skips_execution(monkeypatch, forven_db):
    monkeypatch.setattr(scanner_mod, "_load_deployed_strategies", lambda: {"S-STUB": {"asset": "BTC"}})
    monkeypatch.setattr(scanner_mod, "_load_live_price_cache", lambda: ({}, None))
    monkeypatch.setattr(scanner_mod, "sync_from_trades", lambda: None)
    monkeypatch.setattr(scanner_mod, "_evaluate_signal_matrix", lambda *args, **kwargs: _stub_signals())
    monkeypatch.setattr(scanner_mod, "_scan_trade_summary", lambda: (0, 0, 0.0))
    monkeypatch.setattr(registry_mod, "discover", lambda: None)
    monkeypatch.setattr(registry_mod, "get_active", lambda: {})

    execution_calls = {"count": 0}

    def _fake_execute(_rows, _diagnostics=None):
        execution_calls["count"] += 1
        return ["did-exec"]

    monkeypatch.setattr(scanner_mod, "_apply_execution_actions", _fake_execute)

    signals = scanner_mod.run_scan(execute_positions=False)
    assert "S-STUB" in signals
    assert execution_calls["count"] == 0

    state = kv_get("scanner_state", {})
    assert state.get("execution_enabled") is False
    assert state.get("actions_count") == 0
    assert state.get("last_signal_scan") == state.get("last_scan")
    assert state.get("last_execution_scan") is None
    assert state.get("signal_summary", {}).get("signals", {}).get("S-STUB", {}).get("entry_signal") is True
    assert state.get("execution_summary", {}) == {}
    assert state.get("diagnostics", {}).get("S-STUB", {}).get("execution_decision") == "signal_only"


def test_run_scan_with_execution_applies_actions(monkeypatch, forven_db):
    monkeypatch.setattr(scanner_mod, "_load_deployed_strategies", lambda: {"S-STUB": {"asset": "BTC"}})
    monkeypatch.setattr(scanner_mod, "_load_live_price_cache", lambda: ({}, None))
    monkeypatch.setattr(scanner_mod, "sync_from_trades", lambda: None)
    monkeypatch.setattr(scanner_mod, "_evaluate_signal_matrix", lambda *args, **kwargs: _stub_signals())
    monkeypatch.setattr(scanner_mod, "_scan_trade_summary", lambda: (1, 2, 0.05))
    monkeypatch.setattr(registry_mod, "discover", lambda: None)
    monkeypatch.setattr(registry_mod, "get_active", lambda: {})

    execution_calls = {"count": 0}

    def _fake_execute(_rows, diagnostics):
        execution_calls["count"] += 1
        diagnostics["S-STUB"] = {
            "strategy_id": "S-STUB",
            "execution_decision": "opened",
            "runtime_source": "registry",
        }
        return ["opened-stub"]

    monkeypatch.setattr(scanner_mod, "_apply_execution_actions", _fake_execute)

    scanner_mod.run_scan(execute_positions=True)
    assert execution_calls["count"] == 1

    state = kv_get("scanner_state", {})
    assert state.get("execution_enabled") is True
    assert state.get("actions_count") == 1
    assert state.get("last_execution_scan") == state.get("last_scan")
    assert state.get("last_execution_actions_count") == 1
    assert state.get("execution_summary", {}).get("actions_count") == 1
    assert state.get("diagnostics", {}).get("S-STUB", {}).get("execution_decision") == "opened"


def test_run_scan_execution_request_degrades_to_signal_only_by_policy(monkeypatch, forven_db):
    monkeypatch.setattr(scanner_mod, "_load_deployed_strategies", lambda: {"S-STUB": {"asset": "BTC"}})
    monkeypatch.setattr(scanner_mod, "_load_live_price_cache", lambda: ({}, None))
    monkeypatch.setattr(scanner_mod, "sync_from_trades", lambda: None)
    monkeypatch.setattr(scanner_mod, "_evaluate_signal_matrix", lambda *args, **kwargs: _stub_signals())
    monkeypatch.setattr(scanner_mod, "_scan_trade_summary", lambda: (0, 0, 0.0))
    monkeypatch.setattr(scanner_mod, "_scanner_execution_enabled", lambda: False)
    monkeypatch.setattr(registry_mod, "discover", lambda: None)
    monkeypatch.setattr(registry_mod, "get_active", lambda: {})

    execution_calls = {"count": 0}

    def _fake_execute(_rows, _diagnostics=None):
        execution_calls["count"] += 1
        return ["did-exec"]

    monkeypatch.setattr(scanner_mod, "_apply_execution_actions", _fake_execute)

    scanner_mod.run_scan(execute_positions=True)
    assert execution_calls["count"] == 0

    state = kv_get("scanner_state", {})
    assert state.get("requested_execution") is True
    assert state.get("execution_allowed") is False
    assert state.get("execution_enabled") is False
    assert state.get("mode") == "signal_only_by_policy"
    assert state.get("last_execution_scan") == state.get("last_scan")
    assert state.get("last_execution_actions_count") == 0
    assert state.get("execution_summary", {}).get("execution_allowed") is False


def test_run_scan_signal_only_preserves_prior_execution_summary(monkeypatch, forven_db):
    kv_set(
        "scanner_state",
        {
            "execution_summary": {
                "open_positions": 3,
                "closed_trades": 7,
                "total_pnl_pct": 0.12,
                "last_execution_scan": "2026-03-05T09:00:00+00:00",
            }
        },
    )
    monkeypatch.setattr(scanner_mod, "_load_deployed_strategies", lambda: {"S-STUB": {"asset": "BTC"}})
    monkeypatch.setattr(scanner_mod, "_load_live_price_cache", lambda: ({}, None))
    monkeypatch.setattr(scanner_mod, "sync_from_trades", lambda: None)
    monkeypatch.setattr(scanner_mod, "_evaluate_signal_matrix", lambda *args, **kwargs: _stub_signals())
    monkeypatch.setattr(scanner_mod, "_scan_trade_summary", lambda: (3, 7, 0.12))
    monkeypatch.setattr(registry_mod, "discover", lambda: None)
    monkeypatch.setattr(registry_mod, "get_active", lambda: {})
    monkeypatch.setattr(scanner_mod, "_apply_execution_actions", lambda _rows, _diagnostics=None: ["noop"])

    scanner_mod.run_scan(execute_positions=False)

    state = kv_get("scanner_state", {})
    assert state.get("execution_summary", {}).get("open_positions") == 3
    assert state.get("execution_summary", {}).get("closed_trades") == 7
    assert state.get("execution_summary", {}).get("total_pnl_pct") == 0.12


def test_manage_positions_executes_directly_when_not_paper_local(monkeypatch, forven_db):
    """Non-paper-local execution goes straight to _execute_direct — the legacy
    queue-to-execution-trader route was removed (the agent is retired)."""
    executed: list[dict] = []
    monkeypatch.setattr("forven.config.get_execution_mode", lambda: "paper")
    monkeypatch.setattr(scanner_mod, "_get_open_trades", lambda _strategy_id: [])
    monkeypatch.setattr(scanner_mod, "_paper_test_mode_enabled", lambda: False)
    monkeypatch.setattr(scanner_mod, "_paper_test_bypass_gates_enabled", lambda: False)
    monkeypatch.setattr(
        scanner_mod,
        "_scanner_bool_setting",
        lambda name, default=False: False if name == "paper_stage_local_execution_only" else default,
    )
    monkeypatch.setattr(scanner_mod, "_get_account_equity", lambda: 10000.0)
    monkeypatch.setattr(scanner_mod, "_has_seen_entry_signal", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(scanner_mod, "_remember_entry_signal", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(scanner_mod, "can_open", lambda **_kwargs: (True, 0.01, "ok"))
    monkeypatch.setattr(
        scanner_mod,
        "calculate_position_size",
        lambda **_kwargs: (0.25, {"model": "fixed-risk"}),
    )
    monkeypatch.setattr(scanner_mod, "_open_trade_db", lambda *_args, **_kwargs: "TR-001")
    monkeypatch.setattr(scanner_mod, "register", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(scanner_mod, "_execute_direct", lambda **kwargs: executed.append(kwargs) or {})
    monkeypatch.setattr(scanner_mod, "log_activity", lambda *args, **kwargs: None)
    monkeypatch.setattr("forven.sim.clock.is_sim_active", lambda: False)

    actions = scanner_mod.manage_positions(
        "S-STUB",
        {
            "asset": "BTC",
            "stage": "paper",
            "params": {"risk_pct": 0.01, "leverage": 2.0},
        },
        {
            "price": 100.0,
            "adx": 18.0,
            "entry_signal": True,
            "exit_signal": False,
        },
        account_equity=10000.0,
    )

    assert executed and executed[0]["action"] == "open"
    assert any(action.startswith("OPENED long BTC") for action in actions)


def test_manage_positions_executes_paper_stage_locally_by_default(monkeypatch, forven_db):
    fills: list[dict] = []
    opened: dict = {}

    monkeypatch.setattr("forven.config.get_execution_mode", lambda: "paper")
    monkeypatch.setattr(scanner_mod, "_get_open_trades", lambda _strategy_id: [])
    monkeypatch.setattr(scanner_mod, "_paper_test_mode_enabled", lambda: False)
    monkeypatch.setattr(scanner_mod, "_paper_test_bypass_gates_enabled", lambda: False)
    monkeypatch.setattr(scanner_mod, "_scanner_bool_setting", lambda _name, default=False: default)
    monkeypatch.setattr(scanner_mod, "_get_account_equity", lambda: 10000.0)
    monkeypatch.setattr(scanner_mod, "_has_seen_entry_signal", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(scanner_mod, "_remember_entry_signal", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(scanner_mod, "can_open", lambda **_kwargs: (True, 0.01, "ok"))
    monkeypatch.setattr(
        scanner_mod,
        "calculate_position_size",
        lambda **_kwargs: (0.25, {"model": "fixed-risk"}),
    )
    monkeypatch.setattr(
        scanner_mod,
        "_open_trade_db",
        lambda strat_id, asset, direction, entry, size, risk_pct, leverage, signal_data, execution_type="live": (
            opened.update({"strategy_id": strat_id, "asset": asset, "execution_type": execution_type}) or "TR-LOCAL"
        ),
    )
    monkeypatch.setattr(scanner_mod, "register", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(scanner_mod, "_execute_direct", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("paper local execution should not call exchange")))
    monkeypatch.setattr(scanner_mod, "_update_trade_fill", lambda **kwargs: fills.append(kwargs))
    monkeypatch.setattr(scanner_mod, "log_activity", lambda *args, **kwargs: None)
    monkeypatch.setattr("forven.sim.clock.is_sim_active", lambda: False)

    actions = scanner_mod.manage_positions(
        "S-LOCAL",
        {
            "asset": "BTC",
            "stage": "paper",
            "params": {"risk_pct": 0.01, "leverage": 2.0},
        },
        {
            "price": 100.0,
            "adx": 18.0,
            "entry_signal": True,
            "exit_signal": False,
        },
        account_equity=10000.0,
    )

    assert opened == {"strategy_id": "S-LOCAL", "asset": "BTC", "execution_type": "paper_challenger"}
    assert fills and fills[0]["trade_id"] == "TR-LOCAL"
    # Must be "entry" — _update_trade_fill silently ignores any other kind, and
    # an unrecorded fill left paper trades to be auto-closed as "stale unfilled".
    assert fills[0]["fill_kind"] == "entry"
    assert any(action.startswith("OPENED long BTC") for action in actions)


def test_manage_positions_preserves_blocked_signal_reason(monkeypatch, forven_db):
    diagnostics: dict[str, dict] = {}

    monkeypatch.setattr("forven.config.get_execution_mode", lambda: "paper")
    monkeypatch.setattr(scanner_mod, "_get_open_trades", lambda _strategy_id: [])
    monkeypatch.setattr(scanner_mod, "_paper_test_mode_enabled", lambda: False)
    monkeypatch.setattr(scanner_mod, "_paper_test_bypass_gates_enabled", lambda: False)
    monkeypatch.setattr(scanner_mod, "_scanner_bool_setting", lambda _name, default=False: default)

    actions = scanner_mod.manage_positions(
        "S-BLOCKED",
        {
            "asset": "BTC",
            "stage": "paper",
            "params": {"risk_pct": 0.01, "leverage": 1.0},
        },
        {
            "price": 100.0,
            "entry_signal": False,
            "exit_signal": False,
            "block_reason": "regime gate: TREND_DOWN not allowed (confidence=0.40)",
        },
        account_equity=10000.0,
        diagnostics=diagnostics,
    )

    assert actions == []
    assert diagnostics["S-BLOCKED"]["execution_decision"] == "blocked"
    assert diagnostics["S-BLOCKED"]["blocked_reason"] == "regime gate: TREND_DOWN not allowed (confidence=0.40)"
