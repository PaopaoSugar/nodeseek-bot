import asyncio
from datetime import UTC, datetime
from pathlib import Path

from nodeseek_bot.config import PollerConfig
from nodeseek_bot.db import Database
from nodeseek_bot.poller import Poller
from nodeseek_bot.repository import ItemRepository, MetaRepository
from nodeseek_bot.sources import RateLimitedError, RawItem


class FakeSource:
    name = "fake"

    def __init__(self, batches: list[object]) -> None:
        self._batches = batches

    async def fetch(self) -> list[RawItem]:
        if not self._batches:
            return []
        batch = self._batches.pop(0)
        if isinstance(batch, Exception):
            raise batch
        return batch  # type: ignore[return-value]


def _item(guid: str) -> RawItem:
    return RawItem(
        guid=guid,
        title=f"标题 {guid}",
        link=f"https://www.nodeseek.com/post-{guid}-1",
        published_at=datetime(2026, 9, 21, 17, 19, 26, tzinfo=UTC),
    )


def _build(
    tmp_path: Path,
    batches: list[object],
    config: PollerConfig | None = None,
) -> tuple[Database, Poller, asyncio.Queue[RawItem], list[str]]:
    db = Database(str(tmp_path / "bot.db"))
    db.initialize()
    alerts: list[str] = []

    async def on_alert(message: str) -> None:
        alerts.append(message)

    queue: asyncio.Queue[RawItem] = asyncio.Queue()
    poller = Poller(
        source=FakeSource(batches),
        items=ItemRepository(db),
        meta=MetaRepository(db),
        queue=queue,
        config=config or PollerConfig(),
        on_alert=on_alert,
    )
    return db, poller, queue, alerts


FAST = PollerConfig(interval_seconds=0.005, max_backoff_seconds=0.01)


async def test_run_once_enqueues_only_new_items(tmp_path: Path) -> None:
    db, poller, queue, _ = _build(tmp_path, [[_item("1"), _item("2")], [_item("2"), _item("3")]])
    assert await poller.run_once() == 2
    assert await poller.run_once() == 1
    drained = [queue.get_nowait().guid for _ in range(queue.qsize())]
    assert drained == ["1", "2", "3"]
    db.close()


async def _run_briefly(poller: Poller) -> None:
    """Drive run() for a moment, then cancel it cleanly."""
    task = asyncio.create_task(poller.run())
    await asyncio.sleep(0.15)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_alerts_after_three_consecutive_failures(tmp_path: Path) -> None:
    db, poller, _, alerts = _build(tmp_path, [RuntimeError("boom")] * 4, FAST)
    await _run_briefly(poller)
    assert len(alerts) == 1
    assert "3" in alerts[0]
    db.close()


async def test_success_resets_failure_counter(tmp_path: Path) -> None:
    db, poller, _, alerts = _build(
        tmp_path, [RuntimeError("boom"), RuntimeError("boom"), [_item("1")]], FAST
    )
    await _run_briefly(poller)
    assert alerts == []
    assert poller.consecutive_failures == 0
    db.close()


async def test_retry_after_from_the_server_is_honoured(tmp_path: Path) -> None:
    db, poller, _, _ = _build(
        tmp_path,
        [RateLimitedError("429", retry_after=42)],
        PollerConfig(interval_seconds=30, max_backoff_seconds=3600),
    )
    await _run_briefly(poller)
    assert poller.next_delay == 42
    db.close()


async def test_bare_429_backs_off_far_beyond_the_interval(tmp_path: Path) -> None:
    db, poller, _, _ = _build(
        tmp_path,
        [RateLimitedError("429")],
        PollerConfig(interval_seconds=30, max_backoff_seconds=3600),
    )
    await _run_briefly(poller)
    assert poller.next_delay == 300
    db.close()


async def test_retry_after_is_capped_by_max_backoff(tmp_path: Path) -> None:
    db, poller, _, _ = _build(
        tmp_path,
        [RateLimitedError("429", retry_after=7200)],
        PollerConfig(interval_seconds=30, max_backoff_seconds=600),
    )
    await _run_briefly(poller)
    assert poller.next_delay == 600
    db.close()


async def test_ordinary_failure_doubles_the_interval(tmp_path: Path) -> None:
    db, poller, _, _ = _build(
        tmp_path,
        [RuntimeError("boom")],
        PollerConfig(interval_seconds=30, max_backoff_seconds=3600),
    )
    await _run_briefly(poller)
    assert poller.next_delay == 60
    db.close()


async def test_success_restores_the_plain_interval(tmp_path: Path) -> None:
    db, poller, _, _ = _build(tmp_path, [[_item("1")]], PollerConfig(interval_seconds=30))
    await poller.run_once()
    assert poller.next_delay == 30
    db.close()


async def test_rate_limit_alert_says_it_is_a_rate_limit(tmp_path: Path) -> None:
    db, poller, _, alerts = _build(tmp_path, [RateLimitedError("429")] * 4, FAST)
    await _run_briefly(poller)
    assert len(alerts) == 1
    assert "限流" in alerts[0]
    db.close()


async def test_overflow_triggers_alert(tmp_path: Path) -> None:
    batch = [_item(str(i)) for i in range(20)]
    db, poller, _, alerts = _build(tmp_path, [batch])
    await poller.run_once()
    assert len(alerts) == 1
    assert "丢帖" in alerts[0]
    db.close()


async def test_last_success_is_persisted(tmp_path: Path) -> None:
    db, poller, _, _ = _build(tmp_path, [[_item("1")]])
    await poller.run_once()
    assert MetaRepository(db).get("last_success_at") is not None
    db.close()


async def test_repeating_the_same_batch_does_not_alert_again(tmp_path: Path) -> None:
    batch = [_item(str(i)) for i in range(20)]
    db, poller, _, alerts = _build(tmp_path, [batch, batch])
    await poller.run_once()
    await poller.run_once()
    assert len(alerts) == 1  # 同一批帖子第二次不会重复插入，也不会再告警
    db.close()


async def test_run_keeps_going_after_a_failure(tmp_path: Path) -> None:
    db, poller, queue, _ = _build(tmp_path, [RuntimeError("boom"), [_item("1")]], FAST)
    await _run_briefly(poller)
    assert queue.qsize() == 1
    db.close()
