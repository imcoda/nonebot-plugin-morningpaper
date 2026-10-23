import asyncio
import importlib.util
import os
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from xml.sax.saxutils import escape

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nonebot_plugin_morningpaper"))

from news import (
    NewsConfig, NewsItem, SourceConfig, SourceResult, fetch_source,
    apply_env_overrides, collect_sources, format_digest, load_config,
    parse_feed, parse_zjol, select_news,
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
        cfg = load_config(ROOT / "nonebot_plugin_morningpaper" / "morning_news.yaml")
        self.assertEqual(cfg.schedule.time, "08:00")
        self.assertEqual(cfg.schedule.timezone, "Asia/Shanghai")
        self.assertEqual(sum(s.count for s in cfg.sources), 13)
        self.assertEqual(len(cfg.sources), 10)
        self.assertEqual(cfg.output.max_items, 12)
        self.assertFalse(cfg.output.ai_enabled)
        self.assertFalse(cfg.output.show_category)
        self.assertEqual(cfg.output.title, "今日早报")
        self.assertEqual(cfg.sources[0].id, "zhejiang")

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
        full = format_digest(selected, config(a, output={"show_category": True}), NOW)
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

    def test_env_overrides_are_validated_and_do_not_mutate_yaml(self):
        cfg = config(SourceConfig(id="zhejiang", name="Zhejiang", category="Opinion", count=1, url="https://example.com/rss"))
        modified = apply_env_overrides(cfg, {
            "MORNINGPAPER_ZHEJIANG_ENABLED": "false",
            "morningpaper_zhejiang_count": "3",
            "morningpaper_max_items": "20",
        })
        self.assertFalse(modified.sources[0].enabled)
        self.assertEqual(modified.sources[0].count, 3)
        self.assertEqual(modified.output.max_items, 20)
        self.assertTrue(cfg.sources[0].enabled)
        self.assertEqual(cfg.sources[0].count, 1)
        for key, value in [("COUNT", "-1"), ("COUNT", "abc"), ("ENABLED", "maybe")]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                apply_env_overrides(cfg, {f"MORNINGPAPER_ZHEJIANG_{key}": value})

    def test_duplicate_ids_rejected_and_legacy_source_without_id_allowed(self):
        a = source()
        self.assertIsNone(apply_env_overrides(config(a), {}).sources[0].id)
        duplicate = SourceConfig(id="same", name="Same", category="Same", count=1, url="https://example.com/rss")
        with self.assertRaises(ValueError):
            config(duplicate, duplicate)

    def test_lunar_heading_at_timezone_boundary_and_leap_month(self):
        cfg = config()
        new_year = datetime(2026, 2, 16, 16, 0, tzinfo=timezone.utc)
        heading = format_digest([], cfg, new_year).splitlines()[0]
        self.assertEqual(heading, "今日早报 农历正月初一")
        before = format_digest([], cfg, new_year - timedelta(seconds=1)).splitlines()[0]
        self.assertNotEqual(before, heading)
        leap = format_digest([], cfg, datetime(2025, 7, 25, 0, tzinfo=timezone.utc))
        self.assertIn("农历闰六月初一", leap)
        solar = format_digest([], config(output={"show_lunar": False}), new_year)
        self.assertTrue(solar.startswith("今日早报 2026-02-17"))

    def test_default_output_has_no_category_headings(self):
        cfg = config()
        item = NewsItem("Article", "https://example.com/article", NOW)
        message = format_digest(select_news([SourceResult(cfg.sources[0], [item])], cfg), cfg, NOW)
        self.assertNotIn("【", message)
        self.assertNotIn("☀", message)

    def test_zhejiang_official_column_dates_and_links(self):
        cfg = config(SourceConfig(id="zhejiang", type="zjol", name="Zhejiang", category="Opinion", count=1, url="https://zjnews.zjol.com.cn/zjxc/"))
        document = '''<html><body><a href="/navigation">Not an article</a>
        <ul class="listUl">
          <li class="listLi"><span class="listSpan">2026年10月23日07时</span>
            <a href="//zjnews.zjol.com.cn/zjxc/new.shtml">浙江宣传 | 新文章</a></li>
          <li class="listLi"><span class="listSpan">2026年10月20日07时</span>
            <a href="/old.shtml">Old article</a></li>
          <li class="listLi"><span class="listSpan">Invalid</span>
            <a href="/undated.shtml">Undated article</a></li>
        </ul></body></html>'''.encode()
        result = parse_zjol(document, cfg.sources[0], cfg, NOW)
        self.assertEqual([item.title for item in result.items], ["浙江宣传 | 新文章"])
        self.assertEqual(result.items[0].url, "https://zjnews.zjol.com.cn/zjxc/new.shtml")
        self.assertEqual(result.items[0].published, NOW - timedelta(hours=1))
        self.assertEqual(result.skipped_dates, 2)
        with self.assertRaises(ValueError):
            parse_zjol(b"<html>Access denied</html>", cfg.sources[0], cfg, NOW)


class FetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_and_zero_count_sources_make_no_requests(self):
        cfg = config(source("Disabled"), source("Zero"), source("Active"))
        cfg.sources[0].enabled = False
        cfg.sources[1].count = 0
        with patch("news.fetch_source", new_callable=AsyncMock) as fetch:
            fetch.return_value = SourceResult(cfg.sources[2])
            results = await collect_sources(cfg, NOW)
        self.assertEqual(fetch.await_count, 1)
        self.assertEqual(fetch.await_args.args[1].name, "Active")
        self.assertEqual(len(results), 1)

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


@unittest.skipUnless(importlib.util.find_spec("nonebot"), "NoneBot is required for plugin load tests")
class PluginLoadTests(unittest.TestCase):
    def check_load(self, env_text="", expected_groups=None, expected_max=12, disabled=False):
        script = '''
import nonebot
from nonebot.adapters.onebot.v11 import Adapter
from unittest.mock import AsyncMock, patch
nonebot.init(_env_file=".env.prod", driver="~fastapi")
with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as request:
    plugin = nonebot.load_plugin("nonebot_plugin_morningpaper")
    assert plugin is not None, "plugin failed to load"
    request.assert_not_called()
module = plugin.module
assert module.GROUPS == EXPECTED_GROUPS
assert module.CONFIG.output.max_items == EXPECTED_MAX
assert plugin.metadata.config is module.Config
assert plugin.metadata.get_supported_adapters() == {Adapter}
assert module.config_path.name == "morning_news.yaml"
assert module.config_path.is_file()
if DISABLED:
    assert not module.CONFIG.sources[0].enabled
    assert module.CONFIG.sources[0].count == 0
job = module.scheduler.get_job("morningpaper")
assert str(job.trigger.timezone) == "Asia/Shanghai"
print("PLUGIN_LOAD_OK")
'''
        script = (
            f"EXPECTED_GROUPS={expected_groups or []!r}\n"
            f"EXPECTED_MAX={expected_max!r}\nDISABLED={disabled!r}\n" + script
        )
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith("MORNINGPAPER_")}
        env["PYTHONPATH"] = str(ROOT)
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, ".env.prod").write_text(env_text, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-c", script], cwd=folder, env=env,
                text=True, capture_output=True, timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PLUGIN_LOAD_OK", result.stdout)

    def test_loads_without_groups_or_external_yaml_and_does_not_fetch(self):
        self.check_load()

    def test_real_dotenv_parses_groups_and_source_overrides(self):
        self.check_load(
            "MORNINGPAPER_GROUPS=[123456789,123456789]\n"
            "MORNINGPAPER_MAX_ITEMS=5\n"
            "MORNINGPAPER_ZHEJIANG_ENABLED=false\n"
            "MORNINGPAPER_ZHEJIANG_COUNT=0\n",
            expected_groups=[123456789], expected_max=5, disabled=True,
        )


if __name__ == "__main__":
    unittest.main()
