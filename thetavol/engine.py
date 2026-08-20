"""Candidate generation and gating for every ETF in the universe."""
from __future__ import annotations
import math
from . import surface, forecast, structures as stc
from .config import *
from .structures import Leg, Structure


def _q(surf, K, cp):
    return surf.quotes.get((K, cp))


def _leg(surf, exp, K, cp, qty):
    q = _q(surf, K, cp)
    if not q:
        return None
    return Leg(exp, K, cp, qty, q["bid"], q["ask"], q.get("iv_refit", q["iv"]), q["oi"])


def _deltas(surf):
    from . import bs
    out = {}
    for (K, cp), q in surf.quotes.items():
        iv = q.get("iv_refit", surf.iv(K))
        out[(K, cp)] = abs(bs.greeks(surf.F, surf.S, K, surf.T, iv, RISK_FREE, cp)["delta"])
    return out


def _pick(dl, cp, target, exclude=()):
    """Quoted strike whose |delta| is closest to target, on the requested side."""
    cands = [(K, d) for (K, c), d in dl.items() if c == cp and K not in exclude]
    if not cands:
        return None
    return min(cands, key=lambda kd: abs(kd[1] - target))[0]


def candidates(sym: str, S: dict) -> list[Structure]:
    m = S[sym]
    out = []
    s1 = m.get("T1")
    s2 = m.get("T2")
    if not s1:
        return out

    # ---------- calendars (need both expiries, same strikes) ----------------
    if s2:
        both_p = sorted({K for (K, cp) in s1.quotes if cp == "P"} &
                        {K for (K, cp) in s2.quotes if cp == "P"})
        both_c = sorted({K for (K, cp) in s1.quotes if cp == "C"} &
                        {K for (K, cp) in s2.quotes if cp == "C"})
        otm_p = [K for K in both_p if K < s1.F]
        otm_c = [K for K in both_c if K > s1.F]
        if otm_p and otm_c:
            Kp, Kc = max(otm_p), min(otm_c)
            legs = [_leg(s1, "T1", Kp, "P", -1), _leg(s2, "T2", Kp, "P", +1),
                    _leg(s1, "T1", Kc, "C", -1), _leg(s2, "T2", Kc, "C", +1)]
            if all(legs):
                out.append(Structure(sym, "double calendar", legs, m,
                                     f"sell {s1.date} {Kp:g}P/{Kc:g}C, buy {s2.date} {Kp:g}P/{Kc:g}C"))
        atm = [K for K in both_c if abs(K - s1.F) / s1.F < 0.02]
        if atm:
            K = min(atm, key=lambda k: abs(k - s1.F))
            legs = [_leg(s1, "T1", K, "C", -1), _leg(s2, "T2", K, "C", +1)]
            if all(legs):
                out.append(Structure(sym, "ATM call calendar", legs, m,
                                     f"sell {s1.date} {K:g}C, buy {s2.date} {K:g}C"))

    # ---------- single-expiry credit structures -----------------------------
    for lab, surf in (("T1", s1), ("T2", s2)):
        if surf is None:
            continue
        dl = _deltas(surf)
        sp = _pick(dl, "P", SHORT_DELTA["iron_condor"] + 0.01)
        sc = _pick(dl, "C", SHORT_DELTA["call_credit_spread"] - 0.05)
        lp = _pick(dl, "P", 0.10, exclude=(sp,)) if sp else None
        lc = _pick(dl, "C", 0.08, exclude=(sc,)) if sc else None
        if sp and lp and lp < sp:
            legs = [_leg(surf, lab, sp, "P", -1), _leg(surf, lab, lp, "P", +1)]
            if all(legs):
                out.append(Structure(sym, f"put credit spread {lab}", legs, m,
                                     f"{surf.date}: short {sp:g}P / long {lp:g}P"))
        if sc and lc and lc > sc:
            legs = [_leg(surf, lab, sc, "C", -1), _leg(surf, lab, lc, "C", +1)]
            if all(legs):
                out.append(Structure(sym, f"call credit spread {lab}", legs, m,
                                     f"{surf.date}: short {sc:g}C / long {lc:g}C"))
        if sp and lp and sc and lc and lp < sp and lc > sc:
            legs = [_leg(surf, lab, sp, "P", -1), _leg(surf, lab, lp, "P", +1),
                    _leg(surf, lab, sc, "C", -1), _leg(surf, lab, lc, "C", +1)]
            if all(legs):
                out.append(Structure(sym, f"iron condor {lab}", legs, m,
                                     f"{surf.date}: {lp:g}/{sp:g}P + {sc:g}/{lc:g}C"))
    return [s for s in out if s.legs and all(s.legs)]


GATE_ORDER = ["liquidity", "open interest", "sleeve/VRP", "horizontal skew",
              "forward factor", "theta rate", "hold EV", "managed EV",
              "sizing (2% cap)", "DTE window"]


