"""Central configuration — thresholds and settings live here, not in strategy files."""

from pathlib import Path

from dotenv import load_dotenv
import os

# Load .env from project root (parent of src/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

# --- Promotion overrides overlay ---
# The research promotion engine (src/research/promotion.py) writes vetted
# config changes to data/config_overrides.json. Applied here, before any
# os.getenv() below, so a promoted value wins over .env defaults. Keys are
# whitelisted at write time; this loader additionally refuses anything that
# is not a plain scalar. Delete the file (or run the rollback command) to
# return to .env values. Requires worker restart to take effect.
import json as _json

# Same whitelist as src/research/promotion.py (duplicated here to avoid a
# circular import). Defense in depth: even a hand-edited overrides file can
# only touch strategy-tuning keys, never credentials/DB/direction policy.
_OVERRIDE_PREFIXES = (
    "MEANREV_", "CONFLUENCE_", "LSR_", "SETUP_", "SCAN_", "COOLDOWN_",
    "FEE_", "FUNDING_RATE_", "CARRY_", "POS_SHORT_", "BREAKOUT_", "TSMOM_",
)
_OVERRIDE_EXACT = {
    "ACTIVE_STRATEGY",
    "MIN_CONFIDENCE",
    "MAX_OPEN_POSITIONS",
    "CATEGORY_STRATEGY_MODE",
}

_OVERRIDES_FILE = Path(
    os.getenv("DATA_DIR", str(_PROJECT_ROOT / "data"))
) / "config_overrides.json"
if _OVERRIDES_FILE.exists():
    try:
        for _key, _value in _json.loads(_OVERRIDES_FILE.read_text()).items():
            if (
                isinstance(_key, str)
                and isinstance(_value, (str, int, float, bool))
                and (_key in _OVERRIDE_EXACT or _key.startswith(_OVERRIDE_PREFIXES))
            ):
                os.environ[_key] = str(_value)
    except Exception:  # corrupt overrides must never brick the worker
        pass


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() in ("true", "1", "yes", "on")

# --- Paths / database ---
DATA_DIR = Path(os.getenv("DATA_DIR", str(_PROJECT_ROOT / "data")))
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", DATA_DIR / "trading.db"))
# When set (e.g. by Railway's Postgres plugin), storage uses Postgres instead
# of the SQLite file above. Postgres survives redeploys and can be shared by
# the web and worker services.
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# --- API server ---
API_HOST = os.getenv("API_HOST", "0.0.0.0")


def is_cloud_runtime() -> bool:
    """True when running on a cloud host (no local Mac / ngrok needed)."""
    if os.getenv("DEPLOYMENT_MODE", "").lower() == "cloud":
        return True
    return bool(
        os.getenv("RAILWAY_ENVIRONMENT")
        or os.getenv("RAILWAY_PUBLIC_DOMAIN")
        or os.getenv("RENDER")
        or os.getenv("RENDER_EXTERNAL_URL")
        or os.getenv("FLY_APP_NAME")
    )


def public_base_url() -> str | None:
    """HTTPS base URL for phone/browser access (cloud URL or ngrok)."""
    explicit = os.getenv("PUBLIC_URL", "").strip().rstrip("/")
    if explicit:
        return explicit
    railway = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
    if railway:
        return f"https://{railway.rstrip('/')}"
    render = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    if render:
        return render
    fly_app = os.getenv("FLY_APP_NAME", "").strip()
    if fly_app:
        return f"https://{fly_app}.fly.dev"
    return None


# Cloud hosts set PORT and require the app to bind it; API_PORT remains the local override.
API_PORT = int(
    os.getenv("PORT")
    if is_cloud_runtime() and os.getenv("PORT")
    else (os.getenv("API_PORT") or os.getenv("PORT") or "8000")
)

# --- Binance / ccxt ---
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
BINANCE_TESTNET = os.getenv("BINANCE_TESTNET", "true").lower() in ("true", "1", "yes")
# Paper trading should use real public market data even when live orders stay
# disabled/testnet. Binance futures testnet often lacks real alt history, which
# produces 0/48 candle cards on the dashboard.
BINANCE_MARKET_DATA_TESTNET = _env_bool("BINANCE_MARKET_DATA_TESTNET", default=False)

# Default trading pair (legacy / BTC focus)
SYMBOL = os.getenv("SYMBOL", "BTC/USDT")
CCXT_SYMBOL = os.getenv("CCXT_SYMBOL", "BTC/USDT:USDT")  # perpetual futures

