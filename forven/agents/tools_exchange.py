"""Exchange execution tools and ownership/error routing helpers."""

import json
import logging

from forven.db import get_db, kv_get, log_activity
from .context import _current_agent_id_var, _current_task_display_id_var
from .tool_registry import register_tool

log = logging.getLogger("forven.agents.runner")


def _register_live_execution_tool(**kwargs):
    """No-op decorator: place_order / close_position are NOT exposed as agent tools.

    Trade execution is owned end-to-end by the scanner's parity kernel
    (``manage_positions_via_kernel`` → ``_execute_direct`` / the local paper
    applier) and by the operator's manual position controls (``api_domains/
    paper_control.py``, ``api_domains/trading.py``). The LLM agents must NOT place
    or close orders out-of-band: an execution-trader "close phantom positions"
    task ran exchange closes against LOCAL paper trades, which returned no fill,
    marked them pending_close_reconcile, and the sweep then finalized them as
    "unknown"/incomplete closes — corrupting the paper book. (See the unknown-close
    investigation, 2026-06-30; the out-of-band-trade-write hazard from the security
    hardening initiative.)

    Returning a no-op decorator means the tool name NEVER enters the registry, so
    the model literally cannot call it — no permission flag to misconfigure, defense
    in depth matching the original beta-build rationale (security audit 2026-04-23).
    The handler functions stay defined for direct/internal use and tests.
    """
    return lambda fn: fn


def _execution_bool_setting(name: str, default: bool) -> bool:
    settings = kv_get("forven:settings", {})
    payload = settings if isinstance(settings, dict) else {}
    raw = payload.get(name, default)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in {"1", "true", "yes", "on", "y"}:
            return True
        if normalized in {"0", "false", "no", "off", "n"}:
            return False
    return default

def _resolve_strategy_from_trade(trade_id: str | None) -> str | None:
    """Resolve strategy ID from a trade record."""
    if not trade_id:
        return None
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT COALESCE(strategy_id, strategy) as strategy_id FROM trades WHERE id = ?",
                (trade_id,),
            ).fetchone()
        if row:
            return row["strategy_id"]
    except Exception:
        return None
    return None


def _resolve_paper_mark_price(asset: str) -> float | None:
    """Current mark (mid) for ``asset`` — used to close a LOCAL paper trade at a
    real price without touching the exchange. Returns None when no live mark is
    available; callers must refuse rather than fabricate a fill."""
    symbol = str(asset or "").strip().upper()
    if not symbol:
        return None
    try:
        from forven.exchange.hyperliquid import get_all_mids

        mids = get_all_mids(testnet=True) or {}
        raw = mids.get(symbol)
        if raw is None:
            raw = mids.get(asset)
        return float(raw) if raw is not None else None
    except Exception as exc:
        log.warning("Could not resolve mark price for %s: %s", asset, exc)
        return None


def _extract_task_strategy_id(task: dict) -> str | None:
    """Resolve strategy_id from a task row, using explicit field or input payload."""
    if not isinstance(task, dict):
        return None

    strategy_id = task.get("strategy_id")
    if isinstance(strategy_id, str) and strategy_id.strip():
        return strategy_id.strip()

    input_data = task.get("input_data")
    if isinstance(input_data, str):
        try:
            input_data = json.loads(input_data)
        except Exception:
            return None

    if not isinstance(input_data, dict):
        return None

    strategy_id = input_data.get("strategy_id") or input_data.get("strategy")
    if isinstance(strategy_id, str) and strategy_id.strip():
        return strategy_id.strip()
    return None


_STAGE_TO_OWNER_GUARD = {
    "quick_screen": "simulation-agent",
    "gauntlet": "simulation-agent",
    "paper": "risk-manager",
    # execution-trader retired — live oversight ownership is risk-manager's.
    "live_graduated": "risk-manager",
}


