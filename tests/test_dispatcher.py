"""The aiogram glue is thin, so it is tested through a fake session."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from nodeseek_bot.bot.dispatcher import build_dispatcher
from nodeseek_bot.bot.router import build_bot_deps
from nodeseek_bot.config import Config, StorageConfig, TelegramConfig
from nodeseek_bot.db import Database


class FakeSession(BaseSession):
    """Records outgoing methods and answers them with canned payloads."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[object] = []

    async def close(self) -> None:
        return None

    def stream_content(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    async def make_request(self, bot, method, timeout=None):  # type: ignore[no-untyped-def]
        self.requests.append(method)
        if isinstance(method, SendMessage):
            payload = {
                "ok": True,
                "result": {
                    "message_id": 1,
                    "date": 1789000000,
                    "chat": {"id": method.chat_id, "type": "private"},
                    "text": method.text,
                },
            }
        else:
            payload = {"ok": True, "result": True}
        return self.check_response(bot, method, 200, self.json_dumps(payload))


@pytest.fixture()
def wired(tmp_path: Path):
    db = Database(str(tmp_path / "bot.db"))
    db.initialize()
    config = Config(
        telegram=TelegramConfig(bot_token="1:x", admin_user_ids=[1]),
        storage=StorageConfig(db_path=str(tmp_path / "bot.db")),
    )
    session = FakeSession()
    bot = Bot(token="1:x", session=session)
    dispatcher = build_dispatcher(build_bot_deps(db, config))
    yield bot, dispatcher, session, db
    db.close()


def _message(update_id: int, tg_user_id: int, text: str) -> Update:
    return Update(
        update_id=update_id,
        message=Message(
            message_id=update_id,
            date=datetime.now(UTC),
            chat=Chat(id=tg_user_id, type="private"),
            from_user=User(id=tg_user_id, is_bot=False, first_name="tester"),
            text=text,
        ),
    )


def _callback(update_id: int, tg_user_id: int, data: str) -> Update:
    return Update(
        update_id=update_id,
        callback_query=CallbackQuery(
            id=str(update_id),
            from_user=User(id=tg_user_id, is_bot=False, first_name="tester"),
            chat_instance="1",
            data=data,
            message=Message(
                message_id=update_id,
                date=datetime.now(UTC),
                chat=Chat(id=tg_user_id, type="private"),
                text="旧消息",
            ),
        ),
    )


async def test_text_message_is_answered(wired) -> None:
    bot, dispatcher, session, _ = wired
    await dispatcher.feed_update(bot, _message(1, 555, "/start"))
    sent = [request for request in session.requests if isinstance(request, SendMessage)]
    assert len(sent) == 1
    assert sent[0].chat_id == 555
    assert "邀请码" in sent[0].text


async def test_non_text_message_is_ignored(wired) -> None:
    bot, dispatcher, session, _ = wired
    update = Update(
        update_id=2,
        message=Message(
            message_id=2,
            date=datetime.now(UTC),
            chat=Chat(id=555, type="private"),
            from_user=User(id=555, is_bot=False, first_name="tester"),
        ),
    )
    await dispatcher.feed_update(bot, update)
    assert session.requests == []


async def test_button_press_answers_the_callback(wired) -> None:
    bot, dispatcher, session, _ = wired
    await dispatcher.feed_update(bot, _callback(3, 555, "del:999"))
    assert any(isinstance(request, AnswerCallbackQuery) for request in session.requests)
    sent = [request for request in session.requests if isinstance(request, SendMessage)]
    assert sent and "不存在" in sent[0].text
