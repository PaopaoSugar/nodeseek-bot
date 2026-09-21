"""NodeSeek RSS source."""

from __future__ import annotations

import email.utils
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import httpx

from nodeseek_bot.sources import RateLimitedError, RawItem, SourceError, parse_retry_after

DC_NS = "http://purl.org/dc/elements/1.1/"
ACCEPT = "application/rss+xml,application/xml,text/xml;q=0.9,*/*;q=0.8"


class FeedParseError(Exception):
    """Raised when the payload is not a usable RSS document."""


def _text(node: ET.Element | None) -> str | None:
    if node is None or node.text is None:
        return None
    stripped = node.text.strip()
    return stripped or None


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def parse_feed(payload: bytes) -> list[RawItem]:
    if not payload.strip():
        raise FeedParseError("empty payload")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise FeedParseError(f"invalid XML: {exc}") from exc

    if root.tag != "rss":
        raise FeedParseError(f"unexpected root element: {root.tag}")

    items: list[RawItem] = []
    for node in root.iter("item"):
        guid = _text(node.find("guid"))
        title = _text(node.find("title"))
        link = _text(node.find("link"))
        published = _parse_date(_text(node.find("pubDate")))
        if not guid or not title or not link or published is None:
            continue
        items.append(
            RawItem(
                guid=guid,
                title=title,
                link=link,
                published_at=published,
                author=_text(node.find(f"{{{DC_NS}}}creator")),
                category=_text(node.find("category")),
                excerpt=_text(node.find("description")),
            )
        )
    return items


class NodeSeekSource:
    name = "nodeseek"

    def __init__(self, *, feed_url: str, user_agent: str, timeout: float) -> None:
        self._feed_url = feed_url
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "User-Agent": user_agent,
                "Accept": ACCEPT,
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
            follow_redirects=True,
        )

    async def fetch(self) -> list[RawItem]:
        response = await self._client.get(self._feed_url)
        if response.status_code == 429:
            header = response.headers.get("retry-after")
            raise RateLimitedError(
                f"429 Too Many Requests（Retry-After: {header or '未提供'}）",
                retry_after=parse_retry_after(header),
            )
        if response.status_code >= 400:
            raise SourceError(
                f"HTTP {response.status_code} from {self._feed_url}: {response.text[:200]}"
            )
        return parse_feed(response.content)

    async def aclose(self) -> None:
        await self._client.aclose()
