"""Telegram message construction."""

from __future__ import annotations

import html

from nodeseek_bot.repository import PendingDelivery

HEADER = "🔍 命中「{keyword}」的新帖 {count} 条"
SEPARATOR = "\n\n"
MAX_TITLE_CHARS = 120


def escape_html(text: str) -> str:
    return html.escape(text, quote=False)


def _title(text: str) -> str:
    if len(text) <= MAX_TITLE_CHARS:
        return text
    return text[: MAX_TITLE_CHARS - 1] + "…"


def _entry(index: int, item: PendingDelivery) -> str:
    return f"{index}. {escape_html(_title(item.title))}\n   {escape_html(item.link)}"


def build_messages(pending: list[PendingDelivery], max_chars: int) -> list[str]:
    """Group deliveries into as few messages as fit under *max_chars*.

    A single entry is never split, so one message may slightly exceed
    *max_chars*; title truncation keeps every entry small enough that the
    result stays far below Telegram's hard limit.
    """
    if not pending:
        return []

    keyword = escape_html(pending[0].keyword_raw)
    messages: list[str] = []
    buffer: list[str] = []
    size = 0
    batch_start = 1

    def flush() -> None:
        nonlocal buffer, size, batch_start
        if not buffer:
            return
        header = HEADER.format(keyword=keyword, count=len(buffer))
        messages.append(header + SEPARATOR + "\n\n".join(buffer))
        batch_start += len(buffer)
        buffer = []
        size = 0

    for item in pending:
        entry = _entry(batch_start + len(buffer), item)
        if buffer and size + len(entry) + 2 > max_chars:
            flush()
            entry = _entry(batch_start, item)
        buffer.append(entry)
        size += len(entry) + 2

    flush()
    return messages