def _normalize_stage_guard(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    aliases = {
        "researching": "quick_screen",
        "developing": "quick_screen",
        "backtesting": "gauntlet",
        "paper_trading": "paper",
        "papertrading": "paper",
        "paper-trading": "paper",
        "review": "live_graduated",
        "ceoreview": "live_graduated",
        "ceo-review": "live_graduated",
        "ceo_review": "live_graduated",
        "deployed": "live_graduated",
        "retired": "archived",
    }
    return aliases.get(normalized, normalized)


def _check_task_owner(
    agent_id: str,
    strategy_id: str | None,
    task_type: str | None = None,
) -> tuple[str | None, bool]:
    """Verify that the current strategy owner matches the worker owner."""
    normalized_agent = _normalize_agent_id(agent_id)
    normalized_task_type = str(task_type or "").strip().lower()

    # strategy-developer codes containers at any stage; ownership is irrelevant.
    if normalized_agent == "strategy-developer" and normalized_task_type in (
        "code_strategy", "code_strategy_container", "coding_cycle", "phantom_repair",
    ):
        return None, True

    if not strategy_id:
        return None, True

    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT owner, stage, status FROM strategies WHERE id = ?",
                (strategy_id,),
            ).fetchone()
            if row:
                current_owner = str(row["owner"] or "").strip().lower() or "brain"
                strategy_stage = _normalize_stage_guard(row["stage"] or row["status"])
                expected_owner = _STAGE_TO_OWNER_GUARD.get(strategy_stage)

                if current_owner != normalized_agent and expected_owner == normalized_agent and current_owner == "brain":
                    conn.execute(
                        "UPDATE strategies SET owner = ? WHERE id = ? "
                        "AND (owner IS NULL OR TRIM(owner) = '' OR LOWER(TRIM(owner)) = 'brain')",
                        (normalized_agent, strategy_id),
                    )
                    current_owner = normalized_agent
                if current_owner == normalized_agent or current_owner == "brain":
                    return None, True
    except Exception as exc:
        return f"Unable to verify ownership for strategy {strategy_id}: {exc}", False

    if not row:
        return f"Strategy {strategy_id} not found", False

    current_owner = str(row["owner"] or "").strip().lower() or "brain"
    if current_owner == "brain":
        return None, True
        
    return (
        f"Ownership mismatch for strategy {strategy_id}: expected {normalized_agent}, found {current_owner}",
        False,
    )


def _normalize_agent_id(agent_id: str | None) -> str:
    normalized = str(agent_id or "").strip().lower()
    if normalized == "backtest-engineer":
        return "simulation-agent"
    if normalized == "system":
        return "brain"
    return normalized


def _route_execution_failure(
    action: str,
    reason: str,
    trade_id: str | None = None,
    strategy_id: str | None = None,
) -> None:
    """Route execution failures back to the strategy developer for post-mortem fixes."""
    strategy = strategy_id or _resolve_strategy_from_trade(trade_id)
    if not strategy:
        log.warning("Execution failure not routed: no strategy for %s (%s)", action, trade_id or "--")
        return

    details = f"execution-trader {action}: {reason}"
    if trade_id:
        details = f"{details} (trade={trade_id})"
    try:
        from forven.brain import handoff_execution_failure_to_developer
        handoff_execution_failure_to_developer(
            strategy_id=strategy,
            failure_reason=details,
            actor="execution-trader",
        )
        log_activity("warning", "execution-trader", details)
    except ValueError as exc:
        log.debug("Execution failure already routed for %s: %s", strategy, exc)
    except Exception as exc:
        log.warning("Could not route execution failure for %s: %s", strategy, exc)


