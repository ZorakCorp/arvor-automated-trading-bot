"""Parse TradingView alert webhook payloads into TradeSignal objects."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from ai_analyzer import TradeSignal, parse_trade_signal

logger = logging.getLogger(__name__)

# LONG|3500.5|3480|3550  or  SHORT|3500|3520|3450
_PIPE_RE = re.compile(
    r"^\s*(LONG|SHORT|NO_TRADE)\s*\|\s*([^|]*)\s*(?:\|\s*([^|]*)\s*(?:\|\s*([^|]*)\s*)?)?$",
    re.IGNORECASE,
)


def parse_tradingview_payload(raw_body: str) -> TradeSignal | None:
    """
    Parse alert message body from TradingView.

    Supported formats:
    1. JSON: {"action":"LONG","entry":3500,"stop_loss":3480,"take_profit":3550}
    2. Pipe: LONG|3500|3480|3550
    3. TradingView sometimes wraps JSON in extra text — first {...} block is used.
    """
    text = (raw_body or "").strip()
    if not text:
        logger.error("Empty webhook body")
        return None

    data = _try_json_object(text)
    if data is not None:
        return parse_trade_signal(data, text)

    pipe = _try_pipe_format(text)
    if pipe is not None:
        return parse_trade_signal(pipe, text)

    logger.error("Unrecognized webhook payload: %s", text[:500])
    return None


def _try_json_object(text: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    while start != -1:
        try:
            obj, _end = json.JSONDecoder().raw_decode(text[start:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        start = text.find("{", start + 1)
    return None


def _try_pipe_format(text: str) -> dict[str, Any] | None:
    match = _PIPE_RE.match(text.strip())
    if not match:
        return None

    action = match.group(1).upper()
    if action == "NO_TRADE":
        return {"action": "NO_TRADE", "reasoning": "TradingView NO_TRADE alert"}

    entry_s, sl_s, tp_s = match.group(2), match.group(3), match.group(4)
    if not entry_s or not sl_s or not tp_s:
        logger.error("Pipe format requires action|entry|stop_loss|take_profit")
        return None

    try:
        return {
            "action": action,
            "entry": float(entry_s.strip()),
            "stop_loss": float(sl_s.strip()),
            "take_profit": float(tp_s.strip()),
            "reasoning": "TradingView alert (pipe format)",
        }
    except ValueError as exc:
        logger.error("Invalid numbers in pipe payload: %s", exc)
        return None
