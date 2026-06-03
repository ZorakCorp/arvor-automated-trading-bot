"""Send chart screenshot to OpenAI vision and parse Nestal Fractal response."""

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

MIN_TRADE_CONFIDENCE = 65.0

# Nestal structured output (labels may be on same line or next line)
_RE_ACTION = re.compile(r"\b(LONG|SHORT|NO\s*TRADE)\b", re.IGNORECASE)
_RE_ENTRY = re.compile(r"Entry:\s*\n?\s*([\d,]+\.?\d*)", re.IGNORECASE)
_RE_TP = re.compile(r"Take\s*Profit:\s*\n?\s*([\d,]+\.?\d*)", re.IGNORECASE)
_RE_SL = re.compile(r"Stop\s*Loss:\s*\n?\s*([\d,]+\.?\d*)", re.IGNORECASE)
_RE_CONFIDENCE = re.compile(r"Confidence:\s*\n?\s*([\d.]+)\s*%?", re.IGNORECASE)
_RE_REASON = re.compile(r"Reason:\s*\n?\s*(.+?)(?:\n\n|\Z)", re.IGNORECASE | re.DOTALL)


@dataclass
class TradeSignal:
    """Parsed AI trade decision."""

    action: str
    entry: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    confidence: float | None = None
    reasoning: str = ""
    raw_response: str = ""


def _parse_price(raw: str) -> float:
    return float(raw.replace(",", "").strip())


def _parse_reason_block(text: str) -> str:
    m = _RE_REASON.search(text)
    if not m:
        return ""
    return m.group(1).strip()[:500]


def parse_nestal_response(raw: str) -> TradeSignal | None:
    """Parse Nestal Fractal structured text output."""
    text = raw.strip()
    if not text:
        return None

    action_m = _RE_ACTION.search(text)
    if not action_m:
        return None

    action = action_m.group(1).upper().replace(" ", "_")
    if action == "NO_TRADE":
        conf_m = _RE_CONFIDENCE.search(text)
        confidence = float(conf_m.group(1)) if conf_m else None
        reason = _parse_reason_block(text)
        return TradeSignal(
            action="NO_TRADE",
            confidence=confidence,
            reasoning=reason,
            raw_response=raw,
        )

    if action not in ("LONG", "SHORT"):
        return None

    try:
        entry_m = _RE_ENTRY.search(text)
        tp_m = _RE_TP.search(text)
        sl_m = _RE_SL.search(text)
        conf_m = _RE_CONFIDENCE.search(text)
        if not entry_m or not tp_m or not sl_m:
            logger.error("Nestal response missing Entry, Take Profit, or Stop Loss")
            return None

        entry = validate_eth_price(_parse_price(entry_m.group(1)), "entry")
        take_profit = validate_eth_price(_parse_price(tp_m.group(1)), "take_profit")
        stop_loss = validate_eth_price(_parse_price(sl_m.group(1)), "stop_loss")
        confidence = float(conf_m.group(1)) if conf_m else None
    except (ValueError, TypeError) as exc:
        logger.error("Invalid prices in Nestal response: %s", exc)
        return None

    if confidence is not None and confidence < MIN_TRADE_CONFIDENCE:
        logger.warning(
            "Nestal confidence %.1f%% below minimum %.0f%% — NO_TRADE",
            confidence,
            MIN_TRADE_CONFIDENCE,
        )
        return TradeSignal(
            action="NO_TRADE",
            confidence=confidence,
            reasoning=f"Confidence {confidence:.0f}% below {MIN_TRADE_CONFIDENCE:.0f}%",
            raw_response=raw,
        )

    if action == "LONG":
        if not (stop_loss < entry < take_profit):
            logger.error("LONG: expected stop_loss < entry < take_profit")
            return None
    elif not (take_profit < entry < stop_loss):
        logger.error("SHORT: expected take_profit < entry < stop_loss")
        return None

    return TradeSignal(
        action=action,
        entry=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        confidence=confidence,
        raw_response=raw,
    )


def _extract_json(text: str) -> dict[str, Any] | None:
    """Extract first valid JSON object (legacy fallback)."""
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
            obj, _ = json.JSONDecoder().raw_decode(text[start:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        start = text.find("{", start + 1)
    return None


def parse_trade_signal(data: dict[str, Any], raw: str = "") -> TradeSignal | None:
    """Validate JSON fallback (legacy)."""
    action = str(data.get("action", "")).upper().strip().replace(" ", "_")
    if action not in ("LONG", "SHORT", "NO_TRADE"):
        logger.error("Invalid action in AI response: %s", action)
        return None

    reasoning = str(data.get("reasoning", "") or data.get("reason", "")).strip()[:500]
    conf_raw = data.get("confidence")
    confidence = float(conf_raw) if conf_raw is not None else None

    if action == "NO_TRADE":
        return TradeSignal(
            action="NO_TRADE",
            confidence=confidence,
            reasoning=reasoning,
            raw_response=raw,
        )

    try:
        entry = validate_eth_price(float(data["entry"]), "entry")
        stop_loss = validate_eth_price(float(data["stop_loss"]), "stop_loss")
        take_profit = validate_eth_price(float(data["take_profit"]), "take_profit")
    except (KeyError, TypeError, ValueError) as exc:
        logger.error("Missing or invalid price fields: %s", exc)
        return None

    if confidence is not None and confidence < MIN_TRADE_CONFIDENCE:
        return TradeSignal(
            action="NO_TRADE",
            confidence=confidence,
            reasoning=reasoning or f"Confidence {confidence:.0f}% below minimum",
            raw_response=raw,
        )

    if action == "LONG":
        if not (stop_loss < entry < take_profit):
            logger.error("LONG: expected stop_loss < entry < take_profit")
            return None
    elif not (take_profit < entry < stop_loss):
        logger.error("SHORT: expected take_profit < entry < stop_loss")
        return None

    return TradeSignal(
        action=action,
        entry=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        confidence=confidence,
        reasoning=reasoning,
        raw_response=raw,
    )


def parse_ai_response(raw: str) -> TradeSignal | None:
    """Parse Nestal text format first, then JSON fallback."""
    sig = parse_nestal_response(raw)
    if sig is not None:
        return sig

    data = _extract_json(raw)
    if data is not None:
        return parse_trade_signal(data, raw)

    logger.error("Unrecognized AI response format: %s", raw[:500])
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
    prompt = settings.ai_prompt

    try:
        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
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
            max_tokens=400,
            temperature=0.0,
        )
    except Exception as exc:
        logger.error("OpenAI API call failed: %s", exc)
        return None

    if not response.choices:
        logger.error("OpenAI returned empty choices")
        return None

    raw = (response.choices[0].message.content or "").strip()
    logger.debug("AI raw response: %s", raw[:300])

    sig = parse_ai_response(raw)
    if sig is not None and sig.confidence in (58.0, 60.0, 65.0):
        logger.warning(
            "AI returned round confidence %.0f%% — may be a canned value; "
            "verify prompt is latest (5m-only, no example 60%% in prompt)",
            sig.confidence,
        )
    return sig


def log_signal_decision(signal: TradeSignal) -> None:
    """Log AI decision (minimal — no trade reasoning unless NO_TRADE)."""
    logger.info("AI decision: %s", signal.action)
    if signal.confidence is not None:
        logger.info("AI confidence: %.0f%%", signal.confidence)
    if signal.action == "NO_TRADE" and signal.reasoning:
        logger.info("AI reason: %s", signal.reasoning)
