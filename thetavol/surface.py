"""Per-expiry implied forward, smile fit, and term structure in TOTAL VARIANCE."""
from __future__ import annotations
import json, math, os
from dataclasses import dataclass, field
from . import bs
from .config import RISK_FREE

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "snapshots")


def load(snapshot: str) -> dict:
    root = os.path.join(DATA, snapshot)
    und = json.load(open(os.path.join(root, "underlyings.json")))
    ch = json.load(open(os.path.join(root, "chains.json")))
    px = {}
    for f in sorted(os.listdir(root)):
        if f.startswith("px_"):
            d = json.load(open(os.path.join(root, f)))
            px[d["symbol"]] = d["close"]
    return {"underlyings": {r["symbol"]: r for r in und["rows"]},
            "chains": ch, "px": px, "meta": {"und": und, "ch": ch}}


@dataclass
class ExpirySurface:
    sym: str
    label: str
    date: str
    dte: int
    T: float
    S: float
    F: float                 # implied forward from put-call parity
    atm_K: float
    atm_iv: float            # smile value at K = F  (ATM-forward vol)
    quotes: dict = field(default_factory=dict)   # (K, cp) -> dict
    smile: tuple = (0.0, 0.0, 0.0)               # quadratic in log-moneyness ln(K/F)

    xlo: float = -1e9
    xhi: float = 1e9
    wing_damp: float = 0.0

    def iv(self, K: float) -> float:
        """Quadratic in log-moneyness inside the quoted strike range; linear
        continuation (damped) outside it.  An unrestrained quadratic explodes in
        the wings and makes delta-based strike selection meaningless -- this is a
        MODEL choice and is disclosed as such wherever a wing IV is used."""
        a, b, c = self.smile
        x = math.log(K / self.F)
        if x < self.xlo:
            y0 = a + b * self.xlo + c * self.xlo ** 2
            sl = b + 2 * c * self.xlo
            return max(y0 + self.wing_damp * sl * (x - self.xlo), 0.02)
        if x > self.xhi:
            y0 = a + b * self.xhi + c * self.xhi ** 2
            sl = b + 2 * c * self.xhi
            return max(y0 + self.wing_damp * sl * (x - self.xhi), 0.02)
        return max(a + b * x + c * x * x, 0.02)

    def total_var(self) -> float:
        return self.atm_iv ** 2 * self.T

    def atm_iv_side(self, side: str) -> float:
        """ATM-forward vol re-inverted from the BID or ASK of the ATM straddle.

        side='bid' is what a seller of vol actually receives; side='ask' is what
        a buyer actually pays.  The gap between them IS this snapshot's measured
        execution cost, expressed in vol points.
        """
        K = self.atm_K
        tot = 0.0
        n = 0
        for cp in ("C", "P"):
            q = self.quotes.get((K, cp))
            if not q:
                continue
            px = q["bid"] if side == "bid" else q["ask"]
            iv = bs.implied_vol(px, self.F, K, self.T, RISK_FREE, cp)
            if iv:
                tot += iv
                n += 1
        return tot / n if n else self.atm_iv