@register_tool(
    name="request_fix",
    description=(
        "Report a code-level bug you cannot resolve to the operator's triage queue. "
        "Use this when you encounter a bug, broken import, API error, or infrastructure issue "
        "that you cannot resolve with your own tools. It records the bug for human / Claude-Code "
        "review (a notification + the review log) — NO autonomous code change is made; the system "
        "is fixed through the normal dev workflow. Provide a clear description of the problem, what "
        "you tried, and what files/systems are affected."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short summary of the problem (shown in the operator triage queue)"},
            "description": {
                "type": "string",
                "description": "Detailed problem description: what failed, error messages, what you already tried, affected files/systems",
            },
            "severity": {
                "type": "string",
                "enum": ["low", "medium", "high", "critical"],
                "description": "Impact severity. Default: medium",
            },
            "context": {"type": "object", "description": "Optional context: error traces, file paths, strategy_id, etc."},
        },
        "required": ["title", "description"],
    },
)
def _tool_request_fix(params: dict) -> str:
    """Report a code-level problem to the operator bug-triage queue (report-only)."""
    title = str(params.get("title", "")).strip()
    description = str(params.get("description", "")).strip()
    if not title or not description:
        return "Error: both 'title' and 'description' are required."

    severity = str(params.get("severity", "medium")).strip().lower()
    if severity not in ("low", "medium", "high", "critical"):
        severity = "medium"

    context = params.get("context") or {}
    requesting_agent = _current_agent_id_var.get()
    requesting_task = _current_task_display_id_var.get()

    try:
        from forven.brain import escalate_to_engineer
        result = escalate_to_engineer(
            title=title,
            description=description,
            requesting_agent=requesting_agent,
            requesting_task_id=requesting_task,
            severity=severity,
            context=context if isinstance(context, dict) else {},
        )
        return json.dumps({
            "status": result.get("status", "reported"),
            "queue": result.get("queue", "operator_triage"),
            "message": (
                f"Bug reported to the operator triage queue (severity={severity}). "
                f"No autonomous code change is made; it will be fixed via the normal dev workflow."
            ),
        })
    except Exception as e:
        return f"Bug report failed: {e}"


