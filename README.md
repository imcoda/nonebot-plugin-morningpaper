# nonebot-plugin-morningpaper

NoneBot2 群聊早报插件，支持 RSS 新闻、浙江宣传、农历标题和定时推送。适用于 OneBot V11。

## 使用方式

私聊或群聊发送以下文字，无需命令前缀：

| 消息 | 功能 | 权限 |
| --- | --- | --- |
| `早报` | 查看当前早报 | 所有人 |
| `发送早报` | 群聊推送到当前群，私聊推送到配置群 | 超级用户 |

默认每天北京时间 08:00 推送最近 24 小时的新闻，最多 12 条。显示农历、原标题、来源和链接，不显示分类小标题，不调用 AI。

## 配置项

在 NoneBot 的 `.env.prod` 中添加，需要什么加什么：

```dotenv
# 定时推送的 QQ 群；不填时仍可手动查看早报
MORNINGPAPER_GROUPS=[123456789]

# 可选：自定义 YAML 路径，不填使用内置配置
# MORNINGPAPER_CONFIG="./morning_news.yaml"

# 整份早报最多条数
MORNINGPAPER_MAX_ITEMS=12

# 浙江宣传：是否启用、最多条数
MORNINGPAPER_ZHEJIANG_ENABLED=true
MORNINGPAPER_ZHEJIANG_COUNT=1

# 国内要闻：是否启用、最多条数
MORNINGPAPER_DOMESTIC_ENABLED=true
MORNINGPAPER_DOMESTIC_COUNT=2
```

| 配置项 | 类型 | 默认值 |
| --- | --- | --- |
| `MORNINGPAPER_GROUPS` | `list[int]` | `[]` |
| `MORNINGPAPER_CONFIG` | 文件路径 | 优先使用运行目录同名 YAML，否则使用内置配置 |
| `MORNINGPAPER_MAX_ITEMS` | `int` | YAML 中的 `output.max_items`，内置为 `12` |

每个来源均可通过 `MORNINGPAPER_<ID大写>_ENABLED` 和 `MORNINGPAPER_<ID大写>_COUNT` 控制。环境配置优先于 YAML；设为 `false` 或条数 `0` 均停止获取该来源。

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

全部来源默认启用，按配置顺序分配配额，整份早报仍受总条数上限限制。自定义来源、推送时间、时区和显示项可参照 [morning_news.yaml](https://github.com/imcoda/nonebot-plugin-morningpaper/blob/main/nonebot_plugin_morningpaper/morning_news.yaml)，修改配置后重启机器人。

相同链接或标题会去重，新闻不足时不使用旧闻补位。浙江宣传读取官方转载栏目，更新可能晚于公众号；各订阅可能停更或失效，可自行关闭或更换。

## 安装

使用 nb-cli 安装（推荐），自动添加插件加载项：

```bash
nb plugin install nonebot-plugin-morningpaper
```

或使用 pip 安装：

```bash
pip install --upgrade nonebot-plugin-morningpaper
```

pip 安装后，在 `pyproject.toml` 已有的 `[tool.nonebot]` 插件列表中加入：

```toml
[tool.nonebot]
plugins = ["nonebot_plugin_morningpaper"]
```

从 GitHub 安装开发版：

```bash
pip install --upgrade git+https://github.com/imcoda/nonebot-plugin-morningpaper.git
```
