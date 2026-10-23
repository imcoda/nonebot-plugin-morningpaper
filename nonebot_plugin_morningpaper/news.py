from __future__ import annotations

import asyncio
import calendar
import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import feedparser
import httpx
import yaml

try:
    from pydantic.v1 import BaseModel, Field, validator
except ImportError:
    from pydantic import BaseModel, Field, validator


class ConfigModel(BaseModel):
    class Config:
        extra = "forbid"


class ScheduleConfig(ConfigModel):
    time: str = "08:00"
    timezone: str = "Asia/Shanghai"

    @validator("time")
    def valid_time(cls, value: str) -> str:
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("schedule.time must be HH:MM")
        return value

    @validator("timezone")
    def valid_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value


class FetchConfig(ConfigModel):
    lookback_hours: float = Field(24, gt=0)
    timeout_seconds: float = Field(10, gt=0)
    retry_times: int = Field(2, ge=0, le=5)
    user_agent: str = "Mozilla/5.0 (compatible; MorningNewsBot/1.0)"


class FilterConfig(ConfigModel):
    sort_by: Literal["published_desc"] = "published_desc"
    deduplicate_url: bool = True
    deduplicate_title: bool = True
    exclude_keywords: list[str] = Field(default_factory=list)


class OutputConfig(ConfigModel):
    title: str = "今日早报"
    max_items: int = Field(12, ge=1, le=100)
    show_category: bool = True
    show_source: bool = True
    show_link: bool = True
    use_rss_description: Literal[False] = False
    ai_enabled: Literal[False] = False
    allow_old_news_to_fill: Literal[False] = False


class SourceConfig(ConfigModel):
    name: str = Field(..., min_length=1)
    category: str = Field(..., min_length=1)
    count: int = Field(..., ge=1, le=100)
    url: str

    @validator("url")
    def valid_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("source.url must be an HTTP(S) URL")
        return value


class NewsConfig(ConfigModel):
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    fetch: FetchConfig = Field(default_factory=FetchConfig)
    filter: FilterConfig = Field(default_factory=FilterConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    sources: list[SourceConfig] = Field(..., min_items=1)


def load_config(path: Path) -> NewsConfig:
    with path.open(encoding="utf-8-sig") as file:
        data = yaml.safe_load(file)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return NewsConfig.parse_obj(data)


@dataclass(frozen=True)
class NewsItem:
    title: str
    url: str
    published: datetime


@dataclass
class SourceResult:
    source: SourceConfig
    items: list[NewsItem] = field(default_factory=list)
    error: str | None = None
    skipped_dates: int = 0


@dataclass(frozen=True)
class SelectedNews:
    source: SourceConfig
    item: NewsItem


def _clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    return " ".join(html.unescape(value).split())


def parse_feed(
    content: bytes, source: SourceConfig, config: NewsConfig, now: datetime
) -> SourceResult:
    feed = feedparser.parse(content)
    if not feed.version or (feed.bozo and not feed.entries):
        raise ValueError("response is not a readable RSS/Atom feed")
    result = SourceResult(source)
    cutoff = now - timedelta(hours=config.fetch.lookback_hours)
    for entry in feed.entries:
        # An updated date must not make an already old publication appear new.
        date = entry.get("published_parsed") if entry.get("published") else entry.get("updated_parsed")
        try:
            published = datetime.fromtimestamp(calendar.timegm(date), timezone.utc) if date else None
        except (TypeError, ValueError, OverflowError):
            published = None
        if published is None or not cutoff <= published <= now:
            result.skipped_dates += 1
            continue
        title = _clean_text(str(entry.get("title", "")))
        url = urljoin(source.url, str(entry.get("link", "")).strip()) if entry.get("link") else ""
        if not title or urlsplit(url).scheme not in {"http", "https"}:
            continue
        if any(keyword in title for keyword in config.filter.exclude_keywords if keyword):
            continue
        result.items.append(NewsItem(title, url, published))
    result.items.sort(key=lambda item: item.published, reverse=True)
    return result


async def fetch_source(
    client: httpx.AsyncClient,
    source: SourceConfig,
    config: NewsConfig,
    now: datetime,
    semaphore: asyncio.Semaphore,
) -> SourceResult:
    for attempt in range(config.fetch.retry_times + 1):
        try:
            async with semaphore:
                response = await client.get(source.url, timeout=config.fetch.timeout_seconds)
                response.raise_for_status()
                return await asyncio.to_thread(parse_feed, response.content, source, config, now)
        except (httpx.HTTPError, ValueError) as exc:
            permanent = (
                isinstance(exc, httpx.HTTPStatusError)
                and 400 <= exc.response.status_code < 500
                and exc.response.status_code not in {408, 429}
            )
            if permanent or attempt >= config.fetch.retry_times:
                return SourceResult(source, error=f"{type(exc).__name__}: {exc}")
            await asyncio.sleep(min(2 ** attempt, 4))
    raise AssertionError("unreachable")


async def collect_sources(config: NewsConfig, now: datetime | None = None) -> list[SourceResult]:
    now = now or datetime.now(timezone.utc)
    semaphore = asyncio.Semaphore(3)
    async with httpx.AsyncClient(
        headers={"User-Agent": config.fetch.user_agent},
        follow_redirects=True,
        trust_env=False,
    ) as client:
        return await asyncio.gather(
            *(fetch_source(client, source, config, now, semaphore) for source in config.sources)
        )


def select_news(results: list[SourceResult], config: NewsConfig) -> list[SelectedNews]:
    selected: list[SelectedNews] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    # Apply each source's quota in YAML order; duplicates do not consume quota.
    for result in results:
        count = 0
        for item in sorted(result.items, key=lambda item: item.published, reverse=True):
            if len(selected) >= config.output.max_items or count >= result.source.count:
                break
            if config.filter.deduplicate_url and item.url in seen_urls:
                continue
            if config.filter.deduplicate_title and item.title in seen_titles:
                continue
            selected.append(SelectedNews(result.source, item))
            seen_urls.add(item.url)
            seen_titles.add(item.title)
            count += 1
    return selected


def format_digest(
    selected: list[SelectedNews], config: NewsConfig, now: datetime | None = None
) -> str:
    now = now or datetime.now(timezone.utc)
    date = now.astimezone(ZoneInfo(config.schedule.timezone)).strftime("%Y-%m-%d")
    lines = [f"{config.output.title} {date}"]
    last_category = None
    for index, news in enumerate(selected, 1):
        if config.output.show_category and news.source.category != last_category:
            lines.extend(["", f"【{news.source.category}】"])
            last_category = news.source.category
        elif not config.output.show_category:
            lines.append("")
        lines.append(f"{index}. {news.item.title}")
        if config.output.show_source:
            lines.append(f"来源：{news.source.name}")
        if config.output.show_link:
            lines.append(news.item.url)
    if not selected:
        lines.extend(["", "最近时段暂无可用新闻，请稍后再试。"])
    return "\n".join(lines)
