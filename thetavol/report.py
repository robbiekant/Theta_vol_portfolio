"""Assemble the full result set and write machine-readable outputs."""
from __future__ import annotations
import csv, json, math, os
from . import engine, portfolio as pf, surface
from .config import *

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out")


def build(snapshot: str = SNAPSHOT, sweep: bool = True) -> dict:
    os.makedirs(OUT, exist_ok=True)
    r = engine.run(snapshot)
    d = r["meta"]["data"]
    P = pf.panel(d["px"], UNIVERSE)
    betas, corr = pf.beta_corr(P)
    spy = d["underlyings"]["SPY"]["last"]
    cands = [st for rec in r["symbols"].values() for st in rec["structures"]]

    books = {}
    for key, label in (("mev", "managed EV, all regimes"),
                       ("ev", "hold EV, all regimes"),
                       ("ev_calm", "hold EV, calm-regime subsample")):
        o = pf.optimise(cands, P, betas, spy, ev_key=key)
        bk = o["book"]
        v1 = pf.book_var(bk, P, 1.0)
        v0 = pf.book_var(bk, P, 0.0)
        v2 = pf.book_var(bk, P, 2.0)
        inc = sum(pf.expected_monthly(st, key) * n for st, n in bk)
        pnl = v1.get("pnl", [])
        sd = (math.sqrt(sum(x * x for x in pnl) / len(pnl)) if pnl else 0.0)
        ann_sharpe = ((inc * 12) / (sd * math.sqrt(252))) if sd else 0.0
        books[key] = {
            "label": label, "book": bk,
            "income": inc, "income_pct": inc / NLV,
            "var": {"none": v0["var"], "base": v1["var"], "severe": v2["var"]},
            "cvar": v1["cvar"], "worst_day": v1["worst"],
            "daily_sd": sd, "sharpe": ann_sharpe,
            "maxloss": sum(st.meta["max_loss"] * n for st, n in bk),
            "theta": sum(st.meta["greeks"]["theta"] * n for st, n in bk),
            "vega": sum(st.meta["greeks"]["vega"] * n for st, n in bk),
            "gamma": sum(st.meta["greeks"]["gamma"] * n for st, n in bk),
            "bwd": o["bwd_before"], "hedge_spy": o["hedge_spy_shares"],
            "stress": pf.stress(bk, {"SPY -1%": -0.01, "SPY -3%": -0.03,
                                     "SPY -5%": -0.05, "SPY -8% (2020-03-16 scale)": -0.08,
                                     "SPY +3%": 0.03}, P),
        }

    sweep_rows = []
    if sweep:
        for k in (1.00, 1.05, 1.10, 1.15, 1.20, 1.30, 1.40, 1.50, 1.60):
            rs = engine.run_scaled(snapshot, k)
            cs = [st for rec in rs["symbols"].values() for st in rec["structures"]]
            o = pf.optimise(cs, P, betas, spy, ev_key="ev_calm")
            bk = o["book"]
            inc = sum(pf.expected_monthly(st, "ev_calm") * n for st, n in bk)
            v = pf.book_var(bk, P, 1.0) if bk else {"var": 0, "cvar": 0}
            sweep_rows.append({"k": k, "income": inc, "income_pct": inc / NLV,
                               "var": v["var"], "cvar": v["cvar"],
                               "n_pos": len(bk),
                               "names": [(st.sym, st.name, n) for st, n in bk],
                               "maxloss": sum(st.meta["max_loss"] * n for st, n in bk)})

    res = {"snapshot": snapshot, "screen": r, "betas": betas, "corr": corr,
           "panel_days": len(next(iter(P.values()))), "books": books,
           "sweep": sweep_rows, "spy": spy,
           "frontier": pf.frontier(cands, P, betas, spy, "ev_calm",
                                   [1500, 3000, 4500, 6000, 9000, 12000])}
    _write_csv(res)
    return res


