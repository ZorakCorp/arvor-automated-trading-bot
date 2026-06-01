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

logger = logging.getLogger(__name__)

# Prompt tuned for "Nested Fractal - Clean" Pine indicator on TradingView ETH 5m
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
OUTPUT RULES
══════════════════════════════════════════════════════════════════════════════

* ETH 5-minute chart only. No news. No outside data.
* Do not explain. Return ONLY valid JSON. No markdown.

Examples:

{"action": "LONG", "entry": 3500.00, "stop_loss": 3475.00, "take_profit": 3550.00}

{"action": "SHORT", "entry": 3500.00, "stop_loss": 3525.00, "take_profit": 3450.00}

{"action": "NO_TRADE"}"""


@dataclass
class TradeSignal:
    """Parsed AI trade decision."""

    action: str  # LONG, SHORT, NO_TRADE
    entry: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    raw_response: str = ""


def _extract_json(text: str) -> dict[str, Any] | None:
    """Extract JSON object from model response."""
    text = text.strip()
    if not text:
        return None

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass

    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


def analyze_chart(screenshot_path: Path, settings: Settings) -> TradeSignal | None:
    """
    Send screenshot to OpenAI and return TradeSignal.
    Returns None if API fails or JSON is invalid.
    """
    if not screenshot_path.exists():
        logger.error("Screenshot not found: %s", screenshot_path)
        return None

    try:
        image_bytes = screenshot_path.read_bytes()
        b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    except OSError as exc:
        logger.error("Could not read screenshot: %s", exc)
        return None

    client = OpenAI(api_key=settings.openai_api_key)

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
            max_tokens=300,
            temperature=0.0,
        )
    except Exception as exc:
        logger.error("OpenAI API call failed: %s", exc)
        return None

    raw = (response.choices[0].message.content or "").strip()
    logger.debug("AI raw response: %s", raw)

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

    if action == "NO_TRADE":
        return TradeSignal(action="NO_TRADE", raw_response=raw)

    try:
        entry = float(data["entry"])
        stop_loss = float(data["stop_loss"])
        take_profit = float(data["take_profit"])
    except (KeyError, TypeError, ValueError) as exc:
        logger.error("Missing or invalid price fields: %s", exc)
        return None

    if entry <= 0 or stop_loss <= 0 or take_profit <= 0:
        logger.error("Prices must be positive")
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
        raw_response=raw,
    )
