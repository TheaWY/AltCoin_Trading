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

# Default trading pair
SYMBOL = os.getenv("SYMBOL", "BTC/USDT")
CCXT_SYMBOL = os.getenv("CCXT_SYMBOL", "BTC/USDT:USDT")  # perpetual futures

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
