"""Render ETH 5-minute chart from Hyperliquid candles (no TradingView)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

INTERVAL = "5m"
CANDLE_LIMIT = 150
MICRO_TREND_LOOKBACK = 10


@dataclass(frozen=True)
class _Bar:
    t: int
    open: float
    high: float
    low: float
    close: float


def _parse_candles(raw: list[dict[str, Any]]) -> list[_Bar]:
    bars: list[_Bar] = []
    for row in raw:
        try:
            bars.append(
                _Bar(
                    t=int(row["t"]),
                    open=float(row["o"]),
                    high=float(row["h"]),
                    low=float(row["l"]),
                    close=float(row["c"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    bars.sort(key=lambda b: b.t)
    return bars


def _closed_bars(bars: list[_Bar]) -> list[_Bar]:
    """Drop the forming (last) candle so trends use completed bars only."""
    return bars[:-1] if len(bars) > 1 else bars


def _trend_label(bars: list[_Bar], lookback: int) -> str:
    closed = _closed_bars(bars)
    if len(closed) < lookback + 1:
        return "N/A"
    return "Bullish" if closed[-1].close > closed[-1 - lookback].close else "Bearish"


def _format_bar_time(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def render_hyperliquid_chart_image(client: Any, output_path: Path) -> bool:
    """Build a single ETH 5m PNG from Hyperliquid API (5m candles only)."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle
    except ImportError:
        logger.error("matplotlib not installed — cannot render Hyperliquid chart")
        return False

    try:
        raw = client.get_candles(INTERVAL, CANDLE_LIMIT)
        bars = _parse_candles(raw)
    except Exception as exc:
        logger.error("5m candle fetch failed: %s", exc)
        return False

    trend = _trend_label(bars, MICRO_TREND_LOOKBACK)

    if bars:
        closed = _closed_bars(bars)
        last = closed[-1] if closed else bars[-1]
        logger.info(
            "Chart candles 5m only: %d bars | last closed %s @ $%.2f | micro trend(%d)=%s",
            len(bars),
            _format_bar_time(last.t),
            last.close,
            MICRO_TREND_LOOKBACK,
            trend,
        )
    else:
        logger.error("Chart candles 5m: no bars returned")
        return False

    fig, ax = plt.subplots(figsize=(14, 8), facecolor="#1a1a2e")
    ax.set_facecolor("#0f0f1a")
    ax.tick_params(colors="#aaaaaa")

    for i, b in enumerate(bars):
        color = "#26a69a" if b.close >= b.open else "#ef5350"
        ax.plot([i, i], [b.low, b.high], color=color, linewidth=1.2)
        body_low = min(b.open, b.close)
        body_high = max(b.open, b.close)
        ax.add_patch(
            Rectangle(
                (i - 0.35, body_low),
                0.7,
                max(body_high - body_low, (b.high - b.low) * 0.02 or 0.01),
                facecolor=color,
                edgecolor=color,
            )
        )

    closed = _closed_bars(bars)
    last_closed = closed[-1] if closed else bars[-1]
    ax.axhline(last_closed.close, color="#ffd700", linestyle="--", linewidth=0.9, alpha=0.8)

    fig.suptitle(
        f"Hyperliquid ETH — 5-MINUTE chart only | interval={INTERVAL} | {len(bars)} candles\n"
        f"Last closed: {_format_bar_time(last_closed.t)} @ ${last_closed.close:,.2f} | "
        f"Micro trend ({MICRO_TREND_LOOKBACK} bars): {trend}",
        color="#eeeeee",
        fontsize=12,
        y=0.98,
    )
    ax.set_xlim(-1, len(bars))
    ax.grid(True, alpha=0.15, color="#444444")
    ax.set_xticks([])
    ax.set_ylabel("Price (USD)", color="#aaaaaa")

    fig.tight_layout(rect=(0, 0, 1, 0.93))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=130, facecolor=fig.get_facecolor())
    plt.close(fig)

    if not output_path.exists() or output_path.stat().st_size < 1000:
        logger.error("Rendered chart too small or missing: %s", output_path)
        return False

    logger.info(
        "Hyperliquid 5m chart rendered: %s (%d bytes)",
        output_path,
        output_path.stat().st_size,
    )
    return True
