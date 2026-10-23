# nonebot-plugin-morningpaper

NoneBot2 群聊 RSS 早报。默认北京时间 08:00 推送最近 24 小时的国内、国际、科技、医学和科学新闻，最多 12 条，显示原标题、来源和原文链接，不调用 AI 或翻译服务。

发送 `早报` 查看，超级用户发送 `发送早报` 立即推送。

## 安装与配置

```bash
pip install git+https://github.com/PeachCoda/nonebot-plugin-morningpaper.git
```

在 `pyproject.toml` 的插件列表中加入 `nonebot_plugin_morningpaper`，将仓库中的 `morning_news.yaml` 放到机器人目录，在 `.env.prod` 配置：

```dotenv
MORNINGPAPER_GROUPS=[142979214]
MORNINGPAPER_CONFIG="/root/LimBoT2/morning_news.yaml"
```

编辑 YAML 可调整推送时间、时区、回溯小时数、超时、重试次数、排除关键词、显示项和各 RSS 来源及配额，修改后重启机器人。`retry_times: 2` 表示首次失败后最多重试两次。最多并发抓取 3 个源。

每个来源按发布时间倒序选择，按 YAML 顺序分配配额，全局去重并限制总数。缺少有效日期、过期、未来日期的条目不发送；只有更新时间的订阅使用更新时间。源失败或新闻不足时留空，不用旧新闻或其他类别补位。失败原因记录在日志中。

只支持 RSS/Atom，不保证示例订阅持续更新或可访问。未指定配置时优先读取机器人目录的 `morning_news.yaml`，否则使用内置配置；显式指定的文件丢失或配置无效会报错。

旧的 `MORNINGPAPER_HOUR`、`MINUTE`、`TOP_N`、`SOURCES`、`TRANSLATE_AI` 配置已由 YAML 取代。
