#!/usr/bin/env python3
"""Create/update a high-turnover strategy and fast-track scanner cadence.

This is intended for end-to-end pipeline and trade-review feature validation.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from forven.config import (
    get_execution_fast_path,
    get_execution_mode,
    set_execution_fast_path,
    set_execution_mode,
)
from forven.db import get_db, init_db, kv_get, kv_set
from forven.scheduler import apply_runtime_scheduler_overrides


VALID_TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h", "1d")
VALID_STRATEGY_TYPES = ("stress_toggle", "rsi_momentum", "ema_cross")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_target(target: str) -> tuple[str, str]:
    normalized = str(target or "").strip().lower()
    if normalized == "paper":
        return "paper", "paper_trading"
    if normalized == "deployed":
        return "deployed", "deployed"
    raise ValueError(f"unsupported target: {target}")


def _build_params(args: argparse.Namespace) -> dict:
    strategy_type = str(args.strategy_type).strip().lower()
    if strategy_type == "stress_toggle":
        return {
            "risk_pct": float(args.risk_pct),
            "leverage": float(args.leverage),
        }
    if strategy_type == "ema_cross":
        return {
            "ema_fast": int(args.ema_fast),
            "ema_slow": int(args.ema_slow),
            "adx_period": int(args.adx_period),
            "adx_min": float(args.adx_min),
            "risk_pct": float(args.risk_pct),
            "leverage": float(args.leverage),
        }
    return {
        "rsi_period": int(args.rsi_period),
        "rsi_entry": float(args.rsi_entry),
        "rsi_exit": float(args.rsi_exit),
        "ema_fast": int(args.ema_fast),
        "ema_slow": int(args.ema_slow),
        "adx_period": int(args.adx_period),
        "adx_min": float(args.adx_min),
        "risk_pct": float(args.risk_pct),
        "leverage": float(args.leverage),
    }


def _upsert_strategy(
    *,
    strategy_id: str,
    name: str,
    strategy_type: str,
    asset: str,
    timeframe: str,
    status: str,
    stage: str,
    params: dict,
    dry_run: bool,
) -> str:
    now = _now_iso()
    metrics = {
        "fitness_v2": 99.0,
        "purpose": "pipeline_stress_test",
        "expected_behavior": "high_turnover",
        "strategy_type": strategy_type,
    }
    notes = "Created by setup_trade_stress_strategy.py for pipeline/trade-review validation."

    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM strategies WHERE id = ?",
            (strategy_id,),
        ).fetchone()

        if dry_run:
            return "update" if existing else "insert"

        if existing:
            conn.execute(
                """
                UPDATE strategies
                SET name = ?, type = ?, symbol = ?, timeframe = ?, params = ?, metrics = ?,
                    status = ?, stage = ?, owner = ?, notes = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    name,
                    strategy_type,
                    asset,
                    timeframe,
                    json.dumps(params),
                    json.dumps(metrics),
                    status,
                    stage,
                    "execution-trader",
                    notes,
                    now,
                    strategy_id,
                ),
            )
            return "update"

        conn.execute(
            """
            INSERT INTO strategies
            (id, name, type, symbol, timeframe, params, metrics, status, owner, stage, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                strategy_id,
                name,
                strategy_type,
                asset,
                timeframe,
                json.dumps(params),
                json.dumps(metrics),
                status,
                "execution-trader",
                stage,
                notes,
                now,
                now,
            ),
        )
    return "insert"


def _apply_scanner_cadence(signal_minutes: int, execution_minutes: int, dry_run: bool) -> int:
    if dry_run:
        return 0

    raw = kv_get("forven:settings", {})
    settings = dict(raw) if isinstance(raw, dict) else {}
    settings["throughput_auto_scheduler_control"] = True
    settings["scanner_signal_interval_minutes"] = int(signal_minutes)
    settings["scanner_execution_interval_minutes"] = int(execution_minutes)
    settings["scanner_allow_direct_market_fetch"] = True
    kv_set("forven:settings", settings)
    return int(apply_runtime_scheduler_overrides())


def _ensure_execution_agent(dry_run: bool) -> str:
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM agents WHERE id = 'execution-trader'",
        ).fetchone()
        if row:
            return "present"
        if dry_run:
            return "would_insert"
        now = _now_iso()
        conn.execute(
            """
            INSERT INTO agents
            (id, name, role, model, model_id, enabled, instructions, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "execution-trader",
                "Execution Trader",
                "Execute trades and reconcile fills.",
                "openai",
                "",
                1,
                "Auto-seeded for trade stress strategy execution queue compatibility.",
                now,
                now,
            ),
        )
        return "inserted"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a high-turnover stress strategy and fast-track scanner cadence.",
    )
    parser.add_argument("--strategy-id", default="stress-rsi-churn-eth-1m")
    parser.add_argument("--name", default="Stress Toggle Churn (ETH 1m)")
    parser.add_argument("--strategy-type", default="stress_toggle", choices=VALID_STRATEGY_TYPES)
    parser.add_argument("--asset", default="ETH")
    parser.add_argument("--timeframe", default="1m", choices=VALID_TIMEFRAMES)
    parser.add_argument("--target", default="paper", choices=("paper", "deployed"))

    parser.add_argument("--rsi-period", type=int, default=7)
    parser.add_argument("--rsi-entry", type=float, default=45.0)
    parser.add_argument("--rsi-exit", type=float, default=46.0)
    parser.add_argument("--ema-fast", type=int, default=5)
    parser.add_argument("--ema-slow", type=int, default=13)
    parser.add_argument("--adx-period", type=int, default=7)
    parser.add_argument("--adx-min", type=float, default=0.0)
    parser.add_argument("--risk-pct", type=float, default=0.002)
    parser.add_argument("--leverage", type=float, default=1.5)

    parser.add_argument("--signal-minutes", type=int, default=1)
    parser.add_argument("--execution-minutes", type=int, default=1)
    parser.add_argument("--execution-mode", choices=("paper", "live"))
    parser.add_argument("--enable-fast-path", action="store_true")
    parser.add_argument("--disable-fast-path", action="store_true")

    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.enable_fast_path and args.disable_fast_path:
        raise SystemExit("Choose either --enable-fast-path or --disable-fast-path, not both.")

    init_db()
    agent_state = _ensure_execution_agent(bool(args.dry_run))

    status, stage = _resolve_target(args.target)
    params = _build_params(args)
    action = _upsert_strategy(
        strategy_id=str(args.strategy_id).strip(),
        name=str(args.name).strip(),
        strategy_type=str(args.strategy_type).strip().lower(),
        asset=str(args.asset).strip().upper(),
        timeframe=str(args.timeframe).strip().lower(),
        status=status,
        stage=stage,
        params=params,
        dry_run=bool(args.dry_run),
    )

    scheduler_updates = _apply_scanner_cadence(
        signal_minutes=max(1, int(args.signal_minutes)),
        execution_minutes=max(1, int(args.execution_minutes)),
        dry_run=bool(args.dry_run),
    )

    mode_before = get_execution_mode()
    fast_before = get_execution_fast_path()
    mode_after = mode_before
    fast_after = fast_before

    if not args.dry_run:
        if args.execution_mode:
            set_execution_mode(args.execution_mode)
            mode_after = get_execution_mode()
        if args.enable_fast_path:
            set_execution_fast_path(True)
            fast_after = get_execution_fast_path()
        elif args.disable_fast_path:
            set_execution_fast_path(False)
            fast_after = get_execution_fast_path()

    print(f"strategy_action={action}")
    print(f"execution_agent={agent_state}")
    print(f"strategy_id={args.strategy_id}")
    print(f"strategy_type={args.strategy_type}")
    print(f"target_status={status}")
    print(f"target_stage={stage}")
    print(f"timeframe={args.timeframe}")
    print(f"scanner_signal_interval_minutes={max(1, int(args.signal_minutes))}")
    print(f"scanner_execution_interval_minutes={max(1, int(args.execution_minutes))}")
    print(f"scheduler_jobs_updated={scheduler_updates}")
    print(f"execution_mode_before={mode_before}")
    print(f"execution_mode_after={mode_after}")
    print(f"execution_fast_path_before={fast_before}")
    print(f"execution_fast_path_after={fast_after}")
    print(f"dry_run={bool(args.dry_run)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