# Multi-symbol universe.
# SYMBOL_UNIVERSE=auto (default): discover every active Binance USDT perpetual
# at runtime, ranked by 24h dollar volume. TRADING_SYMBOLS then acts as the
# core list (gets full multi-timeframe collection) and the fallback when
# discovery fails. SYMBOL_UNIVERSE=static: track only TRADING_SYMBOLS.
SYMBOL_UNIVERSE = os.getenv("SYMBOL_UNIVERSE", "auto").strip().lower()
# 0 = no cap (all discovered symbols); set e.g. 100 to track only the top 100.
TRADING_SYMBOLS_LIMIT = int(os.getenv("TRADING_SYMBOLS_LIMIT", "0"))
_default_alts = "BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT,DOGE/USDT,ADA/USDT,AVAX/USDT,LINK/USDT,DOT/USDT"
TRADING_SYMBOLS = [
    s.strip() for s in os.getenv("TRADING_SYMBOLS", _default_alts).split(",") if s.strip()
]
# Active trading cycles use the top-ranked slice of the discovered/configured
# universe. Research/bootstrap scripts can still use the full universe when
# they call trading_symbols() directly.
ACTIVE_TRADING_SYMBOLS_LIMIT = int(os.getenv("ACTIVE_TRADING_SYMBOLS_LIMIT", "20"))
# 2026-07-14: no longer the binding constraint -- a position COUNT was an
# arbitrary proxy for risk. Kept only as a high safety ceiling against
# runaway bugs; the binding constraints are TOTAL_RISK_BUDGET_PCT and
# MAX_NET_BETA_EXPOSURE below (src/engine/risk_budget.py, both engines).
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "20"))
# Sum over open positions of dollars-at-risk-to-current-stop / equity.
# Research axis: [0.03, 0.05, 0.08].
TOTAL_RISK_BUDGET_PCT = float(os.getenv("TOTAL_RISK_BUDGET_PCT", "0.05"))
# |direction-signed, beta-weighted notional / equity| cap -- the real
# diversification constraint (measured effective breadth was 1.89: many alt
# positions collapse into one leveraged BTC bet). >=999 = unlimited.
# Research axis: [0.5, 1.0, 999].
MAX_NET_BETA_EXPOSURE = float(os.getenv("MAX_NET_BETA_EXPOSURE", "1.0"))
# Dashboard payload is expensive with hundreds of symbols; serve a cached
# build for this many seconds (a finished trading cycle invalidates it).
DASHBOARD_CACHE_SECONDS = int(os.getenv("DASHBOARD_CACHE_SECONDS", "45"))

# --- Analysis thresholds (not hardcoded in analyzer) ---
MOMENTUM_24H_STRONG_PCT = float(os.getenv("MOMENTUM_24H_STRONG_PCT", "3.0"))
MOMENTUM_7D_STRONG_PCT = float(os.getenv("MOMENTUM_7D_STRONG_PCT", "5.0"))
MOMENTUM_COUNTER_TREND_PCT = float(os.getenv("MOMENTUM_COUNTER_TREND_PCT", "5.0"))
SHORT_TERM_MIN_CONFIDENCE = float(os.getenv("SHORT_TERM_MIN_CONFIDENCE", "0.55"))
SWING_MIN_CONFIDENCE = float(os.getenv("SWING_MIN_CONFIDENCE", "0.55"))
PAPER_MIN_CONFIDENCE = float(os.getenv("PAPER_MIN_CONFIDENCE", "0.60"))
CATEGORY_STRATEGY_MODE = os.getenv("CATEGORY_STRATEGY_MODE", "off").strip().lower()

# Optional overrides: BTC/USDT=BTC/USDT:USDT,ETH/USDT=ETH/USDT:USDT
SYMBOL_CCXT_MAP: dict[str, str] = {}
for part in os.getenv("SYMBOL_CCXT_MAP", "").split(","):
    part = part.strip()
    if "=" in part:
        spot, ccxt_sym = part.split("=", 1)
        SYMBOL_CCXT_MAP[spot.strip()] = ccxt_sym.strip()

# --- Scheduler ---
COLLECTION_INTERVAL_MINUTES = int(os.getenv("COLLECTION_INTERVAL_MINUTES", "5"))
OHLCV_TIMEFRAMES = [
    s.strip()
    for s in os.getenv("OHLCV_TIMEFRAMES", "15m,1h,1d").split(",")
    if s.strip()
]
OHLCV_TIMEFRAME = os.getenv("OHLCV_TIMEFRAME", "1h")
# First fetch per symbol/timeframe pulls this many candles (720 x 1h = 30 days,
# enough for the quant evaluation metrics); later fetches are incremental.
OHLCV_LIMIT = int(os.getenv("OHLCV_LIMIT", "720"))
# The scheduler runs in a background thread and never blocks the web server.
# On cloud hosts (Railway, Fly, Render) it defaults OFF — Binance returns HTTP
# 451 from US cloud IPs, so collection/paper trading must run on the Mac Mini.
# Set RUN_TRADING_SCHEDULER=true only for local dev or a dedicated worker host.
RUN_TRADING_SCHEDULER = _env_bool(
    "RUN_TRADING_SCHEDULER",
    default=not is_cloud_runtime(),
)

# --- Funding Rate Reversal strategy thresholds ---
# Rates are expressed as decimals (0.001 = 0.1%)
FUNDING_RATE_SHORT_THRESHOLD = float(os.getenv("FUNDING_RATE_SHORT_THRESHOLD", "0.001"))
FUNDING_RATE_LONG_THRESHOLD = float(os.getenv("FUNDING_RATE_LONG_THRESHOLD", "-0.0005"))