@_register_live_execution_tool(
    name="place_order",
    description=(
        "Place an order on HyperLiquid (testnet). Supports market orders (IOC with slippage) "
        "and limit orders (GTC). Optional stop-loss attached. Returns order result with fill details."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "asset": {"type": "string", "description": "Coin symbol: BTC, ETH, SOL"},
            "side": {"type": "string", "description": "Order side: 'buy'/'long' or 'sell'/'short'"},
            "size": {"type": "number", "description": "Position size in coins"},
            "order_type": {"type": "string", "description": "'market' or 'limit'. Default: 'market'"},
            "price": {"type": "number", "description": "Limit price (required for limit orders)"},
            "stop_loss": {"type": "number", "description": "Stop-loss trigger price (optional)"},
            "trade_id": {"type": "string", "description": "Internal trade ID to link this order to"},
        },
        "required": ["asset", "side", "size"],
    },
    permissions={"execution-trader"},
)
def _tool_place_order(params: dict) -> str:
    """Place an order on HyperLiquid testnet.

    Both paper and live modes execute on testnet. Paper mode IS testnet trading.
    """
    from forven.exchange.hyperliquid import market_order, limit_order
    from forven.agents.trade_safeguards import TradeSafeguards, MarketRegime

    asset = params["asset"]
    side = params["side"]
    size = params["size"]
    stop_loss = params.get("stop_loss")
    order_type = params.get("order_type", "market")

    trade_id = params.get("trade_id")
    strategy_id = params.get("strategy_id") or params.get("strategy")
    relaxed_trade_filters = _execution_bool_setting("relaxed_trade_filters_enabled", False)

    # === HARD-STOP GATE ===
    # The kill-switch, daily-loss halt, system pause, and startup-recovery block
    # must bind on the agent's own order path too — not just the scanner's
    # can_open() path. This is intentionally UNCONDITIONAL: relaxed_trade_filters
    # relaxes the regime/rationale safeguards below, but never the hard risk halt.
    # If is_trading_allowed() raises, we let it propagate (fail closed — no order).
    from forven.exchange.risk import is_trading_allowed
    halt_ok, halt_reason = is_trading_allowed()
    if not halt_ok:
        log.warning("place_order BLOCKED by risk halt: %s (trade %s)", halt_reason, trade_id)
        _route_execution_failure(
            "place_order", f"Trading halted: {halt_reason}", trade_id=trade_id, strategy_id=strategy_id
        )
        return json.dumps({"error": f"Trading halted: {halt_reason}", "blocked": True})

    # === TRADE SAFEGUARDS CHECK ===
    if trade_id and not relaxed_trade_filters:
        try:
            with get_db() as conn:
                trade = conn.execute(
                    "SELECT direction, signal_entry_price, signal_data FROM trades WHERE id = ?",
                    (trade_id,),
                ).fetchone()
            
            if trade:
                signal_data = json.loads(trade["signal_data"] or "{}")
                direction = (trade["direction"] or "long").lower()
                regime_str = signal_data.get("regime", "range_bound")
                
                try:
                    regime = MarketRegime(regime_str)
                except ValueError:
                    regime = MarketRegime.RANGE_BOUND
                
                safeguards = TradeSafeguards()
                
                # Regime sanity: block opening a LONG into a confirmed downtrend
                # or a SHORT into a confirmed uptrend (symmetric guard).
                if direction == "long":
                    regime_check = safeguards.check_regime_for_long(regime)
                    if not regime_check.passed:
                        log.warning("SAFEGUARD BLOCKED: %s - Trade %s", regime_check.message, trade_id)
                        _route_execution_failure("place_order", regime_check.message, trade_id=trade_id, strategy_id=strategy_id)
                        return json.dumps({"error": f"SAFEGUARD BLOCKED: {regime_check.message}", "blocked": True})
                elif direction == "short":
                    regime_check = safeguards.check_regime_for_short(regime)
                    if not regime_check.passed:
                        log.warning("SAFEGUARD BLOCKED: %s - Trade %s", regime_check.message, trade_id)
                        _route_execution_failure("place_order", regime_check.message, trade_id=trade_id, strategy_id=strategy_id)
                        return json.dumps({"error": f"SAFEGUARD BLOCKED: {regime_check.message}", "blocked": True})
                
                # Check trade rationale documentation
                rationale = {
                    "direction": direction,
                    "regime": regime_str,
                    "asset": asset,
                    "strategy_id": strategy_id,
                    "invalidation_level": signal_data.get("invalidation_level", 0),
                    "why_direction": signal_data.get("why_direction", ""),
                    "why_asset_validated": signal_data.get("why_asset_validated", "")
                }
                
                # Block if missing critical rationale
                if not rationale.get("why_direction") or not rationale.get("why_asset_validated"):
                    msg = "SAFEGUARD BLOCKED: Missing trade rationale (why_direction or why_asset_validated)"
                    log.warning("%s - Trade %s", msg, trade_id)
                    _route_execution_failure("place_order", msg, trade_id=trade_id, strategy_id=strategy_id)
                    return json.dumps({"error": msg, "blocked": True})
                
                log.info("SAFEGARDS PASSED: regime=%s, direction=%s", regime_str, direction)
        except Exception as e:
            log.warning("Could not run safeguards for trade %s: %s", trade_id, e)
    elif trade_id and relaxed_trade_filters:
        log.warning(
            "SAFEGUARD OVERRIDE: relaxed_trade_filters_enabled=true, skipping regime/rationale checks for trade %s",
            trade_id,
        )
    # === END SAFEGUARDS ===

    log.info("EXECUTION: Placing %s %s %s %.6f (stop=%s)", order_type, side, asset, size, stop_loss)
    log_activity("trade", "execution-trader",
                 f"ORDER: {order_type} {side} {asset} size={size} stop={stop_loss}")

    # Deterministic idempotency key so a retry after an ambiguous/timeout submit
    # reuses the same Hyperliquid client-order-id and is deduped by the exchange,
    # instead of opening a duplicate position. Keyed on the internal trade_id when
    # present (one entry per trade); None when there is no trade_id (no dedupe but
    # the cloid builder handles an empty key gracefully).
    idempotency_key = f"{trade_id}:{order_type}" if trade_id else None

    result = None
    try:
        if order_type == "limit":
            price = params.get("price")
            if not price:
                return "Error: limit orders require a 'price' parameter"
            result = limit_order(
                asset=asset,
                side=side,
                size=size,
                price=price,
                stop_loss_price=stop_loss,
                idempotency_key=idempotency_key,
                testnet=True,
            )
        else:
            result = market_order(
                asset=asset,
                side=side,
                size=size,
                stop_loss_price=stop_loss,
                idempotency_key=idempotency_key,
                testnet=True,
            )
    except Exception as exc:
        message = str(exc)
        _route_execution_failure("place_order", message, trade_id=trade_id, strategy_id=strategy_id)
        return json.dumps({"error": message}, default=str)

    if isinstance(result, dict) and result.get("error"):
        _route_execution_failure(
            "place_order",
            str(result.get("error")),
            trade_id=trade_id,
            strategy_id=strategy_id,
        )
        # Exchange rejected the order: no fill happened. Do NOT write a
        # zero-price fill onto the trade record or broadcast "TRADE OPENED".
        return json.dumps(result, default=str)

    # Update trade record with exchange order ID if trade_id provided
    if trade_id and isinstance(result, dict):
        try:
            fill_price = result.get("entry_price") or result.get("mid", 0)
            slippage = None
            with get_db() as conn:
                trade = conn.execute(
                    "SELECT direction, signal_entry_price FROM trades WHERE id = ?",
                    (trade_id,),
                ).fetchone()
                if trade:
                    direction = (trade["direction"] or "long").lower()
                    signal_price = trade["signal_entry_price"] or 0
                    slippage = None
                    if signal_price:
                        side_for_entry = "buy" if direction == "long" else "sell"
                        slippage = _signed_slippage_bps(signal_price, fill_price, side_for_entry)
                conn.execute(
                    "UPDATE trades SET fill_entry_price=?, entry_price=?, entry_slippage_bps=COALESCE(?, entry_slippage_bps) WHERE id=?",
                    (fill_price, fill_price, slippage, trade_id),
                )
        except Exception as e:
            log.warning("Could not update trade %s with fill: %s", trade_id, e)

    if trade_id:
        try:
            from forven.reporter import broadcast_agent_task
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(broadcast_agent_task(
                    "execution-trader", "🟢 TRADE OPENED", 
                    f"Asset: {asset} | Side: {side} | Size: {size} | Type: {order_type}"
                ))
        except Exception:
            pass

    return json.dumps(result, default=str)


