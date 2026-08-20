"""All tunable trading rules live here. A strategy change should rarely touch another file."""

SNAPSHOT = "2026-08-20"
NLV = 300_000.0                  # reference portfolio, USD
RISK_FREE = 0.0386               # 3m CMT, 2026-08-18, Alpha Vantage TREASURY_YIELD (LIVE)
MULT = 100                       # equity-option contract multiplier

UNIVERSE = ["SPY", "QQQ", "IWM", "GLD", "SLV", "ARKK", "SMH", "KWEB", "FXI", "EWZ"]

ASSET_CLASS = {
    "SPY": "us large cap", "QQQ": "us tech", "IWM": "us small cap",
    "SMH": "semiconductors", "ARKK": "us growth/innovation",
    "GLD": "precious metals", "SLV": "precious metals",
    "KWEB": "china internet", "FXI": "china large cap", "EWZ": "latam equity",
}

# ---- objective -------------------------------------------------------------
MONTHLY_INCOME_TARGET = 0.02     # 2% of NLV per month
MAX_DAILY_DRAWDOWN = 0.02        # 2% of NLV, enforced as 99% 1-day VaR
VAR_CONFIDENCE = 0.99

# ---- regime split (which sleeve an ETF belongs in) -------------------------
# vrp = ATM implied at the front expiry / forecast realised vol over the same horizon
SELL_VOL_MIN_VRP = 1.05          # rich enough to sell
BUY_VOL_MAX_VRP = 0.98           # cheap enough to buy (calendar / long vega)
# between the two: no edge, stand aside

# ---- calendar / term-structure gates ---------------------------------------
# forward factor FF = (front_iv - forward_iv) / forward_iv  (Ravi's own definition)
FF_ENTRY = 0.20                  # long calendar wants front rich vs the forward
HORIZONTAL_SKEW_TOL = -1.00      # front_iv - back_iv, vol points; below this = reject
CAL_MIN_THETA_PCT_OF_DEBIT = 0.010   # 1.0% of debit per day at entry
CAL_MIN_ZONE_OVER_EM = 0.80      # profit-band half-width / front expected move

# ---- credit-structure gates ------------------------------------------------
SHORT_DELTA = {                  # default short-strike deltas by structure
    "iron_condor": 0.19, "wide_iron_condor": 0.14,
    "put_credit_spread": 0.26, "call_credit_spread": 0.24,
    "jade_lizard": 0.25, "short_strangle": 0.20,
}
CREDIT_TAKE_PROFIT = 0.50        # close winners at 50% of credit
CREDIT_STOP = 2.0                # defined risk: stop at 2x credit received
MANAGE_DTE = 21                  # close/roll regardless of P&L

# ---- sizing / restrictions -------------------------------------------------
MAX_LOSS_PER_TRADE = 0.02        # 2% of NLV, hard ceiling
MAX_POSITIONS_PER_UNDERLYING = 1
MAX_BOOK_AVG_CORR = 0.50
MAX_PAIR_CORR = 0.80
MAX_US_EQUITY_BLOCK = 3          # SPY/QQQ/IWM/SMH/ARKK are one bet wearing five tickers
TARGET_POSITIONS = (8, 12)       # LLN ceiling flattens here; costs bite past it

# ---- deployment scaled to the volatility regime (moderate column) ----------
DEPLOYMENT_MODERATE = [(0, 15, 0.38), (15, 30, 0.46), (30, 40, 0.55), (40, 999, 0.65)]

# ---- liquidity -------------------------------------------------------------
MAX_SPREAD_PCT_OF_MID = 0.08     # 8% gate
MIN_OPEN_INTEREST = 100

# ---- beta-weighted delta ---------------------------------------------------
BWD_BENCHMARK = "SPY"
BWD_LIMIT_USD = 0.02 * NLV       # |beta-weighted delta| in $ notional <= 2% of NLV

# ---- execution friction ----------------------------------------------------
# fill assumption: mid + FILL_FRACTION * half-spread against you, per leg
FILL_FRACTION = 0.35
COMMISSION_PER_CONTRACT = 0.65

# ---- vol-shock channel for 1-day VaR (MODEL, disclosed) --------------------
# dIV(vol points) = -LEVERAGE * sigma * r  +  CONVEX * sigma * (|r| - E|r|)
VOL_SHOCK = {
    "equity": {"leverage": 1.20, "convex": 0.50},
    "metal":  {"leverage": 0.30, "convex": 1.00},
}
VOL_SHOCK_CLASS = {
    "SPY": "equity", "QQQ": "equity", "IWM": "equity", "SMH": "equity",
    "ARKK": "equity", "KWEB": "equity", "FXI": "equity", "EWZ": "equity",
    "GLD": "metal", "SLV": "metal",
}
VOL_SHOCK_SCENARIOS = {"none": 0.0, "base": 1.0, "severe": 2.0}
