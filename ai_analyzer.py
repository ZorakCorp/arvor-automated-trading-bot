"""Send chart screenshot to OpenAI vision and parse trade JSON."""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI

from config import Settings
from security_utils import MAX_SCREENSHOT_BYTES, validate_eth_price

logger = logging.getLogger(__name__)

AI_PROMPT = """You are an expert ETH futures trader reading a TradingView screenshot.

The chart uses the custom indicator "Nested Fractal - Clean" (Nested Fractal).

Answer this question using ONLY what you see in the screenshot:

"I'm using the Nested Fractal indicator — where would you place stop loss and take profit?"

══════════════════════════════════════════════════════════════════════════════
HOW TO READ THE NESTED FRACTAL INDICATOR ON THE CHART
══════════════════════════════════════════════════════════════════════════════

An ACTIVE trade setup is shown only when the indicator has fired a signal. Look for ALL of these:

1. GOLD horizontal line (#FFD700) — Take Profit. Label on the right often reads "TP: <price>".
2. ORANGE horizontal line (#FF8C00) — Stop Loss. Label on the right often reads "SL: <price>".
3. WHITE dashed horizontal line — Entry price at signal.
4. Signal panel (dark box) showing either "LONG" (gold text) or "SHORT" (purple text), plus a large confidence % number and entry price.
5. Optional: "FRACTAL SIGNAL" label above the pattern box, purple curved prediction path, white pattern box.

If NONE of the gold TP line, orange SL line, and LONG/SHORT panel are visible → action MUST be NO_TRADE.
Stale/old lines from prior signals without a current panel → NO_TRADE.

══════════════════════════════════════════════════════════════════════════════
EXTRACT PRICES (read labels and line levels carefully)
══════════════════════════════════════════════════════════════════════════════

* action: "LONG" if panel says LONG; "SHORT" if panel says SHORT.
* entry: white dashed entry line OR entry price printed in the panel.
* stop_loss: price of the ORANGE "SL:" line (exact number from label if visible).
* take_profit: price of the GOLD "TP:" line (exact number from label if visible).

Direction rules (must match indicator geometry):
* LONG: stop_loss < entry < take_profit
* SHORT: take_profit < entry < stop_loss

Purple prediction path is directional context only — do NOT invent prices from it if TP/SL labels exist.

══════════════════════════════════════════════════════════════════════════════
WHEN TO RETURN NO_TRADE
══════════════════════════════════════════════════════════════════════════════

* No active Nested Fractal signal (no TP + SL + LONG/SHORT panel together).
* Cannot read TP or SL price clearly.
* Chart is not ETH or not 5-minute (check timeframe in UI).
* Conflicting or ambiguous signal.

══════════════════════════════════════════════════════════════════════════════
REASONING (required for every response)
══════════════════════════════════════════════════════════════════════════════

Always include "reasoning": a short plain-English explanation (2–5 sentences) of WHAT you see
on the chart and WHY you chose this action. Be specific about indicator elements.

* NO_TRADE reasoning — state what is missing or unclear (e.g. no TP/SL panel, stale lines, wrong timeframe).
* LONG reasoning — cite LONG panel, entry, gold TP, orange SL, confidence %, trend alignment if visible.
* SHORT reasoning — cite SHORT panel, entry, gold TP, orange SL, confidence %, trend alignment if visible.

══════════════════════════════════════════════════════════════════════════════
OUTPUT RULES
══════════════════════════════════════════════════════════════════════════════

* ETH 5-minute chart only. No news. No outside data.
* Return ONLY valid JSON. No markdown. No text outside the JSON object.

Examples:

{"action": "LONG", "entry": 3500.00, "stop_loss": 3475.00, "take_profit": 3550.00, "reasoning": "Active LONG signal: gold TP at 3550, orange SL at 3475, entry dashed line at 3500. LONG panel visible with confidence above threshold. Geometry valid for long."}

{"action": "SHORT", "entry": 3500.00, "stop_loss": 3525.00, "take_profit": 3450.00, "reasoning": "Active SHORT signal: panel shows SHORT, SL above entry at 3525, TP below at 3450. Fractal signal box present. Prices read from labels."}

{"action": "NO_TRADE", "reasoning": "No active Nested Fractal setup: gold TP and orange SL lines not visible together with a LONG/SHORT panel. Chart is ETH 5m but indicator has not fired a tradeable signal."}"""


