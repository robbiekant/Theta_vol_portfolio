"""thetavol CLI:  python -m thetavol.cli <verb>

verbs:
  screen      per-ETF vol-surface screen (forward, smile, term structure, VRP sleeve)
  structures  full structure scorecard with gates and expectancy
  portfolio   run the optimiser and print the book under all three EV measures
  sweep       VRP regime sweep: what implied-vol level the 2%/month target needs
  dashboard   regenerate the HTML task board into out/
  run         everything, then write out/*.csv and the dashboard
"""
from __future__ import annotations
import sys, json
from .config import *
from . import report, portfolio as pf


def _fmt(res):
    r = res["screen"]
    print(f"\n=== SURFACE SCREEN  ({res['snapshot']}) ===")
    print(f"{'sym':5s} {'spot':>9s} {'F29':>9s} {'atm29':>6s} {'atm57':>6s} {'hskew':>6s} "
          f"{'fwdvol':>6s} {'FF':>7s} {'RR25':>6s} {'BF25':>6s} {'vrpMed':>6s} sleeve")
    for sym, rec in r["symbols"].items():
        print(f"{sym:5s} {rec['S']:9.2f} {rec['F1']:9.2f} {rec['atm_iv1']*100:6.2f} "
              f"{(rec['atm_iv2'] or float('nan'))*100 if rec['atm_iv2'] else float('nan'):6.2f} "
              f"{rec['hskew']*100 if rec['hskew']==rec['hskew'] else float('nan'):6.2f} "
              f"{rec['fwd_vol']*100 if rec['fwd_vol']==rec['fwd_vol'] else float('nan'):6.2f} "
              f"{rec['ff'] if rec['ff']==rec['ff'] else float('nan'):7.3f} "
              f"{rec['rr25']*100:6.2f} {rec['bf25']*100:6.2f} {rec['vrp']['median']:6.3f} {rec['side']}")


def main(argv=None):
    argv = argv or sys.argv[1:]
    verb = argv[0] if argv else "run"
    res = report.build(sweep=(verb in ("run", "sweep")))
    if verb in ("screen", "run"):
        _fmt(res)
    if verb in ("structures", "run"):
        print("\n=== STRUCTURE SCORECARD ===")
        for sym, rec in res["screen"]["symbols"].items():
            for st in rec["structures"]:
                m = st.meta
                print(f"{sym:5s} {st.name:24s} {st.front_dte:3d}d  net ${st.fill_cost:8.0f}  "
                      f"maxloss ${m['max_loss']:7.0f}  EVall ${m['ev'].get('ev',0):7.0f}  "
                      f"EVcalm ${m['ev_calm'].get('ev',0):7.0f}  mEV ${m['mev']['ev']:7.0f}  "
                      f"FAIL {m['fails']} MARG {m['marginals']}")
    if verb in ("portfolio", "run"):
        print("\n=== PORTFOLIO ===")
        for key, bk in res["books"].items():
            print(f"\n[{bk['label']}]  income ${bk['income']:,.0f}/mo = {bk['income_pct']*100:.2f}% "
                  f"| VaR99 ${bk['var']['base']:,.0f} (cap ${MAX_DAILY_DRAWDOWN*NLV:,.0f}) "
                  f"| CVaR ${bk['cvar']:,.0f} | Sharpe {bk['sharpe']:.2f}")
            for st, n in bk["book"]:
                print(f"    {n:4d}x {st.sym:5s} {st.name:24s} {st.note}")
            print(f"    beta-weighted delta ${bk['bwd']['usd']:,.0f} "
                  f"-> hedge {bk['hedge_spy']:+.1f} SPY-equivalent shares")
    if verb in ("sweep", "run"):
        print("\n=== VRP REGIME SWEEP (only implied vol changes) ===")
        for row in res["sweep"]:
            print(f"  IV x{row['k']:.2f}  income ${row['income']:7,.0f}/mo "
                  f"({row['income_pct']*100:5.2f}%)  VaR ${row['var']:6,.0f}  "
                  f"{row['n_pos']} positions")
    if verb in ("dashboard", "run"):
        from . import dashboard
        p = dashboard.render(res)
        print(f"\ndashboard -> {p}")
    return res


if __name__ == "__main__":
    main()
