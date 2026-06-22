"""Central configuration — thresholds and settings live here, not in strategy files."""

from pathlib import Path

from dotenv import load_dotenv
import os

# Load .env from project root (parent of src/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

# --- Paths ---
DATA_DIR = _PROJECT_ROOT / "data"
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", DATA_DIR / "trading.db"))

# --- API server ---
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8000"))

# --- Binance / ccxt ---
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
BINANCE_TESTNET = os.getenv("BINANCE_TESTNET", "true").lower() in ("true", "1", "yes")

# Default trading pair (legacy / BTC focus)
SYMBOL = os.getenv("SYMBOL", "BTC/USDT")
CCXT_SYMBOL = os.getenv("CCXT_SYMBOL", "BTC/USDT:USDT")  # perpetual futures

# Multi-symbol universe (comma-separated spot pairs)
_default_alts = "BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT,DOGE/USDT,ADA/USDT,AVAX/USDT,LINK/USDT,DOT/USDT"
TRADING_SYMBOLS = [
    s.strip() for s in os.getenv("TRADING_SYMBOLS", _default_alts).split(",") if s.strip()
]
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "5"))

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
OHLCV_TIMEFRAME = os.getenv("OHLCV_TIMEFRAME", "1h")
OHLCV_LIMIT = int(os.getenv("OHLCV_LIMIT", "100"))

# --- Funding Rate Reversal strategy thresholds ---
# Rates are expressed as decimals (0.001 = 0.1%)
FUNDING_RATE_SHORT_THRESHOLD = float(os.getenv("FUNDING_RATE_SHORT_THRESHOLD", "0.001"))
FUNDING_RATE_LONG_THRESHOLD = float(os.getenv("FUNDING_RATE_LONG_THRESHOLD", "-0.0005"))

# --- Paper trading ---
PAPER_STARTING_CAPITAL = float(os.getenv("PAPER_STARTING_CAPITAL", "10000.0"))
MAX_POSITION_PCT = float(os.getenv("MAX_POSITION_PCT", "0.20"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.03"))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "0.06"))

# --- Strategy ---
ACTIVE_STRATEGY = os.getenv("ACTIVE_STRATEGY", "funding_rate")

# --- Market comparison ---
MARKET_COMPARE_HORIZONS_HOURS = (1, 4, 24)
SIGNAL_ACCURACY_ROLLING_DAYS = int(os.getenv("SIGNAL_ACCURACY_ROLLING_DAYS", "7"))

# --- Dashboard ---
DASHBOARD_DIR = Path(__file__).resolve().parent / "dashboard"

# --- Ngrok (public phone access) ---
NGROK_ENABLED = os.getenv("NGROK_ENABLED", "false").lower() in ("true", "1", "yes")
NGROK_AUTHTOKEN = os.getenv("NGROK_AUTHTOKEN", "")  # optional if ngrok CLI is configured
NGROK_USE_CLI = os.getenv("NGROK_USE_CLI", "true").lower() in ("true", "1", "yes")
NGROK_BIN = os.getenv("NGROK_BIN", "")  # auto-detect homebrew ngrok if empty
NGROK_REGION = os.getenv("NGROK_REGION", "")  # e.g. us, eu, ap, au, sa, jp, in
NGROK_WEB_PORT = int(os.getenv("NGROK_WEB_PORT", "4042"))

# --- Health monitoring ---
# Mark degraded if no cycle within this many seconds
HEALTH_STALE_SECONDS = int(
    os.getenv(
        "HEALTH_STALE_SECONDS",
        str(COLLECTION_INTERVAL_MINUTES * 60 * 2 + 120),
    )
)
