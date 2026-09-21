from pathlib import Path

from nodeseek_bot.config import NotifierConfig
from nodeseek_bot.db import Database
from nodeseek_bot.notifier import Notifier
from nodeseek_bot.repository import (
    DeliveryRepository,
    ItemRepository,
    NewItem,
    RuleRepository,
    UserRepository,
)


class FakeBot:
    def __init__(self, error: Exception | None = None) -> None:
        self.sent: list[tuple[int, str]] = []
        self._error = error

    async def send_message(self, chat_id: int, text: str, **kwargs: object) -> None:
        if self._error is not None:
            raise self._error
        self.sent.append((chat_id, text))


async def _noop(message: str) -> None:
    return None


def _setup(tmp_path: Path, deliveries: int = 3) -> tuple[Database, DeliveryRepository]:
    db = Database(str(tmp_path / "bot.db"))
    db.initialize()
    uid = UserRepository(db).ensure(tg_user_id=555, username="u")
    rid = RuleRepository(db).add(uid, "vps", limit=3)
    ItemRepository(db).insert_many(
        [
            NewItem(
                guid=str(i),
                title=f"标题 {i}",
                title_norm=f"标题 {i}",
                link=f"https://www.nodeseek.com/post-{i}-1",
                published_at=f"2026-09-21T17:19:{i:02d}+00:00",
                fetched_at="2026-09-21T17:19:30+00:00",
            )
            for i in range(deliveries)
        ]
    )
    repo = DeliveryRepository(db)
    repo.create_pending([(str(i), rid, uid) for i in range(deliveries)])
    return db, repo


async def test_pending_for_one_user_becomes_a_single_message(tmp_path: Path) -> None:
    db, repo = _setup(tmp_path)
    bot = FakeBot()
    await Notifier(bot=bot, deliveries=repo, config=NotifierConfig(), on_alert=_noop).drain_once()
    assert len(bot.sent) == 1
    assert bot.sent[0][0] == 555
    assert bot.sent[0][1].count("https://www.nodeseek.com/post-") == 3
    db.close()


async def test_deliveries_are_marked_sent(tmp_path: Path) -> None:
    db, repo = _setup(tmp_path)
    await Notifier(
        bot=FakeBot(), deliveries=repo, config=NotifierConfig(), on_alert=_noop
    ).drain_once()
    assert repo.pending_users() == []
    db.close()


async def test_no_pending_is_a_noop(tmp_path: Path) -> None:
    db, repo = _setup(tmp_path, deliveries=0)
    bot = FakeBot()
    await Notifier(bot=bot, deliveries=repo, config=NotifierConfig(), on_alert=_noop).drain_once()
    assert bot.sent == []
    db.close()


async def test_failure_keeps_delivery_pending_then_marks_failed(tmp_path: Path) -> None:
    db, repo = _setup(tmp_path, deliveries=1)
    alerts: list[str] = []

    async def on_alert(message: str) -> None:
        alerts.append(message)

    config = NotifierConfig(max_attempts=2)
    notifier = Notifier(
        bot=FakeBot(RuntimeError("boom")), deliveries=repo, config=config, on_alert=on_alert
    )
    await notifier.drain_once()
    assert len(repo.pending_users()) == 1
    notifier.cooldown_until.clear()
    await notifier.drain_once()
    assert repo.pending_users() == []
    assert alerts and "失败" in alerts[0]
    db.close()


async def test_rate_limit_sets_cooldown_without_counting_attempt(tmp_path: Path) -> None:
    class RetryAfter(Exception):
        retry_after = 30

    db, repo = _setup(tmp_path, deliveries=1)
    notifier = Notifier(
        bot=FakeBot(RetryAfter()), deliveries=repo, config=NotifierConfig(), on_alert=_noop
    )
    await notifier.drain_once()
    assert len(repo.pending_users()) == 1
    assert notifier.cooldown_until
    db.close()


async def test_sent_delivery_is_not_resent(tmp_path: Path) -> None:
    db, repo = _setup(tmp_path, deliveries=1)
    config = NotifierConfig()
    bot = FakeBot()
    notifier = Notifier(bot=bot, deliveries=repo, config=config, on_alert=_noop)
    await notifier.drain_once()
    notifier.cooldown_until.clear()
    await notifier.drain_once()
    assert len(bot.sent) == 1
    db.close()