# --- Mean Reversion strategy thresholds ---
MEANREV_RSI_PERIOD = int(os.getenv("MEANREV_RSI_PERIOD", "14"))
MEANREV_RSI_OVERSOLD = float(os.getenv("MEANREV_RSI_OVERSOLD", "30"))
MEANREV_RSI_OVERBOUGHT = float(os.getenv("MEANREV_RSI_OVERBOUGHT", "70"))
MEANREV_BB_PERIOD = int(os.getenv("MEANREV_BB_PERIOD", "20"))
MEANREV_BB_STD = float(os.getenv("MEANREV_BB_STD", "2.0"))

# --- Funding Carry strategy thresholds ---
# Rates are per-8h-settlement decimals (0.0001 = 0.01%/8h)
CARRY_ENTRY_RATE = float(os.getenv("CARRY_ENTRY_RATE", "0.0001"))
CARRY_ENTRY_CONSECUTIVE = int(os.getenv("CARRY_ENTRY_CONSECUTIVE", "6"))
CARRY_EXIT_RATE = float(os.getenv("CARRY_EXIT_RATE", "0.00005"))
CARRY_EXIT_CONSECUTIVE = int(os.getenv("CARRY_EXIT_CONSECUTIVE", "3"))
CARRY_MIN_HOLD_SETTLEMENTS = int(os.getenv("CARRY_MIN_HOLD_SETTLEMENTS", "21"))
# spot taker in+out (0.1% x2) + futures taker in+out (0.05% x2) = 0.30% notional
CARRY_FEE_ROUNDTRIP = float(os.getenv("CARRY_FEE_ROUNDTRIP", "0.003"))

# --- Positioning Short strategy thresholds ---
POS_SHORT_RATIO_PCTILE = float(os.getenv("POS_SHORT_RATIO_PCTILE", "0.95"))
POS_SHORT_RATIO_ABS = float(os.getenv("POS_SHORT_RATIO_ABS", "2.5"))
POS_SHORT_FUNDING_MIN = float(os.getenv("POS_SHORT_FUNDING_MIN", "0.0003"))
POS_SHORT_OI_PROXIMITY = float(os.getenv("POS_SHORT_OI_PROXIMITY", "0.05"))
POS_SHORT_MIN_HISTORY_DAYS = int(os.getenv("POS_SHORT_MIN_HISTORY_DAYS", "90"))
POS_SHORT_OI_WINDOW_DAYS = int(os.getenv("POS_SHORT_OI_WINDOW_DAYS", "30"))

# --- Direction / holding-horizon policy ---
# Directional LONG means an upside trade with active exits. It is distinct from
# long-term holding (장투), which remains disabled by policy.
ALLOW_LONG = _env_bool("ALLOW_LONG", default=True)
ALLOW_SHORT = _env_bool("ALLOW_SHORT", default=True)
LONG_TERM_HOLD_ENABLED = _env_bool("LONG_TERM_HOLD_ENABLED", default=False)


def direction_allowed(direction: str | None) -> bool:
    if direction == "LONG":
        return ALLOW_LONG
    if direction == "SHORT":
        return ALLOW_SHORT
    return False


def normalize_holding_style(style: str | None) -> str | None:
    raw = (style or "").strip().lower()
    if raw in ("scalp", "short_term", "short-term", "단타"):
        return "scalp"
    if raw in ("swing", "스윙"):
        return "swing"
    # "시장중립72h" is evaluation.py's STYLE_REL_STRENGTH_NEUTRAL (raw Korean
    # setup label). evaluate_symbol() calls holding_style_allowed()/
    # direction_blocked() on setup["style"] directly, BEFORE cycle.py's
    # STYLE_MAP ever translates it -- scalp/swing's Korean labels ("단타"/
    # "스윙") are recognized here for the same reason. Without this, every
    # rel_strength_neutral setup gets silently blocked inside evaluate_symbol
    # itself (policy_blocked_setups), in both backtest and live, before it
    # can ever become the verdict -- not a hypothetical, this is exactly how
    # it failed on first real backtest run (2026-07-13).
    if raw in ("rel_strength_neutral", "시장중립72h"):
        return "rel_strength_neutral"
    # Same "raw Korean label must be recognized here, not just the English
    # key" lesson as rel_strength_neutral above -- these three are the setup
    # labels evaluation.py's _capitulation_bar_setup/_volume_zscore_setup/
    # _pump24_extreme_setup return directly (research_decisions,
    # subject='candle_signals_round2'; event-study horizons that survived
    # multiple-comparisons correction).
    if raw in ("capitulation_bounce", "반등72h"):
        return "capitulation_bounce"
    if raw in ("volume_zscore_breakout", "거래량72h"):
        return "volume_zscore_breakout"
    if raw in ("pump24_continuation", "펌프24h"):
        return "pump24_continuation"
    if raw in ("failed_pump_long", "펌프반등"):
        return "failed_pump_long"
    if raw in ("long_term_hold", "long-term-hold", "long_term", "장투", "hold"):
        return "long_term_hold"
    return None