def gate(st: Structure, side: str, ff: float, hskew: float, ev: dict, mev: dict) -> dict:
    g = {}
    g["liquidity"] = ("PASS" if st.worst_spread_pct <= MAX_SPREAD_PCT_OF_MID else "FAIL",
                      f"widest leg {st.worst_spread_pct*100:.1f}% of mid vs {MAX_SPREAD_PCT_OF_MID*100:.0f}% gate")
    g["open interest"] = ("PASS" if st.min_oi >= MIN_OPEN_INTEREST else "FAIL",
                          f"thinnest leg {st.min_oi:,} OI vs {MIN_OPEN_INTEREST} floor")
    is_cal = "calendar" in st.name
    if is_cal:
        g["sleeve/VRP"] = ("PASS" if side == "BUY VOL" else
                           ("MARGINAL" if side == "no edge" else "FAIL"),
                           f"screen says {side}; a calendar is a long-vega trade")
        g["horizontal skew"] = ("PASS" if hskew > 0 else
                                ("MARGINAL" if hskew >= HORIZONTAL_SKEW_TOL else "FAIL"),
                                f"front-back {hskew*100:+.2f} vol pts (want > 0)")
        g["forward factor"] = ("PASS" if ff >= FF_ENTRY else
                               ("MARGINAL" if ff > 0 else "FAIL"),
                               f"FF {ff:+.3f} vs {FF_ENTRY:+.2f} entry")
        gk = st.greeks()
        rate = gk["theta"] / abs(st.fill_cost) if st.fill_cost else 0
        g["theta rate"] = ("PASS" if rate >= CAL_MIN_THETA_PCT_OF_DEBIT else "FAIL",
                           f"{rate*100:+.2f}%/day of debit vs {CAL_MIN_THETA_PCT_OF_DEBIT*100:.1f}% floor")
    else:
        g["sleeve/VRP"] = ("PASS" if side == "SELL VOL" else
                           ("MARGINAL" if side == "no edge" else "FAIL"),
                           f"screen says {side}; a credit structure is a short-vega trade")
        g["horizontal skew"] = ("n/a", "single expiry")
        g["forward factor"] = ("n/a", "single expiry")
        gk = st.greeks()
        cr = abs(st.fill_cost)
        rate = gk["theta"] / cr if cr else 0
        g["theta rate"] = ("PASS" if rate >= 0.005 else "MARGINAL",
                           f"{rate*100:+.2f}%/day of credit")
    g["hold EV"] = ("PASS" if ev.get("ev", -1) > 0 else "FAIL",
                    f"${ev.get('ev', float('nan')):,.0f} per lot on {ev.get('n_windows',0)} real windows "
                    f"({ev.get('n_distinct_episodes',0)} distinct episodes)")
    g["managed EV"] = ("PASS" if mev.get("ev", -1) > 0 else "FAIL",
                       f"${mev.get('ev', float('nan')):,.0f} per lot, win {mev.get('win',0)*100:.0f}%")
    maxloss = max_loss(st)
    g["sizing (2% cap)"] = ("PASS" if maxloss <= MAX_LOSS_PER_TRADE * NLV else "FAIL",
                            f"max loss ${maxloss:,.0f} vs ${MAX_LOSS_PER_TRADE*NLV:,.0f} cap")
    dte = min(st.surfaces[l.exp].dte for l in st.legs)
    g["DTE window"] = ("PASS" if 30 <= dte <= 60 else "MARGINAL",
                       f"front {dte} DTE vs 30-60 entry window")
    return g


def max_loss(st: Structure) -> float:
    """Worst outcome across a wide terminal grid at the front expiry."""
    s1 = st.surfaces["T1"]
    Tf = st.front_T()
    entry = st.fill_cost
    lo, hi = s1.S * 0.35, s1.S * 2.2
    worst = 1e18
    for i in range(801):
        ST = lo + (hi - lo) * i / 800
        worst = min(worst, st.value_at(ST, Tf) - entry)
    return -worst


