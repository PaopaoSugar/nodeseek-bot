"""Feed source abstractions."""

from __future__ import annotations

import email.utils
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol


class SourceError(Exception):
    """A fetch failed in a way the poller may want to react to."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class RateLimitedError(SourceError):
    """The upstream explicitly asked us to slow down (HTTP 429)."""


def parse_retry_after(raw: str | None) -> float | None:
    """Seconds from a Retry-After header, which is either a delay or a date."""
    if not raw:
        return None
    value = raw.strip()
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max((when - datetime.now(UTC)).total_seconds(), 0.0)


@dataclass(frozen=True, slots=True)
class RawItem:
    guid: str
    title: str
    link: str
    published_at: datetime
    author: str | None = None
    category: str | None = None
    excerpt: str | None = None


class Source(Protocol):
    name: str

    async def fetch(self) -> list[RawItem]:
        """Return the current feed window. Raises on network or parse failure."""