def holding_style_allowed(style: str | None) -> bool:
    normalized = normalize_holding_style(style)
    if normalized in (
        "scalp", "swing", "rel_strength_neutral",
        "capitulation_bounce", "volume_zscore_breakout", "pump24_continuation",
        "failed_pump_long",
    ):
        return True
    if normalized == "long_term_hold":
        return LONG_TERM_HOLD_ENABLED
    return False


def max_hold_hours_for_style(style: str | None) -> float:
    normalized = normalize_holding_style(style)
    if normalized == "scalp":
        return SCALP_MAX_HOLD_HOURS
    if normalized == "swing":
        return SWING_MAX_HOLD_HOURS
    if normalized == "rel_strength_neutral":
        return REL_STRENGTH_NEUTRAL_HOLD_HOURS
    if normalized == "capitulation_bounce":
        return CAPITULATION_BOUNCE_HOLD_HOURS
    if normalized == "volume_zscore_breakout":
        return VOLUME_ZSCORE_HOLD_HOURS
    if normalized == "pump24_continuation":
        return PUMP24_EXTREME_HOLD_HOURS
    if normalized == "failed_pump_long":
        return FAILED_PUMP_LONG_HOLD_HOURS
    if normalized == "long_term_hold" and LONG_TERM_HOLD_ENABLED:
        return SWING_MAX_HOLD_HOURS
    return 0.0


# --- Paper trading ---
LIVE_TRADING = os.getenv("LIVE_TRADING", "false").lower() in ("true", "1", "yes")
# Research default is the actual target account size; override in .env for demos.
PAPER_STARTING_CAPITAL = float(os.getenv("PAPER_STARTING_CAPITAL", "730.0"))
MAX_POSITION_PCT = float(os.getenv("MAX_POSITION_PCT", "0.20"))
# Fallback fixed-percent exits (used when ATR is unavailable)
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.03"))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "0.06"))

# --- Risk management (ATR-based exits + volatility-scaled sizing) ---
# Stop distance = ATR_STOP_MULT x 1h ATR%; target = ATR_TP_MULT x ATR%.
# Exit-geometry research axes (2026-07-14, the DEXE capture-window finding:
# a 2.17-ATR favorable move surrendered to ~breakeven because every peak in
# [arm, TP) that reverses exits near entry). The walk-forward judges these;
# do not hand-tune.
ATR_STOP_MULT = float(os.getenv("ATR_STOP_MULT", "1.5"))
ATR_TP_MULT = float(os.getenv("ATR_TP_MULT", "2.5"))
# How many ATRs in favor before the trailing ratchet arms (was hardcoded 1.0).
TRAIL_ARM_ATR = float(os.getenv("TRAIL_ARM_ATR", "1.0"))
# Volatility entry filters (src/engine/entry_filters.py, research axes,
# pre-registered subject='atr_entry_filters'). Both default UNLIMITED (999)
# so live behavior is unchanged until the walk-forward promotes a cap.
# MAX_ENTRY_ATR_PCT: refuse entry if 1h ATR% exceeds this.
MAX_ENTRY_ATR_PCT = float(os.getenv("MAX_ENTRY_ATR_PCT", "999"))
# MAX_STOP_GAP_TOLERANCE: refuse if recent max 1h range > N x intended stop
# distance (scales with the stop -- more targeted than a flat ATR cap).
MAX_STOP_GAP_TOLERANCE = float(os.getenv("MAX_STOP_GAP_TOLERANCE", "999"))

