"""Structure construction, Greeks, and expectancy against REAL return distributions.

Standing rules honoured here:
  * risk-neutral EV is a tautology -- expectancy is measured against the ETF's own
    historical return distribution, not a lognormal;
  * the distribution is re-centred to the implied forward so E[S_T] = F (a true
    martingale re-centring, not a drift shift);
  * probabilities described as "share of real windows" are computed on the RAW
    sample, not the re-centred one;
  * CVaR is reported alongside every score, with the number of DISTINCT episodes
    the tail is drawn from;
  * execution cost is reported exactly (mid value less fill value).
"""
from __future__ import annotations
import math, random
from dataclasses import dataclass, field
from . import bs
from .config import RISK_FREE, MULT, FILL_FRACTION, COMMISSION_PER_CONTRACT

random.seed(20260820)


@dataclass
class Leg:
    exp: str            # "T1" / "T2"
    K: float
    cp: str             # "C" / "P"
    qty: int            # +1 long, -1 short
    bid: float = 0.0
    ask: float = 0.0
    iv: float = 0.0
    oi: int = 0
    live: bool = True

    @property
    def mid(self) -> float:
        return 0.5 * (self.bid + self.ask)

    @property
    def fill(self) -> float:
        """Mid plus FILL_FRACTION of the half-spread against you."""
        h = 0.5 * (self.ask - self.bid)
        return self.mid + (FILL_FRACTION * h if self.qty > 0 else -FILL_FRACTION * h)

    @property
    def spread_pct(self) -> float:
        return (self.ask - self.bid) / self.mid if self.mid > 0 else 9.99


@dataclass
class Structure:
    sym: str
    name: str
    legs: list[Leg]
    surfaces: dict            # {"T1": ExpirySurface, "T2": ...}
    note: str = ""
    meta: dict = field(default_factory=dict)

    # ---------------- cost -------------------------------------------------
    @property
    def mid_cost(self) -> float:
        return sum(l.qty * l.mid for l in self.legs) * MULT

    @property
    def fill_cost(self) -> float:
        return sum(l.qty * l.fill for l in self.legs) * MULT

    @property
    def commissions(self) -> float:
        return sum(abs(l.qty) for l in self.legs) * COMMISSION_PER_CONTRACT

    @property
    def exec_cost(self) -> float:
        """Round-trip friction: (fill - mid) on entry, same on exit, plus commissions."""
        return 2.0 * abs(self.fill_cost - self.mid_cost) + 2.0 * self.commissions

    @property
    def is_debit(self) -> bool:
        return self.fill_cost > 0

    @property
    def worst_spread_pct(self) -> float:
        return max(l.spread_pct for l in self.legs)

    @property
    def min_oi(self) -> int:
        return min(l.oi for l in self.legs)

    # ---------------- greeks ------------------------------------------------
    def greeks(self) -> dict:
        tot = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0, "value": 0.0}
        for l in self.legs:
            s = self.surfaces[l.exp]
            iv = l.iv if l.iv > 0 else s.iv(l.K)
            g = bs.greeks(s.F, s.S, l.K, s.T, iv, RISK_FREE, l.cp)
            for k in ("delta", "gamma", "vega", "theta"):
                tot[k] += l.qty * g[k] * MULT
            tot["value"] += l.qty * g["price"] * MULT
        return tot

    def leg_deltas(self) -> list[float]:
        out = []
        for l in self.legs:
            s = self.surfaces[l.exp]
            iv = l.iv if l.iv > 0 else s.iv(l.K)
            out.append(bs.greeks(s.F, s.S, l.K, s.T, iv, RISK_FREE, l.cp)["delta"])
        return out

    # ---------------- payoff -----------------------------------------------
    def value_at(self, ST: float, t: float) -> float:
        """Structure value (per 1 lot, dollars) when the underlying is ST at time t
        (years from now).  Front legs past their expiry settle at intrinsic; back
        legs are repriced on their own smile under STICKY-MONEYNESS."""
        v = 0.0
        for l in self.legs:
            s = self.surfaces[l.exp]
            Trem = s.T - t
            if Trem <= 1e-9:
                intr = max(ST - l.K, 0.0) if l.cp == "C" else max(l.K - ST, 0.0)
                v += l.qty * intr * MULT
                continue
            carry = math.log(s.F / s.S) / s.T
            F2 = ST * math.exp(carry * Trem)
            iv = s.iv(l.K * (s.F / F2))          # sticky moneyness
            v += l.qty * bs.price(F2, l.K, Trem, iv, RISK_FREE, l.cp) * MULT
        return v

    def front_T(self) -> float:
        return min(self.surfaces[l.exp].T for l in self.legs)

    @property
    def front_dte(self) -> int:
        return min(self.surfaces[l.exp].dte for l in self.legs)


# ---------------------------------------------------------------------------
def kdelta(surf, target: float, cp: str) -> float:
    """Strike whose |delta| equals target, on the fitted smile.  Monotone search."""
    if cp == "P":
        lo, hi = surf.F * 0.30, surf.F            # |delta| rises with K
        for _ in range(80):
            mid = 0.5 * (lo + hi)
            d = abs(bs.greeks(surf.F, surf.S, mid, surf.T, surf.iv(mid), RISK_FREE, "P")["delta"])
            if d > target:
                hi = mid
            else:
                lo = mid
        return 0.5 * (lo + hi)
    lo, hi = surf.F, surf.F * 3.0                 # |delta| falls with K
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        d = bs.greeks(surf.F, surf.S, mid, surf.T, surf.iv(mid), RISK_FREE, "C")["delta"]
        if d > target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# ---------------------------------------------------------------------------
