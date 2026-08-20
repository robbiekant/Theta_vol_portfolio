"""Realised-volatility forecasting and the variance risk premium.

Three independent forecasts x two IV calibrations.  A name only earns a sleeve
if it keeps the same SIDE in every cell of the grid; that is the discipline from
the 2026-08-20 ETF feasibility study, which found 30 of 38 liquid names flipping
side somewhere in the grid (their signal was inside the measurement error).

The IV stress is NOT an imported constant.  It is this snapshot's own measured
execution cost: the ATM implied vol re-inverted from the side of the market you
would actually trade against.
"""
from __future__ import annotations
import math

ANN = math.sqrt(252.0)


def rets(close: list[float]) -> list[float]:
    return [math.log(close[i] / close[i - 1]) for i in range(1, len(close))]


def rv(r: list[float], n: int) -> float:
    w = r[-n:]
    m = sum(w) / len(w)
    return math.sqrt(sum((x - m) ** 2 for x in w) / (len(w) - 1)) * ANN


def har(r):
    v5, v22, v66, v252 = rv(r, 5), rv(r, 22), rv(r, 66), rv(r, min(252, len(r)))
    return math.sqrt(0.30 * v5**2 + 0.35 * v22**2 + 0.25 * v66**2 + 0.10 * v252**2)


def blend5050(r):
    return math.sqrt(0.5 * rv(r, 22) ** 2 + 0.5 * rv(r, min(252, len(r))) ** 2)


def longrun(r):
    return rv(r, min(252, len(r)))


FORECASTS = {"HAR": har, "50-50": blend5050, "1yr mean": longrun}

SELL_MEDIAN = 1.05
BUY_MEDIAN = 0.95


def grid(close: list[float], iv_mid: float, iv_sell: float, iv_buy: float) -> dict:
    """iv_sell / iv_buy are the execution-adjusted ATM vols (bid side / ask side)."""
    r = rets(close)
    fc = {k: f(r) for k, f in FORECASTS.items()}
    cells = {}
    for name, v in fc.items():
        cells[(name, "mid")] = iv_mid / v
        cells[(name, "exec-sell")] = iv_sell / v
        cells[(name, "exec-buy")] = iv_buy / v
    mid_ratios = sorted(iv_mid / v for v in fc.values())
    allr = sorted(cells.values())
    med = mid_ratios[len(mid_ratios) // 2]
    side = "no edge"
    if med >= SELL_MEDIAN and min(cells[(n, "exec-sell")] for n in fc) > 1.0:
        side = "SELL VOL"
    elif med <= BUY_MEDIAN and max(cells[(n, "exec-buy")] for n in fc) < 1.0:
        side = "BUY VOL"
    return {"cells": cells, "forecasts": fc, "median": med,
            "min": allr[0], "max": allr[-1], "side": side,
            "mid_ratios": {n: iv_mid / v for n, v in fc.items()},
            "flips": (allr[0] < 1.0 < allr[-1])}


def measured_iv_bias(quoted_surface_iv: dict, refit_atm_iv: dict) -> dict:
    """Diagnostic: how far IBKR's surface-level IV sits from the re-inverted ATM."""
    out = {}
    for k, v in refit_atm_iv.items():
        q = quoted_surface_iv.get(k)
        if q:
            out[k] = v / q - 1.0
    vals = sorted(out.values())
    out["_median"] = vals[len(vals) // 2]
    out["_mean"] = sum(vals) / len(vals)
    return out
