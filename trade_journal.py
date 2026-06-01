"""CSV trade journal with full audit fields."""

from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone
from typing import Any

from config import JOURNAL_PATH
from security_utils import redact_for_log, sanitize_csv_cell

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
    "ai_reasoning",
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
        else:
            self._migrate_headers_if_needed()

    def _write_headers(self) -> None:
        with JOURNAL_PATH.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=JOURNAL_HEADERS)
            writer.writeheader()

    def _migrate_headers_if_needed(self) -> None:
        """Add ai_reasoning column to existing journal files."""
        with JOURNAL_PATH.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames or [])
            if "ai_reasoning" in fieldnames:
                return
            rows = list(reader)
        with JOURNAL_PATH.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=JOURNAL_HEADERS)
            writer.writeheader()
            for row in rows:
                writer.writerow({h: row.get(h, "") for h in JOURNAL_HEADERS})

    def log_entry(self, row: dict[str, Any]) -> None:
        """Append one journal row."""
        record = {h: sanitize_csv_cell(row.get(h, "")) for h in JOURNAL_HEADERS}
        if not record.get("timestamp_utc"):
            record["timestamp_utc"] = datetime.now(timezone.utc).isoformat()

        # Redact secrets from notes and AI fields in storage
        for key in ("notes", "ai_raw_response", "ai_reasoning"):
            if record.get(key):
                record[key] = sanitize_csv_cell(redact_for_log(str(record[key])))

        try:
            with JOURNAL_PATH.open("a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=JOURNAL_HEADERS)
                writer.writerow(record)
                f.flush()
            logger.info(
                "Journal entry saved: action=%s outcome=%s",
                record["action"],
                record["outcome"],
            )
        except OSError as exc:
            logger.error("Failed to write journal: %s", exc)

    def log_no_trade(
        self,
        action: str,
        screenshot_path: str,
        ai_raw: str,
        mode: str,
        reasoning: str = "",
        notes: str = "",
    ) -> None:
        self.log_entry(
            {
                "action": action,
                "outcome": "no_trade",
                "screenshot_path": screenshot_path,
                "ai_reasoning": reasoning,
                "ai_raw_response": ai_raw,
                "mode": mode,
                "notes": notes,
            }
        )
