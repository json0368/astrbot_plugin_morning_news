# astrbot_plugin_morning_news

AstrBot 的飞书每日晨报插件，支持定时推送晨报、飞书卡片展示、纯文本回退，以及订阅、预览、新闻速览、天气查询和管理员立即群发。

## 功能特性

- 每天按设定时间自动向已订阅会话推送晨报
- 优先使用飞书卡片展示，发送失败时自动使用纯文本回退
- 支持按会话保存天气城市
- 支持新闻速览、天气查询、状态查询和即时预览
- 支持管理员立即向所有订阅会话发送晨报

## 安装

将本仓库压缩成 `.zip` 压缩包，然后在 AstrBot WebUI 中安装插件。

## 配置

可在 WebUI 中配置以下常用项目：

- `enabled`: 是否启用插件
- `report_title`: 晨报标题
- `delivery_time`: 每日推送时间，默认 `08:00`
- `delivery_timezone`: 推送时区，默认 `Asia/Shanghai`
- `default_city`: 默认天气城市，默认 `北京`
- `feishu_card_enabled`: 是否优先尝试飞书卡片，默认 `true`
- `weather_provider`: 天气源，支持 `uapi`、`open-meteo`、`custom`
- `custom_weather_api_url`: 自定义天气 API 地址模板
- `custom_weather_response_path`: 自定义天气 API JSON 字段路径
- `custom_weather_headers`: 自定义天气 API 请求头 JSON
- `custom_weather_allowed_domains`: 启用 custom 天气源时必填，例如 weather.example.com 或 api.example.com
- `include_weather`: 是否包含天气
- `include_quote`: 是否包含每日一句
- `include_poem`: 是否包含诗词
- `rss_urls`: RSS 源列表
- `news_limit`: 晨报新闻条数
- `http_timeout_seconds`: HTTP 请求超时
- `http_proxy`: 可选 HTTP 代理
- `bot_display_name`: Bot 展示名称
- `footer`: 晨报页脚文案

## 命令

- `/daily help`
  查看插件帮助。

- `/daily subscribe`
  订阅当前会话的每日晨报。

- `/daily subscribe <city>`
  订阅当前会话，并直接设置天气城市。

- `/daily unsubscribe`
  取消当前会话的晨报订阅。

- `/daily city <city>`
  更新当前会话的天气城市。

- `/daily preview`
  立即预览晨报。

- `/daily preview <city>`
  以指定城市临时预览晨报，不影响当前会话已保存的城市设置。

- `/daily news`
  查看新闻速览。

- `/daily weather`
  查询当前会话或默认城市的天气。

- `/daily weather <city>`
  查询指定城市天气。

- `/daily status`
  查看插件状态、订阅数量和当前会话订阅情况。

- `/daily sendnow`
  立即向所有已订阅会话发送晨报，仅管理员可用。

## 使用说明

### 订阅与推送

插件使用 `event.unified_msg_origin` 作为订阅会话标识。订阅后，插件会在设定时间向当前会话自动推送晨报。

### 城市规则

天气城市优先级如下：

1. 命令中显式传入的城市
2. 当前会话已保存的城市
3. 插件配置中的 `default_city`

### 展示方式

晨报、新闻速览、天气查询和状态查询会优先尝试发送飞书卡片；如果平台拒收或内部发送失败，插件会自动使用纯文本回退，保证消息仍能送达。

### 内容来源

晨报内容可包含天气、新闻、每日一句和诗词。某个外部接口失败时，只会跳过对应内容块，不会让整份晨报直接失效。

- 天气：默认 UAPI，失败时回退到 Open-Meteo，也支持自定义天气 API
- 每日一句：Hitokoto
- 诗词：今日诗词（可选）
- 新闻：用户配置的 RSS 源

## 目录结构

- `main.py`: 插件主入口与命令定义
- `card_rendering_mixin.py`: 飞书卡片渲染
- `feishu_delivery_mixin.py`: 飞书卡片发送与文本回退
- `news_mixin.py`: 新闻拉取与解析
- `weather_mixin.py`: 天气拉取与转换
- `scheduler_mixin.py`: 定时调度与自动推送

## 适用说明

这个插件面向飞书环境下的 AstrBot 部署，适合需要每日定时报送天气与新闻摘要的群聊或私聊场景。

## 致谢

非常感谢以下开源项目提供的灵感和代码参考：
- [AstrBot](https://github.com/AstrBotDevs/AstrBot/tree/master)
- [astrbot_plugin_daliy](https://github.com/zzzwannasleep/astrbot_plugin_daliy)
