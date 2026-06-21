"""Phase 2 (P2-T09) — Sandbox API: runs, scan, test, kill.

Backs the SvelteKit ``/sandbox`` page (P2-T12..T15). All endpoints are
auth-protected by the global ``ApiKeyMiddleware``. The shape of every
response is the contract — any change must be matched in
``frontend/src/lib/api/sandbox.ts`` (P2-T11).

Endpoints:

- ``GET  /api/sandbox/runs``               — paginated history
- ``GET  /api/sandbox/runs/{run_id}``      — full row including findings
- ``POST /api/sandbox/scan``               — AST-only pre-flight (always available)
- ``POST /api/sandbox/test``               — hello-world end-to-end smoke
- ``POST /api/sandbox/runs/{run_id}/kill`` — kill an active run

503 semantics: every endpoint EXCEPT ``/scan`` returns 503 when
``sandbox_enabled=False`` — AST scan is read-only and pure, no spawn.
"""
from __future__ import annotations

import json
import logging
import tempfile
import textwrap
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from forven.api_security import require_operator_access
from forven.db import get_db
from forven.sandbox.ast_guard import scan_file
from forven.sandbox.strategy_adapter import get_sandbox_config
from forven.sandbox.subprocess_runner import (
    _persist_scan_only_row,  # noqa: PLC2701 — internal but stable
    run_strategy_in_subprocess,
)

log = logging.getLogger("forven.routers.sandbox")

router = APIRouter(prefix="/api/sandbox", tags=["sandbox"], dependencies=[Depends(require_operator_access)])


# In-memory registry of currently-active subprocess runs. Maps row id →
# (Popen-like obj, optional Job handle). Populated only by future "live"
# launchers; an empty registry means "no kill targets" → /kill returns 404
# for completed runs.
_ACTIVE_RUNS: dict[int, dict[str, Any]] = {}


def register_active_run(run_id: int, *, kill_callable) -> None:
    """Hook used by long-running launchers (future) to expose a kill switch.

    The router only knows how to call ``kill_callable()`` — it doesn't try
    to manage Popen or Job handles directly. Keeps the router decoupled
    from runtime details.
    """
    _ACTIVE_RUNS[run_id] = {"kill": kill_callable}


def unregister_active_run(run_id: int) -> None:
    _ACTIVE_RUNS.pop(run_id, None)


def _require_enabled() -> None:
    """Guard for endpoints that need the sandbox to be on. Raise 503 if off."""
    cfg = get_sandbox_config()
    if not cfg["enabled"]:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "sandbox_disabled",
                "message": (
                    "Sandbox is disabled. Enable it under Settings → Sandbox."
                ),
            },
        )


# --------------------------------------------------------------------------- #
# GET /api/sandbox/runs                                                        #
# --------------------------------------------------------------------------- #


