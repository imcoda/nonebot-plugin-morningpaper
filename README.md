# nonebot-plugin-morningpaper

NoneBot2 群聊早报。默认北京时间 08:00 推送最近 24 小时的新闻和浙江宣传文章，最多 12 条。标题显示农历，正文不显示分类小标题，保留原标题、来源和链接，不调用 AI 或翻译服务。

发送 `早报` 查看，超级用户发送 `发送早报` 立即推送。

## 安装与配置

```bash
pip install git+https://github.com/PeachCoda/nonebot-plugin-morningpaper.git
```

在 `pyproject.toml` 的插件列表中加入 `nonebot_plugin_morningpaper`，将仓库中的 `morning_news.yaml` 放到机器人目录，在 `.env.prod` 配置：

```dotenv
MORNINGPAPER_GROUPS=[142979214]
MORNINGPAPER_CONFIG="/root/LimBoT2/morning_news.yaml"
MORNINGPAPER_MAX_ITEMS=12
MORNINGPAPER_ZHEJIANG_ENABLED=true
MORNINGPAPER_ZHEJIANG_COUNT=1
MORNINGPAPER_DOMESTIC_ENABLED=true
MORNINGPAPER_DOMESTIC_COUNT=2
```

全部来源保留在 YAML 中，每个来源的 `id` 对应环境变量 `MORNINGPAPER_<ID大写>_ENABLED` 和 `MORNINGPAPER_<ID大写>_COUNT`，环境配置优先于 YAML。`false` 或条数 `0` 均不发起该来源的请求。修改后重启机器人。

| ID | 来源 | 默认条数 |
| --- | --- | --- |
| `zhejiang` | 浙江宣传（浙江在线转载） | 1 |
| `domestic` | 中新网时政 | 2 |
| `society` | 中新网社会 | 1 |
| `finance` | 中新网财经 | 1 |
| `world` | 中新网国际 | 3 |
| `people_world` | 人民网国际 | 1 |
| `ithome` | IT之家 | 1 |
| `kr36` | 36氪 | 1 |
| `health` | 中新网健康 | 1 |
| `science` | 科学网 | 1 |

YAML 还可调整时间、时区、回溯小时数、超时、重试、关键词和显示项。`show_category: false` 隐藏分类，`show_lunar: true` 显示农历。`retry_times: 2` 表示首次失败后最多重试两次，最多并发抓取 3 个源。

每个来源按发布时间倒序选择，按 YAML 顺序分配配额，全局去重并限制总数。缺少有效日期、过期、未来日期的条目不发送；只有更新时间的订阅使用更新时间。源失败或新闻不足时留空，不用旧新闻或其他类别补位。失败原因记录在日志中。

新闻源使用 RSS/Atom；浙江宣传通过 `type: zjol` 读取浙江在线官方转载栏目，使用列表标注的北京时间（小时精度），可能晚于公众号更新。默认不取正文、不需要微信登录。不保证各订阅持续更新或可访问；人民网国际、中新网健康和 36氪可能停更或失效，可在环境中关闭。

未指定配置时优先读取机器人目录的 `morning_news.yaml`，否则使用内置配置；显式指定的文件丢失或配置无效会报错。

旧的 `MORNINGPAPER_HOUR`、`MINUTE`、`TOP_N`、`SOURCES`、`TRANSLATE_AI` 配置已由 YAML 取代。
