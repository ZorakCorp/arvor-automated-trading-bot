"""Render ETH 5m / 15m / 1h charts from Hyperliquid candles (no TradingView)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nestal_score import (
    CANDLE_LIMIT,
    INTERVAL,
    MACRO_CANDLE_LIMIT,
    MACRO_INTERVAL,
    MACRO_TREND_LOOKBACK,
    MESO_CANDLE_LIMIT,
    MESO_INTERVAL,
    MESO_TREND_LOOKBACK,
    MICRO_TREND_LOOKBACK,
    closed_bars,
    parse_candles,
    trend_label,
)
from nestal_score import Bar

logger = logging.getLogger(__name__)

CHART_PANELS: tuple[tuple[str, int, int], ...] = (
    (INTERVAL, CANDLE_LIMIT, MICRO_TREND_LOOKBACK),
    (MESO_INTERVAL, MESO_CANDLE_LIMIT, MESO_TREND_LOOKBACK),
    (MACRO_INTERVAL, MACRO_CANDLE_LIMIT, MACRO_TREND_LOOKBACK),
)


def _format_bar_time(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _draw_candles(ax: Any, bars: list[Bar], *, title: str) -> None:
    from matplotlib.patches import Rectangle

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
    ax.axhline(last_closed.close, color="#ffd700", linestyle="--", linewidth=0.8, alpha=0.8)
    ax.set_xlim(-1, len(bars))
    ax.set_xticks([])
    ax.set_title(title, color="#eeeeee", fontsize=10, loc="left")
    ax.grid(True, alpha=0.15, color="#444444")


def render_hyperliquid_chart_image(client: Any, output_path: Path) -> bool:
    """Build a 5m / 15m / 1h PNG from Hyperliquid API for OpenAI vision."""
    try:
        import matplotlib

        logging.getLogger("matplotlib").setLevel(logging.WARNING)
        logging.getLogger("matplotlib.font_manager").setLevel(logging.WARNING)
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        logger.error("matplotlib not installed — cannot render Hyperliquid chart")
        return False

    panel_data: list[tuple[str, list[Bar], str]] = []
    for interval, limit, lookback in CHART_PANELS:
        try:
            raw = client.get_candles(interval, limit)
            bars = parse_candles(raw)
        except Exception as exc:
            logger.error("%s candle fetch failed: %s", interval, exc)
            return False
        if not bars:
            logger.error("Chart candles %s: no bars returned", interval)
            return False
        trend = trend_label(bars, lookback)
        panel_data.append((interval, bars, trend))

    micro, meso, macro = (t[2] for t in panel_data)
    aligned = micro == meso == macro and micro in ("Bullish", "Bearish")
    alignment = f"ALIGNED {micro.upper()}" if aligned else "NOT ALIGNED"

    bars_5m = panel_data[0][1]
    closed_5m = closed_bars(bars_5m)
    last_5m = closed_5m[-1] if closed_5m else bars_5m[-1]
    logger.info(
        "Chart 5m/15m/1h: %d/%d/%d bars | last 5m %s @ $%.2f | trends 5m=%s 15m=%s 1h=%s | %s",
        len(panel_data[0][1]),
        len(panel_data[1][1]),
        len(panel_data[2][1]),
        _format_bar_time(last_5m.t),
        last_5m.close,
        micro,
        meso,
        macro,
        alignment,
    )

    fig, axes = plt.subplots(3, 1, figsize=(14, 12), facecolor="#1a1a2e")
    labels = {
        INTERVAL: f"Micro (5m) — close vs {MICRO_TREND_LOOKBACK} bars ago",
        MESO_INTERVAL: f"Meso (15m) — close vs {MESO_TREND_LOOKBACK} bars ago",
        MACRO_INTERVAL: f"Macro (1h) — close vs {MACRO_TREND_LOOKBACK} bars ago",
    }
    for ax, (interval, bars, trend) in zip(axes, panel_data, strict=True):
        _draw_candles(
            ax,
            bars,
            title=f"{labels[interval]} | Trend: {trend} | {len(bars)} candles",
        )

    fig.suptitle(
        f"Hyperliquid ETH — 5m + 15m + 1h | {alignment}\n"
        f"5m last closed: {_format_bar_time(last_5m.t)} @ ${last_5m.close:,.2f} | "
        f"5m={micro} | 15m={meso} | 1h={macro}",
        color="#eeeeee",
        fontsize=12,
        y=0.995,
    )
    axes[-1].set_ylabel("Price (USD)", color="#aaaaaa")

    fig.tight_layout(rect=(0, 0, 1, 0.94))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=130, facecolor=fig.get_facecolor())
    plt.close(fig)

    if not output_path.exists() or output_path.stat().st_size < 1000:
        logger.error("Rendered chart too small or missing: %s", output_path)
        return False

    logger.info(
        "Hyperliquid multi-TF chart rendered: %s (%d bytes)",
        output_path,
        output_path.stat().st_size,
    )
    return True
