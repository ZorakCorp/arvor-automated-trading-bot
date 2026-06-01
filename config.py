"""Configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from security_utils import (
    validate_eth_wallet_address,
    validate_private_key_format,
    validate_tradingview_url,
)

load_dotenv()

# Project paths
BOT_ROOT = Path(__file__).resolve().parent
DATA_DIR = BOT_ROOT / "data"
SCREENSHOTS_DIR = DATA_DIR / "screenshots"
JOURNAL_PATH = DATA_DIR / "trade_journal.csv"
PAPER_STATE_PATH = DATA_DIR / "paper_state.json"
RISK_STATE_PATH = DATA_DIR / "risk_state.json"
COOLDOWN_STATE_PATH = DATA_DIR / "cooldown_state.json"
LIVE_POSITION_PATH = DATA_DIR / "live_position.json"
FRACTAL_SIGNAL_STATE_PATH = DATA_DIR / "fractal_signal_state.json"

# Trading constants
COIN = "ETH"
TIMEFRAME_MINUTES = 5
LEVERAGE = 5
RISK_FRACTION = 0.50
DAILY_MAX_LOSS_FRACTION = 0.10
WEEKLY_MAX_LOSS_FRACTION = 0.70
MONTHLY_MAX_LOSS_FRACTION = 0.70
COOLDOWN_MINUTES = 30
LOOP_INTERVAL_SECONDS = 60

# Hyperliquid ETH size precision (sz decimals)
ETH_SIZE_DECIMALS = 4
MIN_ETH_ORDER_SIZE = 0.001


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
    auto_spot_to_perp: bool
    tradingview_storage_state_path: Path | None
    signal_mode: str
    tradingview_webhook_secret: str
    webhook_port: int
    fractal_risk_reward: float
    fractal_require_nested: bool
    fractal_htf_interval: str
    fractal_candle_limit: int

    @property
    def is_paper(self) -> bool:
        return not self.live_trading

    @property
    def uses_webhook(self) -> bool:
        return self.signal_mode == "webhook"

    @property
    def uses_screenshot(self) -> bool:
        return self.signal_mode == "screenshot"

    @property
    def uses_candles(self) -> bool:
        return self.signal_mode == "candles"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, str(default)).strip().lower()
    return raw in ("1", "true", "yes", "on")


def _env_int(name: str, default: int, min_val: int, max_val: int) -> int:
    try:
        val = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not min_val <= val <= max_val:
        raise ValueError(f"{name} must be between {min_val} and {max_val}")
    return val


def _env_float(name: str, default: float, min_val: float, max_val: float) -> float:
    try:
        val = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not min_val <= val <= max_val:
        raise ValueError(f"{name} must be between {min_val} and {max_val}")
    return val


def load_settings() -> Settings:
    """Load and validate settings. Raises ValueError if required vars missing."""
    live = _env_bool("LIVE_TRADING", default=False)
    testnet = _env_bool("HYPERLIQUID_TESTNET", default=False)

    private_key = os.getenv("HYPERLIQUID_PRIVATE_KEY", "").strip()
    wallet = os.getenv("HYPERLIQUID_WALLET_ADDRESS", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    chart_url_raw = os.getenv("TRADINGVIEW_CHART_URL", "").strip()
    signal_mode = os.getenv("SIGNAL_MODE", "candles").strip().lower()
    if signal_mode not in ("candles", "webhook", "screenshot"):
        raise ValueError('SIGNAL_MODE must be "candles", "webhook", or "screenshot"')

    webhook_secret = os.getenv("TRADINGVIEW_WEBHOOK_SECRET", "").strip()
    if signal_mode == "webhook":
        if len(webhook_secret) < 16:
            raise ValueError(
                "TRADINGVIEW_WEBHOOK_SECRET is required in webhook mode (min 16 chars)"
            )
    elif signal_mode == "screenshot":
        if not openai_key or len(openai_key) < 20:
            raise ValueError("OPENAI_API_KEY is required when SIGNAL_MODE=screenshot")
        if not chart_url_raw:
            raise ValueError("TRADINGVIEW_CHART_URL is required when SIGNAL_MODE=screenshot")

    fractal_htf = os.getenv("FRACTAL_HTF_INTERVAL", "15m").strip().lower()
    if fractal_htf not in ("15m", "1h", "none"):
        raise ValueError('FRACTAL_HTF_INTERVAL must be "15m", "1h", or "none"')

    chart_url = ""
    if chart_url_raw:
        chart_url = validate_tradingview_url(chart_url_raw)

    if signal_mode == "webhook" and openai_key and len(openai_key) < 20:
        openai_key = ""

    if live:
        if not _env_bool("ARVOR_CONFIRM_LIVE_RISK", default=False):
            raise ValueError(
                "LIVE_TRADING=true requires ARVOR_CONFIRM_LIVE_RISK=true "
                "(explicit acknowledgment of real-fund risk)"
            )
        if not private_key:
            raise ValueError("HYPERLIQUID_PRIVATE_KEY is required when LIVE_TRADING=true")
        if not wallet:
            raise ValueError("HYPERLIQUID_WALLET_ADDRESS is required when LIVE_TRADING=true")
        private_key = validate_private_key_format(private_key)
        wallet = validate_eth_wallet_address(wallet)

    port_raw = os.getenv("PORT", "").strip()
    if port_raw:
        webhook_port = _env_int("PORT", 8080, 1, 65535)
    else:
        webhook_port = _env_int("WEBHOOK_PORT", 8080, 1024, 65535)

    storage_raw = os.getenv("TRADINGVIEW_STORAGE_STATE_PATH", "").strip()
    storage_path: Path | None = None
    if storage_raw:
        storage_path = Path(storage_raw).expanduser().resolve()
        if not storage_path.is_file():
            raise ValueError(
                f"TRADINGVIEW_STORAGE_STATE_PATH does not exist: {storage_path}"
            )

    return Settings(
        hyperliquid_private_key=private_key,
        hyperliquid_wallet_address=wallet,
        openai_api_key=openai_key,
        tradingview_chart_url=chart_url,
        live_trading=live,
        hyperliquid_testnet=testnet,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o").strip() or "gpt-4o",
        paper_starting_balance=_env_float("PAPER_STARTING_BALANCE", 10000.0, 1.0, 1_000_000_000.0),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        screenshot_wait_ms=_env_int("SCREENSHOT_WAIT_MS", 18000, 8000, 120_000),
        auto_spot_to_perp=_env_bool(
            "AUTO_SPOT_TO_PERP",
            default=live,
        ),
        tradingview_storage_state_path=storage_path,
        signal_mode=signal_mode,
        tradingview_webhook_secret=webhook_secret,
        webhook_port=webhook_port,
        fractal_risk_reward=_env_float("FRACTAL_RISK_REWARD", 2.0, 0.5, 10.0),
        fractal_require_nested=_env_bool("FRACTAL_REQUIRE_NESTED", default=False),
        fractal_htf_interval=fractal_htf,
        fractal_candle_limit=_env_int("FRACTAL_CANDLE_LIMIT", 200, 50, 500),
    )


def ensure_data_dirs() -> None:
    """Create data directories if they do not exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
