"""30-minute cooldown after wins and losses."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from config import COOLDOWN_MINUTES, COOLDOWN_STATE_PATH
from security_utils import atomic_write_json, load_json_file

logger = logging.getLogger(__name__)


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class CooldownManager:
    """Enforce post-trade cooldown."""

    def __init__(self) -> None:
        self._until: datetime | None = None
        self._load()

    def _load(self) -> None:
        raw = load_json_file(COOLDOWN_STATE_PATH)
        if not raw:
            return
        until_str = raw.get("cooldown_until")
        if not until_str:
            return
        try:
            self._until = _ensure_utc(datetime.fromisoformat(str(until_str).replace("Z", "+00:00")))
        except (ValueError, TypeError) as exc:
            logger.warning("Could not load cooldown state: %s", exc)

    def _save(self) -> None:
        payload = {
            "cooldown_until": self._until.isoformat() if self._until else None,
        }
        atomic_write_json(COOLDOWN_STATE_PATH, payload)

    def start_cooldown(self) -> None:
        """Start cooldown from now."""
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
        if self._until is None or not self.is_active():
            return 0
        delta = self._until - datetime.now(timezone.utc)
        return max(0, int(delta.total_seconds()))

    def reason(self) -> str:
        if not self.is_active() or self._until is None:
            return ""
        mins = self.remaining_seconds() // 60
        return f"Cooldown active ({mins} min remaining)"
