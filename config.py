"""Configuration loaded from environment variables."""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from nestal_prompt import build_default_ai_prompt
from security_utils import (
    validate_chart_url,
    validate_eth_wallet_address,
    validate_private_key_format,
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

# Trading constants
COIN = "ETH"
TIMEFRAME_MINUTES = 5
LEVERAGE = 15
RISK_FRACTION = 0.50
DAILY_MAX_LOSS_FRACTION = 0.10
WEEKLY_MAX_LOSS_FRACTION = 0.70
MONTHLY_MAX_LOSS_FRACTION = 0.70
COOLDOWN_MINUTES = 30
# Align scans to UTC 5m candle closes (:00, :05, :10, …) + buffer for exchange finalize
CANDLE_CLOSE_BUFFER_SECONDS = 3
LOOP_INTERVAL_SECONDS = TIMEFRAME_MINUTES * 60

# Hyperliquid ETH size precision (sz decimals)
ETH_SIZE_DECIMALS = 4
MIN_ETH_ORDER_SIZE = 0.001

DEFAULT_AI_PROMPT = build_default_ai_prompt()

_PLACEHOLDER_CHART_RE = re.compile(r"/chart/\.\.\.|/chart/\.\.\./", re.IGNORECASE)


def is_placeholder_chart_url(url: str) -> bool:
    """True if CHART_URL is unset or still the documentation placeholder."""
    if not url or not url.strip():
        return True
    lower = url.strip().lower()
    if _PLACEHOLDER_CHART_RE.search(lower):
        return True
    if lower.rstrip("/").endswith("tradingview.com/chart"):
        return True
    return False


def seconds_until_next_candle_scan(
    interval_minutes: int = TIMEFRAME_MINUTES,
    buffer_seconds: float = CANDLE_CLOSE_BUFFER_SECONDS,
) -> float:
    """Seconds until the next UTC wall-clock candle boundary (+ buffer).

    Scans fire at :00, :05, :10, … UTC plus a short buffer so the closed 5m bar
    is available from Hyperliquid before chart capture.
    """
    interval = interval_minutes * 60
    elapsed = time.time() % interval
    if elapsed < buffer_seconds:
        return buffer_seconds - elapsed
    return interval - elapsed + buffer_seconds


def next_candle_scan_utc(
    interval_minutes: int = TIMEFRAME_MINUTES,
    buffer_seconds: float = CANDLE_CLOSE_BUFFER_SECONDS,
) -> datetime:
    """UTC timestamp of the next aligned chart scan."""
    wait = seconds_until_next_candle_scan(interval_minutes, buffer_seconds)
    return datetime.now(timezone.utc) + timedelta(seconds=wait)


@dataclass(frozen=True)
class Settings:
    """Runtime settings from environment."""

    hyperliquid_private_key: str
    hyperliquid_wallet_address: str
    openai_api_key: str
    chart_url: str
    ai_prompt: str
    live_trading: bool
    hyperliquid_testnet: bool
    openai_model: str
    paper_starting_balance: float
    log_level: str
    screenshot_wait_ms: int
    auto_spot_to_perp: bool
    chart_storage_state_path: Path | None
    chart_source: str

    @property
    def is_paper(self) -> bool:
        return not self.live_trading


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
    live = _env_bool("LIVE_TRADING", default=True)
    testnet = _env_bool("HYPERLIQUID_TESTNET", default=False)

    private_key = os.getenv("HYPERLIQUID_PRIVATE_KEY", "").strip()
    wallet = os.getenv("HYPERLIQUID_WALLET_ADDRESS", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    chart_url_raw = (
        os.getenv("CHART_URL", "").strip()
        or os.getenv("TRADINGVIEW_CHART_URL", "").strip()
    )

    if not openai_key or len(openai_key) < 20:
        raise ValueError("OPENAI_API_KEY is required (min 20 characters)")

    chart_source = os.getenv("CHART_SOURCE", "auto").strip().lower()
    if chart_source not in ("auto", "url", "hyperliquid"):
        raise ValueError('CHART_SOURCE must be "auto", "url", or "hyperliquid"')

    chart_url = ""
    if chart_source == "url":
        if not chart_url_raw or is_placeholder_chart_url(chart_url_raw):
            raise ValueError(
                "CHART_URL must be a real https chart link when CHART_SOURCE=url"
            )
        chart_url = validate_chart_url(chart_url_raw)
    elif chart_url_raw and not is_placeholder_chart_url(chart_url_raw):
        chart_url = validate_chart_url(chart_url_raw)

    ai_prompt = os.getenv("AI_PROMPT", "").strip() or DEFAULT_AI_PROMPT

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

    storage_raw = (
        os.getenv("CHART_STORAGE_STATE_PATH", "").strip()
        or os.getenv("TRADINGVIEW_STORAGE_STATE_PATH", "").strip()
    )
    storage_path: Path | None = None
    if storage_raw:
        storage_path = Path(storage_raw).expanduser().resolve()
        if not storage_path.is_file():
            raise ValueError(f"CHART_STORAGE_STATE_PATH does not exist: {storage_path}")

    return Settings(
        hyperliquid_private_key=private_key,
        hyperliquid_wallet_address=wallet,
        openai_api_key=openai_key,
        chart_url=chart_url,
        ai_prompt=ai_prompt,
        live_trading=live,
        hyperliquid_testnet=testnet,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-5.2").strip() or "gpt-5.2",
        paper_starting_balance=_env_float("PAPER_STARTING_BALANCE", 10000.0, 1.0, 1_000_000_000.0),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        screenshot_wait_ms=_env_int("SCREENSHOT_WAIT_MS", 18000, 8000, 120_000),
        auto_spot_to_perp=_env_bool(
            "AUTO_SPOT_TO_PERP",
            default=live,
        ),
        chart_storage_state_path=storage_path,
        chart_source=chart_source,
    )


def ensure_data_dirs() -> None:
    """Create data directories if they do not exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
