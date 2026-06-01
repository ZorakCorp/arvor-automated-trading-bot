"""30-minute cooldown after wins and losses."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from config import COOLDOWN_MINUTES, COOLDOWN_STATE_PATH

logger = logging.getLogger(__name__)


class CooldownManager:
    """Enforce post-trade cooldown."""

    def __init__(self) -> None:
        self._until: datetime | None = None
        self._load()

    def _load(self) -> None:
        if not COOLDOWN_STATE_PATH.exists():
            return
        try:
            raw = json.loads(COOLDOWN_STATE_PATH.read_text(encoding="utf-8"))
            until_str = raw.get("cooldown_until")
            if until_str:
                self._until = datetime.fromisoformat(until_str)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.warning("Could not load cooldown state: %s", exc)

    def _save(self) -> None:
        COOLDOWN_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cooldown_until": self._until.isoformat() if self._until else None,
        }
        COOLDOWN_STATE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def start_cooldown(self) -> None:
        """Start 30-minute cooldown from now."""
        self._until = datetime.now(timezone.utc) + timedelta(minutes=COOLDOWN_MINUTES)
        self._save()
        logger.info("Cooldown started until %s UTC", self._until.strftime("%Y-%m-%d %H:%M:%S"))

    def is_active(self) -> bool:
        if self._until is None:
            return False
        if datetime.now(timezone.utc) >= self._until:
            self._until = None
            self._save()
            return False
        return True

    def remaining_seconds(self) -> int:
        if not self.is_active() or self._until is None:
            return 0
        delta = self._until - datetime.now(timezone.utc)
        return max(0, int(delta.total_seconds()))

    def reason(self) -> str:
        if not self.is_active() or self._until is None:
            return ""
        mins = self.remaining_seconds() // 60
        return f"Cooldown active ({mins} min remaining)"