def _write_csv(res: dict) -> None:
    r = res["screen"]
    with open(os.path.join(OUT, "etf_vol_screen.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "asset_class", "spot", "fwd_29d", "fwd_57d", "atm_iv_29d",
                    "atm_iv_57d", "iv_bid_29d", "iv_ask_29d", "exec_cost_volpts",
                    "horizontal_skew_volpts", "forward_vol_29_57", "forward_factor",
                    "rr25_volpts", "bf25_volpts", "har_rv", "blend_rv", "longrun_rv",
                    "vrp_median", "vrp_min", "vrp_max", "sleeve", "beta_vs_spy"])
        for sym, rec in r["symbols"].items():
            g = rec["vrp"]
            w.writerow([sym, rec["class"], f"{rec['S']:.4f}", f"{rec['F1']:.4f}",
                        f"{rec['F2']:.4f}" if rec["F2"] else "",
                        f"{rec['atm_iv1']:.6f}",
                        f"{rec['atm_iv2']:.6f}" if rec["atm_iv2"] else "",
                        f"{rec['iv_bid']:.6f}", f"{rec['iv_ask']:.6f}",
                        f"{(rec['iv_ask']-rec['iv_bid'])*100:.3f}",
                        f"{rec['hskew']*100:.3f}" if rec["hskew"] == rec["hskew"] else "",
                        f"{rec['fwd_vol']:.6f}" if rec["fwd_vol"] == rec["fwd_vol"] else "",
                        f"{rec['ff']:.4f}" if rec["ff"] == rec["ff"] else "",
                        f"{rec['rr25']*100:.3f}", f"{rec['bf25']*100:.3f}",
                        f"{g['forecasts']['HAR']:.6f}", f"{g['forecasts']['50-50']:.6f}",
                        f"{g['forecasts']['1yr mean']:.6f}",
                        f"{g['median']:.4f}", f"{g['min']:.4f}", f"{g['max']:.4f}",
                        rec["side"], f"{res['betas'][sym]:.4f}"])

    with open(os.path.join(OUT, "structure_scorecard.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "structure", "detail", "front_dte", "net_cost_usd",
                    "max_loss_usd", "delta", "gamma", "vega", "theta",
                    "ev_hold_all", "ev_hold_calm", "ev_managed", "managed_win",
                    "cvar5_hold", "n_windows", "n_distinct_episodes",
                    "widest_spread_pct", "min_oi", "exec_cost_usd", "gates_failed",
                    "gates_marginal", "expected_monthly_per_lot_calm"])
        for sym, rec in r["symbols"].items():
            for st in rec["structures"]:
                m = st.meta
                g = m["greeks"]
                w.writerow([sym, st.name, st.note, st.front_dte,
                            f"{st.fill_cost:.2f}", f"{m['max_loss']:.2f}",
                            f"{g['delta']:.3f}", f"{g['gamma']:.5f}",
                            f"{g['vega']:.3f}", f"{g['theta']:.3f}",
                            f"{m['ev'].get('ev', float('nan')):.2f}",
                            f"{m['ev_calm'].get('ev', float('nan')):.2f}",
                            f"{m['mev']['ev']:.2f}", f"{m['mev']['win']:.4f}",
                            f"{m['ev'].get('cvar5', float('nan')):.2f}",
                            m['ev'].get('n_windows', 0),
                            m['ev'].get('n_distinct_episodes', 0),
                            f"{st.worst_spread_pct:.4f}", st.min_oi,
                            f"{st.exec_cost:.2f}", m["fails"], m["marginals"],
                            f"{pf.expected_monthly(st, 'ev_calm'):.2f}"])

    for key, bkd in res["books"].items():
        with open(os.path.join(OUT, f"portfolio_{key}.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["symbol", "structure", "detail", "lots", "net_cost_usd",
                        "max_loss_usd", "delta", "beta_wtd_delta_usd", "vega",
                        "theta_per_day", "expected_monthly_usd"])
            for st, n in bkd["book"]:
                g = st.meta["greeks"]
                bw = n * g["delta"] * st.surfaces["T1"].S * res["betas"][st.sym]
                w.writerow([st.sym, st.name, st.note, n,
                            f"{st.fill_cost*n:.2f}", f"{st.meta['max_loss']*n:.2f}",
                            f"{g['delta']*n:.2f}", f"{bw:.2f}", f"{g['vega']*n:.2f}",
                            f"{g['theta']*n:.2f}",
                            f"{pf.expected_monthly(st, key)*n:.2f}"])
