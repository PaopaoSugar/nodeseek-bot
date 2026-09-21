"""Operator alerting."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class AlertDispatcher:
    """Sends operational alerts to every configured admin."""

    def __init__(self, bot: object, admin_user_ids: list[int]) -> None:
        self._bot = bot
        self._admins = list(admin_user_ids)

    async def send(self, message: str) -> None:
        for admin_id in self._admins:
            try:
                await self._bot.send_message(admin_id, message)
            except Exception:  # noqa: BLE001 - one bad admin must not block the rest
                logger.exception("failed to alert admin %s", admin_id)