# DIAGNOSTIC ONLY (2026-07-14 inversion hypothesis): flip every backtest
# signal's direction (LONG<->SHORT), everything else identical, to test
# whether the signals carry information (inverted ~= -normal) or are just
# costs+variance (inverted also negative). Deliberately NOT in any
# OVERRIDE/research-axis list -- it must never be promotable, only run by
# hand for the inverted-arm backtest.
INVERT_SIGNAL_DIRECTION = _env_bool("INVERT_SIGNAL_DIRECTION", default=False)
# Partial take-profit: at PARTIAL_TP_AT_R R-multiples in favor (R = initial
# stop distance), close HALF the position and trail the rest. 0 = off.
PARTIAL_TP_AT_R = float(os.getenv("PARTIAL_TP_AT_R", "0"))
# Risk this fraction of portfolio value per trade (position size is derived
# from the stop distance, so volatile coins automatically get smaller size).
RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", "0.01"))
# Time stops: scalps must resolve fast; swings get up to one month. Anything
# beyond active scalp/swing exits is treated as long-term holding, disabled by
# LONG_TERM_HOLD_ENABLED=false.
SCALP_MAX_HOLD_HOURS = float(os.getenv("SCALP_MAX_HOLD_HOURS", "48"))
SWING_MAX_HOLD_HOURS = float(os.getenv("SWING_MAX_HOLD_HOURS", "720"))
# rel_strength's market-neutral setup only has evidence at the 72h horizon
# (research_decisions, subject='rel_strength_market_neutral') -- the 24h
# effect does not survive market-neutral re-testing, so this style must exit
# well before SWING_MAX_HOLD_HOURS would let it drift into untested territory.
REL_STRENGTH_NEUTRAL_HOLD_HOURS = float(os.getenv("REL_STRENGTH_NEUTRAL_HOLD_HOURS", "72"))
# candle_signals_round2 setups: exit hold hours match exactly the horizon
# that survived multiple-comparisons correction in the event study, not a
# default swing/scalp bucket -- only 72h (capitulation_bar, volume_zscore)
# and 24h (pump24_extreme) have evidence behind them.
CAPITULATION_BOUNCE_HOLD_HOURS = float(os.getenv("CAPITULATION_BOUNCE_HOLD_HOURS", "72"))
VOLUME_ZSCORE_HOLD_HOURS = float(os.getenv("VOLUME_ZSCORE_HOLD_HOURS", "72"))
PUMP24_EXTREME_HOLD_HOURS = float(os.getenv("PUMP24_EXTREME_HOLD_HOURS", "24"))
# failed_pump_long (the validated inversion of the disabled short): hold
# horizon is a research axis -- the event study effect is measurable at both
# 24h (+0.67%) and 72h (+0.82%), so the walk-forward picks.
FAILED_PUMP_LONG_HOLD_HOURS = float(os.getenv("FAILED_PUMP_LONG_HOLD_HOURS", "72"))
# Trailing stop for swing trades: once price moves 1 ATR in favor, trail the
# stop TRAIL_ATR_MULT x ATR behind the best price seen.
TRAILING_STOP_ENABLED = _env_bool("TRAILING_STOP_ENABLED", default=True)
TRAIL_ATR_MULT = float(os.getenv("TRAIL_ATR_MULT", "2.0"))
# Paper-trade cost model: taker fee per side (Binance futures taker is 0.05%).
FEE_PCT_PER_SIDE = float(os.getenv("FEE_PCT_PER_SIDE", "0.0005"))
# 2026-07-xx: SLIPPAGE_PCT_PER_SIDE (a flat per-side assumption folded into
# round_trip_cost_pct()) is REMOVED, not kept, deprecated, or backward-compat
# shimmed -- slippage no longer moves the fee, it moves the FILL PRICE itself
# via src.engine.execution_cost (orderbook-depth-aware where a snapshot
# exists, symbol-median-fallback otherwise, DEFAULT_FALLBACK_SPREAD_BPS/
# DEFAULT_FALLBACK_DEPTH_USD as the absolute last resort). execution_cost.py
# is intentionally config-free (mirrors src/engine/hedge.py's shape), so
# there is no equivalent knob here to keep in sync -- re-adding a
# SLIPPAGE_PCT_PER_SIDE constant here would just be dead weight nothing reads.
# FEE_MODE=taker (default) uses FEE_PCT_PER_SIDE. Use maker only with a maker-fill simulator.
FEE_MODE = os.getenv("FEE_MODE", "taker").strip().lower()
FEE_MAKER_PCT_PER_SIDE = float(os.getenv("FEE_MAKER_PCT_PER_SIDE", "0.0002"))

# Execution-cost model (src.engine.execution_cost): stress multiplier applied
# to slippage on stop_loss exits and on any exit during an unusually violent
# bar. Research axis: [1.0, 2.0, 3.0]. 1.0 = no stress amplification (same as
# every other exit), matching current/default live behavior.
STOP_SLIPPAGE_MULT = float(os.getenv("STOP_SLIPPAGE_MULT", "1.0"))
# "N" in "bar range > N x ATR" -- the non-stop_loss half of the stress
# condition (A3). 2.0 is a documented, round starting point: a bar has to be
# twice the symbol's own recent average range to count as unusually violent.
STRESS_BAR_RANGE_ATR_MULT = float(os.getenv("STRESS_BAR_RANGE_ATR_MULT", "2.0"))


def fee_pct_per_side() -> float:
    if FEE_MODE == "maker":
        return FEE_MAKER_PCT_PER_SIDE
    return FEE_PCT_PER_SIDE


def round_trip_cost_pct() -> float:
    """FEE ONLY. Slippage no longer lives here -- see FEE_PCT_PER_SIDE's
    comment above and src.engine.execution_cost, which moves the fill PRICE
    instead. Do not re-add a slippage term here; that would double-count it
    on top of the price adjustment."""
    return 2 * fee_pct_per_side()


