"""Central configuration — thresholds and settings live here, not in strategy files."""

from pathlib import Path

from dotenv import load_dotenv
import os

# Load .env from project root (parent of src/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


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
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "5"))
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
# The scheduler runs in a background thread and never blocks the web server,
# so it is on by default everywhere — a single Railway service collects data
# out of the box. When you add a dedicated worker service
# (scripts/start-worker.sh), set RUN_TRADING_SCHEDULER=false on the web
# service to split the roles.
RUN_TRADING_SCHEDULER = _env_bool("RUN_TRADING_SCHEDULER", default=True)

# --- Funding Rate Reversal strategy thresholds ---
# Rates are expressed as decimals (0.001 = 0.1%)
FUNDING_RATE_SHORT_THRESHOLD = float(os.getenv("FUNDING_RATE_SHORT_THRESHOLD", "0.001"))
FUNDING_RATE_LONG_THRESHOLD = float(os.getenv("FUNDING_RATE_LONG_THRESHOLD", "-0.0005"))

# --- Paper trading ---
LIVE_TRADING = os.getenv("LIVE_TRADING", "false").lower() in ("true", "1", "yes")
PAPER_STARTING_CAPITAL = float(os.getenv("PAPER_STARTING_CAPITAL", "10000.0"))
MAX_POSITION_PCT = float(os.getenv("MAX_POSITION_PCT", "0.20"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.03"))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "0.06"))

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
