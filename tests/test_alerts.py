from nodeseek_bot.alerts import AlertDispatcher


class FakeBot:
    def __init__(self, fail_for: set[int] | None = None) -> None:
        self.sent: list[tuple[int, str]] = []
        self._fail_for = fail_for or set()

    async def send_message(self, chat_id: int, text: str, **kwargs: object) -> None:
        if chat_id in self._fail_for:
            raise RuntimeError("blocked")
        self.sent.append((chat_id, text))


async def test_alerts_reach_every_admin() -> None:
    bot = FakeBot()
    await AlertDispatcher(bot, [1, 2]).send("hello")
    assert bot.sent == [(1, "hello"), (2, "hello")]


async def test_one_bad_admin_does_not_block_the_rest() -> None:
    bot = FakeBot(fail_for={1})
    await AlertDispatcher(bot, [1, 2]).send("hello")
    assert bot.sent == [(2, "hello")]


async def test_no_admins_is_a_noop() -> None:
    bot = FakeBot()
    await AlertDispatcher(bot, []).send("hello")
    assert bot.sent == []
