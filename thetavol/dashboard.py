"""Renders the HTML task board from a report.build() result."""
from __future__ import annotations
import html, math, os, re
from .config import *
from . import portfolio as pf, structures as stc

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out")

PILL = {"PASS": "ok", "MARGINAL": "warn", "FAIL": "bad", "n/a": "na"}


def _e(x):
    return html.escape(str(x))


def _num(x, d=2, pct=False, sign=False):
    if x is None or (isinstance(x, float) and x != x):
        return '<span class="na">n/a</span>'
    v = x * 100 if pct else x
    s = f"{v:+,.{d}f}" if sign else f"{v:,.{d}f}"
    cls = "pos" if v > 0 else ("neg" if v < 0 else "")
    return f'<span class="{cls}">{s}{"%" if pct else ""}</span>'


def _money(x, d=0):
    if x is None or (isinstance(x, float) and x != x):
        return '<span class="na">n/a</span>'
    cls = "pos" if x > 0 else ("neg" if x < 0 else "")
    return f'<span class="{cls}">{"-" if x < 0 else ""}${abs(x):,.{d}f}</span>'


# ---------------------------------------------------------------- smile card
def _smile_svg(rec, surfaces) -> str:
    s1 = surfaces[rec["sym"]]["T1"]
    s2 = surfaces[rec["sym"]].get("T2")
    W, H = 220, 96
    span = max(abs(s1.xlo), abs(s1.xhi)) * 1.06     # only the quoted strike range
    xs = [-span + 2 * span * i / 48 for i in range(49)]
    def pts(s):
        out = []
        for x in xs:
            K = s.F * math.exp(x)
            out.append((x, s.iv(K)))
        return out
    p1 = pts(s1)
    p2 = pts(s2) if s2 else []
    allv = [v for _, v in p1 + p2]
    lo, hi = min(allv) * 0.96, max(allv) * 1.04
    def X(x): return 8 + (x + span) / (2 * span) * (W - 16)
    def Y(v): return H - 14 - (v - lo) / (hi - lo) * (H - 28)
    def path(p):
        return " ".join(("M" if i == 0 else "L") + f"{X(x):.1f},{Y(v):.1f}"
                        for i, (x, v) in enumerate(p))
    put = " ".join(("M" if i == 0 else "L") + f"{X(x):.1f},{Y(v):.1f}"
                   for i, (x, v) in enumerate([q for q in p1 if q[0] <= 0]))
    call = " ".join(("M" if i == 0 else "L") + f"{X(x):.1f},{Y(v):.1f}"
                    for i, (x, v) in enumerate([q for q in p1 if q[0] >= 0]))
    dots = ""
    for (K, cp), q in sorted(s1.quotes.items()):
        x = math.log(K / s1.F)
        if abs(x) > span:
            continue
        iv = q.get("iv_refit", s1.iv(K))
        col = "var(--call)" if cp == "C" else "var(--put)"
        dots += f'<circle cx="{X(x):.1f}" cy="{Y(iv):.1f}" r="2.4" fill="{col}"/>'
    back = f'<path d="{path(p2)}" fill="none" stroke="var(--line-2)" stroke-width="1.6" stroke-dasharray="3 2.5"/>' if p2 else ""
    return (f'<svg viewBox="0 0 {W} {H}" class="smile" role="img" '
            f'aria-label="{_e(rec["sym"])} implied volatility smile">'
            f'<line x1="{X(0):.1f}" y1="6" x2="{X(0):.1f}" y2="{H-8}" stroke="var(--line)" stroke-width="1"/>'
            f'{back}'
            f'<path d="{put}" fill="none" stroke="var(--put)" stroke-width="2.2"/>'
            f'<path d="{call}" fill="none" stroke="var(--call)" stroke-width="2.2"/>'
            f'{dots}</svg>')


