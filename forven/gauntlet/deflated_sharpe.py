"""Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2014).

Corrects the in-sample Sharpe for SELECTION bias: a deployed strategy is the
best of N optimizer trials, so its observed Sharpe is upward-biased. DSR is the
probability the *true* Sharpe exceeds the selection-adjusted benchmark, given the
number of trials, the sample length, and the return skew/kurtosis.

DSR is in [0, 1]; values near 1 mean the edge is unlikely to be a selection
artifact (~>=0.95 is the conventional "significant" bar). This is the suite's
guard against the optimizer-overfitting blind spot (no untouched holdout).

Observe-first wiring: the value is surfaced as an informational metric; the
reject gate is OPT-IN (robustness_thresholds.deflated_sharpe_gate_enabled,
default off) so its behaviour can be watched before it blocks anything.

Note: returns scale cancels in the Sharpe / skew / kurtosis, so per-trade pnl in
ratio or percent units gives the same DSR — no unit normalisation needed.
"""

from __future__ import annotations

import math

# Euler-Mascheroni constant (used in the expected-maximum-Sharpe estimator).
_EULER_GAMMA = 0.5772156649015329


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    # scipy is a hard dep elsewhere in the gauntlet; use it for the inverse CDF.
    from scipy.stats import norm

    return float(norm.ppf(p))


def probabilistic_sharpe_ratio(
    sr_hat: float, sr_benchmark: float, n_obs: int, skew: float, kurt: float
) -> float:
    """P(true Sharpe > sr_benchmark) given the observed (per-period) Sharpe.

    ``kurt`` is NON-excess (normal == 3). ``sr_hat`` is the per-period Sharpe
    (mean/std of per-period returns), NOT annualised.
    """
    if n_obs < 2:
        return 0.0
    denom = 1.0 - skew * sr_hat + ((kurt - 1.0) / 4.0) * (sr_hat ** 2)
    if denom <= 0:
        return 0.0
    z = (sr_hat - sr_benchmark) * math.sqrt(n_obs - 1) / math.sqrt(denom)
    return float(_norm_cdf(z))


def expected_max_sharpe(trial_sharpe_var: float, n_trials: int) -> float:
    """Expected maximum per-period Sharpe under the null across ``n_trials`` trials."""
    n = max(int(n_trials), 1)
    if n <= 1 or trial_sharpe_var <= 0:
        return 0.0
    sd = math.sqrt(trial_sharpe_var)
    a = _norm_ppf(1.0 - 1.0 / n)
    b = _norm_ppf(1.0 - 1.0 / (n * math.e))
    return float(sd * ((1.0 - _EULER_GAMMA) * a + _EULER_GAMMA * b))


def deflated_sharpe_ratio(
    returns: list[float], n_trials: int, trial_sharpe_var: float | None = None
) -> dict:
    """Compute the Deflated Sharpe Ratio from a list of per-period returns.

    ``trial_sharpe_var`` is the cross-trial variance of the optimizer's Sharpe
    estimates; when unavailable (only the winning trial is persisted) we fall
    back to the Sharpe-estimator variance as a documented proxy.
    """
    rs = [float(r) for r in returns if r is not None and math.isfinite(float(r))]
    t = len(rs)
    if t < 2:
        return {"dsr": None, "reason": "insufficient_returns", "n_obs": t}

    mean_r = sum(rs) / t
    var_r = sum((r - mean_r) ** 2 for r in rs) / t  # population variance
    sd_r = math.sqrt(var_r)
    if sd_r <= 1e-12:
        return {"dsr": None, "reason": "zero_variance", "n_obs": t}
    sr_hat = mean_r / sd_r

    # Sample skewness / non-excess kurtosis (scale-invariant).
    if t >= 3:
        m3 = sum((r - mean_r) ** 3 for r in rs) / t
        skew = m3 / (sd_r ** 3)
    else:
        skew = 0.0
    if t >= 4:
        m4 = sum((r - mean_r) ** 4 for r in rs) / t
        kurt = m4 / (sd_r ** 4)  # non-excess (normal == 3)
    else:
        kurt = 3.0

    if trial_sharpe_var is not None and trial_sharpe_var > 0:
        v = float(trial_sharpe_var)
        v_source = "trials"
    else:
        # Variance of the Sharpe estimator (Lo 2002, skew/kurt-adjusted) as a
        # conservative stand-in for cross-trial dispersion.
        v = max((1.0 - skew * sr_hat + ((kurt - 1.0) / 4.0) * (sr_hat ** 2)) / (t - 1), 1e-9)
        v_source = "estimator_proxy"

    sr0 = expected_max_sharpe(v, n_trials)
    dsr = probabilistic_sharpe_ratio(sr_hat, sr0, t, skew, kurt)
    return {
        "dsr": round(float(dsr), 5),
        "sr_hat": round(float(sr_hat), 5),
        "sr0_benchmark": round(float(sr0), 5),
        "n_obs": t,
        "n_trials": int(max(n_trials, 1)),
        "skew": round(float(skew), 4),
        "kurtosis": round(float(kurt), 4),
        "trial_var_source": v_source,
    }


