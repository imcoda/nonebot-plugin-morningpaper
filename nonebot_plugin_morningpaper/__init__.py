from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from nonebot import get_bot, get_driver, get_plugin_config, on_regex, require
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from pydantic import BaseModel, Field

from .news import apply_env_overrides, collect_sources, format_digest, load_config, select_news

scheduler = require("nonebot_plugin_apscheduler").scheduler


class Config(BaseModel):
    morningpaper_groups: list[int] = Field(default_factory=list)
    morningpaper_config: Optional[Path] = None
    morningpaper_time: Optional[str] = None
    morningpaper_max_items: Optional[int] = Field(None, ge=1, le=100)


__plugin_meta__ = PluginMetadata(
    name="早报",
    description="定时推送 RSS 新闻和浙江宣传，支持农历标题及来源开关。",
    usage="早报：立即查看早报\n发送早报：超级用户立即推送到当前群或配置群",
    type="application",
    homepage="https://github.com/imcoda/nonebot-plugin-morningpaper",
    config=Config,
    supported_adapters={"~onebot.v11"},
)

driver_config = get_driver().config
plugin_config = get_plugin_config(Config)
GROUPS = list(dict.fromkeys(plugin_config.morningpaper_groups))

config_path = plugin_config.morningpaper_config
if config_path is None:
    config_path = Path.cwd() / "morning_news.yaml"
    if not config_path.is_file():
        config_path = Path(__file__).with_name("morning_news.yaml")
env_values = driver_config.model_dump() if hasattr(driver_config, "model_dump") else driver_config.dict()
CONFIG = apply_env_overrides(load_config(Path(config_path)), env_values)
_build_lock = asyncio.Lock()


async def build_digest() -> Message:
    async with _build_lock:
        results = await collect_sources(CONFIG)
        selected = select_news(results, CONFIG)
        for result in results:
            if result.error:
                logger.warning(f"早报源 {result.source.name} 获取失败：{result.error}")
            logger.debug(
                f"早报源 {result.source.name}：{len(result.items)} 条符合时间与关键词条件，"
                f"{result.skipped_dates} 条因日期无效、过期或在未来跳过"
            )
        return Message(MessageSegment.text(format_digest(selected, CONFIG)))


async def send_to_configured_groups(bot: Bot | None = None) -> None:
    if not GROUPS:
        logger.warning("MORNINGPAPER_GROUPS 未配置，跳过早报推送")
        return
    bot = bot or get_bot()
    message = await build_digest()
    for group_id in GROUPS:
        try:
            await bot.send_group_msg(group_id=group_id, message=message)
        except Exception as exc:
            logger.warning(f"发送早报到群 {group_id} 失败：{type(exc).__name__}: {exc}")
        await asyncio.sleep(1.5)


morningpaper_matcher = on_regex(r"^早报$", priority=20)
push_matcher = on_regex(r"^发送早报$", priority=20, permission=SUPERUSER)


@morningpaper_matcher.handle()
async def _() -> None:
    await morningpaper_matcher.finish(await build_digest())


@push_matcher.handle()
async def _(bot: Bot, event: MessageEvent) -> None:
    if isinstance(event, GroupMessageEvent):
        await bot.send_group_msg(group_id=event.group_id, message=await build_digest())
    else:
        await send_to_configured_groups(bot)
    await push_matcher.finish("早报已发送")


hour, minute = (int(value) for value in CONFIG.schedule.time.split(":"))
scheduler.add_job(
    send_to_configured_groups,
    "cron",
    hour=hour,
    minute=minute,
    timezone=CONFIG.schedule.timezone,
    id="morningpaper",
    replace_existing=True,
    max_instances=1,
    coalesce=True,
    misfire_grace_time=300,
)
logger.info(
    f"MorningPaper 配置：{config_path}；"
    f"每日 {CONFIG.schedule.time} ({CONFIG.schedule.timezone}) 推送，"
    f"最多 {CONFIG.output.max_items} 条，群：{GROUPS or '未配置'}"
)
