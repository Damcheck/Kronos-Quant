"""An ERRORED validation job (worker crash, data gap, or a "lookback exceeds bars
per split" failure on an incompatible/stale-container timeframe) is a NON-RESULT, not
a quality verdict. _extract_gauntlet_verdict_payloads must SKIP it like a pending row —
never read it as a merit FAIL.

Root cause it guards (S03523): the container's stale timeframe (1d) drove a doomed 1d
walk_forward that errored ("lookback (210) exceeds available bars per split (84)") with
status='failed' and NO splits. Read as a verdict, its folds defaulted to 0, tripping the
S00552 "Walk-forward has 0 folds, requires minimum 2" reject — which archived a strategy
whose GENUINE (succeeded) 5-fold BTC-1h walk_forward actually passed. Generic: any
strategy with an errored validation row alongside a valid one was being mis-archived.
"""

from __future__ import annotations

import json

from forven.db import get_db
from forven.policy import _extract_gauntlet_verdict_payloads


def _insert_strategy(sid: str):
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO strategies "
            "(id, name, type, symbol, timeframe, params, metrics, status, owner, stage, "
            " stage_changed_at, created_at, updated_at) "
            "VALUES (?, ?, 'rsi_momentum', 'BTC', '1h', '{}', '{}', 'gauntlet', 'brain', "
            "'gauntlet', datetime('now'), datetime('now'), datetime('now'))",
            (sid, sid),
        )


def _insert_wf(sid: str, rid: str, *, symbol, timeframe, created_at, metrics: dict):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO backtest_results "
            "(result_id, strategy_id, result_type, symbol, timeframe, metrics_json, config_json, created_at) "
            "VALUES (?, ?, 'walk_forward', ?, ?, ?, ?, ?)",
            (rid, sid, symbol, timeframe, json.dumps(metrics), json.dumps({"status": metrics.get("status")}), created_at),
        )


def _valid_wf_metrics() -> dict:
    # 5 OOS-positive folds, each with enough trades to be evaluated.
    splits = [{"out_of_sample": {"sharpe": 1.2, "total_trades": 12}} for _ in range(5)]
    return {
        "status": "succeeded",
        "verdict": "PASS",
        "splits": splits,
        "aggregate_oos": {"sharpe": 1.1, "total_trades": 60},
        "avg_is_sharpe": 1.0,
        "avg_oos_sharpe": 1.1,
        "degradation": 0.1,
    }


def _errored_wf_metrics() -> dict:
    return {
        "status": "failed",
        "error": "Parameter lookback (210) exceeds or equals available bars per split (84)",
    }


def test_errored_wf_skipped_in_favour_of_valid(forven_db):
    # The errored 1d row is NEWER than the valid 1h row, so a naive "most recent wins"
    # would pick it (folds -> 0). The skip must make the gate read the valid 5-fold run.
    _insert_strategy("S-WFERR")
    _insert_wf("S-WFERR", "wf-valid", symbol="BTC", timeframe="1h",
               created_at="2026-06-29T19:52:09+00:00", metrics=_valid_wf_metrics())
    _insert_wf("S-WFERR", "wf-errored", symbol="BTC/USDT", timeframe="1d",
               created_at="2026-06-29T19:55:00+00:00", metrics=_errored_wf_metrics())  # NEWER

    payloads, _overall = _extract_gauntlet_verdict_payloads("S-WFERR", {"verdict": ""}, {})
    wf = payloads.get("walk_forward")
    assert isinstance(wf, dict), "the valid walk_forward must be present"
    assert int(wf.get("folds") or 0) >= 2, "must read the valid 5-fold run, not the errored 0-fold row"
    assert not wf.get("error"), "the errored row must not contaminate the verdict"


def test_errored_only_wf_is_not_a_verdict(forven_db):
    # When the ONLY walk_forward row errored, it must NOT appear as a (failed) verdict —
    # the test is simply 'not run', so the strategy isn't archived on a phantom 0-fold FAIL.
    _insert_strategy("S-WFERR2")
    _insert_wf("S-WFERR2", "wf-only-errored", symbol="BTC/USDT", timeframe="1d",
               created_at="2026-06-29T19:55:00+00:00", metrics=_errored_wf_metrics())

    payloads, _overall = _extract_gauntlet_verdict_payloads("S-WFERR2", {"verdict": ""}, {})
    assert "walk_forward" not in payloads, "an errored-only walk_forward must be skipped, not read as FAIL"
