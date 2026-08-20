"""Beta-weighted delta, correlation, 1-day VaR/CVaR and the sizing optimiser."""
from __future__ import annotations
import math, random
from .config import *
from .forecast import rets
from . import bs

random.seed(20260820)


# ---------------------------------------------------------------------------
def panel(px: dict, syms: list[str]) -> dict:
    """Aligned daily log-return panel over the common tail of the sample."""
    n = min(len(px[s]) for s in syms)
    r = {s: rets(px[s][-n:]) for s in syms}
    m = min(len(v) for v in r.values())
    return {s: v[-m:] for s, v in r.items()}


def beta_corr(P: dict, bench: str = BWD_BENCHMARK) -> tuple[dict, dict]:
    b = P[bench]
    mb = sum(b) / len(b)
    vb = sum((x - mb) ** 2 for x in b) / (len(b) - 1)
    betas, sds = {}, {}
    for s, v in P.items():
        mv = sum(v) / len(v)
        sds[s] = math.sqrt(sum((x - mv) ** 2 for x in v) / (len(v) - 1))
        cov = sum((x - mv) * (y - mb) for x, y in zip(v, b)) / (len(v) - 1)
        betas[s] = cov / vb
    corr = {}
    for a in P:
        for c in P:
            va, vc = P[a], P[c]
            ma, mc = sum(va) / len(va), sum(vc) / len(vc)
            cov = sum((x - ma) * (y - mc) for x, y in zip(va, vc)) / (len(va) - 1)
            corr[(a, c)] = cov / (sds[a] * sds[c])
    return betas, corr


# ---------------------------------------------------------------------------
def position_pnl(st, n: int, ret: float, shock_mult: float) -> float:
    """1-day P&L of n lots under an underlying return `ret`, delta/gamma/vega.

    The vol channel is a MODEL: dIV = -leverage*sigma*ret + convex*sigma*(|ret|-E|ret|),
    in vol points, with the coefficients in config.VOL_SHOCK.  shock_mult scales it
    (0 = no vol move, 1 = base, 2 = severe).
    """
    g = st.meta["greeks"]
    s1 = st.surfaces["T1"]
    dS = s1.S * ret
    cls = VOL_SHOCK_CLASS[st.sym]
    c = VOL_SHOCK[cls]
    sig = s1.atm_iv
    e_abs = sig / math.sqrt(252.0) * math.sqrt(2 / math.pi)
    div = (-c["leverage"] * sig * ret + c["convex"] * sig * (abs(ret) - e_abs)) * 100.0
    div *= shock_mult
    return n * (g["delta"] * dS + 0.5 * g["gamma"] * dS * dS + g["vega"] * div + g["theta"])


def book_var(book: list[tuple], P: dict, shock_mult: float = 1.0,
             conf: float = VAR_CONFIDENCE) -> dict:
    """Historical-simulation VaR: replay every day of the aligned return panel
    across the whole book at once, so cross-asset correlation is the real one."""
    if not book:
        return {"var": 0.0, "cvar": 0.0, "worst": 0.0, "n": 0}
    n_days = len(next(iter(P.values())))
    pnl = []
    for d in range(n_days):
        tot = 0.0
        for st, n in book:
            tot += position_pnl(st, n, P[st.sym][d], shock_mult)
        pnl.append(tot)
    pnl.sort()
    k = max(1, int((1 - conf) * len(pnl)))
    return {"var": -pnl[k - 1], "cvar": -sum(pnl[:k]) / k, "worst": -pnl[0],
            "n": len(pnl), "pnl": pnl}


def stress(book: list[tuple], scen: dict, P: dict) -> dict:
    """Named scenarios applied through each ETF's beta to SPY."""
    betas, _ = beta_corr(P)
    out = {}
    for name, spy_move in scen.items():
        tot = 0.0
        for st, n in book:
            tot += position_pnl(st, n, betas[st.sym] * spy_move, 2.0)
        out[name] = tot
    return out