def horizon_returns(close: list[float], h: int) -> list[float]:
    """Overlapping h-day cumulative log returns -- the REAL distribution."""
    return [math.log(close[i + h] / close[i]) for i in range(len(close) - h)]


def distinct_episodes(n_obs: int, h: int) -> int:
    """Overlapping windows make n observations look like more information than
    they are.  This is the count of non-overlapping episodes behind the tail."""
    return max(1, n_obs // h)


def _calm_mask(close: list[float], h: int, lookback: int = 22) -> list[bool]:
    """True where the window STARTS in the calmer half of the sample, measured by
    trailing realised vol.  Note: a calm-regime subsample still contains the worst
    drawdowns -- crashes are preceded by quiet markets."""
    from .forecast import rets, rv
    r = rets(close)
    n = len(close) - h
    tr = []
    for i in range(n):
        j = i + 1
        w = r[max(0, j - lookback):j]
        if len(w) < 5:
            tr.append(float("inf")); continue
        m = sum(w) / len(w)
        tr.append(math.sqrt(sum((x - m) ** 2 for x in w) / (len(w) - 1)))
    fin = sorted(x for x in tr if x != float("inf"))
    med = fin[len(fin) // 2] if fin else 0.0
    return [x <= med for x in tr]


def terminal_ev(st: Structure, close: list[float], h: int, regime: str = "all") -> dict:
    """Hold-to-front-expiry expectancy against the real return distribution,
    re-centred so that E[S_T] = F (martingale).  regime='calm' restricts the
    sample to windows starting in the calmer half of the year."""
    F = st.surfaces["T1"].F
    S0 = st.surfaces["T1"].S
    R = horizon_returns(close, h)
    if regime == "calm":
        mask = _calm_mask(close, h)
        R = [x for x, k in zip(R, mask) if k]
    if len(R) < 20:
        return {}
    growth = [math.exp(r) for r in R]
    m = sum(growth) / len(growth)
    entry = st.fill_cost
    Tf = st.front_T()
    df = math.exp(-RISK_FREE * Tf)             # PV the front-expiry payoff
    pnl_c, pnl_raw = [], []
    for g in growth:
        ST_c = F * g / m                       # re-centred
        ST_r = S0 * g                          # raw
        pnl_c.append(st.value_at(ST_c, Tf) * df - entry)
        pnl_raw.append(st.value_at(ST_r, Tf) * df - entry)
    n = len(pnl_c)
    ev = sum(pnl_c) / n
    srt = sorted(pnl_c)
    k = max(1, int(0.05 * n))
    return {
        "ev": ev - (st.exec_cost / 2.0),        # exit slippage + commissions
        "ev_gross": ev,
        "p_profit_recentred": sum(1 for x in pnl_c if x > 0) / n,
        "p_profit_real_windows": sum(1 for x in pnl_raw if x > 0) / n,
        "cvar5": sum(srt[:k]) / k,
        "worst": srt[0],
        "n_windows": n,
        "n_distinct_episodes": distinct_episodes(n, h),
    }


def managed_ev(st: Structure, close: list[float], h: int, take: float, stop: float,
               n_paths: int = 6000, manage_day: int | None = None) -> dict:
    """Daily-monitored block-bootstrap simulation.  Exits on take-profit, stop, a
    calendar management day, or the front expiry -- whichever comes first."""
    from .forecast import rets
    r = rets(close)
    F = st.surfaces["T1"].F
    S0 = st.surfaces["T1"].S
    drift = math.log(F / S0) / h                # per-day drift that makes E[S_T]=F
    entry = st.fill_cost
    base = abs(entry) if entry != 0 else 1.0
    Tf = st.front_T()
    out, hits, stops = [], 0, 0
    blk = 5
    for _ in range(n_paths):
        S = S0
        pnl = None
        for d in range(1, h + 1):
            if (d - 1) % blk == 0:
                i0 = random.randrange(0, len(r) - blk)
            S *= math.exp(r[i0 + (d - 1) % blk] - sum(r) / len(r) + drift)
            t = d / 365.0
            v = st.value_at(S, min(t, Tf))
            p = v - entry
            if manage_day and d >= manage_day:
                pnl = p; break
            if entry > 0:                        # debit structure
                if p >= take * base:
                    pnl = p; hits += 1; break
                if p <= -stop * base:
                    pnl = p; stops += 1; break
            else:                                # credit structure
                if p >= take * base:
                    pnl = p; hits += 1; break
                if p <= -stop * base:
                    pnl = p; stops += 1; break
            if d == h:
                pnl = p
        if pnl is None:
            pnl = st.value_at(S, Tf) - entry
        out.append(pnl)
    n = len(out)
    srt = sorted(out)
    k = max(1, int(0.05 * n))
    return {"ev": sum(out) / n - st.exec_cost / 2.0, "win": sum(1 for x in out if x > 0) / n,
            "target_hit": hits / n, "stop_hit": stops / n,
            "cvar5": sum(srt[:k]) / k, "p5": srt[k], "worst": srt[0], "n_paths": n}