# --- Setup toggles (research_space.yaml) ---
# 2026-07-13: _swing_setup (evaluation.py) had no enable flag at all, so
# BacktestEngine.EVALUATION_ENGINE_STRATEGIES's isolation ("force every OTHER
# setup off so evaluate_symbol's only possible verdict is the one strategy
# under test") could never actually silence it -- a rel_strength_rotation
# backtest was really testing whichever of {_swing_setup, _rel_strength_setup}
# scored higher, contaminating any result. Default True: unchanged live
# behavior; scripts/backtest.py force-disables it during isolated tests.
SETUP_SWING_ENABLED = _env_bool("SETUP_SWING_ENABLED", default=True)
SETUP_MEANREV_ENABLED = _env_bool("SETUP_MEANREV_ENABLED", default=True)
# Disable weak/high-turnover branches by default; research runner can re-enable.
SETUP_BREAKOUT_ENABLED = _env_bool("SETUP_BREAKOUT_ENABLED", default=False)
SETUP_TSMOM_ENABLED = _env_bool("SETUP_TSMOM_ENABLED", default=False)
SETUP_FUNDING_ENABLED = _env_bool("SETUP_FUNDING_ENABLED", default=True)
# Volume spike is noisy as a standalone entry; default to confirmation modifier only.
SETUP_VOLUME_ENABLED = _env_bool("SETUP_VOLUME_ENABLED", default=False)
# 7d rel-strength-vs-BTC rotation (event_study.py round 2: n=529/530, +1.46%/+2.88%
# effect at 24h/72h, CI excludes zero, consistent across regimes). Off until a
# walk-forward backtest of this exact setup beats the champion.
SETUP_REL_STRENGTH_ENABLED = _env_bool("SETUP_REL_STRENGTH_ENABLED", default=False)
# candle_signals_round2 (research_decisions, subject='candle_signals_round2'):
# each fires ONLY on its exact src.research.candle_signals/event_study
# function definition -- no re-derivation. Direction and hold horizon come
# from the event study, not assumption (pump24_extreme's own code comment
# calls it a "chase fade" but the measured 24h effect is POSITIVE --
# continuation, not reversal; this setup is LONG, matching the data).
# All off by default until each clears its own walk-forward gates.
SETUP_CAPITULATION_BAR_ENABLED = _env_bool("SETUP_CAPITULATION_BAR_ENABLED", default=False)
SETUP_VOLUME_ZSCORE_ENABLED = _env_bool("SETUP_VOLUME_ZSCORE_ENABLED", default=False)
SETUP_PUMP24_EXTREME_ENABLED = _env_bool("SETUP_PUMP24_EXTREME_ENABLED", default=False)
# failed_pump_long: the validated LONG inversion of the permanently-disabled
# failed_pump_short. Default OFF; read dynamically in evaluation.py so the
# walk-forward subprocess can toggle it. failed_pump_short (SETUP_FAILED_PUMP_
# ENABLED) is now default-False in code -- permanently disabled, evidence
# settled (anti-predictive at n=1660).
SETUP_FAILED_PUMP_LONG_ENABLED = _env_bool("SETUP_FAILED_PUMP_LONG_ENABLED", default=False)
# pump24_early: fire during the pump's DECELERATION rather than after the
# 99th-pctl bar completes. Research axis on pump24_extreme (bigger winners,
# more false positives -- the walk-forward decides if it nets out).
PUMP24_EARLY_ENTRY = _env_bool("PUMP24_EARLY_ENTRY", default=False)
# 2026-07-13: funding_carry's execution_mode="delta_neutral" P&L math computes
# returns for a spot-long hedge leg that has never been implemented in either
# path -- it has simply never fired (funding never crossed CARRY_ENTRY_RATE),
# so no bad numbers have shipped, but that was luck, not a guarantee. Off
# until a real hedge leg exists. See research_decisions,
# subject='funding_carry_disabled'.
SETUP_FUNDING_CARRY_ENABLED = _env_bool("SETUP_FUNDING_CARRY_ENABLED", default=False)

# --- Market-neutral hedge sizing (src/engine/hedge.py) ---
# Ex-ante beta lookback for hedge sizing -- matches the weekly beta window
# scripts/event_study_market_neutral.py used to validate the underlying
# effect, so live sizing uses the same beta definition the evidence is
# based on.
HEDGE_BETA_LOOKBACK_H = int(os.getenv("HEDGE_BETA_LOOKBACK_H", "720"))
HEDGE_BETA_MIN_POINTS = int(os.getenv("HEDGE_BETA_MIN_POINTS", "24"))
# Relative deviation of realized beta from the ex-ante beta used for sizing,
# above which a trade is flagged "correlation_spiked" rather than
# "correlation_held" in basis-risk reporting (research_decisions,
# subject='rel_strength_market_neutral', addition 3).
BASIS_RISK_BETA_TOLERANCE = float(os.getenv("BASIS_RISK_BETA_TOLERANCE", "0.5"))

# Minimum confidence to treat a setup as tradable.
# Live/future stages keep MIN_CONFIDENCE. Paper can run a lower gate to collect
# out-of-sample calibration data without changing live risk policy.
MIN_CONFIDENCE = float(os.getenv("MIN_CONFIDENCE", "0.70"))
ENTRY_MIN_CONFIDENCE = PAPER_MIN_CONFIDENCE if not LIVE_TRADING else MIN_CONFIDENCE

