"""ML-3 volatility forecasts — EWMA + GARCH baselines.

Baselines (research-backed, Hansen & Lunde 2005 verified §9):
1. EWMA (RiskMetrics): sigma_t^2 = lambda * sigma_{t-1}^2 + (1-lambda) * r_{t-1}^2
2. GARCH(1,1): sigma_t^2 = omega + alpha * r_{t-1}^2 + beta * sigma_{t-1}^2
3. Leverage-effect models (GJR-GARCH / EGARCH) for equities — Hansen & Lunde
   found GARCH(1,1) hard to beat for FX but inferior for stocks where
   leverage-effect models win.

Output: rows for `app.vol_forecasts` table (symbol, ts, model, horizon, forecast).

Usage:
    from ml3_volatility import ewma_forecast, garch_forecast
    fc = ewma_forecast(returns_df, symbol_col="symbol", ret_col="ret", lambda_=0.94)
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def ewma_forecast(
    returns: pd.DataFrame,
    symbol_col: str = "symbol",
    ts_col: str = "ts",
    ret_col: str = "ret",
    lambda_: float = 0.94,
    horizon: int = 1,
) -> pd.DataFrame:
    """EWMA volatility forecast (RiskMetrics lambda=0.94 default).

    Computes sigma_t^2 = lambda * sigma_{t-1}^2 + (1-lambda) * r_{t-1}^2
    recursively per symbol, returns the latest forecast per symbol.

    Returns DataFrame: symbol, ts, model='ewma', horizon, forecast.
    """
    df = returns.sort_values([symbol_col, ts_col]).copy()
    results = []
    for sym, g in df.groupby(symbol_col):
        g = g.sort_values(ts_col)
        r2 = g[ret_col].pow(2).values
        n = len(r2)
        var = np.full(n, np.nan)
        var[0] = r2[0]  # initialize with first squared return
        for t in range(1, n):
            var[t] = lambda_ * var[t - 1] + (1 - lambda_) * r2[t - 1]
        sigma = np.sqrt(var)
        # horizon-step forecast: for EWMA, constant = latest variance
        forecast = math.sqrt(var[-1]) * math.sqrt(horizon)
        results.append(
            {
                "symbol": sym,
                "ts": g[ts_col].iloc[-1],
                "model": "ewma",
                "horizon": horizon,
                "forecast": forecast,
            }
        )
    return pd.DataFrame(results)


def cond_var(r, omega, alpha, gamma, beta):
    """GJR-GARCH(1,1) filtered conditional variances (gamma=0 gives plain GARCH).

    var[0] = sample variance of r (initialisation);
    var[t] = omega + (alpha + gamma * 1[r_{t-1} < 0]) * r_{t-1}^2 + beta * var[t-1]
    Returns n+1 values: var[0..n-1] pair with r[0..n-1] in the likelihood and
    var[n] is the one-step-ahead forecast for the day after the last return.
    The recursion is a first-order linear filter, so scipy.signal.lfilter runs it
    in C (a Python loop per likelihood evaluation x ~2,250 symbols is too slow on a
    Pi); the loop is kept as the no-scipy fallback and the test oracle.
    """
    r = np.asarray(r, dtype=float)
    shock = omega + (alpha + gamma * (r < 0)) * r * r
    v0 = float(np.var(r))
    try:
        from scipy.signal import lfilter
        out, _ = lfilter([1.0], [1.0, -beta], shock, zi=[beta * v0])
    except ImportError:
        out = np.empty_like(shock)
        prev = v0
        for t, x in enumerate(shock):
            prev = x + beta * prev
            out[t] = prev
    return np.concatenate([[v0], out])


def horizon_vol(var_next, omega, persistence, horizon):
    """sqrt of the summed variance over the next `horizon` days (h=1: sqrt(var_next)).

    var_{T+k} = vbar + p^(k-1) * (var_{T+1} - vbar), vbar = omega / (1 - p);
    summing matches the EWMA convention (constant variance -> sqrt(h * var)).
    """
    p = persistence
    if p >= 1:
        return math.sqrt(max(var_next, 0.0) * horizon)
    vbar = omega / (1 - p)
    total = sum(vbar + p ** (k - 1) * (var_next - vbar) for k in range(1, horizon + 1))
    return math.sqrt(max(total, 0.0))


def _nll(r, omega, alpha, gamma, beta):
    if omega <= 0 or alpha < 0 or gamma < 0 or beta < 0 or alpha + 0.5 * gamma + beta >= 1:
        return 1e10
    var = cond_var(r, omega, alpha, gamma, beta)[:-1]
    if not np.all(np.isfinite(var)) or np.any(var <= 0):
        return 1e10
    return float(0.5 * np.sum(np.log(2 * np.pi * var) + r * r / var))


def fit_garch(r, gjr=False):
    """Variance-targeted MLE of (omega, alpha, gamma, beta); gamma = 0 unless gjr.

    Variance targeting (Engle & Mezrich 1996; consistency: Francq, Horvath &
    Zakoian 2011): omega = var(r) * (1 - alpha - gamma/2 - beta), so only the
    dynamics are optimised. Chosen 2026-11-12 for speed — the unrestricted
    3/4-parameter fit took ~70 min for ~2,250 symbols on a Pi (> the Job deadline);
    targeted it is ~6x faster, median one-day forecast within ~1-2 % of the
    unrestricted MLE (benchmark on simulated t(4) returns, TX2).
    Nelder-Mead (derivative-free) because stationarity is a penalty in _nll.
    Returns None if the fit fails.
    """
    from scipy.optimize import minimize
    r = np.asarray(r, dtype=float)
    v = float(np.var(r))
    if not v > 0:
        return None

    def full(p):
        alpha, gamma, beta = (p[0], p[1], p[2]) if gjr else (p[0], 0.0, p[1])
        return v * (1 - alpha - 0.5 * gamma - beta), alpha, gamma, beta

    x0 = [0.05, 0.10, 0.85] if gjr else [0.10, 0.85]
    res = minimize(lambda p: _nll(r, *full(p)), x0, method="Nelder-Mead",
                   options={"maxiter": 2000, "xatol": 1e-4, "fatol": 1e-4})
    if not np.isfinite(res.fun) or res.fun >= 1e9:
        return None
    return tuple(float(x) for x in full(res.x))


def _garch_family(returns, symbol_col, ts_col, ret_col, horizon, gjr, min_obs=50):
    """Fit per symbol; symbols whose fit is impossible are SKIPPED (no row), never
    relabelled as another model — skipped count is in DataFrame.attrs["skipped"]."""
    model = "gjr_garch" if gjr else "garch"
    try:
        import scipy.optimize  # noqa: F401
    except ImportError:
        out = pd.DataFrame(columns=["symbol", "ts", "model", "horizon", "forecast"])
        out.attrs["skipped"] = int(returns[symbol_col].nunique()) if len(returns) else 0
        out.attrs["reason"] = "scipy not installed"
        return out
    results, skipped = [], 0
    df = returns.sort_values([symbol_col, ts_col])
    for sym, g in df.groupby(symbol_col):
        r = g[ret_col].to_numpy(dtype=float)
        r = r[np.isfinite(r)]
        params = fit_garch(r, gjr=gjr) if len(r) >= min_obs else None
        if params is None:
            skipped += 1
            continue
        omega, alpha, gamma, beta = params
        var_next = cond_var(r, omega, alpha, gamma, beta)[-1]
        forecast = horizon_vol(var_next, omega, alpha + 0.5 * gamma + beta, horizon)
        results.append({"symbol": sym, "ts": g[ts_col].iloc[-1], "model": model,
                        "horizon": horizon, "forecast": forecast})
    out = pd.DataFrame(results, columns=["symbol", "ts", "model", "horizon", "forecast"])
    out.attrs["skipped"] = skipped
    return out


def garch_forecast(
    returns: pd.DataFrame,
    symbol_col: str = "symbol",
    ts_col: str = "ts",
    ret_col: str = "ret",
    horizon: int = 1,
) -> pd.DataFrame:
    """GARCH(1,1) volatility forecast via MLE.

    sigma_t^2 = omega + alpha * r_{t-1}^2 + beta * sigma_{t-1}^2
    One-step forecast from the LAST FILTERED conditional variance. Needs scipy;
    symbols with < 50 returns or a failed fit get no row (fixed 2026-11-12: the old
    code silently emitted EWMA rows labelled 'ewma' that collided and were dropped).
    """
    return _garch_family(returns, symbol_col, ts_col, ret_col, horizon, gjr=False)


def gjr_garch_forecast(
    returns: pd.DataFrame,
    symbol_col: str = "symbol",
    ts_col: str = "ts",
    ret_col: str = "ret",
    horizon: int = 1,
) -> pd.DataFrame:
    """GJR-GARCH leverage-effect forecast for equities.

    sigma_t^2 = omega + alpha * r_{t-1}^2 + gamma * r_{t-1}^2 * I(r_{t-1} < 0)
                + beta * sigma_{t-1}^2
    Hansen & Lunde (2005): leverage-effect models beat GARCH(1,1) for stocks.
    The next-day variance uses the KNOWN sign of the last return; multi-step terms
    use persistence alpha + gamma/2 + beta (symmetric returns).
    """
    return _garch_family(returns, symbol_col, ts_col, ret_col, horizon, gjr=True)


if __name__ == "__main__":
    # Smoke test
    import numpy as np

    np.random.seed(42)
    n = 200
    rets = pd.DataFrame(
        {
            "symbol": ["AAA"] * n,
            "ts": pd.date_range("2026-01-01", periods=n, freq="D"),
            "ret": np.random.normal(0, 0.02, n),
        }
    )
    fc_ewma = ewma_forecast(rets)
    fc_garch = garch_forecast(rets)
    fc_gjr = gjr_garch_forecast(rets)
    print(f"EWMA forecast: {fc_ewma['forecast'].iloc[0]:.6f}")
    print(f"GARCH forecast: {fc_garch['forecast'].iloc[0]:.6f}")
    print(f"GJR-GARCH forecast: {fc_gjr['forecast'].iloc[0]:.6f}")
