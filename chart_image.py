"""Render ETH multi-timeframe charts from Hyperliquid candles (no TradingView)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


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


def _plot_candles(ax, bars: list[_Bar], title: str) -> None:
    """Simple candlestick panel (dark theme)."""
    from matplotlib.patches import Rectangle

    if not bars:
        ax.set_title(f"{title} — no data")
        ax.set_facecolor("#0f0f1a")
        return

    xs = list(range(len(bars)))
    ax.set_facecolor("#0f0f1a")
    ax.tick_params(colors="#aaaaaa")
    ax.title.set_color("#eeeeee")

    for i, b in enumerate(bars):
        color = "#26a69a" if b.close >= b.open else "#ef5350"
        ax.plot([i, i], [b.low, b.high], color=color, linewidth=1)
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

    last = bars[-1].close
    ax.axhline(last, color="#ffd700", linestyle="--", linewidth=0.8, alpha=0.7)
    ax.set_title(f"{title}  |  last ${last:,.2f}", fontsize=11, pad=6)
    ax.set_xlim(-1, len(bars))
    ax.grid(True, alpha=0.15, color="#444444")
    ax.set_xticks([])


def render_hyperliquid_chart_image(client: Any, output_path: Path) -> bool:
    """
    Build a 3-panel PNG: ETH 5m (micro), 15m (meso), 1h (macro) from Hyperliquid API.
    Works from Railway without TradingView.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        logger.error("matplotlib not installed — cannot render Hyperliquid chart")
        return False

    panels = (
        ("5m", 120, "ETH 5m — Micro trend"),
        ("15m", 80, "ETH 15m — Meso trend"),
        ("1h", 48, "ETH 1h — Macro trend"),
    )

    fig, axes = plt.subplots(3, 1, figsize=(14, 10), facecolor="#1a1a2e")
    fig.suptitle(
        "Hyperliquid ETH — Nestal Fractal chart (live API)",
        color="#eeeeee",
        fontsize=13,
        y=0.98,
    )

    for ax, (interval, limit, title) in zip(axes, panels, strict=True):
        try:
            raw = client.get_candles(interval, limit)
            bars = _parse_candles(raw)
        except Exception as exc:
            logger.warning("Candle fetch failed for %s: %s", interval, exc)
            bars = []
        _plot_candles(ax, bars, title)

    fig.tight_layout(rect=(0, 0, 1, 0.96))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=120, facecolor=fig.get_facecolor())
    plt.close(fig)

    if not output_path.exists() or output_path.stat().st_size < 1000:
        logger.error("Rendered chart too small or missing: %s", output_path)
        return False

    logger.info(
        "Hyperliquid chart rendered: %s (%d bytes)",
        output_path,
        output_path.stat().st_size,
    )
    return True
