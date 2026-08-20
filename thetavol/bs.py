"""Black-76 pricing off the IMPLIED FORWARD, not the cash spot.

Standing rule (SPX Income Program, rule 2): index and ETF options price off the
forward. Computing deltas from the spot print with a zero dividend yield is wrong
twice over -- it ignores dividends, and the underlying print is often taken at a
different moment from the option quotes. Every Greek in this package is computed
off a forward fitted per expiration from that expiration's own quotes.
"""
from __future__ import annotations
import math

SQRT2PI = math.sqrt(2.0 * math.pi)


def _n(x: float) -> float:
    return math.exp(-0.5 * x * x) / SQRT2PI


def _N(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def price(F: float, K: float, T: float, sigma: float, r: float, cp: str) -> float:
    """Black-76 price of a European option on a forward F, discounted at r."""
    df = math.exp(-r * T)
    if T <= 0 or sigma <= 0:
        intr = max(F - K, 0.0) if cp == "C" else max(K - F, 0.0)
        return df * intr
    v = sigma * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * v * v) / v
    d2 = d1 - v
    if cp == "C":
        return df * (F * _N(d1) - K * _N(d2))
    return df * (K * _N(-d2) - F * _N(-d1))


def implied_vol(target: float, F: float, K: float, T: float, r: float, cp: str,
                lo: float = 1e-4, hi: float = 5.0, tol: float = 1e-8) -> float | None:
    """Bisection on Black-76.  Returns None if the target is outside no-arb bounds."""
    df = math.exp(-r * T)
    intrinsic = df * (max(F - K, 0.0) if cp == "C" else max(K - F, 0.0))
    upper = df * (F if cp == "C" else K)
    if target <= intrinsic + 1e-10 or target >= upper - 1e-10:
        return None
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if price(F, K, T, mid, r, cp) > target:
            hi = mid
        else:
            lo = mid
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi)


def greeks(F: float, S: float, K: float, T: float, sigma: float, r: float, cp: str) -> dict:
    """Per-share Greeks. delta/gamma are w.r.t. SPOT; the forward is held at F/S*S.

    dV/dS = dV/dF * dF/dS = dV/dF * (F/S).
    theta is a one-calendar-day finite difference with the forward rolled down
    consistently (F shrinks toward S at the carry rate), which is what actually
    happens to the position overnight.
    """
    df = math.exp(-r * T)
    if T <= 0 or sigma <= 0:
        return {"price": price(F, K, T, sigma, r, cp), "delta": 0.0, "gamma": 0.0,
                "vega": 0.0, "theta": 0.0}
    v = sigma * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * v * v) / v
    dVdF = df * (_N(d1) if cp == "C" else _N(d1) - 1.0)
    dFdS = F / S
    delta = dVdF * dFdS
    gamma = df * _n(d1) / (F * v) * dFdS * dFdS
    vega = df * F * _n(d1) * math.sqrt(T) / 100.0          # per 1 vol point
    dt = 1.0 / 365.0
    carry = math.log(F / S) / T if T > 0 else 0.0          # implied (r - q)
    T2 = max(T - dt, 1e-8)
    F2 = S * math.exp(carry * T2)
    theta = price(F2, K, T2, sigma, r, cp) - price(F, K, T, sigma, r, cp)
    return {"price": price(F, K, T, sigma, r, cp), "delta": delta, "gamma": gamma,
            "vega": vega, "theta": theta}