@_register_live_execution_tool(
    name="close_position",
    description=(
        "Close an open position on HyperLiquid with an aggressive IOC market order (3% slippage). "
        "Use for normal exits and emergency closes."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "asset": {"type": "string", "description": "Coin symbol: BTC, ETH, SOL"},
            "size": {"type": "number", "description": "Position size to close"},
            "side": {"type": "string", "description": "'sell' to close long, 'buy' to close short. Default: 'sell'"},
            "trade_id": {"type": "string", "description": "Internal trade ID being closed"},
        },
        "required": ["asset", "size"],
    },
    permissions={"execution-trader"},
)
def _tool_close_position(params: dict) -> str:
    """Close a position on HyperLiquid testnet.

    Both paper and live modes execute on testnet. Paper mode IS testnet trading.
    """
    from forven.exchange.hyperliquid import close_position
    from forven.trade_state import (
        close_trade_record,
        is_local_only_paper_trade,
        mark_trade_pending_close_reconcile,
    )

    asset = params["asset"]
    size = params["size"]
    side = params.get("side", "sell")

    trade_id = params.get("trade_id")
    strategy_id = params.get("strategy_id") or params.get("strategy")

    # A local-only paper trade NEVER reached the exchange — there is no position
    # there to close. Routing it through close_position() returns no fill, marks
    # the trade pending_close_reconcile, and the reconcile sweep then finalizes it
    # as an "unknown"/incomplete close (the bug this fixes). Close it LOCALLY at
    # the current mark instead. Refuse rather than fabricate when no mark is
    # available — never invent a fill price.
    if trade_id:
        try:
            with get_db() as conn:
                _trade_row = conn.execute(
                    "SELECT * FROM trades WHERE id = ?", (str(trade_id),)
                ).fetchone()
            local_trade = dict(_trade_row) if _trade_row else None
        except Exception as exc:
            local_trade = None
            log.warning("close_position: could not load trade %s: %s", trade_id, exc)
        if local_trade and is_local_only_paper_trade(local_trade):
            mark_price = _resolve_paper_mark_price(asset)
            if mark_price is None:
                msg = (
                    f"Cannot close local paper trade {trade_id}: no current mark price "
                    f"for {asset} (refusing to fabricate an exit)."
                )
                log.warning(msg)
                return json.dumps({"error": msg})
            closed = close_trade_record(
                str(trade_id),
                signal_exit_price=mark_price,
                exit_price=mark_price,
                close_reason=str(params.get("close_reason") or "agent_paper_local_close"),
                close_price_source="agent_paper_local",
                only_if_open=True,
            )
            log_activity(
                "trade", "execution-trader",
                f"CLOSE (paper-local) {asset} trade={trade_id} @ {mark_price}",
            )
            return json.dumps(
                {
                    "status": "closed_local_paper",
                    "trade_id": trade_id,
                    "asset": asset,
                    "exit_price": mark_price,
                    "updated": bool((closed or {}).get("updated")),
                },
                default=str,
            )

    log.info("EXECUTION: Closing %s %.6f %s", side, size, asset)
    log_activity("trade", "execution-trader", f"CLOSE: {side} {asset} size={size}")

    try:
        result = close_position(asset, size, side, testnet=True)
    except Exception as exc:
        message = str(exc)
        _route_execution_failure("close_position", message, trade_id=trade_id, strategy_id=strategy_id)
        return json.dumps({"error": message}, default=str)

    if isinstance(result, dict) and result.get("error"):
        _route_execution_failure(
            "close_position",
            str(result.get("error")),
            trade_id=trade_id,
            strategy_id=strategy_id,
        )

    # Update trade record with exit fill if trade_id provided
    if trade_id and isinstance(result, dict):
        try:
            fill_exit_price = result.get("exit_price") or result.get("fill_price")
            exchange_order_id = result.get("order_id") or result.get("orderId") or result.get("oid")
            if fill_exit_price is not None:
                close_price = fill_exit_price
                slippage = None
                with get_db() as conn:
                    trade = conn.execute(
                        "SELECT direction, signal_exit_price FROM trades WHERE id = ?",
                        (trade_id,),
                    ).fetchone()
                    if trade:
                        direction = (trade["direction"] or "long").lower()
                        signal_price = trade["signal_exit_price"] or 0
                        slippage = None
                        if signal_price:
                            side_for_exit = "sell" if direction == "long" else "buy"
                            slippage = _signed_slippage_bps(signal_price, close_price, side_for_exit)
                    conn.execute(
                        "UPDATE trades SET fill_exit_price=?, exit_price=?, exit_slippage_bps=COALESCE(?, exit_slippage_bps) WHERE id=?",
                        (close_price, close_price, slippage, trade_id),
                    )
            else:
                pending_signal_data = {
                    "exit_exchange_order_id": str(exchange_order_id) if exchange_order_id is not None else None,
                }
                if result.get("close_price") is not None:
                    pending_signal_data["pending_close_requested_execution_price"] = result.get("close_price")
                if result.get("mid") is not None:
                    pending_signal_data["pending_close_mid_price"] = result.get("mid")
                mark_trade_pending_close_reconcile(
                    str(trade_id),
                    close_reason=str(params.get("close_reason") or "execution_close_requested"),
                    close_price_source="execution_close_requested",
                    extra_signal_data=pending_signal_data,
                )
        except Exception as e:
            log.warning("Could not update trade %s with close fill: %s", trade_id, e)

    if trade_id and isinstance(result, dict):
        try:
            from forven.reporter import broadcast_agent_task
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(broadcast_agent_task(
                    "execution-trader", "🔴 TRADE CLOSED", 
                    f"Asset: {asset} | Side: {side} | Size: {size}"
                ))
        except Exception:
            pass

    return json.dumps(result, default=str)


