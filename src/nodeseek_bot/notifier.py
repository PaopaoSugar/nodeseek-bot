"""Delivery of matches to Telegram."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from nodeseek_bot.config import NotifierConfig
from nodeseek_bot.formatting import build_messages
from nodeseek_bot.repository import DeliveryRepository

logger = logging.getLogger(__name__)

SCAN_INTERVAL = 1.0


class TokenBucket:
    """Shared rate limiter for every outgoing message."""

    def __init__(self, rate_per_second: float) -> None:
        self._rate = max(rate_per_second, 0.001)
        self._tokens = self._rate
        self._updated = time.monotonic()

    async def acquire(self) -> None:
        while True:
            now = time.monotonic()
            elapsed = now - self._updated
            self._updated = now
            self._tokens = min(self._rate, self._tokens + elapsed * self._rate)
            if self._tokens >= 1:
                self._tokens -= 1
                return
            await asyncio.sleep((1 - self._tokens) / self._rate)


class Notifier:
    """Turns pending deliveries into Telegram messages, one user at a time."""

    def __init__(
        self,
        *,
        bot: object,
        deliveries: DeliveryRepository,
        config: NotifierConfig,
        on_alert: Callable[[str], Awaitable[None]],
    ) -> None:
        self._bot = bot
        self._deliveries = deliveries
        self._config = config
        self._on_alert = on_alert
        self._bucket = TokenBucket(config.global_rate_per_second)
        #: user_id -> monotonic deadline before which we must stay quiet.
        self.cooldown_until: dict[int, float] = {}

    async def drain_once(self) -> None:
        for user_id in self._deliveries.pending_users():
            if time.monotonic() < self.cooldown_until.get(user_id, 0.0):
                continue
            await self._send_user(user_id)

    async def _send_user(self, user_id: int) -> None:
        pending = self._deliveries.claim_for_user(user_id)
        if not pending:
            return
        ids = [item.delivery_id for item in pending]
        chat_id = pending[0].tg_user_id
        try:
            for message in build_messages(pending, self._config.max_message_chars):
                await self._bucket.acquire()
                await self._bot.send_message(
                    chat_id,
                    message,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
        except Exception as exc:  # noqa: BLE001 - classified right below
            retry_after = getattr(exc, "retry_after", None)
            logger.warning("send failed for user %s: %s", user_id, exc)
            if retry_after:
                # Telegram is throttling us: back off without burning an attempt.
                self.cooldown_until[user_id] = time.monotonic() + float(retry_after)
                return
            failures = self._deliveries.mark_failed(ids, str(exc)[:200], self._config.max_attempts)
            if failures:
                await self._on_alert(f"🔴 有 {failures} 条通知发送失败，已超过重试上限。")
            return

        self._deliveries.mark_sent(ids)
        self.cooldown_until[user_id] = time.monotonic() + self._config.coalesce_window_seconds

    async def run(self) -> None:
        while True:
            try:
                await self.drain_once()
            except Exception:  # noqa: BLE001 - the notifier must never die
                logger.exception("notifier scan failed")
            await asyncio.sleep(SCAN_INTERVAL)
