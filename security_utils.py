"""Security helpers: URL allowlists, atomic state I/O, log redaction, CSV safety."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Allowed chart hosts for Playwright screenshots (blocks SSRF)
_ALLOWED_CHART_HOSTS = frozenset(
    {
        "www.tradingview.com",
        "tradingview.com",
        "app.hyperliquid.xyz",
        "hyperliquid.xyz",
    }
)

_SECRET_PATTERNS = (
    re.compile(r"(0x)?[a-fA-F0-9]{64}"),  # private keys / long hex
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),  # OpenAI-style keys
)

_ETH_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")

# Sane bounds for ETH perp prices (reject hallucinated AI levels)
MIN_ETH_PRICE = 50.0
MAX_ETH_PRICE = 500_000.0

MAX_SCREENSHOT_BYTES = 12 * 1024 * 1024  # 12 MiB


def validate_chart_url(url: str) -> str:
    """Return normalized URL or raise ValueError."""
    parsed = urlparse(url.strip())
    if parsed.scheme != "https":
        raise ValueError("CHART_URL must use https://")
    host = (parsed.hostname or "").lower()
    if host not in _ALLOWED_CHART_HOSTS:
        raise ValueError(
            f"CHART_URL host not allowed: {host!r}. "
            f"Use https://www.tradingview.com/chart/... or https://app.hyperliquid.xyz/trade/ETH"
        )
    if not parsed.path or parsed.path == "/":
        raise ValueError("CHART_URL must include a path (e.g. /chart/... or /trade/ETH)")
    return url.strip()


def validate_eth_wallet_address(address: str) -> str:
    """Validate checksummed-length Ethereum address."""
    addr = address.strip()
    if not _ETH_ADDRESS_RE.match(addr):
        raise ValueError("HYPERLIQUID_WALLET_ADDRESS must be a 42-char 0x-prefixed address")
    return addr


def validate_private_key_format(key: str) -> str:
    """Basic format check without logging the key."""
    k = key.strip()
    hex_part = k[2:] if k.startswith("0x") else k
    if len(hex_part) != 64 or not re.fullmatch(r"[a-fA-F0-9]{64}", hex_part):
        raise ValueError("HYPERLIQUID_PRIVATE_KEY must be 64 hex characters (optional 0x prefix)")
    return k if k.startswith("0x") else f"0x{k}"


def validate_eth_price(price: float, field: str) -> float:
    if not (MIN_ETH_PRICE <= price <= MAX_ETH_PRICE):
        raise ValueError(f"{field}={price} outside sane ETH range")
    return price


def redact_for_log(message: str) -> str:
    """Strip likely secrets before writing errors to logs/journal."""
    out = message
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub("[REDACTED]", out)
    return out


def sanitize_csv_cell(value: Any) -> str:
    """
    Prevent CSV/formula injection in Excel/Sheets.
    Prefix risky leading characters with a single quote.
    """
    s = "" if value is None else str(value)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON atomically to avoid corrupted state on crash."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        tmp_path.replace(path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def load_json_file(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return default if default is not None else {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read %s: %s", path.name, exc)
        return default if default is not None else {}
