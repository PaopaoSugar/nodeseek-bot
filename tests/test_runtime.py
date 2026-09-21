from datetime import UTC, datetime
from pathlib import Path

from nodeseek_bot.config import Config, StorageConfig, TelegramConfig
from nodeseek_bot.db import Database
from nodeseek_bot.runtime import build_runtime, run_grant, run_maintenance


def _config(tmp_path: Path) -> Config:
    return Config(
        telegram=TelegramConfig(bot_token="1:x", admin_user_ids=[]),
        storage=StorageConfig(db_path=str(tmp_path / "bot.db")),
    )


def test_build_runtime_creates_the_database(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    runtime = build_runtime(cfg)
    assert Path(cfg.storage.db_path).exists()
    runtime.close()


def test_build_runtime_wires_poller_and_notifier(tmp_path: Path) -> None:
    runtime = build_runtime(_config(tmp_path))
    assert runtime.poller is not None
    assert runtime.notifier is not None
    assert runtime.dispatcher.message.handlers  # 消息处理器已注册
    assert runtime.dispatcher.callback_query.handlers  # 按钮回调已注册
    assert runtime.queue.maxsize == 0
    runtime.close()


def test_maintenance_purges_old_items(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    runtime = build_runtime(cfg)
    assert runtime.poller is not None
    runtime.close()
    assert run_maintenance(cfg) == 0


def test_main_reports_config_errors(tmp_path: Path) -> None:
    from nodeseek_bot.__main__ import main

    assert main(["--config", str(tmp_path / "missing.toml")]) == 2


async def test_match_batch_creates_deliveries(tmp_path: Path) -> None:
    from nodeseek_bot.repository import (
        DeliveryRepository,
        ItemRepository,
        NewItem,
        RuleRepository,
        UserRepository,
    )
    from nodeseek_bot.sources import RawItem

    runtime = build_runtime(_config(tmp_path))
    uid = UserRepository(runtime.db).ensure(tg_user_id=1, username=None)
    RuleRepository(runtime.db).add(uid, "vps", limit=3)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    ItemRepository(runtime.db).insert_many(
        [
            NewItem(
                guid="1",
                title="VPS 补货",
                title_norm="vps 补货",
                link="https://x/1",
                published_at=now,
                fetched_at=now,
            )
        ]
    )
    item = RawItem(
        guid="1",
        title="VPS 补货",
        link="https://x/1",
        published_at=datetime.now(UTC),
    )
    assert runtime.match_batch([item]) == 1
    assert runtime.match_batch([item]) == 0  # 同一条帖子不会被重复投递
    assert DeliveryRepository(runtime.db).pending_users() == [uid]
    runtime.close()


async def test_match_batch_without_rules_is_a_noop(tmp_path: Path) -> None:
    from nodeseek_bot.sources import RawItem

    runtime = build_runtime(_config(tmp_path))
    item = RawItem(
        guid="1",
        title="VPS 补货",
        link="https://x/1",
        published_at=datetime.now(UTC),
    )
    assert runtime.match_batch([item]) == 0
    runtime.close()


def test_grant_creates_a_joined_user(tmp_path: Path) -> None:
    from nodeseek_bot.repository import UserRepository

    cfg = _config(tmp_path)
    assert run_grant(cfg, 12345) == 0
    db = Database(cfg.storage.db_path)
    db.initialize()
    assert UserRepository(db).find_by_tg_id(12345) is not None
    db.close()


def test_grant_then_grant_again_keeps_one_row(tmp_path: Path) -> None:

    cfg = _config(tmp_path)
    run_grant(cfg, 12345)
    run_grant(cfg, 12345)
    db = Database(cfg.storage.db_path)
    db.initialize()
    with db.tx() as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    db.close()
    assert total == 1
