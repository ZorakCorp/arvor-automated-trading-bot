"""Render ETH multi-timeframe charts from Hyperliquid candles (no TradingView)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Nestal lookbacks per timeframe (closed candles only)
_TREND_LOOKBACK = {"5m": 10, "15m": 5, "1h": 3}


@dataclass(frozen=True)
class _Bar:
    t: int
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class _PanelData:
    interval: str
    bars: list[_Bar]
    trend: str
    lookback: int


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


def _plot_candles(ax, panel: _PanelData, *, primary: bool = False) -> None:
    """Simple candlestick panel (dark theme)."""
    from matplotlib.patches import Rectangle

    bars = panel.bars
    interval = panel.interval
    trend = panel.trend
    lookback = panel.lookback

    if not bars:
        ax.set_title(f"ETH {interval} — NO DATA", color="#ff6666")
        ax.set_facecolor("#0f0f1a")
        return

    closed = _closed_bars(bars)
    last_closed = closed[-1] if closed else bars[-1]

    ax.set_facecolor("#0f0f1a")
    ax.tick_params(colors="#aaaaaa")
    ax.title.set_color("#eeeeee")

    for i, b in enumerate(bars):
        color = "#26a69a" if b.close >= b.open else "#ef5350"
        ax.plot([i, i], [b.low, b.high], color=color, linewidth=1.2 if primary else 1)
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

    ax.axhline(last_closed.close, color="#ffd700", linestyle="--", linewidth=0.9, alpha=0.8)

    primary_tag = "PRIMARY ENTRY TF" if primary else ""
    title = (
        f"ETH {interval} | {len(bars)} bars | last closed {_format_bar_time(last_closed.t)} "
        f"| ${last_closed.close:,.2f} | trend({lookback} bars): {trend}"
    )
    if primary_tag:
        title = f"{primary_tag} — {title}"
    ax.set_title(title, fontsize=12 if primary else 10, pad=8 if primary else 6, fontweight="bold" if primary else "normal")

    ax.set_xlim(-1, len(bars))
    ax.grid(True, alpha=0.15, color="#444444")
    ax.set_xticks([])


def render_hyperliquid_chart_image(client: Any, output_path: Path) -> bool:
    """
    Build a 3-panel PNG: ETH 5m (primary), 15m, 1h from Hyperliquid API.
    Top panel is enlarged — 5m is the trade entry timeframe.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        logger.error("matplotlib not installed — cannot render Hyperliquid chart")
        return False

    panel_specs = (
        ("5m", 150, True),
        ("15m", 80, False),
        ("1h", 48, False),
    )

    panels: list[_PanelData] = []
    for interval, limit, _ in panel_specs:
        lookback = _TREND_LOOKBACK[interval]
        try:
            raw = client.get_candles(interval, limit)
            bars = _parse_candles(raw)
            trend = _trend_label(bars, lookback)
        except Exception as exc:
            logger.warning("Candle fetch failed for %s: %s", interval, exc)
            bars = []
            trend = "N/A"

        panels.append(_PanelData(interval=interval, bars=bars, trend=trend, lookback=lookback))
        if bars:
            closed = _closed_bars(bars)
            last = closed[-1] if closed else bars[-1]
            logger.info(
                "Chart candles %s: %d bars | last closed %s @ $%.2f | trend(%d)=%s",
                interval,
                len(bars),
                _format_bar_time(last.t),
                last.close,
                lookback,
                trend,
            )
        else:
            logger.warning("Chart candles %s: no bars returned", interval)

    micro, meso, macro = (p.trend for p in panels)
    if micro != "N/A" and meso != "N/A" and macro != "N/A":
        aligned = (micro == meso == macro)
        alignment = f"ALIGNED {micro.upper()}" if aligned else f"NOT ALIGNED (5m={micro}, 15m={meso}, 1h={macro})"
    else:
        alignment = "INSUFFICIENT DATA"

    fig = plt.figure(figsize=(14, 11), facecolor="#1a1a2e")
    # 5m panel gets ~45% height — primary entry chart
    gs = fig.add_gridspec(3, 1, height_ratios=[2.2, 1.2, 1.2], hspace=0.28)
    axes = [fig.add_subplot(gs[i]) for i in range(3)]

    fig.suptitle(
        "Hyperliquid ETH live — TOP panel is 5-MINUTE (entry timeframe)\n"
        f"Nestal trends: 5m={micro} | 15m={meso} | 1h={macro} | {alignment}",
        color="#eeeeee",
        fontsize=12,
        y=0.98,
    )

    for ax, panel, (_, _, is_primary) in zip(axes, panels, panel_specs, strict=True):
        _plot_candles(ax, panel, primary=is_primary)

    fig.subplots_adjust(top=0.90)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=130, facecolor=fig.get_facecolor())
    plt.close(fig)

    if not output_path.exists() or output_path.stat().st_size < 1000:
        logger.error("Rendered chart too small or missing: %s", output_path)
        return False

    logger.info(
        "Hyperliquid chart rendered (5m primary): %s (%d bytes) | %s",
        output_path,
        output_path.stat().st_size,
        alignment,
    )
    return True
