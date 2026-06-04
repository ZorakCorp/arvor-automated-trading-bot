"""Render ETH 5-minute chart from Hyperliquid candles (no TradingView)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nestal_score import (
    INTERVAL,
    MICRO_TREND_LOOKBACK,
    Bar,
    closed_bars,
    parse_candles,
    trend_label,
)

logger = logging.getLogger(__name__)

# ~25h of 5m candles — TradingView-style day view for AI vision (not the old ~12h / 150 bar window).
CHART_CANDLE_LIMIT = 300
# Padding above/below session high/low so price uses ~76% of chart height (matches TV screenshots).
CHART_Y_PADDING_FRACTION = 0.12
CHART_FIGSIZE = (16, 9)
CHART_DPI = 130


def _format_bar_time(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _price_ylim(bars: list[Bar]) -> tuple[float, float]:
    """Vertical scale: full visible range with top/bottom breathing room."""
    y_min = min(b.low for b in bars)
    y_max = max(b.high for b in bars)
    span = y_max - y_min
    if span <= 0:
        span = max(abs(y_max) * 0.01, 1.0)
    pad = span * CHART_Y_PADDING_FRACTION
    return y_min - pad, y_max + pad


def render_hyperliquid_chart_image(client: Any, output_path: Path) -> bool:
    """Build a single ETH 5m PNG from Hyperliquid API (5m candles only)."""
    try:
        import matplotlib

        logging.getLogger("matplotlib").setLevel(logging.WARNING)
        logging.getLogger("matplotlib.font_manager").setLevel(logging.WARNING)
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle
    except ImportError:
        logger.error("matplotlib not installed — cannot render Hyperliquid chart")
        return False

    try:
        raw = client.get_candles(INTERVAL, CHART_CANDLE_LIMIT)
        bars = parse_candles(raw)
    except Exception as exc:
        logger.error("5m candle fetch failed: %s", exc)
        return False

    trend = trend_label(bars)

    if bars:
        closed = closed_bars(bars)
        last = closed[-1] if closed else bars[-1]
        y_lo, y_hi = _price_ylim(bars)
        logger.info(
            "Chart candles 5m only: %d bars (~%.1fh) | last closed %s @ $%.2f | "
            "y-range $%.2f–$%.2f | micro trend(%d)=%s",
            len(bars),
            len(bars) * 5 / 60,
            _format_bar_time(last.t),
            last.close,
            y_lo,
            y_hi,
            MICRO_TREND_LOOKBACK,
            trend,
        )
    else:
        logger.error("Chart candles 5m: no bars returned")
        return False

    fig, ax = plt.subplots(figsize=CHART_FIGSIZE, facecolor="#1a1a2e")
    ax.set_facecolor("#0f0f1a")
    ax.tick_params(colors="#aaaaaa")

    for i, b in enumerate(bars):
        color = "#26a69a" if b.close >= b.open else "#ef5350"
        ax.plot([i, i], [b.low, b.high], color=color, linewidth=1.0)
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

    closed = closed_bars(bars)
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
    ax.set_ylim(_price_ylim(bars))
    ax.grid(True, alpha=0.15, color="#444444")
    ax.set_xticks([])
    ax.set_ylabel("Price (USD)", color="#aaaaaa")

    fig.tight_layout(rect=(0, 0, 1, 0.93))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=CHART_DPI, facecolor=fig.get_facecolor())
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