@register_tool(
    name="get_exchange_positions",
    description="Get all open positions and margin summary from HyperLiquid testnet.",
    input_schema={"type": "object", "properties": {}},
    permissions={"execution-trader"},
)
def _tool_get_exchange_positions() -> str:
    """Get open positions from HyperLiquid testnet."""
    from forven.exchange.hyperliquid import get_positions
    result = get_positions(testnet=True)

    positions = result.get("positions", [])
    if not positions:
        return "No open positions on HyperLiquid testnet."

    parts = ["Open positions:"]
    for pos in positions:
        p = pos.get("position", pos)
        coin = p.get("coin", "")
        szi = float(p.get("szi", 0))
        entry_px = p.get("entryPx", "?")
        unrealized_pnl = p.get("unrealizedPnl", "?")
        leverage = p.get("leverage", {}).get("value", "?")
        if szi != 0:
            direction = "LONG" if szi > 0 else "SHORT"
            parts.append(
                f"  {coin}: {direction} {abs(szi)} @ ${entry_px} | "
                f"uPnL: ${unrealized_pnl} | lev: {leverage}x"
            )
    return "\n".join(parts)


@register_tool(
    name="get_account_info",
    description="Get account equity, margin used, and available balance from HyperLiquid testnet.",
    input_schema={"type": "object", "properties": {}},
    permissions={"execution-trader"},
)
def _tool_get_account_info() -> str:
    """Get account info from HyperLiquid testnet."""
    from forven.exchange.hyperliquid import get_account_value
    result = get_account_value(testnet=True)
    return json.dumps({
        "account_value": f"${result['accountValue']:,.2f}",
        "margin_used": f"${result['totalMarginUsed']:,.2f}",
        "notional_position": f"${result['totalNtlPos']:,.2f}",
        "available_usd": f"${result['totalRawUsd']:,.2f}",
    }, indent=2)


