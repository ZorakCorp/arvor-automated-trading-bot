"""Free local fractal signals from Hyperliquid OHLC (no TradingView / OpenAI)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ai_analyzer import TradeSignal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Candle:
    """Single OHLCV bar."""

    t: int  # open time (ms)
    open: float
    high: float
    low: float
    close: float


def parse_hyperliquid_candles(raw: list[dict[str, Any]]) -> list[Candle]:
    """Convert Hyperliquid candleSnapshot rows to Candle list (sorted by time)."""
    out: list[Candle] = []
    for row in raw:
        try:
            out.append(
                Candle(
                    t=int(row["t"]),
                    open=float(row["o"]),
                    high=float(row["h"]),
                    low=float(row["l"]),
                    close=float(row["c"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda c: c.t)
    return out


def _williams_fractal_indices(
    candles: list[Candle],
    periods: int = 2,
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """
    Bill Williams 5-bar fractals (periods=2).
    Returns confirmed up-fractal (index, high) and down-fractal (index, low) points.
    """
    up: list[tuple[int, float]] = []
    down: list[tuple[int, float]] = []
    if len(candles) < periods * 2 + 1:
        return up, down

    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    last_confirmable = len(candles) - 1 - periods

    for i in range(periods, last_confirmable + 1):
        h = highs[i]
        if all(h > highs[i - j] and h > highs[i + j] for j in range(1, periods + 1)):
            up.append((i, h))

        low_val = lows[i]
        if all(low_val < lows[i - j] and low_val < lows[i + j] for j in range(1, periods + 1)):
            down.append((i, low_val))

    return up, down


def _last_before(values: list[tuple[int, float]], idx: int) -> tuple[int, float] | None:
    candidates = [v for v in values if v[0] < idx]
    return candidates[-1] if candidates else None


def _htf_bias_bullish(candles_htf: list[Candle], sma_period: int = 20) -> bool | None:
    """True = bullish bias, False = bearish, None = not enough data."""
    if len(candles_htf) < sma_period + 2:
        return None
    closes = [c.close for c in candles_htf[:-1]]  # exclude forming bar
    sma = sum(closes[-sma_period:]) / sma_period
    return closes[-1] > sma


def evaluate_fractal_signal(
    candles_5m: list[Candle],
    candles_htf: list[Candle] | None,
    *,
    risk_reward: float,
    require_nested: bool,
    last_signal_candle_t: int | None,
    fractal_periods: int = 2,
) -> TradeSignal | None:
    """
    Detect fractal breakout on the last *closed* 5m bar.

    LONG: close breaks above last up-fractal; SL at last down-fractal low.
    SHORT: close breaks below last down-fractal; SL at last up-fractal high.
    TP: risk_reward × distance to stop.
    """
    min_bars = fractal_periods * 2 + 5
    if len(candles_5m) < min_bars:
        logger.debug("Not enough 5m candles for fractal scan (%d)", len(candles_5m))
        return None

    closed = candles_5m[:-1]
    if len(closed) < min_bars - 1:
        return None

    signal_bar = closed[-1]
    prev_bar = closed[-2]
    signal_idx = len(closed) - 1

    if last_signal_candle_t is not None and signal_bar.t <= last_signal_candle_t:
        return TradeSignal(
            action="NO_TRADE",
            reasoning="Already processed this 5m candle.",
            raw_response="",
        )

    up_fractals, down_fractals = _williams_fractal_indices(closed, fractal_periods)
    last_up = _last_before(up_fractals, signal_idx)
    last_down = _last_before(down_fractals, signal_idx)

    if last_up is None or last_down is None:
        return TradeSignal(
            action="NO_TRADE",
            reasoning="No confirmed fractal levels yet on 5m ETH.",
            raw_response="",
        )

    up_level = last_up[1]
    down_level = last_down[1]
    htf_bias = _htf_bias_bullish(candles_htf) if candles_htf else None

    # LONG breakout
    if signal_bar.close > up_level and prev_bar.close <= up_level:
        entry = signal_bar.close
        stop_loss = down_level
        if stop_loss >= entry:
            return TradeSignal(
                action="NO_TRADE",
                reasoning="LONG fractal break but stop would be above entry.",
                raw_response="",
            )
        if require_nested and htf_bias is False:
            return TradeSignal(
                action="NO_TRADE",
                reasoning="5m LONG fractal break rejected — higher timeframe not bullish.",
                raw_response="",
            )
        take_profit = entry + risk_reward * (entry - stop_loss)
        nested_note = (
            " Higher-TF trend aligned."
            if htf_bias is True
            else (" No HTF filter." if htf_bias is None else "")
        )
        return TradeSignal(
            action="LONG",
            entry=round(entry, 2),
            stop_loss=round(stop_loss, 2),
            take_profit=round(take_profit, 2),
            reasoning=(
                f"5m close broke above fractal high {up_level:.2f}; "
                f"SL at fractal low {stop_loss:.2f}; TP {risk_reward:.1f}R.{nested_note}"
            ),
            raw_response=f'{{"signal_candle_t":{signal_bar.t}}}',
        )

    # SHORT breakout
    if signal_bar.close < down_level and prev_bar.close >= down_level:
        entry = signal_bar.close
        stop_loss = up_level
        if stop_loss <= entry:
            return TradeSignal(
                action="NO_TRADE",
                reasoning="SHORT fractal break but stop would be below entry.",
                raw_response="",
            )
        if require_nested and htf_bias is True:
            return TradeSignal(
                action="NO_TRADE",
                reasoning="5m SHORT fractal break rejected — higher timeframe not bearish.",
                raw_response="",
            )
        take_profit = entry - risk_reward * (stop_loss - entry)
        nested_note = (
            " Higher-TF trend aligned."
            if htf_bias is False
            else (" No HTF filter." if htf_bias is None else "")
        )
        return TradeSignal(
            action="SHORT",
            entry=round(entry, 2),
            stop_loss=round(stop_loss, 2),
            take_profit=round(take_profit, 2),
            reasoning=(
                f"5m close broke below fractal low {down_level:.2f}; "
                f"SL at fractal high {stop_loss:.2f}; TP {risk_reward:.1f}R.{nested_note}"
            ),
            raw_response=f'{{"signal_candle_t":{signal_bar.t}}}',
        )

    return TradeSignal(
        action="NO_TRADE",
        reasoning="No new 5m fractal breakout on the last closed bar.",
        raw_response=f'{{"signal_candle_t":{signal_bar.t}}}',
    )


def signal_candle_time(signal: TradeSignal) -> int | None:
    """Extract candle timestamp from signal raw_response for dedupe state."""
    if not signal.raw_response:
        return None
    try:
        import json

        data = json.loads(signal.raw_response)
        t = data.get("signal_candle_t")
        return int(t) if t is not None else None
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