def beta_weighted_delta(book: list[tuple], betas: dict, spy_px: float) -> dict:
    """Total book delta expressed as SPY-equivalent shares and dollars."""
    tot_usd = 0.0
    per = {}
    for st, n in book:
        g = st.meta["greeks"]
        d_usd = n * g["delta"] * st.surfaces["T1"].S
        bw = d_usd * betas[st.sym]
        per[st.sym] = per.get(st.sym, 0.0) + bw
        tot_usd += bw
    return {"usd": tot_usd, "spy_shares": tot_usd / spy_px, "per_symbol": per}


# ---------------------------------------------------------------------------
def expected_monthly(st, ev_key: str = "mev") -> float:
    """Expected P&L per lot per MONTH.  One entry per monthly expiry cycle."""
    ev = st.meta[ev_key]["ev"] if ev_key in st.meta and st.meta[ev_key] else 0.0
    hold = st.front_dte if "calendar" in st.name else max(st.front_dte - MANAGE_DTE, 8)
    turns_per_year = min(12.0, 365.0 / max(hold, 30.0))
    return ev * turns_per_year / 12.0


def optimise(cands: list, P: dict, betas: dict, spy_px: float,
             ev_key: str = "mev", max_lots: int = 40,
             shock_mult: float = 1.0, var_cap: float | None = None) -> dict:
    """Greedy integer sizing, then a delta hedge.

    Selection maximises expected monthly income per dollar of max loss, subject to
    the 99% 1-day VaR cap, the 2%-of-NLV per-trade max-loss cap, one structure per
    underlying, and at most MAX_US_EQUITY_BLOCK names from the US equity block.

    Beta-weighted delta is NOT a selection constraint -- it is neutralised
    afterwards with a SPY-equivalent hedge, which is what the futures/stock sleeve
    exists for.  The required hedge is reported as a line item.
    """
    us_block = {"SPY", "QQQ", "IWM", "SMH", "ARKK"}
    var_cap = var_cap if var_cap is not None else MAX_DAILY_DRAWDOWN * NLV
    pool = [c for c in cands if c.meta.get(ev_key) and expected_monthly(c, ev_key) > 0]
    pool.sort(key=lambda c: -expected_monthly(c, ev_key) / max(c.meta["max_loss"], 1))
    book: list[list] = []
    used: set[str] = set()
    n_us = 0
    for c in pool:
        if c.sym in used:
            continue
        if c.sym in us_block and n_us >= MAX_US_EQUITY_BLOCK:
            continue
        best_n = 0
        for n in range(1, max_lots + 1):
            if c.meta["max_loss"] * n > MAX_LOSS_PER_TRADE * NLV:
                break
            trial = [(st, k) for st, k in book] + [(c, n)]
            if book_var(trial, P, shock_mult)["var"] <= var_cap:
                best_n = n
            else:
                break
        if best_n:
            book.append([c, best_n])
            used.add(c.sym)
            if c.sym in us_block:
                n_us += 1
        if len(book) >= TARGET_POSITIONS[1]:
            break
    bk = [(c, n) for c, n in book]
    bw = beta_weighted_delta(bk, betas, spy_px)
    hedge_shares = -bw["spy_shares"]
    return {"book": bk, "used": used, "bwd_before": bw, "hedge_spy_shares": hedge_shares}


def frontier(cands, P, betas, spy_px, ev_key, caps) -> list[dict]:
    """Income vs risk: re-run the optimiser at a ladder of VaR caps."""
    out = []
    for cap in caps:
        o = optimise(cands, P, betas, spy_px, ev_key=ev_key, var_cap=cap)
        bk = o["book"]
        inc = sum(expected_monthly(st, ev_key) * n for st, n in bk)
        v = book_var(bk, P, 1.0)
        out.append({"cap": cap, "income": inc, "income_pct": inc / NLV,
                    "var": v["var"], "cvar": v["cvar"], "n_pos": len(bk),
                    "maxloss": sum(st.meta["max_loss"] * n for st, n in bk)})
    return out