@register_tool(
    name="cancel_orders",
    description="Cancel open orders on HyperLiquid. Optionally filter by asset.",
    input_schema={
        "type": "object",
        "properties": {
            "asset": {"type": "string", "description": "Coin symbol to filter (optional — cancels all if omitted)"},
        },
    },
    permissions={"execution-trader"},
)
def _tool_cancel_orders(asset: str | None = None) -> str:
    """Cancel open orders on HyperLiquid testnet."""
    from forven.exchange.hyperliquid import cancel_all_orders

    results = cancel_all_orders(asset, testnet=True)
    if not results:
        return "No open orders to cancel."

    log_activity("trade", "execution-trader",
                 f"CANCELLED {len(results)} orders" + (f" for {asset}" if asset else ""))
    return f"Cancelled {len(results)} orders: {json.dumps(results, default=str)}"


@register_tool(
    name="update_trade",
    description=(
        "Update a trade record in SQLite with actual fill data from the exchange. "
        "Use after placing/closing an order to reconcile fill prices and slippage."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "trade_id": {"type": "string", "description": "The trade ID to update"},
            "fill_price": {"type": "number", "description": "Actual fill price from the exchange"},
            "fill_kind": {"type": "string", "description": "Which leg to update: 'entry' or 'exit'. Auto-detected from trade status if omitted."},
            "signal_price": {"type": "number", "description": "Optional signal price override for slippage calculation"},
            "exchange_order_id": {"type": "string", "description": "Order ID from HyperLiquid"},
            "notes": {"type": "string", "description": "Execution notes (slippage, fill quality, etc.)"},
        },
        "required": ["trade_id"],
    },
    permissions={"execution-trader"},
)
def _tool_update_trade(params: dict) -> str:
    """Update a trade record with actual fill data from the exchange."""
    trade_id = params["trade_id"]
    fill_price = params.get("fill_price")
    fill_kind = params.get("fill_kind")
    signal_price_override = params.get("signal_price")
    exchange_order_id = params.get("exchange_order_id")
    notes = params.get("notes")

    updates = []
    values = []
    updated_fields = []

    with get_db() as conn:
        row = conn.execute(
            "SELECT id, status, direction, signal_data, signal_entry_price, signal_exit_price FROM trades WHERE id = ?",
            (trade_id,),
        ).fetchone()
        if not row:
            return f"Trade {trade_id} not found."

        trade = dict(row)
        direction = (trade.get("direction") or "long").lower()

        # Merge signal_data updates in Python so we only SET signal_data once.
        signal_data_raw = trade.get("signal_data")
        if isinstance(signal_data_raw, str):
            try:
                signal_data = json.loads(signal_data_raw) if signal_data_raw else {}
            except json.JSONDecodeError:
                signal_data = {}
        elif isinstance(signal_data_raw, dict):
            signal_data = signal_data_raw
        else:
            signal_data = {}

        if exchange_order_id:
            signal_data["exchange_order_id"] = exchange_order_id
            updated_fields.append("exchange_order_id")
        if notes:
            signal_data["execution_notes"] = notes
            updated_fields.append("execution_notes")

        if fill_price is not None:
            if fill_kind not in ("entry", "exit"):
                fill_kind = "entry" if trade.get("status") == "OPEN" else "exit"

            if fill_kind == "entry":
                updates.extend(["fill_entry_price = ?", "entry_price = ?"])
                values.extend([fill_price, fill_price])
                updated_fields.extend(["fill_entry_price", "entry_price"])

                signal_price = signal_price_override or trade.get("signal_entry_price")
                if signal_price_override is not None:
                    updates.append("signal_entry_price = ?")
                    values.append(signal_price_override)
                    updated_fields.append("signal_entry_price")
                if signal_price:
                    side = "buy" if direction == "long" else "sell"
                    slippage = _signed_slippage_bps(float(signal_price), float(fill_price), side)
                    updates.append("entry_slippage_bps = ?")
                    values.append(slippage)
                    updated_fields.append("entry_slippage_bps")

            else:
                updates.extend(["fill_exit_price = ?", "exit_price = ?"])
                values.extend([fill_price, fill_price])
                updated_fields.extend(["fill_exit_price", "exit_price"])

                signal_price = signal_price_override or trade.get("signal_exit_price")
                if signal_price_override is not None:
                    updates.append("signal_exit_price = ?")
                    values.append(signal_price_override)
                    updated_fields.append("signal_exit_price")
                if signal_price:
                    side = "sell" if direction == "long" else "buy"
                    slippage = _signed_slippage_bps(float(signal_price), float(fill_price), side)
                    updates.append("exit_slippage_bps = ?")
                    values.append(slippage)
                    updated_fields.append("exit_slippage_bps")

        if exchange_order_id or notes:
            updates.append("signal_data = ?")
            values.append(json.dumps(signal_data))
            updated_fields.append("signal_data")

        if not updates:
            return "Nothing to update."

        values.append(trade_id)
        result = conn.execute(
            f"UPDATE trades SET {', '.join(updates)} WHERE id = ?",
            values,
        )
        if result.rowcount == 0:
            return f"Trade {trade_id} not found."

    log.info("Updated trade %s (%s)", trade_id, ", ".join(updated_fields))
    return f"Updated trade {trade_id}: {', '.join(updated_fields)}"


def _signed_slippage_bps(signal_price: float, fill_price: float, side: str) -> float:
    """Signed slippage in bps where positive means adverse execution."""
    if signal_price <= 0:
        return 0.0
    if side == "buy":
        return round((fill_price - signal_price) / signal_price * 10_000, 6)
    return round((signal_price - fill_price) / signal_price * 10_000, 6)
