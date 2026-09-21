import email.utils
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from nodeseek_bot.sources import RateLimitedError, SourceError, parse_retry_after
from nodeseek_bot.sources.nodeseek import FeedParseError, NodeSeekSource, parse_feed

FIXTURE = Path(__file__).parent / "fixtures" / "nodeseek_sample.xml"

NO_DESCRIPTION = b"""<?xml version="1.0"?><rss version="2.0"><channel>
  <item><title><![CDATA[no body]]></title>
    <link>https://www.nodeseek.com/post-1-1</link>
    <guid isPermaLink="false">1</guid>
    <pubDate>Mon, 21 Sep 2026 17:19:26 GMT</pubDate>
  </item></channel></rss>"""

NO_GUID = b"""<?xml version="1.0"?><rss version="2.0"><channel>
  <item><title><![CDATA[orphan]]></title>
    <link>https://www.nodeseek.com/post-1-1</link>
    <pubDate>Mon, 21 Sep 2026 17:19:26 GMT</pubDate>
  </item>
  <item><title><![CDATA[ok]]></title>
    <link>https://www.nodeseek.com/post-2-1</link>
    <guid>2</guid>
    <pubDate>Mon, 21 Sep 2026 17:19:26 GMT</pubDate>
  </item></channel></rss>"""

NO_PUBDATE = b"""<?xml version="1.0"?><rss version="2.0"><channel>
  <item><title><![CDATA[no date]]></title>
    <link>https://www.nodeseek.com/post-1-1</link>
    <guid>1</guid>
  </item></channel></rss>"""

BAD_DATE = b"""<?xml version="1.0"?><rss version="2.0"><channel>
  <item><title><![CDATA[bad date]]></title>
    <link>https://www.nodeseek.com/post-1-1</link>
    <guid>1</guid>
    <pubDate>not a date</pubDate>
  </item></channel></rss>"""

ENTITIES = b"""<?xml version="1.0"?><rss version="2.0"><channel>
  <item><title>Rock &amp; Roll &lt;test&gt;</title>
    <link>https://www.nodeseek.com/post-3-1</link>
    <guid>3</guid>
    <pubDate>Mon, 21 Sep 2026 17:19:26 GMT</pubDate>
  </item></channel></rss>"""

CDATA_ENTITIES = b"""<?xml version="1.0"?><rss version="2.0"><channel>
  <item><title><![CDATA[Rock &amp; Roll <test>]]></title>
    <link>https://www.nodeseek.com/post-4-1</link>
    <guid>4</guid>
    <pubDate>Mon, 21 Sep 2026 17:19:26 GMT</pubDate>
  </item></channel></rss>"""


def test_parses_all_items_from_fixture() -> None:
    items = parse_feed(FIXTURE.read_bytes())
    assert len(items) >= 15
    assert all(item.guid for item in items)
    assert all(item.title for item in items)
    assert all(item.link.startswith("https://www.nodeseek.com/post-") for item in items)


def test_guid_is_the_numeric_post_id() -> None:
    assert parse_feed(FIXTURE.read_bytes())[0].guid.isdigit()


def test_published_at_is_timezone_aware_and_in_the_past() -> None:
    item = parse_feed(FIXTURE.read_bytes())[0]
    assert item.published_at.tzinfo is not None
    assert item.published_at < datetime.now(UTC)


def test_fixture_contains_an_item_without_description() -> None:
    items = parse_feed(FIXTURE.read_bytes())
    assert any(item.excerpt is None for item in items)


def test_item_without_description_and_extras() -> None:
    item = parse_feed(NO_DESCRIPTION)[0]
    assert item.excerpt is None
    assert item.author is None
    assert item.category is None


def test_item_without_guid_is_skipped() -> None:
    assert [item.guid for item in parse_feed(NO_GUID)] == ["2"]


def test_item_without_pubdate_is_skipped() -> None:
    assert parse_feed(NO_PUBDATE) == []


def test_unparseable_date_is_skipped() -> None:
    assert parse_feed(BAD_DATE) == []


def test_entities_outside_cdata_are_decoded() -> None:
    assert parse_feed(ENTITIES)[0].title == "Rock & Roll <test>"


def test_cdata_content_is_kept_literal() -> None:
    # The real feed wraps titles in CDATA, so "&" arrives as a bare ampersand.
    assert parse_feed(CDATA_ENTITIES)[0].title == "Rock &amp; Roll <test>"


def test_non_xml_payload_raises() -> None:
    with pytest.raises(FeedParseError):
        parse_feed(b"<html><body>maintenance</body></html>")


def test_empty_payload_raises() -> None:
    with pytest.raises(FeedParseError):
        parse_feed(b"")


def test_whitespace_payload_raises() -> None:
    with pytest.raises(FeedParseError):
        parse_feed(b"   \n  ")


def _source_replying(response: httpx.Response) -> tuple[NodeSeekSource, httpx.AsyncClient]:
    transport = httpx.MockTransport(lambda request: response)
    client = httpx.AsyncClient(transport=transport)
    source = NodeSeekSource(feed_url="https://rss.nodeseek.com/", user_agent="ua", timeout=5)
    source._client = client
    return source, client


async def test_fetch_maps_429_to_a_rate_limit_with_retry_after() -> None:
    source, client = _source_replying(httpx.Response(429, headers={"Retry-After": "120"}))
    with pytest.raises(RateLimitedError) as info:
        await source.fetch()
    assert info.value.retry_after == 120
    assert "120" in str(info.value)
    await client.aclose()


async def test_fetch_without_retry_after_still_reports_a_rate_limit() -> None:
    source, client = _source_replying(httpx.Response(429))
    with pytest.raises(RateLimitedError) as info:
        await source.fetch()
    assert info.value.retry_after is None
    assert "未提供" in str(info.value)
    await client.aclose()


async def test_fetch_maps_other_errors_to_a_source_error() -> None:
    source, client = _source_replying(httpx.Response(503, text="upstream is sad"))
    with pytest.raises(SourceError) as info:
        await source.fetch()
    assert "503" in str(info.value)
    assert "upstream is sad" in str(info.value)
    await client.aclose()


def test_parse_retry_after_accepts_a_delay() -> None:
    assert parse_retry_after("120") == 120.0
    assert parse_retry_after(" 90 ") == 90.0


def test_parse_retry_after_accepts_an_http_date() -> None:
    header = email.utils.formatdate(time.time() + 60, usegmt=True)
    seconds = parse_retry_after(header)
    assert seconds is not None
    assert 50 <= seconds <= 60


def test_parse_retry_after_ignores_junk() -> None:
    assert parse_retry_after(None) is None
    assert parse_retry_after("") is None
    assert parse_retry_after("soon") is None