# Per-symbol re-entry cooldown after any prior entry (0 = disabled).
COOLDOWN_HOURS_PER_SYMBOL = float(os.getenv("COOLDOWN_HOURS_PER_SYMBOL", "48"))

# Entry decision engine: evaluation = use the rich confluence engine directly;
# signal = legacy primary-strategy signal + AltAnalyzer route.
ENTRY_DECISION_ENGINE = os.getenv("ENTRY_DECISION_ENGINE", "evaluation").strip().lower()

# --- Capital stage gates (display / criteria only; no live routing here) ---
CAPITAL_STAGE = os.getenv("CAPITAL_STAGE", "paper").strip().lower()
CAPITAL_STAGE_MIN_TRADES = int(os.getenv("CAPITAL_STAGE_MIN_TRADES", "60"))
CAPITAL_STAGE_MIN_EXPECTANCY = float(os.getenv("CAPITAL_STAGE_MIN_EXPECTANCY", "0"))
CAPITAL_STAGE_MAX_DD_PCT = float(os.getenv("CAPITAL_STAGE_MAX_DD_PCT", "12"))
CAPITAL_STAGE_CONSECUTIVE_MONTHS = int(os.getenv("CAPITAL_STAGE_CONSECUTIVE_MONTHS", "2"))
CAPITAL_DEMOTE_DD_PCT = float(os.getenv("CAPITAL_DEMOTE_DD_PCT", "15"))
CAPITAL_DEMOTE_ROLLING_TRADES = int(os.getenv("CAPITAL_DEMOTE_ROLLING_TRADES", "30"))

# --- BTC regime filter ---
# Alts follow BTC in stress: block new LONGs when BTC is dumping, block new
# SHORTs when BTC is squeezing up. Thresholds in percent.
REGIME_FILTER_ENABLED = _env_bool("REGIME_FILTER_ENABLED", default=True)
REGIME_BTC_DROP_24H_PCT = float(os.getenv("REGIME_BTC_DROP_24H_PCT", "-3.0"))
REGIME_BTC_DROP_7D_PCT = float(os.getenv("REGIME_BTC_DROP_7D_PCT", "-8.0"))
REGIME_BTC_PUMP_24H_PCT = float(os.getenv("REGIME_BTC_PUMP_24H_PCT", "3.0"))
REGIME_BTC_PUMP_7D_PCT = float(os.getenv("REGIME_BTC_PUMP_7D_PCT", "8.0"))

# Regime conditioning for the four volatility-dislocation setups (rel_strength
# _rotation, capitulation_bar, volume_zscore_3plus, pump24_extreme). Diagnosis
# (research_decisions, subject='regime_gate_diagnosis', 2026-07-13): trailing
# BTC TREND showed no consistent ex-ante signal across the four strategies
# (direction/magnitude varied, sometimes inverted); trailing BTC 30d realized
# VOLATILITY did, but in the OPPOSITE direction from the original "trade
# during dislocation/high-vol" hypothesis -- LOW trailing vol precedes
# meaningfully higher win rates (10-35pp gap for 3/4 strategies), robust
# across 14/21/30/45/60-day lookback choices. "off" leaves every setup
# exactly as validated; "low_vol_only" blocks new entries in those four
# setups specifically when BTC's trailing 30d annualized realized vol is at
# or above REGIME_GATE_VOL_THRESHOLD_PCT -- not a universal filter on every
# strategy, since only these four were diagnosed.
REGIME_GATE = os.getenv("REGIME_GATE", "off").strip().lower()
# Empirical median of trailing-30d BTC realized vol across the 40 walk-forward
# windows (~48.4%), rounded. Fixed/documented threshold, matching how
# REGIME_BTC_DROP_24H_PCT etc. above are also fixed constants rather than a
# recomputed expanding percentile.
REGIME_GATE_VOL_THRESHOLD_PCT = float(os.getenv("REGIME_GATE_VOL_THRESHOLD_PCT", "48.4"))

# --- Confidence calibration ---
# Blend hardcoded setup scores with the realized win rate of closed trades for
# the same strategy+direction. The prior weight is how many "virtual trades"
# the hardcoded score is worth — with few real trades the base score dominates,
# with many the empirical win rate takes over.
CALIBRATION_PRIOR_WEIGHT = int(os.getenv("CALIBRATION_PRIOR_WEIGHT", "20"))
CALIBRATION_MIN_TRADES = int(os.getenv("CALIBRATION_MIN_TRADES", "5"))

# --- Strategy ---
# ACTIVE_STRATEGIES: comma-separated list. The first entry is the primary
# strategy that drives trades; the others also record signals every cycle so
# their accuracy can be compared on real data before promoting one.
ACTIVE_STRATEGY = os.getenv("ACTIVE_STRATEGY", "funding_rate")
ACTIVE_STRATEGIES = [
    s.strip()
    for s in os.getenv("ACTIVE_STRATEGIES", ACTIVE_STRATEGY).split(",")
    if s.strip()
]
PRIMARY_STRATEGY = ACTIVE_STRATEGIES[0]