def _term_svg(res) -> str:
    rows = [(s, r) for s, r in res["screen"]["symbols"].items() if r["atm_iv2"]]
    W, H = 980, 250
    pad_l, pad_b = 46, 30
    maxv = max(max(r["atm_iv1"], r["atm_iv2"], r["fwd_vol"]) for _, r in rows) * 1.10
    n = len(rows)
    bw = (W - pad_l - 16) / n
    body = ""
    for i, (sym, r) in enumerate(rows):
        cx = pad_l + bw * (i + 0.5)
        def Y(v): return H - pad_b - v / maxv * (H - pad_b - 18)
        y1, y2, yf = Y(r["atm_iv1"]), Y(r["atm_iv2"]), Y(r["fwd_vol"])
        good = r["hskew"] > 0
        col = "var(--ok)" if good else "var(--bad)"
        body += (f'<line x1="{cx-14:.1f}" y1="{y1:.1f}" x2="{cx+14:.1f}" y2="{y1:.1f}" stroke="var(--front)" stroke-width="3"/>'
                 f'<line x1="{cx-14:.1f}" y1="{y2:.1f}" x2="{cx+14:.1f}" y2="{y2:.1f}" stroke="var(--back)" stroke-width="3"/>'
                 f'<line x1="{cx:.1f}" y1="{y1:.1f}" x2="{cx:.1f}" y2="{y2:.1f}" stroke="{col}" stroke-width="1.4"/>'
                 f'<circle cx="{cx:.1f}" cy="{yf:.1f}" r="3" fill="none" stroke="var(--accent)" stroke-width="1.6"/>'
                 f'<text class="ax" x="{cx:.1f}" y="{H-10}" text-anchor="middle">{_e(sym)}</text>'
                 f'<text class="ax sm" x="{cx:.1f}" y="{min(y1,y2)-11:.1f}" text-anchor="middle" fill="{col}">{r["hskew"]*100:+.1f}</text>')
    grid = ""
    step = 0.10
    v = step
    while v < maxv:
        y = H - pad_b - v / maxv * (H - pad_b - 18)
        grid += (f'<line x1="{pad_l-6}" y1="{y:.1f}" x2="{W-8}" y2="{y:.1f}" stroke="var(--line)" stroke-width="1"/>'
                 f'<text class="ax" x="{pad_l-10}" y="{y+4:.1f}" text-anchor="end">{v*100:.0f}%</text>')
        v += step
    return (f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" '
            f'aria-label="ATM implied vol at 29 and 57 days, and the forward vol between them">'
            f'{grid}{body}</svg>')


def _sweep_svg(res) -> str:
    rows = res["sweep"]
    if not rows:
        return ""
    W, H = 980, 260
    pl, pb = 56, 34
    maxy = max(max(r["income_pct"] for r in rows), MONTHLY_INCOME_TARGET) * 1.15
    xs = [r["k"] for r in rows]
    def X(k): return pl + (k - xs[0]) / (xs[-1] - xs[0]) * (W - pl - 18)
    def Y(v): return H - pb - v / maxy * (H - pb - 20)
    line = " ".join(("M" if i == 0 else "L") + f"{X(r['k']):.1f},{Y(r['income_pct']):.1f}"
                    for i, r in enumerate(rows))
    area = f"M{X(xs[0]):.1f},{H-pb} " + " ".join(f"L{X(r['k']):.1f},{Y(r['income_pct']):.1f}" for r in rows) + f" L{X(xs[-1]):.1f},{H-pb} Z"
    ty = Y(MONTHLY_INCOME_TARGET)
    dots = "".join(f'<circle cx="{X(r["k"]):.1f}" cy="{Y(r["income_pct"]):.1f}" r="3.2" fill="var(--accent)"/>'
                   f'<text class="ax sm" x="{X(r["k"]):.1f}" y="{Y(r["income_pct"])-9:.1f}" text-anchor="middle">{r["income_pct"]*100:.2f}%</text>'
                   for r in rows)
    ticks = "".join(f'<text class="ax" x="{X(r["k"]):.1f}" y="{H-12}" text-anchor="middle">{r["k"]:.2f}x</text>' for r in rows)
    return (f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" '
            f'aria-label="Achievable monthly income as implied volatility scales">'
            f'<path d="{area}" fill="var(--accent-soft)"/>'
            f'<path d="{line}" fill="none" stroke="var(--accent)" stroke-width="2.4"/>'
            f'<line x1="{pl}" y1="{ty:.1f}" x2="{W-18}" y2="{ty:.1f}" stroke="var(--bad)" stroke-width="1.4" stroke-dasharray="5 4"/>'
            f'<text class="ax" x="{pl+4}" y="{ty-8:.1f}" fill="var(--bad)">2.00% monthly target</text>'
            f'{dots}{ticks}</svg>')


def _gate_row(st) -> str:
    cells = ""
    for k in ["liquidity", "open interest", "sleeve/VRP", "horizontal skew",
              "forward factor", "theta rate", "hold EV", "managed EV",
              "sizing (2% cap)", "DTE window"]:
        v = st.meta["gates"].get(k)
        if not v:
            continue
        cells += (f'<div class="gate"><span class="pill {PILL[v[0]]}">{_e(v[0])}</span>'
                  f'<span class="gk">{_e(k)}</span><span class="gd">{_e(v[1])}</span></div>')
    return cells