def run(snapshot: str = SNAPSHOT) -> dict:
    d = surface.load(snapshot)
    S = surface.build(snapshot)
    res = {"snapshot": snapshot, "symbols": {}, "meta": {}}
    refit, quoted = {}, {}
    for sym in UNIVERSE:
        if sym not in S:
            continue
        s1 = S[sym]["T1"]
        s2 = S[sym].get("T2")
        refit[sym] = s1.atm_iv
        quoted[sym] = d["underlyings"][sym]["iv30"]
        g = forecast.grid(d["px"][sym], s1.atm_iv,
                          s1.atm_iv_side("bid"), s1.atm_iv_side("ask"))
        ff = surface.forward_factor(s1, s2) if s2 else float("nan")
        fv = surface.forward_vol(s1, s2) if s2 else float("nan")
        hskew = (s1.atm_iv - s2.atm_iv) if s2 else float("nan")
        rec = {"sym": sym, "S": s1.S, "F1": s1.F, "F2": s2.F if s2 else None,
               "dte1": s1.dte, "dte2": s2.dte if s2 else None,
               "atm_iv1": s1.atm_iv, "atm_iv2": s2.atm_iv if s2 else None,
               "iv_bid": s1.atm_iv_side("bid"), "iv_ask": s1.atm_iv_side("ask"),
               "fwd_vol": fv, "ff": ff, "hskew": hskew,
               "vrp": g, "side": g["side"], "class": ASSET_CLASS[sym],
               "structures": []}
        # 25-delta smile diagnostics
        Kp25 = stc.kdelta(s1, 0.25, "P"); Kc25 = stc.kdelta(s1, 0.25, "C")
        rec["rr25"] = s1.iv(Kc25) - s1.iv(Kp25)
        rec["bf25"] = 0.5 * (s1.iv(Kc25) + s1.iv(Kp25)) - s1.atm_iv
        rec["k25p"], rec["k25c"] = Kp25, Kc25

        for st in candidates(sym, S):
            h = min(S[sym][l.exp].dte for l in st.legs)
            ev = stc.terminal_ev(st, d["px"][sym], h)
            ev_calm = stc.terminal_ev(st, d["px"][sym], h, regime="calm")
            is_cal = "calendar" in st.name
            take, stop = (0.25, 0.50) if is_cal else (CREDIT_TAKE_PROFIT, CREDIT_STOP)
            mday = None if is_cal else (max(1, h - MANAGE_DTE) if h > MANAGE_DTE else None)
            mev = stc.managed_ev(st, d["px"][sym], h, take, stop, manage_day=mday)
            gk = st.greeks()
            gates = gate(st, g["side"], ff, hskew, ev, mev)
            fails = sum(1 for v in gates.values() if v[0] == "FAIL")
            margs = sum(1 for v in gates.values() if v[0] == "MARGINAL")
            st.meta = {"ev": ev, "ev_calm": ev_calm, "mev": mev, "greeks": gk, "gates": gates,
                       "fails": fails, "marginals": margs,
                       "max_loss": max_loss(st), "leg_deltas": st.leg_deltas()}
            rec["structures"].append(st)
        res["symbols"][sym] = rec
    res["meta"]["iv_bias"] = forecast.measured_iv_bias(quoted, refit)
    res["meta"]["surfaces"] = S
    res["meta"]["data"] = d
    return res


def run_scaled(snapshot: str, k: float) -> dict:
    """Regime sweep: hold universe, correlations, forecasts and costs fixed and
    scale every quoted implied vol by k, repricing every leg from the model.
    Answers 'is it a bad regime or a bad idea?'"""
    from . import bs
    d = surface.load(snapshot)
    S = surface.build(snapshot)
    for sym, m in S.items():
        for lab, s in m.items():
            a, b_, c_ = s.smile
            s.smile = (a * k, b_ * k, c_ * k)
            s.atm_iv = s.iv(s.F)
            for (K, cp), q in s.quotes.items():
                iv = s.iv(K)
                half = 0.5 * (q["ask"] - q["bid"])
                mid = bs.price(s.F, K, s.T, iv, RISK_FREE, cp)
                q["bid"], q["ask"], q["iv_refit"] = max(mid - half, 0.01), mid + half, iv
    res = {"symbols": {}, "k": k}
    for sym in UNIVERSE:
        if sym not in S:
            continue
        s1 = S[sym]["T1"]
        rec = {"structures": []}
        for st in candidates(sym, S):
            h = min(S[sym][l.exp].dte for l in st.legs)
            ev = stc.terminal_ev(st, d["px"][sym], h)
            ev_calm = stc.terminal_ev(st, d["px"][sym], h, regime="calm")
            is_cal = "calendar" in st.name
            take, stop = (0.25, 0.50) if is_cal else (CREDIT_TAKE_PROFIT, CREDIT_STOP)
            mday = None if is_cal else (max(1, h - MANAGE_DTE) if h > MANAGE_DTE else None)
            mev = stc.managed_ev(st, d["px"][sym], h, take, stop, n_paths=2500, manage_day=mday)
            st.meta = {"ev": ev, "ev_calm": ev_calm, "mev": mev,
                       "greeks": st.greeks(), "max_loss": max_loss(st),
                       "gates": {}, "fails": 0, "marginals": 0,
                       "leg_deltas": st.leg_deltas()}
            rec["structures"].append(st)
        res["symbols"][sym] = rec
    res["meta"] = {"data": d, "surfaces": S}
    return res
