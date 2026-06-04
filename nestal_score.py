"""Deterministic Nestal Fractal scoring from Hyperliquid candles.

Trade confidence is computed here — not taken from the AI response — so the model
cannot copy canned percentages from the prompt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

INTERVAL = "5m"
MESO_INTERVAL = "15m"
MACRO_INTERVAL = "1h"

CANDLE_LIMIT = 150
MESO_CANDLE_LIMIT = 80
MACRO_CANDLE_LIMIT = 60

MICRO_TREND_LOOKBACK = 10
MESO_TREND_LOOKBACK = 5
MACRO_TREND_LOOKBACK = 3

FIDELITY_WINDOW = 20
PATTERN_LOOKBACK = 14

MIN_FRACTAL_FIDELITY = 70.0
MIN_TRADE_CONFIDENCE = 65.0

# Values the model often copies from prompt examples; ignored when gating trades.
SUSPICIOUS_AI_CONFIDENCE = frozenset({58.0, 60.0, 65.0})


@dataclass(frozen=True)
class Bar:
    t: int
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class NestalScore:
    """Objective Nestal metrics from Hyperliquid candles (5m + 15m + 1h trends)."""

    micro_trend: str
    meso_trend: str
    macro_trend: str
    fractal_fidelity: float
    pattern_size: float
    last_close: float
    bar_count: int

    def required_trend(self, action: str) -> str | None:
        if action == "LONG":
            return "Bullish"
        if action == "SHORT":
            return "Bearish"
        return None

    def trends_aligned(self, action: str) -> bool:
        """Nestal rule: micro, meso (15m), and macro (1h) must all agree."""
        required = self.required_trend(action)
        if required is None:
            return True
        for label in (self.micro_trend, self.meso_trend, self.macro_trend):
            if label == "N/A" or label != required:
                return False
        return True

    def alignment_label(self) -> str:
        if (
            self.micro_trend == self.meso_trend == self.macro_trend
            and self.micro_trend in ("Bullish", "Bearish")
        ):
            return f"ALIGNED {self.micro_trend.upper()}"
        return "NOT ALIGNED"

    def confidence_for(self, action: str) -> float:
        """Nestal formula: 40% fidelity + 40% if all trends align + 20% base."""
        trend_bonus = 40.0 if self.trends_aligned(action) else 0.0
        return min(100.0, round(self.fractal_fidelity * 0.4 + trend_bonus + 20.0, 1))


def parse_candles(raw: list[dict[str, Any]]) -> list[Bar]:
    bars: list[Bar] = []
    for row in raw:
        try:
            bars.append(
                Bar(
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


def closed_bars(bars: list[Bar]) -> list[Bar]:
    """Drop the forming (last) candle so trends use completed bars only."""
    return bars[:-1] if len(bars) > 1 else bars


def trend_label(bars: list[Bar], lookback: int = MICRO_TREND_LOOKBACK) -> str:
    closed = closed_bars(bars)
    if len(closed) < lookback + 1:
        return "N/A"
    return "Bullish" if closed[-1].close > closed[-1 - lookback].close else "Bearish"


def _returns(closes: list[float]) -> list[float]:
    out: list[float] = []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        if prev == 0:
            continue
        out.append((closes[i] - prev) / prev)
    return out


def fractal_fidelity(bars: list[Bar], window: int = FIDELITY_WINDOW) -> float:
    """Compare normalized return patterns across two adjacent windows."""
    closed = closed_bars(bars)
    if len(closed) < window * 2:
        return 50.0

    closes_recent = [b.close for b in closed[-window:]]
    closes_prior = [b.close for b in closed[-window * 2 : -window]]
    r_recent = _returns(closes_recent)
    r_prior = _returns(closes_prior)
    n = min(len(r_recent), len(r_prior))
    if n < 3:
        return 50.0

    r_recent = r_recent[-n:]
    r_prior = r_prior[-n:]
    mean_r = sum(r_recent) / n
    mean_p = sum(r_prior) / n
    num = sum((r_recent[i] - mean_r) * (r_prior[i] - mean_p) for i in range(n))
    den_r = sum((r_recent[i] - mean_r) ** 2 for i in range(n)) ** 0.5
    den_p = sum((r_prior[i] - mean_p) ** 2 for i in range(n)) ** 0.5
    if den_r == 0 or den_p == 0:
        return 40.0

    corr = num / (den_r * den_p)
    return max(0.0, min(100.0, round((corr + 1.0) * 50.0, 1)))


def pattern_size(bars: list[Bar], lookback: int = PATTERN_LOOKBACK) -> float:
    closed = closed_bars(bars)
    recent = closed[-lookback:] if len(closed) >= lookback else closed
    if not recent:
        return 0.0
    ranges = [b.high - b.low for b in recent]
    return (sum(ranges) / len(ranges)) * 1.2


def compute_nestal_score(
    bars_5m: list[Bar],
    *,
    meso_trend: str = "N/A",
    macro_trend: str = "N/A",
) -> NestalScore | None:
    if not bars_5m:
        return None
    closed = closed_bars(bars_5m)
    last = closed[-1] if closed else bars_5m[-1]
    return NestalScore(
        micro_trend=trend_label(bars_5m, MICRO_TREND_LOOKBACK),
        meso_trend=meso_trend,
        macro_trend=macro_trend,
        fractal_fidelity=fractal_fidelity(bars_5m),
        pattern_size=pattern_size(bars_5m),
        last_close=last.close,
        bar_count=len(bars_5m),
    )


def fetch_nestal_score(client: Any) -> NestalScore | None:
    """Load 5m / 15m / 1h candles and compute Nestal metrics."""
    try:
        raw_5m = client.get_candles(INTERVAL, CANDLE_LIMIT)
        raw_15m = client.get_candles(MESO_INTERVAL, MESO_CANDLE_LIMIT)
        raw_1h = client.get_candles(MACRO_INTERVAL, MACRO_CANDLE_LIMIT)
        bars_5m = parse_candles(raw_5m)
        bars_15m = parse_candles(raw_15m)
        bars_1h = parse_candles(raw_1h)
    except Exception as exc:
        logger.error("Nestal score: candle fetch failed: %s", exc)
        return None

    meso = trend_label(bars_15m, MESO_TREND_LOOKBACK)
    macro = trend_label(bars_1h, MACRO_TREND_LOOKBACK)
    score = compute_nestal_score(bars_5m, meso_trend=meso, macro_trend=macro)
    if score is None:
        logger.error("Nestal score: no 5m bars returned")
        return None

    closed = closed_bars(bars_5m)
    last_bar = closed[-1] if closed else bars_5m[-1]
    last_bar_utc = datetime.fromtimestamp(last_bar.t / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC"
    )

    logger.info(
        "Nestal score (computed): micro=%s meso(15m)=%s macro(1h)=%s alignment=%s "
        "fidelity=%.1f%% confidence(LONG)=%.1f%% confidence(SHORT)=%.1f%% "
        "pattern_size=$%.2f last=$%.2f last_bar=%s",
        score.micro_trend,
        score.meso_trend,
        score.macro_trend,
        score.alignment_label(),
        score.fractal_fidelity,
        score.confidence_for("LONG"),
        score.confidence_for("SHORT"),
        score.pattern_size,
        score.last_close,
        last_bar_utc,
    )
    return score