@dataclass
class TradeSignal:
    """Parsed AI trade decision."""

    action: str
    entry: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    reasoning: str = ""
    raw_response: str = ""


def _parse_reasoning(data: dict[str, Any]) -> str:
    """Extract and cap reasoning text."""
    text = str(data.get("reasoning", "")).strip()
    if not text:
        return "No reasoning provided."
    return text[:2000]


def _extract_json(text: str) -> dict[str, Any] | None:
    """Extract first valid JSON object from model response."""
    text = text.strip()
    if not text:
        return None

    try:
        obj, _ = json.JSONDecoder().raw_decode(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if fenced:
        try:
            obj, _ = json.JSONDecoder().raw_decode(fenced.group(1).strip())
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    start = text.find("{")
    while start != -1:
        try:
            obj, end = json.JSONDecoder().raw_decode(text[start:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        start = text.find("{", start + 1)
    return None


def analyze_chart(screenshot_path: Path, settings: Settings) -> TradeSignal | None:
    """Send screenshot to OpenAI and return TradeSignal."""
    if not screenshot_path.exists():
        logger.error("Screenshot not found: %s", screenshot_path)
        return None

    size = screenshot_path.stat().st_size
    if size > MAX_SCREENSHOT_BYTES:
        logger.error("Screenshot too large (%d bytes, max %d)", size, MAX_SCREENSHOT_BYTES)
        return None

    try:
        image_bytes = screenshot_path.read_bytes()
        b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    except OSError as exc:
        logger.error("Could not read screenshot: %s", exc)
        return None

    client = OpenAI(api_key=settings.openai_api_key, timeout=120.0)

    try:
        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": AI_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{b64}",
                                "detail": "high",
                            },
                        },
                    ],
                }
            ],
            max_tokens=500,
            temperature=0.0,
        )
    except Exception as exc:
        logger.error("OpenAI API call failed: %s", exc)
        return None

    if not response.choices:
        logger.error("OpenAI returned empty choices")
        return None

    raw = (response.choices[0].message.content or "").strip()
    logger.debug("AI raw response: %s", raw[:200])

    data = _extract_json(raw)
    if data is None:
        logger.error("AI returned invalid JSON: %s", raw[:500])
        return None

    return parse_trade_signal(data, raw)


def parse_trade_signal(data: dict[str, Any], raw: str = "") -> TradeSignal | None:
    """Validate and normalize parsed JSON."""
    action = str(data.get("action", "")).upper().strip()
    if action not in ("LONG", "SHORT", "NO_TRADE"):
        logger.error("Invalid action in AI response: %s", action)
        return None

    reasoning = _parse_reasoning(data)

    if action == "NO_TRADE":
        return TradeSignal(action="NO_TRADE", reasoning=reasoning, raw_response=raw)

    try:
        entry = validate_eth_price(float(data["entry"]), "entry")
        stop_loss = validate_eth_price(float(data["stop_loss"]), "stop_loss")
        take_profit = validate_eth_price(float(data["take_profit"]), "take_profit")
    except (KeyError, TypeError, ValueError) as exc:
        logger.error("Missing or invalid price fields: %s", exc)
        return None

    if action == "LONG":
        if not (stop_loss < entry < take_profit):
            logger.error("LONG: expected stop_loss < entry < take_profit")
            return None
    else:
        if not (take_profit < entry < stop_loss):
            logger.error("SHORT: expected take_profit < entry < stop_loss")
            return None

    return TradeSignal(
        action=action,
        entry=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        reasoning=reasoning,
        raw_response=raw,
    )


def log_signal_decision(signal: TradeSignal) -> None:
    """Log AI action and reasoning to console."""
    logger.info("AI decision: %s", signal.action)
    logger.info("AI reasoning: %s", signal.reasoning or "(none)")