def _fit_smile(pts: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Least-squares quadratic in log-moneyness. 2 points -> linear, 1 -> flat."""
    n = len(pts)
    if n == 1:
        return (pts[0][1], 0.0, 0.0)
    if n == 2:
        (x0, y0), (x1, y1) = pts
        b = (y1 - y0) / (x1 - x0)
        return (y0 - b * x0, b, 0.0)
    Sx = [sum(x ** k for x, _ in pts) for k in range(5)]
    Sy = [sum(y * x ** k for x, y in pts) for k in range(3)]
    A = [[Sx[0], Sx[1], Sx[2]], [Sx[1], Sx[2], Sx[3]], [Sx[2], Sx[3], Sx[4]]]
    B = [Sy[0], Sy[1], Sy[2]]
    for i in range(3):                          # Gaussian elimination
        p = max(range(i, 3), key=lambda k: abs(A[k][i]))
        A[i], A[p] = A[p], A[i]; B[i], B[p] = B[p], B[i]
        if abs(A[i][i]) < 1e-14:
            return (sum(y for _, y in pts) / n, 0.0, 0.0)
        for k in range(i + 1, 3):
            f = A[k][i] / A[i][i]
            for j in range(i, 3):
                A[k][j] -= f * A[i][j]
            B[k] -= f * B[i]
    z = [0.0, 0.0, 0.0]
    for i in (2, 1, 0):
        z[i] = (B[i] - sum(A[i][j] * z[j] for j in range(i + 1, 3))) / A[i][i]
    return (z[0], z[1], z[2])


def build(snapshot: str) -> dict[str, dict[str, ExpirySurface]]:
    d = load(snapshot)
    ch = d["chains"]
    exps = ch["expiries"]
    rows = ch["rows"]
    out: dict[str, dict[str, ExpirySurface]] = {}
    for sym, u in d["underlyings"].items():
        S = u["last"]
        for lab, e in exps.items():
            legs = [r for r in rows if r["sym"] == sym and r["exp"] == lab]
            if not legs:
                continue
            T = e["dte"] / 365.0
            q = {(r["K"], r["cp"]): {"bid": r["bid"], "ask": r["ask"],
                                     "mid": 0.5 * (r["bid"] + r["ask"]),
                                     "iv": r["iv"], "oi": r["oi"]} for r in legs}
            # --- implied forward from put-call parity at every strike quoted both ways
            pairs = [K for (K, cp) in q if cp == "C" and (K, "P") in q]
            if not pairs:
                continue
            Fs = [K + (q[(K, "C")]["mid"] - q[(K, "P")]["mid"]) * math.exp(RISK_FREE * T)
                  for K in pairs]
            F = sum(Fs) / len(Fs)
            atmK = min(pairs, key=lambda K: abs(K - F))
            # --- smile: re-invert every quoted mid off THIS forward, keep OTM wing only
            pts = []
            for (K, cp), v in q.items():
                if cp == "C" and K < F * 0.995:      # ITM call: use the put instead
                    continue
                if cp == "P" and K > F * 1.005:
                    continue
                iv = bs.implied_vol(v["mid"], F, K, T, RISK_FREE, cp)
                if iv is None:
                    iv = v["iv"]
                v["iv_refit"] = iv
                pts.append((math.log(K / F), iv))
            pts.sort()
            # de-duplicate x (ATM call and put share a strike)
            dedup = {}
            for x, y in pts:
                dedup.setdefault(round(x, 10), []).append(y)
            pts = [(x, sum(ys) / len(ys)) for x, ys in sorted(dedup.items())]
            smile = _fit_smile(pts)
            surf = ExpirySurface(sym, lab, e["date"], e["dte"], T, S, F, atmK,
                                 0.0, q, smile)
            surf.xlo = min(x for x, _ in pts)
            surf.xhi = max(x for x, _ in pts)
            surf.atm_iv = surf.iv(F)
            out.setdefault(sym, {})[lab] = surf
    return out


def forward_vol(s1: ExpirySurface, s2: ExpirySurface) -> float:
    """Vol implied for the window between the two expiries, in TOTAL VARIANCE."""
    w1, w2 = s1.total_var(), s2.total_var()
    dt = s2.T - s1.T
    if dt <= 0 or w2 <= w1:
        return float("nan")
    return math.sqrt((w2 - w1) / dt)


def forward_factor(s1: ExpirySurface, s2: ExpirySurface) -> float:
    """FF = (front IV - forward IV) / forward IV.  Ravi's entry rule: FF >= 0.20."""
    fv = forward_vol(s1, s2)
    if fv != fv:
        return float("nan")
    return (s1.atm_iv - fv) / fv