def render(res: dict, path: str | None = None) -> str:
    path = path or os.path.join(OUT, "thetavol_taskboard.html")
    scr = res["screen"]
    surfaces = scr["meta"]["surfaces"]
    calm = res["books"]["ev_calm"]
    allb = res["books"]["mev"]
    sweep = res["sweep"]
    need = next((r["k"] for r in sweep if r["income_pct"] >= MONTHLY_INCOME_TARGET), None)
    bias = scr["meta"]["iv_bias"]

    # ---- screen rows
    rows = ""
    for sym, r in scr["symbols"].items():
        sleeve_cls = {"BUY VOL": "ok", "SELL VOL": "ok", "no edge": "na"}[r["side"]]
        rows += f"""<tr>
<td class="sym">{_e(sym)}<span class="cls">{_e(r['class'])}</span></td>
<td class="sm">{_smile_svg(r, surfaces)}</td>
<td class="n">{r['S']:,.2f}</td>
<td class="n">{r['F1']:,.2f}<span class="sub">{(r['F1']/r['S']-1)*100:+.2f}%</span></td>
<td class="n">{r['atm_iv1']*100:.2f}</td>
<td class="n">{(f"{r['atm_iv2']*100:.2f}" if r['atm_iv2'] else '<span class="na">n/a</span>')}</td>
<td class="n">{_num(r['hskew']*100, 2, sign=True)}</td>
<td class="n">{_num(r['ff'], 3, sign=True)}</td>
<td class="n">{_num(r['rr25']*100, 2, sign=True)}</td>
<td class="n">{r['vrp']['forecasts']['HAR']*100:.1f}</td>
<td class="n">{r['vrp']['median']:.3f}<span class="sub">{r['vrp']['min']:.2f}–{r['vrp']['max']:.2f}</span></td>
<td class="n">{(r['iv_ask']-r['iv_bid'])*100:.2f}</td>
<td><span class="pill {sleeve_cls}">{_e(r['side'])}</span></td>
<td class="n">{res['betas'][sym]:.2f}</td>
</tr>"""

    # ---- structures
    struct = ""
    for sym, r in scr["symbols"].items():
        for st in r["structures"]:
            m = st.meta
            g = m["greeks"]
            verdict = "bad" if m["fails"] else ("warn" if m["marginals"] else "ok")
            vlabel = f"{m['fails']} FAIL" if m["fails"] else (f"{m['marginals']} MARGINAL" if m["marginals"] else "CLEAR")
            struct += f"""<details class="st {verdict}">
<summary><span class="stsym">{_e(sym)}</span><span class="stname">{_e(st.name)}</span>
<span class="stnote">{_e(st.note)}</span>
<span class="stnums">{_money(st.fill_cost)} net · max loss {_money(m['max_loss'])} · θ {_money(g['theta'],1)}/d · vega {_money(g['vega'],0)}/vp</span>
<span class="pill {verdict}">{_e(vlabel)}</span></summary>
<div class="stbody">
<div class="evgrid">
<div class="ev"><span class="evk">EV, all regimes</span><span class="evv">{_money(m['ev'].get('ev'))}</span><span class="evs">{m['ev'].get('n_windows',0)} windows · {m['ev'].get('n_distinct_episodes',0)} distinct episodes</span></div>
<div class="ev"><span class="evk">EV, calm regime</span><span class="evv">{_money(m['ev_calm'].get('ev'))}</span><span class="evs">calmer half of the sample by trailing RV</span></div>
<div class="ev"><span class="evk">EV, managed exit</span><span class="evv">{_money(m['mev']['ev'])}</span><span class="evs">win {m['mev']['win']*100:.0f}% · stop {m['mev']['stop_hit']*100:.0f}% · {m['mev']['n_paths']:,} paths</span></div>
<div class="ev"><span class="evk">CVaR 5%</span><span class="evv">{_money(m['ev'].get('cvar5'))}</span><span class="evs">mean of the worst 5% of real windows</span></div>
<div class="ev"><span class="evk">Execution cost</span><span class="evv">{_money(st.exec_cost)}</span><span class="evs">round trip, mid less fill + commissions</span></div>
<div class="ev"><span class="evk">Leg deltas</span><span class="evv mono">{' / '.join(f'{abs(d)*100:.0f}' for d in m['leg_deltas'])}</span><span class="evs">standing rule wants 16–25 on every leg</span></div>
</div>
<div class="gates">{_gate_row(st)}</div>
</div></details>"""

    # ---- book
    book_rows = ""
    for st, n in calm["book"]:
        g = st.meta["greeks"]
        bw = n * g["delta"] * st.surfaces["T1"].S * res["betas"][st.sym]
        book_rows += f"""<tr><td class="sym">{_e(st.sym)}</td><td>{_e(st.name)}</td>
<td class="det">{_e(st.note)}</td><td class="n">{n}</td>
<td class="n">{_money(st.fill_cost*n)}</td><td class="n">{_money(st.meta['max_loss']*n)}</td>
<td class="n">{g['delta']*n:+.1f}</td><td class="n">{_money(bw)}</td>
<td class="n">{_money(g['vega']*n,0)}</td><td class="n">{_money(g['theta']*n,1)}</td>
<td class="n">{_money(pf.expected_monthly(st,'ev_calm')*n)}</td></tr>"""

    stress_rows = "".join(
        f'<tr><td>{_e(k)}</td><td class="n">{_money(v)}</td>'
        f'<td class="n">{v/NLV*100:+.2f}%</td></tr>'
        for k, v in calm["stress"].items())

    corr_head = "".join(f"<th>{_e(s)}</th>" for s in UNIVERSE)
    corr_rows = ""
    for a in UNIVERSE:
        cells = ""
        for b in UNIVERSE:
            c = res["corr"][(a, b)]
            op = min(abs(c), 1.0)
            cells += (f'<td class="cc" style="background:color-mix(in oklab, var(--accent) {op*70:.0f}%, transparent)">'
                      f'{c:.2f}</td>')
        corr_rows += f'<tr><th class="rh">{_e(a)}</th>{cells}</tr>'

    sweep_rows = "".join(
        f'<tr><td class="n">{r["k"]:.2f}x</td><td class="n">{_money(r["income"])}</td>'
        f'<td class="n">{r["income_pct"]*100:.2f}%</td><td class="n">{_money(r["var"])}</td>'
        f'<td class="n">{r["n_pos"]}</td><td class="det">{_e(", ".join(f"{n}x {s}" for s,_,n in r["names"]))}</td></tr>'
        for r in sweep)

    front_rows = "".join(
        f'<tr><td class="n">{_money(r["cap"])}</td><td class="n">{_money(r["income"])}</td>'
        f'<td class="n">{r["income_pct"]*100:.2f}%</td><td class="n">{_money(r["var"])}</td>'
        f'<td class="n">{_money(r["cvar"])}</td><td class="n">{r["n_pos"]}</td>'
        f'<td class="n">{_money(r["maxloss"])}</td></tr>'
        for r in res["frontier"])

    bias_rows = "".join(
        f'<tr><td class="sym">{_e(k)}</td><td class="n">{v*100:+.2f}%</td></tr>'
        for k, v in bias.items() if not k.startswith("_"))

    out = _fill(_template(), dict(
        snapshot=res["snapshot"], nlv=f"{NLV:,.0f}",
        rows=rows, struct=struct, book_rows=book_rows,
        term_svg=_term_svg(res), sweep_svg=_sweep_svg(res),
        corr_head=corr_head, corr_rows=corr_rows,
        sweep_rows=sweep_rows, front_rows=front_rows, stress_rows=stress_rows,
        bias_rows=bias_rows,
        calm_income=f"{calm['income']:,.0f}", calm_pct=f"{calm['income_pct']*100:.2f}",
        all_income=f"{allb['income']:,.0f}", all_pct=f"{allb['income_pct']*100:.2f}",
        var_base=f"{calm['var']['base']:,.0f}", var_none=f"{calm['var']['none']:,.0f}",
        var_sev=f"{calm['var']['severe']:,.0f}", var_cap=f"{MAX_DAILY_DRAWDOWN*NLV:,.0f}",
        cvar=f"{calm['cvar']:,.0f}", sharpe=f"{calm['sharpe']:.2f}",
        bwd=f"{calm['bwd']['usd']:,.0f}", bwd_sh=f"{calm['bwd']['spy_shares']:+.1f}",
        hedge=f"{calm['hedge_spy']:+.1f}", theta=f"{calm['theta']:,.0f}",
        vega=f"{calm['vega']:,.0f}", maxloss=f"{calm['maxloss']:,.0f}",
        npos=len(calm["book"]),
        need=(f"{need:.2f}x" if need else "beyond 1.60x"),
        bias_med=f"{bias['_median']*100:+.2f}",
        panel_days=res["panel_days"], r=f"{RISK_FREE*100:.2f}",
        n_struct=sum(len(r["structures"]) for r in scr["symbols"].values()),
        n_pos_ev=sum(1 for r in scr["symbols"].values() for st in r["structures"]
                     if st.meta["ev_calm"].get("ev", 0) > 0),
        sweep_base=(f"{sweep[0]['income_pct']*100:.2f}" if sweep else "n/a"),
    ))
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)
    return path


def _template() -> str:
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "board_template.html")
    return open(p, encoding="utf-8").read()


def _fill(tpl: str, values: dict) -> str:
    """Token substitution on @@name@@.  Deliberately not str.format: the template
    is real CSS and HTML, and doubling every brace to survive format() is how
    template bugs get in."""
    for k, v in values.items():
        tpl = tpl.replace(f"@@{k}@@", str(v))
    left = re.findall(r"@@[a-z_]+@@", tpl)
    if left:
        raise KeyError(f"unfilled template tokens: {sorted(set(left))}")
    return tpl
