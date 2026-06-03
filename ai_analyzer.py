"""Send chart screenshot to OpenAI vision and parse Nestal Fractal response."""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from openai import OpenAI

from config import Settings
from nestal_score import (
    MIN_FRACTAL_FIDELITY,
    MIN_TRADE_CONFIDENCE,
    NestalScore,
    SUSPICIOUS_AI_CONFIDENCE,
)
from security_utils import MAX_SCREENSHOT_BYTES, validate_eth_price

logger = logging.getLogger(__name__)

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
    model_used: str = ""


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


def apply_nestal_score(signal: TradeSignal, score: NestalScore) -> TradeSignal:
    """Gate trades using computed Nestal metrics — never AI-reported confidence."""
    ai_confidence = signal.confidence
    computed = score.confidence_for(signal.action)

    if ai_confidence is not None:
        if ai_confidence in SUSPICIOUS_AI_CONFIDENCE:
            logger.warning(
                "Ignoring AI confidence %.0f%% (known canned value) — using computed %.1f%%",
                ai_confidence,
                computed,
            )
        elif abs(ai_confidence - computed) > 20.0:
            logger.info(
                "AI confidence %.0f%% differs from computed %.1f%% — using computed",
                ai_confidence,
                computed,
            )

    if signal.action not in ("LONG", "SHORT"):
        return replace(signal, confidence=computed)

    blockers: list[str] = []
    if signal.action == "LONG" and score.micro_trend != "Bullish":
        blockers.append("micro trend not bullish")
    elif signal.action == "SHORT" and score.micro_trend != "Bearish":
        blockers.append("micro trend not bearish")
    if score.fractal_fidelity < MIN_FRACTAL_FIDELITY:
        blockers.append(f"fractal fidelity {score.fractal_fidelity:.0f}% < 70%")
    if computed < MIN_TRADE_CONFIDENCE:
        blockers.append(f"confidence {computed:.0f}% < 65%")

    if blockers:
        reason = "; ".join(blockers)
        logger.warning("Nestal gate blocked %s — %s", signal.action, reason)
        return replace(
            signal,
            action="NO_TRADE",
            confidence=computed,
            reasoning=reason,
            entry=None,
            stop_loss=None,
            take_profit=None,
        )

    return replace(signal, confidence=computed)


def log_raw_ai_signal(signal: TradeSignal) -> None:
    """Log the model's raw direction/prices before Nestal gates."""
    model_label = signal.model_used or "unknown"
    if signal.action in ("LONG", "SHORT"):
        logger.info(
            "AI raw signal: %s (model=%s) entry=%s tp=%s sl=%s",
            signal.action,
            model_label,
            signal.entry,
            signal.take_profit,
            signal.stop_loss,
        )
    else:
        logger.info("AI raw signal: %s (model=%s)", signal.action, model_label)
        if signal.reasoning:
            logger.info("AI raw reason: %s", signal.reasoning)


def _chat_completion_kwargs(model: str) -> dict[str, Any]:
    """Build API kwargs compatible with legacy and GPT-5.x models."""
    uses_new_token_param = model.startswith(("chat-latest", "gpt-5", "o1", "o3", "o4"))
    if uses_new_token_param:
        return {"max_completion_tokens": 400}
    return {"max_tokens": 400, "temperature": 0.0}


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
    model = settings.openai_model
    logger.info("OpenAI vision request (model=%s)", model)

    try:
        response = client.chat.completions.create(
            model=model,
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
            **_chat_completion_kwargs(model),
        )
    except Exception as exc:
        logger.error("OpenAI API call failed (model=%s): %s", model, exc)
        return None

    if not response.choices:
        logger.error("OpenAI returned empty choices (model=%s)", model)
        return None

    resolved_model = response.model or model
    raw = (response.choices[0].message.content or "").strip()
    logger.debug("AI raw response (model=%s): %s", resolved_model, raw[:300])

    signal = parse_ai_response(raw)
    if signal is None:
        return None
    return replace(signal, model_used=resolved_model)


def log_signal_decision(signal: TradeSignal) -> None:
    """Log final decision after Nestal gates."""
    model_label = signal.model_used or "unknown"
    logger.info("Final decision: %s (model=%s)", signal.action, model_label)
    if signal.confidence is not None:
        logger.info("Computed confidence: %.1f%%", signal.confidence)
    if signal.action == "NO_TRADE" and signal.reasoning:
        logger.info("AI reason: %s", signal.reasoning)
