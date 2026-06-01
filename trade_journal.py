"""CSV trade journal with full audit fields."""

from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import JOURNAL_PATH

logger = logging.getLogger(__name__)

JOURNAL_HEADERS = [
    "timestamp_utc",
    "action",
    "entry",
    "stop_loss",
    "take_profit",
    "position_size",
    "leverage",
    "outcome",
    "pnl",
    "balance_after",
    "screenshot_path",
    "ai_raw_response",
    "mode",
    "notes",
]


class TradeJournal:
    """Append-only trade journal."""

    def __init__(self) -> None:
        JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not JOURNAL_PATH.exists():
            self._write_headers()

    def _write_headers(self) -> None:
        with JOURNAL_PATH.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=JOURNAL_HEADERS)
            writer.writeheader()

    def log_entry(self, row: dict[str, Any]) -> None:
        """Append one journal row."""
        record = {h: row.get(h, "") for h in JOURNAL_HEADERS}
        if not record.get("timestamp_utc"):
            record["timestamp_utc"] = datetime.now(timezone.utc).isoformat()

        try:
            with JOURNAL_PATH.open("a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=JOURNAL_HEADERS)
                writer.writerow(record)
            logger.info("Journal entry saved: action=%s outcome=%s", record["action"], record["outcome"])
        except OSError as exc:
            logger.error("Failed to write journal: %s", exc)

    def log_no_trade(
        self,
        action: str,
        screenshot_path: str,
        ai_raw: str,
        mode: str,
        notes: str = "",
    ) -> None:
        self.log_entry(
            {
                "action": action,
                "outcome": "no_trade",
                "screenshot_path": screenshot_path,
                "ai_raw_response": ai_raw,
                "mode": mode,
                "notes": notes,
            }
        )