def _latest_n_trials(opt_metrics: dict | None, opt_config: dict | None, default_trials: int) -> int:
    for blob in (opt_metrics, opt_config):
        if isinstance(blob, dict) and blob.get("n_trials") is not None:
            try:
                n = int(float(blob.get("n_trials")))
                if n > 0:
                    return n
            except (TypeError, ValueError):
                continue
    return max(int(default_trials), 1)


def compute_strategy_dsr(strategy_id: str, *, default_trials: int | None = None) -> dict | None:
    """Best-effort DSR for a strategy's latest backtest. Returns None on any issue.

    Pulls per-trade returns from the latest backtest result and the trial count
    from the latest optimization result (falling back to the configured default).
    Never raises — DSR is advisory, not on the critical path.
    """
    try:
        import json

        from forven.db import get_db

        if default_trials is None:
            try:
                from forven.policy import load_pipeline_config

                rob = load_pipeline_config().get("robustness_thresholds", {}) or {}
                default_trials = int(rob.get("deflated_sharpe_default_trials", 50) or 50)
            except Exception:
                default_trials = 50

        with get_db() as conn:
            bt = conn.execute(
                """SELECT result_id FROM backtest_results
                   WHERE strategy_id = ?
                     AND LOWER(TRIM(COALESCE(result_type, 'backtest'))) = 'backtest'
                     AND (deleted_at IS NULL OR TRIM(COALESCE(deleted_at, '')) = '')
                   ORDER BY datetime(created_at) DESC LIMIT 1""",
                (strategy_id,),
            ).fetchone()
            opt = conn.execute(
                """SELECT metrics_json, config_json FROM backtest_results
                   WHERE strategy_id = ?
                     AND LOWER(TRIM(COALESCE(result_type, ''))) = 'optimization'
                     AND (deleted_at IS NULL OR TRIM(COALESCE(deleted_at, '')) = '')
                   ORDER BY datetime(created_at) DESC LIMIT 1""",
                (strategy_id,),
            ).fetchone()

        if not bt:
            return None
        from forven.api_core import get_backtest_result

        detail = get_backtest_result(bt["result_id"], remote_skip=True)
        trades = detail.get("trades") if isinstance(detail, dict) else None
        if not isinstance(trades, list) or not trades:
            return None

        returns: list[float] = []
        for tr in trades:
            if not isinstance(tr, dict):
                continue
            # Prefer the scale-invariant per-trade RETURN fields. ``pnl`` is compounded
            # dollars off a growing equity base — a TIME-VARYING scale that distorts
            # sr_hat/skew/kurt (and thus the DSR + its SR0 benchmark). ``return_pct``
            # (= ratio*100, present on every normalized trade) is a constant scale of the
            # true ratio, so it yields the intended scale-invariant DSR; demote ``pnl``
            # to a last resort.
            r = tr.get("net_pnl_pct")
            if r is None:
                r = tr.get("pnl_pct")
            if r is None:
                r = tr.get("return_pct")
            if r is None:
                r = tr.get("pnl")
            if r is None:
                continue
            try:
                rv = float(r)
            except (TypeError, ValueError):
                continue
            if math.isfinite(rv):
                returns.append(rv)

        if len(returns) < 2:
            return None

        opt_metrics = json.loads(opt["metrics_json"]) if opt and opt["metrics_json"] else None
        opt_config = json.loads(opt["config_json"]) if opt and opt["config_json"] else None
        n_trials = _latest_n_trials(
            opt_metrics if isinstance(opt_metrics, dict) else None,
            opt_config if isinstance(opt_config, dict) else None,
            default_trials,
        )
        result = deflated_sharpe_ratio(returns, n_trials)
        result["trials_source"] = "optimization_result" if opt else "default"
        return result
    except Exception:
        return None
