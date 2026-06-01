"""Configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Project paths
BOT_ROOT = Path(__file__).resolve().parent
DATA_DIR = BOT_ROOT / "data"
SCREENSHOTS_DIR = DATA_DIR / "screenshots"
JOURNAL_PATH = DATA_DIR / "trade_journal.csv"
PAPER_STATE_PATH = DATA_DIR / "paper_state.json"
RISK_STATE_PATH = DATA_DIR / "risk_state.json"
COOLDOWN_STATE_PATH = DATA_DIR / "cooldown_state.json"

# Trading constants
COIN = "ETH"
TIMEFRAME_MINUTES = 5
LEVERAGE = 5
RISK_FRACTION = 0.50  # 50% of available capital at risk per trade
DAILY_MAX_LOSS_FRACTION = 0.10
WEEKLY_MAX_LOSS_FRACTION = 0.70
MONTHLY_MAX_LOSS_FRACTION = 0.70
COOLDOWN_MINUTES = 30
LOOP_INTERVAL_SECONDS = 60  # check every minute; trade logic respects 5m + cooldown


@dataclass(frozen=True)
class Settings:
    """Runtime settings from environment."""

    hyperliquid_private_key: str
    hyperliquid_wallet_address: str
    openai_api_key: str
    tradingview_chart_url: str
    live_trading: bool
    hyperliquid_testnet: bool
    openai_model: str
    paper_starting_balance: float
    log_level: str
    screenshot_wait_ms: int

    @property
    def is_paper(self) -> bool:
        return not self.live_trading


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, str(default)).strip().lower()
    return raw in ("1", "true", "yes", "on")


def load_settings() -> Settings:
    """Load and validate settings. Raises ValueError if required vars missing."""
    live = _env_bool("LIVE_TRADING", default=False)
    testnet = _env_bool("HYPERLIQUID_TESTNET", default=False)

    private_key = os.getenv("HYPERLIQUID_PRIVATE_KEY", "").strip()
    wallet = os.getenv("HYPERLIQUID_WALLET_ADDRESS", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    chart_url = os.getenv("TRADINGVIEW_CHART_URL", "").strip()

    # Paper mode only needs OpenAI + chart URL for full loop testing
    if live:
        if not private_key:
            raise ValueError("HYPERLIQUID_PRIVATE_KEY is required when LIVE_TRADING=true")
        if not wallet:
            raise ValueError("HYPERLIQUID_WALLET_ADDRESS is required when LIVE_TRADING=true")

    if not openai_key:
        raise ValueError("OPENAI_API_KEY is required")
    if not chart_url:
        raise ValueError("TRADINGVIEW_CHART_URL is required")

    return Settings(
        hyperliquid_private_key=private_key,
        hyperliquid_wallet_address=wallet,
        openai_api_key=openai_key,
        tradingview_chart_url=chart_url,
        live_trading=live,
        hyperliquid_testnet=testnet,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o"),
        paper_starting_balance=float(os.getenv("PAPER_STARTING_BALANCE", "10000")),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        screenshot_wait_ms=int(os.getenv("SCREENSHOT_WAIT_MS", "18000")),
    )


def ensure_data_dirs() -> None:
    """Create data directories if they do not exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