@router.get("/runs")
def list_runs(
    strategy_id: str | None = Query(None, description="Filter by strategy id"),
    kind: Literal["scan", "run"] | None = Query(None, description="scan or run"),
    timed_out: bool | None = Query(None, description="Only timed-out runs"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Return the most-recent sandbox runs, paginated, newest-first."""
    _require_enabled()
    where: list[str] = []
    params: list[Any] = []
    if strategy_id is not None:
        where.append("strategy_id = ?")
        params.append(strategy_id)
    if kind is not None:
        where.append("kind = ?")
        params.append(kind)
    if timed_out is not None:
        where.append("timed_out = ?")
        params.append(1 if timed_out else 0)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    with get_db() as conn:
        total_row = conn.execute(
            f"SELECT COUNT(*) AS n FROM sandbox_runs {where_sql}", params
        ).fetchone()
        total = int(total_row["n"]) if total_row else 0

        rows = conn.execute(
            f"""SELECT id, strategy_id, kind, child_pid, started_at, ended_at,
                       exit_code, memory_peak_mb, cpu_seconds, wall_seconds,
                       timed_out, error, created_at
                  FROM sandbox_runs
                  {where_sql}
                  ORDER BY id DESC
                  LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        ).fetchall()

    return {
        "rows": [dict(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# --------------------------------------------------------------------------- #
# GET /api/sandbox/runs/{run_id}                                               #
# --------------------------------------------------------------------------- #


@router.get("/runs/{run_id}")
def get_run(run_id: int) -> dict[str, Any]:
    """Return one full row, including JSON findings columns parsed."""
    _require_enabled()
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM sandbox_runs WHERE id = ?", (run_id,)
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail={"error": "run_not_found"})

    out = dict(row)
    # Parse JSON-text columns into structured objects so the client doesn't
    # have to do JSON.parse on every render.
    for col in ("ast_findings_json", "security_events_json"):
        raw = out.get(col)
        out[col] = None
        parsed_key = col.removesuffix("_json")
        if raw:
            try:
                out[parsed_key] = json.loads(raw)
            except json.JSONDecodeError:
                out[parsed_key] = None
        else:
            out[parsed_key] = None
    out["active"] = run_id in _ACTIVE_RUNS
    return out


# --------------------------------------------------------------------------- #
# POST /api/sandbox/scan  (always available — AST is read-only)                #
# --------------------------------------------------------------------------- #


class _ScanBody(BaseModel):
    path: str = Field(..., description="Filesystem path to a .py strategy file")
    strategy_id: str | None = Field(None, description="Optional id for telemetry")


@router.post("/scan")
def post_scan(body: _ScanBody) -> dict[str, Any]:
    """Run the static AST guard against a file. Persists a scan-only row."""
    p = Path(body.path)
    if not p.exists():
        raise HTTPException(
            status_code=404,
            detail={"error": "path_not_found", "path": str(p)},
        )
    if not p.is_file():
        raise HTTPException(
            status_code=400,
            detail={"error": "not_a_file", "path": str(p)},
        )

    report = scan_file(p)
    findings_serialised = [
        {
            "kind": f.kind,
            "lineno": f.lineno,
            "col": f.col,
            "message": f.message,
            "node_repr": f.node_repr,
        }
        for f in report.findings
    ]

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    row_id = _persist_scan_only_row(
        strategy_id=body.strategy_id,
        started_at=now,
        ended_at=now,
        ast_findings=findings_serialised,
        error=None if report.ok else "ast_block",
    )

    return {
        "ok": report.ok,
        "findings": findings_serialised,
        "file_size_bytes": report.file_size_bytes,
        "line_count": report.line_count,
        "row_id": row_id,
    }


# --------------------------------------------------------------------------- #
# POST /api/sandbox/test  (hello-world smoke)                                  #
# --------------------------------------------------------------------------- #

_HELLO_WORLD_STRATEGY = textwrap.dedent(
    """
    \"\"\"Hello-world fixture used by /api/sandbox/test.\"\"\"
    def generate_signal(ohlcv):
        return {
            "trades": [{"action": "noop", "reason": "smoke"}],
            "stats": {"hello": "world", "bars": len((ohlcv or {}).get("bars", []))},
        }
    """
).strip()


@router.post("/test")
def post_test() -> dict[str, Any]:
    """Run the bundled hello-world strategy through the sandbox.

    Useful as a Settings → Sandbox panel "Test sandbox" button — verifies
    that subprocess spawn, env whitelist, AST guard, and persistence are
    all wired up. Doesn't require the operator to have a real strategy
    file on disk.
    """
    _require_enabled()
    cfg = get_sandbox_config()

    with tempfile.TemporaryDirectory(prefix="forven_sandbox_test_") as td:
        path = Path(td) / "hello_world_test.py"
        path.write_text(_HELLO_WORLD_STRATEGY, encoding="utf-8")

        result = run_strategy_in_subprocess(
            path,
            {"bars": []},
            timeout_s=float(cfg["wall_s"]),
            mem_mb=cfg["mem_mb"],
            cpu_s=cfg["cpu_s"],
            strategy_id="__sandbox_self_test__",
        )

    return {
        "ok": result.exit_code == 0 and not result.timed_out,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "wall_seconds": result.wall_seconds,
        "memory_peak_mb": result.memory_peak_mb,
        "stdout_payload": result.stdout_payload,
        "stderr_text": (result.stderr_text or "")[:2000],
    }


# --------------------------------------------------------------------------- #
# POST /api/sandbox/runs/{run_id}/kill                                         #
# --------------------------------------------------------------------------- #


@router.post("/runs/{run_id}/kill")
def post_kill(run_id: int) -> dict[str, Any]:
    """Kill an active run. 404 if the run already completed."""
    _require_enabled()

    entry = _ACTIVE_RUNS.get(run_id)
    if entry is None:
        # Either the run never existed or it already terminated. Distinguish
        # the two so the UI can show the right message.
        with get_db() as conn:
            row = conn.execute(
                "SELECT id, ended_at FROM sandbox_runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail={"error": "run_not_found"})
        raise HTTPException(
            status_code=409,
            detail={
                "error": "run_not_active",
                "message": "Run has already completed.",
            },
        )

    try:
        entry["kill"]()
    except Exception as exc:  # noqa: BLE001
        log.warning("kill callback raised for run_id=%s: %s", run_id, exc)
        raise HTTPException(
            status_code=500,
            detail={"error": "kill_failed", "message": str(exc)},
        ) from exc

    unregister_active_run(run_id)
    return {"ok": True, "run_id": run_id, "killed": True}
