"""Out-of-process execution of UNTRUSTED strategy signal generation (Phase 2).

The execution kernel (:mod:`forven.strategies.execution_kernel`) is first-party
and trusted; the only untrusted code on the path is a strategy's signal logic
(``generate_signal`` / ``generate_signals``), which the backtest/scanner reach via
:func:`forven.strategies.backtest._vectorized_directional_signals`. Running that
in-process means a strategy that slips past the AST guard gets the host's full
environment (the live HyperLiquid key, the Fernet key) and unrestricted FS/network.

This module moves exactly that step into a subprocess that:

  * inherits a **secret-free** environment (``build_subprocess_env`` — same
    allowlist as the run_code / MCP sandboxes),
  * has **network egress denied** (the strategy can compute, not exfiltrate),
  * is confined to a throwaway working directory,
  * is memory/CPU/process-capped (Win32 Job Object / POSIX rlimit, reused from
    :mod:`forven.sandbox`).

Parity is by construction: the worker builds the same strategy (via the registry)
and runs the same ``generate_signals`` + normalization the in-process path runs, so
the produced :class:`DirectionalSignals` are identical — only the process boundary
(and, crucially, WHICH process imports the untrusted strategy) differs.

NOTE (Phase 2 status): this is the proven isolation primitive. Wiring it into the
backtest/scanner hot paths — so the parent stops importing custom strategy code and
delegates per-bar execution too — is the remaining increment (see
docs/security-hardening-plan.md).

Transport is parquet, never pickle: the parent writes the OHLCV frame, the worker
writes four boolean signal columns back, and the parent reads them as *data* and
re-validates the schema. A compromised worker therefore cannot achieve code
execution in the trusted parent (the failure mode pickle transport would have).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

from forven.sandbox import (
    IS_WINDOWS,
    PYTHON_EXE,
    REPO_ROOT,
    _BLAS_THREAD_ENV,
    _assign_pid_to_job,
    _build_posix_preexec,
    _close_job,
    _create_windows_job_object,
)
from forven.security.env_allowlist import build_subprocess_env
from forven.strategies.base import DirectionalSignals

# A worker spawns with this var set; the flag check in backtest treats it as
# "already isolated" so the worker runs the signal-gen IN-PROCESS instead of
# recursively spawning another worker.
WORKER_ENV_FLAG = "FORVEN_IN_STRATEGY_WORKER"

_SIGNAL_COLUMNS = ("long_entries", "long_exits", "short_entries", "short_exits")

DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_MAX_MEMORY_MB = 1024  # signal-gen over a full history needs more than run_code's 512


class StrategyWorkerError(RuntimeError):
    """Raised when the isolated worker fails to produce valid signals."""


# ---------------------------------------------------------------------------
# Worker side (runs in the locked-down subprocess)
# ---------------------------------------------------------------------------

def _install_network_deny() -> None:
    """Best-effort: make outbound sockets raise inside the worker.

    Defense-in-depth — the env is already secret-free, so there is nothing to
    exfiltrate, and the AST guard blocks ``import socket`` in strategy code. This
    closes the residual where a guard bypass reaches a socket via a transitive
    import. Loopback is left alone (some libs probe it); only routable connects
    are refused. Installed AFTER forven's own (trusted) imports so it never breaks
    them — it gates the untrusted strategy code that runs next.
    """
    try:
        import socket
    except Exception:
        return

    def _deny(*_a, **_k):  # noqa: ANN002, ANN003
        raise OSError("network access is disabled in the strategy sandbox")

    _real_socket = socket.socket

    class _GuardedSocket(_real_socket):  # type: ignore[misc, valid-type]
        def connect(self, address):  # noqa: ANN001
            self._refuse_if_routable(address)
            return super().connect(address)

        def connect_ex(self, address):  # noqa: ANN001
            self._refuse_if_routable(address)
            return super().connect_ex(address)

        @staticmethod
        def _refuse_if_routable(address) -> None:  # noqa: ANN001
            host = address[0] if isinstance(address, tuple) and address else ""
            h = str(host).strip().lower()
            if h in ("127.0.0.1", "::1", "localhost", ""):
                return
            raise OSError("network access is disabled in the strategy sandbox")

    socket.socket = _GuardedSocket  # type: ignore[misc, assignment]
    socket.create_connection = _deny  # type: ignore[assignment]


def _run_worker(workdir: Path) -> int:
    """Entry point inside the subprocess. Reads request + frame, runs the SAME
    signal-gen the in-process path uses, writes boolean signal columns back."""
    status_path = workdir / "status.json"
    try:
        request = json.loads((workdir / "request.json").read_text(encoding="utf-8"))
        df = pd.read_parquet(workdir / "in.parquet")

        # Import the (trusted, first-party) forven modules BEFORE denying network so
        # their import-time work is never blocked. THEN deny network, THEN import +
        # build + run the (untrusted) strategy: registry.discover() imports the
        # strategy module HERE, so the AST guard runs and any custom top-level code
        # executes in THIS confined, secret-free, network-denied process — never the
        # trusted parent. generate_signals() is the untrusted signal logic.
        from forven.strategies import registry
        from forven.strategies.backtest import _normalize_directional_signal_payload

        _install_network_deny()

        registry.discover()
        strategy_type = str(request["strategy_type"])
        cls = registry._TYPE_MAP.get(strategy_type)
        if cls is None:
            raise StrategyWorkerError(f"unknown strategy type {strategy_type!r}")
        strat = cls("isolated", dict(request.get("params") or {}))
        payload = strat.generate_signals(df)
        signals = _normalize_directional_signal_payload(
            payload,
            df.index,
            trade_mode=str(request.get("trade_mode") or "long_only"),
            default_direction=str(request.get("default_direction") or "long"),
        )

        out = pd.DataFrame(
            {
                "long_entries": signals.long_entries.astype(bool).to_numpy(),
                "long_exits": signals.long_exits.astype(bool).to_numpy(),
                "short_entries": signals.short_entries.astype(bool).to_numpy(),
                "short_exits": signals.short_exits.astype(bool).to_numpy(),
            },
            index=df.index,
        )
        out.to_parquet(workdir / "out.parquet")
        status_path.write_text(json.dumps({"ok": True}), encoding="utf-8")
        return 0
    except BaseException as exc:  # noqa: BLE001 — report ANY failure as structured status
        try:
            status_path.write_text(
                json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"[:2000]}),
                encoding="utf-8",
            )
        except Exception:
            pass
        return 1


# ---------------------------------------------------------------------------
# Parent side (runs in the trusted host process)
# ---------------------------------------------------------------------------

def _spawn_worker(workdir: Path, *, timeout: int, max_memory_mb: int) -> dict:
    """Spawn the worker subprocess with the sandbox's env-scrub + resource caps.
    Returns {"returncode", "timed_out", "stderr"}."""
    existing_pythonpath = str(os.environ.get("PYTHONPATH") or "").strip()
    repo_root = str(REPO_ROOT)
    pythonpath = repo_root if not existing_pythonpath else f"{repo_root}{os.pathsep}{existing_pythonpath}"
    extra = {"PYTHONPATH": pythonpath, WORKER_ENV_FLAG: "1"}
    for _k, _v in _BLAS_THREAD_ENV.items():
        extra[_k] = os.environ.get(_k, _v)

    cmd = [PYTHON_EXE, "-m", "forven.sandbox.strategy_worker", str(workdir)]

    if IS_WINDOWS:
        env = build_subprocess_env(extra=extra)
        job_handle, kernel32 = _create_windows_job_object(max_memory_mb)
        try:
            proc = subprocess.Popen(
                cmd, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=env, cwd=str(workdir),
            )
            if job_handle and kernel32:
                _assign_pid_to_job(job_handle, kernel32, proc.pid)
            try:
                _out, err = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                    proc.communicate(timeout=2)
                except Exception:
                    pass
                return {"returncode": -1, "timed_out": True, "stderr": "timed out"}
            return {"returncode": proc.returncode, "timed_out": False, "stderr": err or ""}
        finally:
            _close_job(job_handle, kernel32)
    else:
        # POSIX: minimal env + rlimit preexec, same as the run_code sandbox. Keep
        # the scrubbed allowlist env and add PATH/HOME the interpreter needs.
        env = build_subprocess_env(extra=extra)
        env.setdefault("PATH", os.environ.get("PATH", "/usr/bin:/usr/local/bin"))
        env.setdefault("HOME", tempfile.gettempdir())
        try:
            proc = subprocess.run(
                cmd, shell=False, capture_output=True, text=True, timeout=timeout,
                env=env, cwd=str(workdir), preexec_fn=_build_posix_preexec(max_memory_mb),
            )
            return {"returncode": proc.returncode, "timed_out": False, "stderr": proc.stderr or ""}
        except subprocess.TimeoutExpired:
            return {"returncode": -1, "timed_out": True, "stderr": "timed out"}


def compute_directional_signals_isolated(
    df: pd.DataFrame,
    strategy_type: str,
    params: dict,
    *,
    trade_mode: str,
    default_direction: str = "long",
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    max_memory_mb: int = DEFAULT_MAX_MEMORY_MB,
) -> DirectionalSignals:
    """Build the strategy and run its ``generate_signals(df)`` in an isolated,
    secret-free, network-denied subprocess, returning normalized DirectionalSignals.

    The strategy module is imported INSIDE the worker (under the AST guard), so a
    custom/untrusted strategy never executes in the trusted parent. Output is
    identical to building the same strategy in-process and normalizing its
    ``generate_signals`` payload — only the trust boundary differs. Raises
    :class:`StrategyWorkerError` on timeout, worker error, or malformed output; the
    caller does NOT silently fall back to in-process execution (that would defeat
    the isolation — fail closed).
    """
    with tempfile.TemporaryDirectory(prefix="forven_strat_") as tmp:
        workdir = Path(tmp)
        try:
            df.to_parquet(workdir / "in.parquet")
        except Exception as exc:  # a non-serializable frame is a programming error
            raise StrategyWorkerError(f"failed to serialize input frame: {exc}") from exc
        (workdir / "request.json").write_text(
            json.dumps(
                {
                    "strategy_type": strategy_type,
                    "params": params or {},
                    "trade_mode": trade_mode,
                    "default_direction": default_direction,
                }
            ),
            encoding="utf-8",
        )

        result = _spawn_worker(workdir, timeout=timeout, max_memory_mb=max_memory_mb)

        status_path = workdir / "status.json"
        status = {}
        if status_path.exists():
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
            except Exception:
                status = {}

        if result["timed_out"]:
            raise StrategyWorkerError(
                f"isolated signal generation for {strategy_type!r} timed out after {timeout}s"
            )
        if not status.get("ok"):
            detail = status.get("error") or (result.get("stderr") or "").strip()[:2000] or "unknown error"
            raise StrategyWorkerError(f"isolated signal generation for {strategy_type!r} failed: {detail}")

        out_path = workdir / "out.parquet"
        if not out_path.exists():
            raise StrategyWorkerError(f"isolated worker for {strategy_type!r} produced no output")
        return _read_and_validate_signals(out_path, df.index, strategy_type)


def _read_and_validate_signals(out_path: Path, index: pd.Index, strategy_type: str) -> DirectionalSignals:
    """Read the worker's parquet output as DATA and re-validate its schema before
    trusting it. Parquet carries no executable payload, and we never accept a
    column set / length / index we did not ask for."""
    out = pd.read_parquet(out_path)
    missing = [c for c in _SIGNAL_COLUMNS if c not in out.columns]
    if missing:
        raise StrategyWorkerError(
            f"isolated worker for {strategy_type!r} returned columns {list(out.columns)} "
            f"(missing {missing})"
        )
    if len(out) != len(index):
        raise StrategyWorkerError(
            f"isolated worker for {strategy_type!r} returned {len(out)} rows, expected {len(index)}"
        )
    if not out.index.equals(index):
        # Align by position (the worker preserved order); a mismatched index is a
        # corruption signal, not something to silently reindex.
        raise StrategyWorkerError(f"isolated worker for {strategy_type!r} returned a misaligned index")
    return DirectionalSignals(
        long_entries=out["long_entries"].astype(bool),
        long_exits=out["long_exits"].astype(bool),
        short_entries=out["short_entries"].astype(bool),
        short_exits=out["short_exits"].astype(bool),
    )


if __name__ == "__main__":
    _wd = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    raise SystemExit(_run_worker(_wd))
