import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch
from xml.sax.saxutils import escape

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nonebot_plugin_morningpaper"))

from news import (
    NewsConfig, NewsItem, SourceConfig, SourceResult, fetch_source,
    format_digest, load_config, parse_feed, select_news,
)

NOW = datetime(2026, 10, 23, 0, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def source(name="Source", count=2):
    return SourceConfig(name=name, category=name, count=count, url="https://example.com/rss")


def config(*sources, **kwargs):
    return NewsConfig(sources=list(sources) or [source()], **kwargs)


def rss(*items):
    entries = []
    for title, url, date in items:
        pubdate = f"<pubDate>{format_datetime(date)}</pubDate>" if date else ""
        entries.append(
            f"<item><title>{escape(title)}</title><link>{escape(url)}</link>"
            f"{pubdate}<description>Unused description</description></item>"
        )
    return ("<rss version='2.0'><channel><title>Feed</title>" + "".join(entries) + "</channel></rss>").encode()


class FeedTests(unittest.TestCase):
    def test_supplied_yaml_quotas_and_schedule(self):
        cfg = load_config(ROOT / "morning_news.yaml")
        self.assertEqual(cfg.schedule.time, "08:00")
        self.assertEqual(cfg.schedule.timezone, "Asia/Shanghai")
        self.assertEqual(sum(s.count for s in cfg.sources), 12)
        self.assertEqual(len(cfg.sources), 9)
        self.assertEqual(cfg.output.max_items, 12)
        self.assertFalse(cfg.output.ai_enabled)

    def test_date_window_and_sorting(self):
        cfg = config()
        parsed = parse_feed(rss(
            ("Old", "https://example.com/old", NOW - timedelta(hours=25)),
            ("Boundary", "https://example.com/boundary", NOW - timedelta(hours=24)),
            ("Future", "https://example.com/future", NOW + timedelta(seconds=1)),
            ("No date", "https://example.com/nodate", None),
            ("Latest", "https://example.com/latest", NOW),
        ), cfg.sources[0], cfg, NOW)
        self.assertEqual([n.title for n in parsed.items], ["Latest", "Boundary"])
        self.assertEqual(parsed.skipped_dates, 3)

    def test_atom_published_precedes_updated_and_timezone(self):
        xml = b'''<feed xmlns="http://www.w3.org/2005/Atom">
        <entry><title>Old publication</title><link href="https://example.com/old"/>
          <published>2026-10-20T08:00:00+08:00</published>
          <updated>2026-10-23T07:59:00+08:00</updated></entry>
        <entry><title>New</title><link href="/new"/>
          <published>2026-10-23T07:59:00+08:00</published></entry>
        <entry><title>Updated only</title><link href="https://example.com/update"/>
          <updated>2026-10-23T07:58:00+08:00</updated></entry></feed>'''
        cfg = config()
        parsed = parse_feed(xml, cfg.sources[0], cfg, NOW)
        self.assertEqual([n.title for n in parsed.items], ["New", "Updated only"])
        self.assertEqual(parsed.items[0].url, "https://example.com/new")
        self.assertEqual(parsed.items[0].published, NOW - timedelta(minutes=1))

    def test_exclude_keywords_without_reading_description(self):
        cfg = config(filter={"exclude_keywords": ["广告", "优惠券"]})
        parsed = parse_feed(rss(
            ("限量优惠券", "https://example.com/ad", NOW),
            ("Valid title", "https://example.com/new", NOW),
        ), cfg.sources[0], cfg, NOW)
        self.assertEqual([n.title for n in parsed.items], ["Valid title"])

    def test_html_is_not_a_feed(self):
        cfg = config()
        with self.assertRaises(ValueError):
            parse_feed(b"<html><body>Denied</body></html>", cfg.sources[0], cfg, NOW)

    def test_quotas_dedup_and_no_reallocation(self):
        a, b, c = source("A", 1), source("B", 2), source("C", 2)
        cfg = config(a, b, c)
        def item(title, url, hours=0):
            return NewsItem(title, url, NOW - timedelta(hours=hours))
        results = [
            SourceResult(a, [item("A older", "https://example.com/older", 1), item("A", "https://example.com/a")]),
            SourceResult(b, [item("Different title", "https://example.com/a"), item("A", "https://example.com/duplicate"), item("B", "https://example.com/b")]),
            SourceResult(c, error="offline"),
        ]
        selected = select_news(results, cfg)
        self.assertEqual([n.item.title for n in selected], ["A", "B"])
        self.assertEqual([n.source.name for n in selected], ["A", "B"])

    def test_global_cap_and_toggle_deduplication(self):
        a, b = source("A"), source("B")
        cfg = config(a, b, output={"max_items": 3}, filter={"deduplicate_title": False, "deduplicate_url": False})
        item = NewsItem("Repeated", "https://example.com/repeated", NOW)
        selected = select_news([SourceResult(a, [item, item]), SourceResult(b, [item, item])], cfg)
        self.assertEqual(len(selected), 3)

    def test_format_flags_and_no_description(self):
        a = source()
        item = NewsItem("Original", "https://example.com/article", NOW)
        selected = select_news([SourceResult(a, [item])], config(a))
        full = format_digest(selected, config(a), NOW)
        self.assertIn("【Source】", full)
        self.assertIn("来源：Source", full)
        self.assertIn(item.url, full)
        minimal = format_digest(selected, config(a, output={"show_category": False, "show_source": False, "show_link": False}), NOW)
        self.assertNotIn("Source", minimal)
        self.assertNotIn("https://", minimal)
        self.assertIn("1. Original", minimal)

    def test_invalid_configuration_fails_instead_of_ignoring_keys(self):
        for values in [{"schedule": {"time": "25:00"}}, {"output": {"ai_enabled": True}}, {"output": {"allow_old_news_to_fill": True}}, {"fetch": {"retry_times": -1}}, {"typo": 1}]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                config(**values)


class FetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_transient_failure_retries_twice(self):
        calls = 0
        def handle(request):
            nonlocal calls
            calls += 1
            if calls < 3:
                raise httpx.ConnectTimeout("test", request=request)
            return httpx.Response(200, content=rss(("OK", "https://example.com/item", NOW)))
        cfg = config(fetch={"retry_times": 2})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with patch("news.asyncio.sleep", new_callable=AsyncMock):
                result = await fetch_source(client, cfg.sources[0], cfg, NOW, asyncio.Semaphore(1))
        self.assertEqual(calls, 3)
        self.assertEqual(len(result.items), 1)
        self.assertIsNone(result.error)

    async def test_permanent_failure_isolated_without_retry(self):
        calls = 0
        def handle(request):
            nonlocal calls
            calls += 1
            return httpx.Response(404)
        cfg = config()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            result = await fetch_source(client, cfg.sources[0], cfg, NOW, asyncio.Semaphore(1))
        self.assertEqual(calls, 1)
        self.assertEqual(result.items, [])
        self.assertIn("404", result.error)


if __name__ == "__main__":
    unittest.main()
