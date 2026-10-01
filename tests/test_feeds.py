import asyncio

import pytest

from comicsbot.feeds import FeedEntry, FeedError, ensure_public_url, legacy_last_id, new_entries, parse_feed

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Strip</title>
<item><title>Older</title><link>https://example.com/1</link><pubDate>Mon, 01 Jan 2024 10:00:00 GMT</pubDate></item>
<item><title>Newest</title><link>https://example.com/3</link><pubDate>Wed, 03 Jan 2024 10:00:00 GMT</pubDate></item>
<item><title>Middle</title><link>https://example.com/2</link><pubDate>Tue, 02 Jan 2024 10:00:00 GMT</pubDate></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Strip</title>
<entry><title>Atom strip</title><id>urn:1</id><link href="https://example.com/a"/>
<updated>2024-01-01T00:00:00Z</updated></entry></feed>"""


def test_parse_rss_sorts_newest_first():
    entries = parse_feed(RSS)
    assert [e.title for e in entries] == ["Newest", "Middle", "Older"]
    assert entries[0] == FeedEntry("https://example.com/3", "Newest", "https://example.com/3")
    assert entries[0].text == "Newest - https://example.com/3"


def test_parse_atom():
    assert parse_feed(ATOM) == [FeedEntry("https://example.com/a", "Atom strip", "https://example.com/a")]


def test_parse_garbage_raises():
    with pytest.raises(FeedError):
        parse_feed(b"<html><body>not a feed</body></html>")


def _entries(*ids):
    return [FeedEntry(i, i, i) for i in ids]


def test_new_entries():
    entries = _entries("c", "b", "a")  # newest first
    assert new_entries(entries, "c") == []
    assert new_entries(entries, "a") == _entries("b", "c")  # oldest first
    assert new_entries(entries, None) == _entries("c")  # new subscription: latest only
    assert new_entries(entries, "gone") == _entries("c")  # unknown: latest only, no flood
    assert new_entries(entries, "a", limit=1) == _entries("c")
    assert new_entries([], None) == []


def test_legacy_last_id():
    assert legacy_last_id("Some - title - https://x.com/1") == "https://x.com/1"
    assert legacy_last_id("https://x.com/1") == "https://x.com/1"


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/feed",
        "file:///etc/passwd",
        "http://127.0.0.1/feed",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data",
        "not a url",
    ],
)
def test_ensure_public_url_rejects(url):
    with pytest.raises(FeedError):
        asyncio.run(ensure_public_url(url))


def test_ensure_public_url_allows_public_ip_and_private_when_enabled():
    asyncio.run(ensure_public_url("https://1.1.1.1/feed"))
    asyncio.run(ensure_public_url("http://192.168.1.1/feed", allow_private=True))