# --- Momentum strategy thresholds ---
MOMENTUM_ENTRY_PCT = float(os.getenv("MOMENTUM_ENTRY_PCT", "3.0"))

# --- Trade evaluation gates (단타/스윙 verdict on the dashboard) ---
EVAL_MIN_CANDLES = int(os.getenv("EVAL_MIN_CANDLES", "48"))
# Below this 24h dollar volume the pair is treated as too illiquid to trade.
EVAL_MIN_DOLLAR_VOLUME_24H = float(os.getenv("EVAL_MIN_DOLLAR_VOLUME_24H", "5000000"))
# Minimum 1h ATR% — below this the expected move can't cover fees/slippage.
EVAL_ATR_MIN_PCT = float(os.getenv("EVAL_ATR_MIN_PCT", "0.25"))
# At/above this BTC correlation an alt has no independent edge.
EVAL_BTC_CORR_MAX = float(os.getenv("EVAL_BTC_CORR_MAX", "0.9"))

# --- Volume spike strategy thresholds ---
VOLUME_SPIKE_RATIO = float(os.getenv("VOLUME_SPIKE_RATIO", "2.0"))
VOLUME_SPIKE_MIN_CANDLES = int(os.getenv("VOLUME_SPIKE_MIN_CANDLES", "12"))

# --- Mean-reversion scalp setup (BB 20,2 + RSI 14; Vantixs/StratProof filters) ---
MEANREV_RSI_HIGH = float(os.getenv("MEANREV_RSI_HIGH", "70"))
MEANREV_RSI_LOW = float(os.getenv("MEANREV_RSI_LOW", "30"))
# Squeezes precede expansions — the worst environment for mean reversion.
MEANREV_MIN_BANDWIDTH_PCT = float(os.getenv("MEANREV_MIN_BANDWIDTH_PCT", "4.0"))
# Don't fade a strong trend (regime filter for the counter-trend setup).
MEANREV_MAX_TREND_7D_PCT = float(os.getenv("MEANREV_MAX_TREND_7D_PCT", "15.0"))

# --- Swing setups ---
# 28d time-series momentum (AUT walk-forward study: 28d lookback optimal).
MOMENTUM_28D_STRONG_PCT = float(os.getenv("MOMENTUM_28D_STRONG_PCT", "15.0"))

# --- Confluence (final confidence when setups align) ---
CONFLUENCE_ALIGNED_BONUS = float(os.getenv("CONFLUENCE_ALIGNED_BONUS", "0.06"))
CONFLUENCE_CONFLICT_PENALTY = float(os.getenv("CONFLUENCE_CONFLICT_PENALTY", "0.08"))
# Retail long/short account ratio beyond these = crowded, contrarian bonus.
LSR_CROWDED_LONG = float(os.getenv("LSR_CROWDED_LONG", "1.5"))
LSR_CROWDED_SHORT = float(os.getenv("LSR_CROWDED_SHORT", "0.67"))

# --- Market comparison ---
MARKET_COMPARE_HORIZONS_HOURS = (1, 4, 24)
SIGNAL_ACCURACY_ROLLING_DAYS = int(os.getenv("SIGNAL_ACCURACY_ROLLING_DAYS", "7"))

# --- Dashboard ---
DASHBOARD_DIR = Path(__file__).resolve().parent / "dashboard"

# --- Ngrok (public phone access — local Mac only; disabled on cloud) ---
_ngrok_env = os.getenv("NGROK_ENABLED", "false").lower() in ("true", "1", "yes")
NGROK_ENABLED = _ngrok_env and not is_cloud_runtime()
NGROK_AUTHTOKEN = os.getenv("NGROK_AUTHTOKEN", "")  # optional if ngrok CLI is configured
NGROK_USE_CLI = os.getenv("NGROK_USE_CLI", "true").lower() in ("true", "1", "yes")
NGROK_BIN = os.getenv("NGROK_BIN", "")  # auto-detect homebrew ngrok if empty
NGROK_REGION = os.getenv("NGROK_REGION", "")  # e.g. us, eu, ap, au, sa, jp, in
NGROK_WEB_PORT = int(os.getenv("NGROK_WEB_PORT", "4042"))
# Permanent URL — your free static domain from https://dashboard.ngrok.com/domains
# Example: your-name.ngrok-free.app  or  https://your-name.ngrok-free.app
NGROK_STATIC_DOMAIN = os.getenv("NGROK_STATIC_DOMAIN", "").strip()

# --- Health monitoring ---
# Mark degraded if no cycle within this many seconds
HEALTH_STALE_SECONDS = int(
    os.getenv(
        "HEALTH_STALE_SECONDS",
        str(COLLECTION_INTERVAL_MINUTES * 60 * 2 + 120),
    )
)
