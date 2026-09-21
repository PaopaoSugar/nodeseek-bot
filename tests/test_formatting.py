from nodeseek_bot.formatting import PendingDelivery, build_messages, escape_html


def _delivery(index: int, title: str = "标题") -> PendingDelivery:
    return PendingDelivery(
        delivery_id=index,
        rule_id=1,
        user_id=1,
        tg_user_id=100,
        keyword_raw="vps",
        title=title,
        link=f"https://www.nodeseek.com/post-{index}-1",
    )


def test_escape_html_covers_telegram_specials() -> None:
    assert escape_html("Rock & Roll <b>") == "Rock &amp; Roll &lt;b&gt;"


def test_escape_html_leaves_quotes_alone() -> None:
    assert escape_html('say "hi"') == 'say "hi"'


def test_build_messages_numbers_entries() -> None:
    messages = build_messages([_delivery(1, "A"), _delivery(2, "B")], max_chars=3500)
    assert len(messages) == 1
    assert "1. A" in messages[0]
    assert "2. B" in messages[0]
    assert "命中「vps」" in messages[0]


def test_build_messages_escapes_titles() -> None:
    messages = build_messages([_delivery(1, "A & B <x>")], max_chars=3500)
    assert "&amp;" in messages[0]
    assert "<x>" not in messages[0]


def test_build_messages_splits_when_too_long() -> None:
    pending = [_delivery(i, "T" * 200) for i in range(1, 41)]
    messages = build_messages(pending, max_chars=500)
    assert len(messages) > 1
    assert all(len(message) <= 700 for message in messages)


def test_build_messages_handles_one_very_long_title() -> None:
    messages = build_messages([_delivery(1, "T" * 5000)], max_chars=3500)
    assert len(messages) == 1
    assert "…" in messages[0]
    assert len(messages[0]) < 1000


def test_build_messages_truncates_title() -> None:
    messages = build_messages([_delivery(1, "T" * 300)], max_chars=3500)
    assert "…" in messages[0]


def test_build_messages_with_empty_input() -> None:
    assert build_messages([], max_chars=3500) == []


async def test_token_bucket_limits_rate() -> None:
    import time

    from nodeseek_bot.notifier import TokenBucket

    bucket = TokenBucket(rate_per_second=50)
    start = time.monotonic()
    for _ in range(100):
        await bucket.acquire()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.5
