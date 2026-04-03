from __future__ import annotations

import asyncio
import html
import json
import re
import sys
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register

PLUGIN_DIR = Path(__file__).resolve().parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

try:
    from .card_rendering_mixin import CardRenderingMixin
    from .daily_shared import GEO_CACHE_MAX_SIZE, WEEKDAY_CN
    from .feishu_delivery_mixin import FeishuDeliveryMixin
    from .news_mixin import NewsMixin
    from .rendering_mixin import RenderingMixin
    from .scheduler_mixin import SchedulerMixin
    from .weather_mixin import WeatherMixin
except ImportError:
    from card_rendering_mixin import CardRenderingMixin
    from daily_shared import GEO_CACHE_MAX_SIZE, WEEKDAY_CN
    from feishu_delivery_mixin import FeishuDeliveryMixin
    from news_mixin import NewsMixin
    from rendering_mixin import RenderingMixin
    from scheduler_mixin import SchedulerMixin
    from weather_mixin import WeatherMixin


@register(
    "astrbot_plugin_morning_news",
    "json0368",
    "飞书每日晨报插件",
    "0.2.0",
    "https://github.com/json0368/astrbot_plugin_morning_news",
)
class DailyMorningReportPlugin(
    SchedulerMixin,
    WeatherMixin,
    NewsMixin,
    CardRenderingMixin,
    FeishuDeliveryMixin,
    RenderingMixin,
    Star,
):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._subscriptions: dict[str, dict[str, Any]] = {}
        self._geo_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._news_cache: dict[str, Any] | None = None
        self._state_lock = asyncio.Lock()
        self._news_cache_lock = asyncio.Lock()
        self._news_refresh_task: asyncio.Task | None = None
        self._scheduler_task: asyncio.Task | None = None

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self):
        await self._load_subscriptions()
        await self._maybe_send_startup_catchup()
        self._start_scheduler()

    async def terminate(self):
        await self._stop_scheduler()

    @filter.command_group("daily")
    def daily(self):
        pass

    @daily.command("help")
    async def help(self, event: AstrMessageEvent):
        async for result in self._help_impl(event):
            yield result

    @daily.command("subscribe")
    async def subscribe(self, event: AstrMessageEvent, city: str = ""):
        async for result in self._subscribe_impl(event, city):
            yield result

    async def _subscribe_impl(self, event: AstrMessageEvent, city: str = ""):
        await self._upsert_subscription(event, city.strip())
        effective_city = self._resolve_city(city.strip())
        yield event.plain_result(
            "\n".join(
                [
                    "已订阅每日晨报。",
                    f"会话标识: {event.unified_msg_origin}",
                    f"天气城市: {effective_city or '未设置'}",
                    f"发送时间: {self._delivery_time_text()} ({self._timezone_name()})",
                    f"飞书卡片: {'开启' if self._feishu_card_enabled() else '关闭'}",
                ]
            )
        )

    @daily.command("unsubscribe")
    async def unsubscribe(self, event: AstrMessageEvent):
        async for result in self._unsubscribe_impl(event):
            yield result

    async def _unsubscribe_impl(self, event: AstrMessageEvent):
        removed = await self._remove_subscription(event.unified_msg_origin)
        if removed:
            yield event.plain_result("已取消当前会话的每日晨报订阅。")
        else:
            yield event.plain_result("当前会话未订阅每日晨报。")

    @daily.command("city")
    async def set_city(self, event: AstrMessageEvent, city: str):
        async for result in self._set_city_impl(event, city):
            yield result

    async def _set_city_impl(self, event: AstrMessageEvent, city: str):
        updated = await self._set_subscription_city(event, city.strip())
        if updated is None:
            yield event.plain_result("当前会话还没有订阅，请先执行 `/daily subscribe`。")
            return
        yield event.plain_result(f"当前会话的天气城市已设置为: {city.strip()}")

    @daily.command("preview")
    async def preview(self, event: AstrMessageEvent, city: str = ""):
        async for result in self._preview_impl(event, city):
            yield result

    async def _preview_impl(self, event: AstrMessageEvent, city: str = ""):
        await self._refresh_subscription_transport_if_needed(event)
        resolved_city = city.strip() or await self._city_for_subscription(event.unified_msg_origin)
        payload = await self._build_report_payload(resolved_city)
        result = await self._deliver_payload_to_event(event, payload)
        if result is not None:
            yield result

    @daily.command("news")
    async def news(self, event: AstrMessageEvent):
        async for result in self._news_impl(event):
            yield result

    async def _news_impl(self, event: AstrMessageEvent):
        await self._refresh_subscription_transport_if_needed(event)
        payload = await self._build_news_payload()
        result = await self._deliver_payload_to_event(event, payload)
        if result is not None:
            yield result

    @daily.command("weather")
    async def weather(self, event: AstrMessageEvent, city: str = ""):
        async for result in self._weather_impl(event, city):
            yield result

    async def _weather_impl(self, event: AstrMessageEvent, city: str = ""):
        await self._refresh_subscription_transport_if_needed(event)
        resolved_city = city.strip() or await self._city_for_subscription(event.unified_msg_origin)
        if not resolved_city:
            payload = self._build_weather_payload("", "请提供城市名，或先设置默认城市 / 当前会话城市。")
            result = await self._deliver_payload_to_event(event, payload)
            if result is not None:
                yield result
            return

        try:
            async with self._http_client() as client:
                weather = await self._fetch_weather_summary(client, resolved_city)
        except Exception as exc:
            logger.warning("天气查询失败: city=%s error=%s", resolved_city, exc)
            weather = None

        payload = self._build_weather_payload(
            resolved_city,
            weather or f"{resolved_city}: 暂时无法获取天气信息。",
        )
        result = await self._deliver_payload_to_event(event, payload)
        if result is not None:
            yield result

    @daily.command("status")
    async def status(self, event: AstrMessageEvent):
        async for result in self._status_impl(event):
            yield result

    async def _status_impl(self, event: AstrMessageEvent):
        await self._refresh_subscription_transport_if_needed(event)
        status_data = await self._build_status_data(event)
        payload = self._build_status_payload(status_data)
        result = await self._deliver_payload_to_event(event, payload)
        if result is not None:
            yield result

    @filter.permission_type(filter.PermissionType.ADMIN)
    @daily.command("sendnow")
    async def sendnow(self, event: AstrMessageEvent):
        async for result in self._sendnow_impl(event):
            yield result

    async def _help_impl(self, event: AstrMessageEvent):
        payload = self._build_help_payload()
        result = await self._deliver_payload_to_event(event, payload)
        if result is not None:
            yield result

    def _build_help_payload(self) -> dict[str, Any]:
        return self._compose_payload(self._daily_help_text())

    def _daily_help_text(self) -> str:
        return "\n".join(
            [
                "每日晨报命令",
                "",
                "/daily help - 查看本帮助",
                "/daily subscribe [city] - 订阅当前会话晨报，可选传入天气城市",
                "/daily unsubscribe - 取消当前会话订阅",
                "/daily city <city> - 设置当前会话的天气城市，需要先订阅",
                "/daily preview [city] - 立即预览晨报，可选临时指定城市",
                "/daily news - 查看新闻速览",
                "/daily weather [city] - 查询天气；不传时优先使用当前会话或默认城市",
                "/daily status - 查看插件状态和当前会话订阅情况",
                "/daily sendnow - 立即向所有订阅会话发送晨报，仅管理员可用",
                "",
                "仅支持以上 /daily 开头命令。",
            ]
        )

    async def _sendnow_impl(self, event: AstrMessageEvent):
        success_count = await self._broadcast_daily_report(reason="manual")
        yield event.plain_result(f"晨报已尝试发送，成功投递到 {success_count} 个会话。")
    async def _broadcast_daily_report(self, reason: str) -> int:
        subscriptions = await self._get_subscription_snapshot()
        if not subscriptions:
            logger.info("晨报插件跳过发送，当前没有订阅会话。")
            return 0

        payload_cache: dict[str, dict[str, Any]] = {}
        success_count = 0

        for unified_msg_origin, info in subscriptions.items():
            city = (info.get("city") or self._default_city()).strip()
            cache_key = city or "__default__"
            if cache_key not in payload_cache:
                try:
                    payload_cache[cache_key] = await self._build_report_payload(city)
                except Exception as exc:
                    logger.exception("晨报内容构建失败: city=%s error=%s", city, exc)
                    fallback_report = self._fallback_report()
                    payload_cache[cache_key] = {
                        "mode": "text",
                        "text": fallback_report,
                        "content": fallback_report,
                    }

            try:
                if await self._deliver_payload_to_subscription(unified_msg_origin, payload_cache[cache_key], info):
                    success_count += 1
            except Exception as exc:
                logger.warning(
                    "晨报发送失败: reason=%s target=%s error=%s",
                    reason,
                    unified_msg_origin,
                    exc,
                )

        logger.info("晨报发送完成: reason=%s success=%s", reason, success_count)
        return success_count

    async def _build_report_payload(self, city: str = "") -> dict[str, Any]:
        async with self._http_client() as client:
            report_data = await self._collect_report_data_with_client(client, city)
        return self._compose_payload(
            self._render_report_text(report_data),
            self._render_report_card(report_data) if self._feishu_card_enabled() else None,
        )

    async def _build_news_payload(self) -> dict[str, Any]:
        async with self._http_client() as client:
            news_data = await self._collect_news_data_with_client(client)
        return self._compose_payload(
            self._render_news_text(news_data),
            self._render_news_card(news_data) if self._feishu_card_enabled() else None,
        )

    def _build_weather_payload(self, city: str, weather_text: str) -> dict[str, Any]:
        return self._compose_payload(
            weather_text,
            self._render_weather_card(city, weather_text) if self._feishu_card_enabled() else None,
        )

    def _build_status_payload(self, status_data: dict[str, Any]) -> dict[str, Any]:
        return self._compose_payload(
            self._render_status_text(status_data),
            self._render_status_card(status_data) if self._feishu_card_enabled() else None,
        )

    def _compose_payload(self, text: str, card: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "mode": "text",
            "text": text,
            "content": text,
        }
        if card:
            payload["mode"] = "card"
            payload["card"] = card
        return payload

    def _build_message_chain(self, payload: dict[str, Any]) -> MessageChain:
        return MessageChain().message(self._payload_text(payload))

    async def _build_report(self, city: str = "") -> str:
        async with self._http_client() as client:
            report_data = await self._collect_report_data_with_client(client, city)
        return self._render_report_text(report_data)

    async def _build_news_text(self) -> str:
        async with self._http_client() as client:
            news_data = await self._collect_news_data_with_client(client)
        return self._render_news_text(news_data)

    async def _build_status_data(self, event: AstrMessageEvent) -> dict[str, Any]:
        subscriptions = await self._get_subscription_snapshot()
        current = subscriptions.get(event.unified_msg_origin)
        return {
            "enabled": self._is_enabled(),
            "delivery_time": self._delivery_time_text(),
            "delivery_timezone": self._timezone_name(),
            "default_city": self._default_city() or "未设置",
            "weather_provider": self._weather_provider_label(),
            "rss_count": len(self._rss_urls()),
            "subscription_count": len(subscriptions),
            "current_subscribed": bool(current),
            "current_city": (current.get("city") if current else "") or self._default_city() or "未设置",
            "card_enabled": self._feishu_card_enabled(),
        }

    async def _collect_report_data_with_client(self, client: httpx.AsyncClient, city: str = "") -> dict[str, Any]:
        resolved_city = city.strip() or self._default_city()
        task_map: dict[str, asyncio.Task] = {}

        if self.config.get("include_weather", True) and resolved_city:
            task_map["weather"] = asyncio.create_task(self._fetch_weather_summary(client, resolved_city))
        if self.config.get("include_quote", True):
            task_map["quote"] = asyncio.create_task(self._fetch_hitokoto(client))
        if self.config.get("include_poem", False):
            task_map["poem"] = asyncio.create_task(self._fetch_poem(client))
        if self._rss_urls() and self._news_limit() > 0:
            task_map["news"] = asyncio.create_task(self._fetch_headlines(client))

        results = await asyncio.gather(*task_map.values(), return_exceptions=True)
        sections = dict(zip(task_map.keys(), results))
        now = datetime.now(self._timezone())

        return {
            "title": str(self.config.get("report_title", "每日晨报") or "每日晨报"),
            "date_line": f"{now:%Y-%m-%d} 星期{WEEKDAY_CN[now.weekday()]}",
            "weather": self._result_or_none("weather", sections),
            "news": self._result_or_none("news", sections) or [],
            "quote": self._result_or_none("quote", sections),
            "poem": self._result_or_none("poem", sections),
        }

    async def _collect_news_data_with_client(self, client: httpx.AsyncClient) -> dict[str, Any]:
        now = datetime.now(self._timezone())
        try:
            news = await self._fetch_headlines(client)
        except Exception as exc:
            logger.exception("新闻速览拉取失败: %s", exc)
            news = []

        return {
            "title": "新闻速览",
            "date_line": f"{now:%Y-%m-%d} 星期{WEEKDAY_CN[now.weekday()]}",
            "news": news,
        }

    def _result_or_none(self, key: str, results: dict[str, Any]) -> Any:
        value = results.get(key)
        if isinstance(value, Exception):
            logger.warning("晨报内容块抓取失败: %s error=%s", key, value)
            return None
        return value

    def _http_client(self) -> httpx.AsyncClient:
        proxy = str(self.config.get("http_proxy", "") or "").strip() or None
        timeout = max(int(self.config.get("http_timeout_seconds", 15) or 15), 5)
        kwargs: dict[str, Any] = {
            "timeout": timeout,
            "follow_redirects": True,
            "trust_env": True,
            "headers": {
                "User-Agent": "astrbot_plugin_morning_news/0.2.0",
            },
        }
        if proxy:
            kwargs["proxy"] = proxy
        return httpx.AsyncClient(**kwargs)

    async def _load_subscriptions(self):
        data = await self.get_kv_data("subscriptions", {})
        if not isinstance(data, dict):
            logger.warning("morning-news subscriptions data is invalid; resetting to empty state")
            data = {}
        normalized = self._normalize_subscriptions(data)
        async with self._state_lock:
            self._subscriptions = normalized

    def _normalize_subscriptions(self, data: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        normalized: dict[str, dict[str, Any]] = {}
        for key, value in data.items():
            record = self._normalize_subscription_record(value)
            if record is None:
                logger.warning("morning-news skipped invalid subscription record: key=%s", key)
                continue
            normalized[str(key)] = record
        return normalized

    def _normalize_subscription_record(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        normalized: dict[str, Any] = {}
        for key in (
            "city",
            "sender_name",
            "updated_at",
            "platform_id",
            "receive_id",
            "receive_id_type",
        ):
            raw = value.get(key, "")
            normalized[key] = self._clean_text(str(raw)) if raw is not None else ""
        return normalized

    async def _get_subscription_snapshot(self) -> dict[str, dict[str, Any]]:
        async with self._state_lock:
            return {key: value.copy() for key, value in self._subscriptions.items()}

    async def _persist_subscriptions(self):
        async with self._state_lock:
            data = {key: value.copy() for key, value in self._subscriptions.items()}
        await self.put_kv_data("subscriptions", data)

    async def _upsert_subscription(self, event: AstrMessageEvent, city: str):
        record = {
            "city": city,
            "sender_name": event.get_sender_name(),
            "updated_at": datetime.now(self._timezone()).isoformat(timespec="seconds"),
        }
        record.update(self._transport_snapshot_from_event(event))
        async with self._state_lock:
            self._subscriptions[event.unified_msg_origin] = record
        await self._persist_subscriptions()

    async def _remove_subscription(self, unified_msg_origin: str) -> bool:
        removed = False
        async with self._state_lock:
            removed = unified_msg_origin in self._subscriptions
            if removed:
                self._subscriptions.pop(unified_msg_origin, None)
        if removed:
            await self._persist_subscriptions()
        return removed

    async def _set_subscription_city(self, event: AstrMessageEvent, city: str) -> bool | None:
        changed = False
        async with self._state_lock:
            item = self._subscriptions.get(event.unified_msg_origin)
            if not item:
                return None
            city_changed = item.get("city", "") != city
            item["city"] = city
            item["sender_name"] = event.get_sender_name()
            item["updated_at"] = datetime.now(self._timezone()).isoformat(timespec="seconds")
            transport_changed = self._apply_subscription_transport(item, self._transport_snapshot_from_event(event))
            changed = city_changed or transport_changed
        await self._persist_subscriptions()
        return changed

    async def _refresh_subscription_transport_if_needed(self, event: AstrMessageEvent):
        transport = self._transport_snapshot_from_event(event)
        if not transport:
            return

        should_persist = False
        async with self._state_lock:
            item = self._subscriptions.get(event.unified_msg_origin)
            if not item:
                return
            should_persist = self._apply_subscription_transport(item, transport)
        if should_persist:
            await self._persist_subscriptions()

    @staticmethod
    def _apply_subscription_transport(item: dict[str, Any], transport: dict[str, str]) -> bool:
        changed = False
        for key in ("platform_id", "receive_id", "receive_id_type"):
            value = transport.get(key, "")
            if value and item.get(key) != value:
                item[key] = value
                changed = True
        return changed

    async def _city_for_subscription(self, unified_msg_origin: str) -> str:
        async with self._state_lock:
            item = self._subscriptions.get(unified_msg_origin, {})
            city = item.get("city") if isinstance(item, dict) else ""
        return (city or self._default_city()).strip()

    def _resolve_city(self, city: str) -> str:
        return city.strip() or self._default_city()

    def _default_city(self) -> str:
        return str(self.config.get("default_city", "") or "").strip()

    def _weather_provider(self) -> str:
        value = str(self.config.get("weather_provider", "uapi") or "").strip().lower()
        return value if value in {"uapi", "open-meteo", "custom"} else "uapi"

    def _weather_provider_label(self) -> str:
        provider = self._weather_provider()
        if provider == "custom":
            return "自定义 API"
        if provider == "uapi":
            return "UAPI"
        return "Open-Meteo"

    def _custom_weather_api_url(self) -> str:
        return str(self.config.get("custom_weather_api_url", "") or "").strip()

    def _custom_weather_response_path(self) -> str:
        return str(self.config.get("custom_weather_response_path", "") or "").strip()

    def _custom_weather_headers(self) -> dict[str, str]:
        raw = str(self.config.get("custom_weather_headers", "") or "").strip()
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except Exception as exc:
            logger.warning("custom_weather_headers 解析失败: %s", exc)
            return {}
        if not isinstance(data, dict):
            logger.warning("custom_weather_headers 必须是 JSON 对象。")
            return {}
        return {str(key): str(value) for key, value in data.items() if key and value is not None}

    def _delivery_time_text(self) -> str:
        return f"{self._delivery_hour():02d}:{self._delivery_minute():02d}"

    def _delivery_hour(self) -> int:
        return self._parse_delivery_time()[0]

    def _delivery_minute(self) -> int:
        return self._parse_delivery_time()[1]

    def _parse_delivery_time(self) -> tuple[int, int]:
        raw = str(self.config.get("delivery_time", "08:00") or "08:00").strip()
        try:
            hour_text, minute_text = raw.split(":", 1)
            hour = min(max(int(hour_text), 0), 23)
            minute = min(max(int(minute_text), 0), 59)
            return hour, minute
        except Exception:
            logger.warning("delivery_time 配置异常: %s，已回退到 08:00", raw)
            return 8, 0

    def _scheduler_config_key(self) -> str:
        return json.dumps(
            {
                "enabled": self._is_enabled(),
                "delivery_time": self._delivery_time_text(),
                "delivery_timezone": self._timezone_name(),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def _timezone_name(self) -> str:
        value = str(self.config.get("delivery_timezone", "Asia/Shanghai") or "").strip()
        return value or "Asia/Shanghai"

    def _timezone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self._timezone_name())
        except Exception:
            logger.warning("发送时区配置非法: %s，已回退到 Asia/Shanghai", self._timezone_name())
            return ZoneInfo("Asia/Shanghai")

    def _next_run_datetime(self, now: datetime) -> datetime:
        candidate = now.replace(
            hour=self._delivery_hour(),
            minute=self._delivery_minute(),
            second=0,
            microsecond=0,
        )
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    def _rss_urls(self) -> list[str]:
        raw = str(self.config.get("rss_urls", "") or "")
        return [line.strip() for line in raw.splitlines() if line.strip() and not line.strip().startswith("#")]

    def _news_limit(self) -> int:
        value = int(self.config.get("news_limit", 5) or 5)
        return max(value, 0)

    def _is_enabled(self) -> bool:
        return bool(self.config.get("enabled", True))

    def _bot_display_name(self) -> str:
        return str(self.config.get("bot_display_name", "") or "").strip()

    @staticmethod
    def _clean_text(text: str) -> str:
        return " ".join(html.unescape(text).split())

    @staticmethod
    def _clip_text(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return f"{text[: limit - 1]}…"

    def _fill_url_template(self, template: str, values: dict[str, str]) -> str:
        return re.sub(
            r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}",
            lambda match: values.get(match.group(1), ""),
            template,
        )

    def _extract_data_by_path(self, data: Any, path: str) -> Any:
        current = data
        for segment in [part.strip() for part in path.split(".") if part.strip()]:
            if isinstance(current, dict):
                if segment not in current:
                    return None
                current = current[segment]
                continue
            if isinstance(current, list):
                try:
                    index = int(segment)
                except ValueError:
                    return None
                if index < 0 or index >= len(current):
                    return None
                current = current[index]
                continue
            return None
        return current

    def _guess_weather_text_from_json(self, data: Any) -> str:
        direct_text = self._text_value(data)
        if direct_text:
            return direct_text
        for path in (
            "weather",
            "summary",
            "text",
            "result",
            "message",
            "data.weather",
            "data.summary",
            "data.text",
            "data.result",
            "current.weather",
            "current.summary",
            "current.text",
        ):
            value = self._extract_data_by_path(data, path)
            text = self._text_value(value)
            if text:
                return text
        return ""

    def _text_value(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (str, int, float, bool)):
            return self._clean_text(str(value))
        return ""

    def _safe_float(self, value: Any) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)

        text = self._clean_text(str(value))
        if not text:
            return None
        try:
            return float(text)
        except (TypeError, ValueError):
            logger.warning("天气数值字段解析失败: value=%r", value)
            return None

    def _safe_int(self, value: Any) -> int | None:
        numeric = self._safe_float(value)
        if numeric is None:
            return None
        return int(numeric)

    def _remember_geo_cache(self, cache_key: str, result: dict[str, Any]):
        self._geo_cache.pop(cache_key, None)
        self._geo_cache[cache_key] = result.copy()
        while len(self._geo_cache) > GEO_CACHE_MAX_SIZE:
            self._geo_cache.popitem(last=False)

    @staticmethod
    def _first_or_default(value: Any, default: Any = None) -> Any:
        if isinstance(value, list):
            return value[0] if value else default
        return value if value is not None else default

    @staticmethod
    def _format_time(value: str) -> str:
        if "T" not in value:
            return ""
        return value.split("T", 1)[1][:5]

