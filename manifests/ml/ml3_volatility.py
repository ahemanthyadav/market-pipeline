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


def garch_forecast(
    returns: pd.DataFrame,
    symbol_col: str = "symbol",
    ts_col: str = "ts",
    ret_col: str = "ret",
    horizon: int = 1,
) -> pd.DataFrame:
    """GARCH(1,1) volatility forecast via MLE.

    sigma_t^2 = omega + alpha * r_{t-1}^2 + beta * sigma_{t-1}^2

    Requires scipy.optimize. Falls back to EWMA if scipy unavailable or
    optimization fails. Returns latest forecast per symbol.
    """
    try:
        from scipy.optimize import minimize
    except ImportError:
        # scipy not available — fall back to EWMA
        return ewma_forecast(returns, symbol_col, ts_col, ret_col, horizon=horizon)

    df = returns.sort_values([symbol_col, ts_col]).copy()
    results = []
    for sym, g in df.groupby(symbol_col):
        g = g.sort_values(ts_col)
        r = g[ret_col].values
        r2 = r ** 2
        n = len(r)
        if n < 50:
            # Not enough data for GARCH MLE — fall back to EWMA
            sub = pd.DataFrame(
                {symbol_col: [sym], ts_col: [g[ts_col].iloc[-1]], ret_col: [r[-1]]}
            )
            results.append(ewma_forecast(sub, symbol_col, ts_col, ret_col, horizon=horizon).iloc[0])
            continue

        # Initial parameter guess
        omega0 = np.var(r) * 1e-6
        alpha0 = 0.1
        beta0 = 0.85

        def neg_log_lik(params):
            omega, alpha, beta = params
            if omega <= 0 or alpha < 0 or beta < 0 or alpha + beta >= 1:
                return 1e10
            var = np.full(n, np.var(r))
            for t in range(1, n):
                var[t] = omega + alpha * r2[t - 1] + beta * var[t - 1]
            if np.any(var <= 0):
                return 1e10
            ll = 0.5 * np.sum(np.log(2 * np.pi * var) + r2 / var)
            return ll

        try:
            res = minimize(
                neg_log_lik,
                [omega0, alpha0, beta0],
                method="L-BFGS-B",
                bounds=[(1e-10, None), (0, 1), (0, 1)],
            )
            if not res.success:
                raise ValueError("optimization failed")
            omega, alpha, beta = res.x
        except Exception:
            sub = pd.DataFrame(
                {symbol_col: [sym], ts_col: [g[ts_col].iloc[-1]], ret_col: [r[-1]]}
            )
            results.append(ewma_forecast(sub, symbol_col, ts_col, ret_col, horizon=horizon).iloc[0])
            continue

        # Iterate variance forward from last observation
        var_last = omega + alpha * r2[-1] + beta * np.var(r)
        # h-step forecast: var_{t+h} = omega + (alpha+beta) * var_{t+h-1}
        a_b = alpha + beta
        if a_b >= 1:
            forecast_var = var_last
        else:
            forecast_var = omega / (1 - a_b) + (var_last - omega / (1 - a_b)) * (a_b ** horizon)
        forecast = math.sqrt(max(forecast_var, 0)) * math.sqrt(horizon)
        results.append(
            {
                "symbol": sym,
                "ts": g[ts_col].iloc[-1],
                "model": "garch",
                "horizon": horizon,
                "forecast": forecast,
            }
        )
    return pd.DataFrame(results)


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
    Falls back to GARCH if scipy unavailable.
    """
    try:
        from scipy.optimize import minimize
    except ImportError:
        return garch_forecast(returns, symbol_col, ts_col, ret_col, horizon=horizon)

    df = returns.sort_values([symbol_col, ts_col]).copy()
    results = []
    for sym, g in df.groupby(symbol_col):
        g = g.sort_values(ts_col)
        r = g[ret_col].values
        r2 = r ** 2
        n = len(r)
        if n < 50:
            sub = pd.DataFrame(
                {symbol_col: [sym], ts_col: [g[ts_col].iloc[-1]], ret_col: [r[-1]]}
            )
            results.append(ewma_forecast(sub, symbol_col, ts_col, ret_col, horizon=horizon).iloc[0])
            continue

        omega0 = np.var(r) * 1e-6
        alpha0 = 0.08
        gamma0 = 0.05
        beta0 = 0.85

        def neg_log_lik(params):
            omega, alpha, gamma, beta = params
            if omega <= 0 or alpha < 0 or gamma < 0 or beta < 0 or alpha + 0.5 * gamma + beta >= 1:
                return 1e10
            var = np.full(n, np.var(r))
            for t in range(1, n):
                leverage = 1.0 if r[t - 1] < 0 else 0.0
                var[t] = omega + alpha * r2[t - 1] + gamma * r2[t - 1] * leverage + beta * var[t - 1]
            if np.any(var <= 0):
                return 1e10
            ll = 0.5 * np.sum(np.log(2 * np.pi * var) + r2 / var)
            return ll

        try:
            res = minimize(
                neg_log_lik,
                [omega0, alpha0, gamma0, beta0],
                method="L-BFGS-B",
                bounds=[(1e-10, None), (0, 1), (0, 1), (0, 1)],
            )
            if not res.success:
                raise ValueError("optimization failed")
            omega, alpha, gamma, beta = res.x
        except Exception:
            sub = pd.DataFrame(
                {symbol_col: [sym], ts_col: [g[ts_col].iloc[-1]], ret_col: [r[-1]]}
            )
            results.append(ewma_forecast(sub, symbol_col, ts_col, ret_col, horizon=horizon).iloc[0])
            continue

        # Leverage-adjusted forecast: average leverage = P(r<0) ≈ 0.5
        leverage_avg = 0.5
        a_b_g = alpha + beta + gamma * leverage_avg
        if a_b_g >= 1:
            forecast_var = omega + alpha * r2[-1] + gamma * r2[-1] * leverage_avg + beta * np.var(r)
        else:
            forecast_var = omega / (1 - a_b_g) + (
                (omega + alpha * r2[-1] + gamma * r2[-1] * leverage_avg + beta * np.var(r))
                - omega / (1 - a_b_g)
            ) * (a_b_g ** horizon)
        forecast = math.sqrt(max(forecast_var, 0)) * math.sqrt(horizon)
        results.append(
            {
                "symbol": sym,
                "ts": g[ts_col].iloc[-1],
                "model": "gjr_garch",
                "horizon": horizon,
                "forecast": forecast,
            }
        )
    return pd.DataFrame(results)


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
