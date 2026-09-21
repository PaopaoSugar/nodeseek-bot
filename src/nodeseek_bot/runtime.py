"""Process wiring: one process, four cooperating loops."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from aiogram import Bot, Dispatcher

from nodeseek_bot.alerts import AlertDispatcher
from nodeseek_bot.bot.dispatcher import build_dispatcher
from nodeseek_bot.bot.router import build_bot_deps
from nodeseek_bot.config import Config
from nodeseek_bot.db import Database
from nodeseek_bot.matcher import matches
from nodeseek_bot.normalize import normalize
from nodeseek_bot.notifier import Notifier
from nodeseek_bot.poller import Poller
from nodeseek_bot.repository import (
    DeliveryRepository,
    ItemRepository,
    MetaRepository,
    RuleRepository,
    UserRepository,
)
from nodeseek_bot.sources import RawItem
from nodeseek_bot.sources.nodeseek import NodeSeekSource

logger = logging.getLogger(__name__)

MATCH_BATCH_LIMIT = 500


@dataclass(slots=True)
class Runtime:
    db: Database
    bot: Bot
    source: NodeSeekSource
    dispatcher: Dispatcher
    poller: Poller
    notifier: Notifier
    queue: asyncio.Queue[RawItem]

    def match_batch(self, batch: list[RawItem]) -> int:
        """Match one drained batch against every enabled rule."""
        rules = RuleRepository(self.db).all_enabled()
        if not rules:
            return 0
        pairs: list[tuple[str, int, int]] = []
        for item in batch:
            title_norm = normalize(item.title)
            for rule in rules:
                if matches(title_norm, rule.keyword_norm):
                    pairs.append((item.guid, rule.id, rule.user_id))
        return DeliveryRepository(self.db).create_pending(pairs)

    async def _match_loop(self) -> None:
        while True:
            batch = [await self.queue.get()]
            while len(batch) < MATCH_BATCH_LIMIT:
                try:
                    batch.append(self.queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            matched = self.match_batch(batch)
            logger.info("matched %d delivery(ies) from %d item(s)", matched, len(batch))

    async def _clear_stale_webhook(self) -> None:
        """Polling and webhooks are mutually exclusive.

        A leftover webhook makes ``getUpdates`` fail with a 409, so clear it
        once at startup. Dropping the backlog also stops the bot from replaying
        commands sent while it was down.
        """
        try:
            await self.bot.delete_webhook(drop_pending_updates=True)
        except Exception:  # noqa: BLE001 - a bad network here must not abort startup
            logger.warning("could not clear the webhook, polling may fail", exc_info=True)

    async def run(self) -> None:
        await self._clear_stale_webhook()
        async with asyncio.TaskGroup() as group:
            group.create_task(
                self.dispatcher.start_polling(
                    self.bot,
                    handle_signals=False,
                    close_bot_session=False,
                )
            )
            group.create_task(self.poller.run())
            group.create_task(self.notifier.run())
            group.create_task(self._match_loop())

    async def aclose(self) -> None:
        await self.source.aclose()
        await self.bot.session.close()
        self.close()

    def close(self) -> None:
        self.db.close()


def build_runtime(config: Config) -> Runtime:
    db = Database(config.storage.db_path)
    db.initialize()
    bot = Bot(token=config.telegram.bot_token)
    alerts = AlertDispatcher(bot, config.telegram.admin_user_ids)
    source = NodeSeekSource(
        feed_url=config.poller.feed_url,
        user_agent=config.poller.user_agent,
        timeout=config.poller.request_timeout_seconds,
    )
    queue: asyncio.Queue[RawItem] = asyncio.Queue()
    return Runtime(
        db=db,
        bot=bot,
        source=source,
        dispatcher=build_dispatcher(build_bot_deps(db, config)),
        poller=Poller(
            source=source,
            items=ItemRepository(db),
            meta=MetaRepository(db),
            queue=queue,
            config=config.poller,
            on_alert=alerts.send,
        ),
        notifier=Notifier(
            bot=bot,
            deliveries=DeliveryRepository(db),
            config=config.notifier,
            on_alert=alerts.send,
        ),
        queue=queue,
    )


def run_maintenance(config: Config) -> int:
    """Drop items past the retention window. Safe to run from cron or a timer."""
    db = Database(config.storage.db_path)
    db.initialize()
    try:
        removed = ItemRepository(db).purge_older_than(config.storage.retention_days)
    finally:
        db.close()
    logger.info("maintenance: purged %d item(s)", removed)
    return 0


def run_grant(config: Config, tg_user_id: int) -> int:
    """Add a user without an invite code.

    Bootstrap path: the owner runs this once, because ``/invite`` itself
    requires an already joined user. Afterwards everything happens in Telegram.
    """
    db = Database(config.storage.db_path)
    db.initialize()
    try:
        user_id = UserRepository(db).ensure(tg_user_id=tg_user_id, username=None)
    finally:
        db.close()
    logger.info(
        "已授权 Telegram 用户 %s（内部 id %s），现在可以在 Telegram 里发 /start",
        tg_user_id,
        user_id,
    )
    return 0
