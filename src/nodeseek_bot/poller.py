"""Background poller."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from nodeseek_bot.config import PollerConfig
from nodeseek_bot.normalize import normalize
from nodeseek_bot.repository import ItemRepository, MetaRepository, NewItem
from nodeseek_bot.sources import RateLimitedError, RawItem, Source

logger = logging.getLogger(__name__)

ALERT_AFTER_FAILURES = 3

# A bare 429 means the whole IP is being throttled, not that we asked
# for something odd. It clears in minutes, so a one-second-ish retry is
# pointless: start the curve well above the poll interval.
RATE_LIMIT_MIN_BACKOFF_SECONDS = 300.0


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Poller:
    """Fetches the feed, persists new items and enqueues them for matching."""

    def __init__(
        self,
        *,
        source: Source,
        items: ItemRepository,
        meta: MetaRepository,
        queue: asyncio.Queue[RawItem],
        config: PollerConfig,
        on_alert: Callable[[str], Awaitable[None]],
    ) -> None:
        self._source = source
        self._items = items
        self._meta = meta
        self._queue = queue
        self._config = config
        self._on_alert = on_alert
        self._consecutive_failures = 0
        self._alerted = False
        self._delay = config.interval_seconds

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def next_delay(self) -> float:
        """Seconds until the next attempt, after backoff."""
        return self._delay

    async def run_once(self) -> int:
        """Fetch once. Raises on source failure; the caller drives backoff."""
        raw_items = await self._source.fetch()
        fetched_at = _now_iso()
        rows = [
            NewItem(
                guid=item.guid,
                title=item.title,
                title_norm=normalize(item.title),
                link=item.link,
                author=item.author,
                category=item.category,
                published_at=item.published_at.astimezone(UTC).isoformat(timespec="seconds"),
                fetched_at=fetched_at,
                excerpt=item.excerpt,
            )
            for item in raw_items
        ]
        inserted = self._items.insert_many(rows)
        by_guid = {item.guid: item for item in raw_items}
        for guid in inserted:
            await self._queue.put(by_guid[guid])

        self._meta.set("last_success_at", fetched_at)
        self._consecutive_failures = 0
        self._alerted = False

        if len(inserted) >= self._config.overflow_threshold:
            await self._on_alert(
                f"⚠️ 单次轮询新增 {len(inserted)} 条，已接近或超过 feed 的 "
                f"{len(raw_items)} 条窗口，存在丢帖风险。"
                f"建议缩短轮询间隔，或改用窗口更大的数据源。"
            )
        return len(inserted)

    async def _notify_if_needed(self, error: Exception) -> None:
        if self._consecutive_failures < ALERT_AFTER_FAILURES or self._alerted:
            return
        self._alerted = True
        hint = ""
        if isinstance(error, RateLimitedError):
            hint = (
                "\n看起来是被限流了。先确认这台机器上没有别的进程在抓同一个 feed"
                "（例如同机上别的抓取脚本）。"
            )
        await self._on_alert(
            f"🔴 NodeSeek 轮询已连续失败 {self._consecutive_failures} 次。"
            f"最近错误：{str(error)[:200]}{hint}"
        )

    def _next_delay(self, current: float, exc: Exception) -> float:
        """How long to wait before trying again.

        An upstream ``Retry-After`` is an explicit instruction, so it wins
        over our own curve. Otherwise the delay doubles, and a bare 429
        starts from a much higher floor.
        """
        retry_after = getattr(exc, "retry_after", None)
        if retry_after:
            # An explicit Retry-After beats our own curve, in both
            # directions: waiting longer than asked is needless.
            delay = max(float(retry_after), self._config.interval_seconds)
        else:
            floor = RATE_LIMIT_MIN_BACKOFF_SECONDS if isinstance(exc, RateLimitedError) else 0.0
            delay = max(current * 2, floor, self._config.interval_seconds)
        return min(delay, self._config.max_backoff_seconds)

    async def run(self) -> None:
        self._delay = self._config.interval_seconds
        while True:
            try:
                count = await self.run_once()
                if count:
                    logger.info("poll ok, %d new item(s)", count)
                self._delay = self._config.interval_seconds
            except Exception as exc:  # noqa: BLE001 - one bad poll must not kill the loop
                self._consecutive_failures += 1
                self._delay = self._next_delay(self._delay, exc)
                logger.warning(
                    "poll failed (%d in a row), retry in %.1fs: %s",
                    self._consecutive_failures,
                    self._delay,
                    exc,
                )
                self._meta.set("last_error", str(exc)[:200])
                await self._notify_if_needed(exc)
            await asyncio.sleep(self._delay)
