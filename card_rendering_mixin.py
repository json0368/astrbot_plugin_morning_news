from __future__ import annotations

import re
from typing import Any


class CardRenderingMixin:
    def _render_report_card(self, report_data: dict[str, Any]) -> dict[str, Any]:
        news = report_data.get("news") or []
        weather_summary = self._parse_weather_summary(report_data.get("weather", ""))

        overview_fields = [
            self._card_field("日期", report_data["date_line"]),
            self._card_field("新闻条数", f"{len(news)} 条"),
        ]
        if weather_summary.get("city"):
            overview_fields.append(self._card_field("城市", weather_summary["city"]))

        elements: list[dict[str, Any]] = [self._card_fields(overview_fields)]

        if report_data.get("weather"):
            elements.extend(self._build_weather_section_elements(weather_summary, report_data["weather"]))

        if news:
            elements.extend([self._card_hr(), self._card_section_title("新闻速览")])
            elements.extend(self._build_news_card_elements(news))

        if report_data.get("quote"):
            elements.extend(
                [
                    self._card_hr(),
                    self._card_section_title("今日一句"),
                    self._card_div_markdown(report_data["quote"]),
                ]
            )

        if report_data.get("poem"):
            elements.extend(
                [
                    self._card_hr(),
                    self._card_section_title("诗词"),
                    self._card_div_markdown(report_data["poem"]),
                ]
            )

        footer = self._footer_text()
        if footer:
            elements.extend([self._card_hr(), self._card_note(footer)])

        return self._build_card(report_data["title"], "green", elements)

    def _render_news_card(self, news_data: dict[str, Any]) -> dict[str, Any]:
        news = news_data.get("news") or []
        elements: list[dict[str, Any]] = [
            self._card_fields(
                [
                    self._card_field("日期", news_data["date_line"]),
                    self._card_field("新闻条数", f"{len(news)} 条"),
                ]
            )
        ]

        elements.append(self._card_hr())
        if news:
            elements.extend(self._build_news_card_elements(news))
        else:
            elements.append(self._card_section("提示", "当前没有可用新闻，请检查 RSS 源或接口配置。"))

        footer = self._footer_text()
        if footer:
            elements.extend([self._card_hr(), self._card_note(footer)])

        return self._build_card(news_data["title"], "blue", elements)

    def _render_weather_card(self, city: str, weather_text: str) -> dict[str, Any]:
        weather_summary = self._parse_weather_summary(weather_text, city_hint=city)
        elements: list[dict[str, Any]] = []

        overview_fields = self._weather_metric_fields(weather_summary)
        if overview_fields:
            elements.append(self._card_fields(overview_fields))
        else:
            elements.append(self._card_fields([self._card_field("城市", city.strip() or "未指定城市")]))

        detail_text = self._weather_detail_text(weather_summary, weather_text)
        if detail_text:
            elements.extend(
                [
                    self._card_hr(),
                    self._card_section_title("天气概览"),
                    self._card_div_markdown(detail_text),
                ]
            )

        footer = self._footer_text()
        if footer:
            elements.extend([self._card_hr(), self._card_note(footer)])
        return self._build_card("天气查询", "turquoise", elements)

    def _render_status_card(self, status_data: dict[str, Any]) -> dict[str, Any]:
        fields = [
            self._card_field("启用状态", "开启" if status_data.get("enabled") else "关闭"),
            self._card_field(
                "发送时间",
                f"{status_data.get('delivery_time', '')} ({status_data.get('delivery_timezone', '')})",
            ),
            self._card_field("默认城市", status_data.get("default_city", "未设置")),
            self._card_field("天气源", status_data.get("weather_provider", "未知")),
            self._card_field("RSS 源数量", str(status_data.get("rss_count", 0))),
            self._card_field("总订阅数", str(status_data.get("subscription_count", 0))),
            self._card_field("当前会话已订阅", "是" if status_data.get("current_subscribed") else "否"),
            self._card_field("飞书卡片", "开启" if status_data.get("card_enabled") else "关闭"),
        ]
        if status_data.get("current_subscribed"):
            fields.append(self._card_field("当前会话城市", status_data.get("current_city", "未设置")))

        elements = [self._card_fields(fields)]

        footer = self._footer_text()
        if footer:
            elements.extend([self._card_hr(), self._card_note(footer)])

        return self._build_card("晨报状态", "grey", elements)

    def _build_weather_section_elements(
        self,
        weather_summary: dict[str, Any],
        raw_weather_text: str,
    ) -> list[dict[str, Any]]:
        elements = [self._card_hr(), self._card_section_title("天气概览")]
        weather_fields = self._weather_metric_fields(weather_summary)
        if weather_fields:
            elements.append(self._card_fields(weather_fields))

        detail_text = self._weather_detail_text(weather_summary, raw_weather_text)
        if detail_text:
            elements.append(self._card_div_markdown(detail_text))
        return elements

    def _build_news_card_elements(self, news: list[dict[str, str]]) -> list[dict[str, Any]]:
        elements: list[dict[str, Any]] = []
        for index, item in enumerate(news):
            title = item.get("title", "").strip()
            summary = self._news_summary_text(title, item.get("summary", "").strip())
            source = item.get("source", "").strip()
            link = item.get("link", "").strip()

            headline = title or summary
            if not headline:
                continue

            elements.append(self._card_div_markdown(f"**{index + 1}. {headline}**"))
            if summary:
                elements.append(self._card_div_markdown(summary))

            meta_fields = []
            if source:
                meta_fields.append(self._card_field("来源", source))
            if link:
                meta_fields.append(self._card_field("原文", f"[查看详情]({link})"))
            if meta_fields:
                elements.append(self._card_fields(meta_fields))

            if index != len(news) - 1:
                elements.append(self._card_hr())
        return elements or [self._card_section("提示", "当前没有可展示的新闻内容。")]

    def _parse_weather_summary(self, weather_text: str, city_hint: str = "") -> dict[str, Any]:
        raw_text = str(weather_text or "").strip()
        city = city_hint.strip()
        detail = raw_text
        if ":" in raw_text:
            possible_city, possible_detail = raw_text.split(":", 1)
            if possible_city.strip():
                city = possible_city.strip()
                detail = possible_detail.strip()

        segments = [self._clean_card_text(part) for part in detail.split("，") if self._clean_card_text(part)]
        parsed: dict[str, Any] = {
            "city": city,
            "condition": "",
            "range": "",
            "current": "",
            "air": "",
            "extras": [],
        }

        for segment in segments:
            if not parsed["condition"] and not self._looks_like_weather_metric(segment):
                parsed["condition"] = segment
                continue
            if not parsed["range"] and self._is_temperature_range(segment):
                parsed["range"] = segment
                continue
            if not parsed["current"] and segment.startswith("当前"):
                parsed["current"] = segment.replace("当前", "", 1).strip() or segment
                continue
            if not parsed["air"] and "AQI" in segment.upper():
                parsed["air"] = segment
                continue
            parsed["extras"].append(segment)

        if not parsed["condition"] and segments:
            parsed["condition"] = segments[0]
            parsed["extras"] = [segment for segment in segments[1:] if segment != parsed["range"]]

        return parsed

    def _weather_metric_fields(self, weather_summary: dict[str, Any]) -> list[dict[str, Any]]:
        fields: list[dict[str, Any]] = []
        if weather_summary.get("city"):
            fields.append(self._card_field("城市", weather_summary["city"]))
        if weather_summary.get("condition"):
            fields.append(self._card_field("天气", weather_summary["condition"]))
        if weather_summary.get("range"):
            fields.append(self._card_field("温度", weather_summary["range"]))
        if weather_summary.get("current"):
            fields.append(self._card_field("当前", weather_summary["current"]))
        if weather_summary.get("air"):
            fields.append(self._card_field("空气", weather_summary["air"]))
        return fields

    def _weather_detail_text(self, weather_summary: dict[str, Any], fallback_text: str) -> str:
        extras = [segment for segment in weather_summary.get("extras", []) if segment]
        if extras:
            return "，".join(extras)
        return str(fallback_text or "").strip()

    @staticmethod
    def _looks_like_weather_metric(segment: str) -> bool:
        segment = segment.strip()
        if not segment:
            return False
        if segment.startswith(("当前", "湿度", "体感", "AQI")):
            return True
        if "风" in segment and len(segment) <= 16:
            return True
        return CardRenderingMixin._is_temperature_range(segment)

    @staticmethod
    def _is_temperature_range(segment: str) -> bool:
        return bool(re.search(r"-?\d+\s*(?:~|～|-|至)\s*-?\d+\s*[°℃CFcfx]*", segment))

    @staticmethod
    def _clean_card_text(text: str) -> str:
        return " ".join(str(text).split())

    @staticmethod
    def _build_card(title: str, template: str, elements: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "config": {
                "wide_screen_mode": True,
                "enable_forward": True,
            },
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": title,
                },
                "template": template,
            },
            "elements": elements,
        }

    @staticmethod
    def _card_div_markdown(content: str) -> dict[str, Any]:
        return {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": content,
            },
        }

    def _card_section_title(self, title: str) -> dict[str, Any]:
        return self._card_div_markdown(f"**{title}**")

    def _card_section(self, title: str, body: str) -> dict[str, Any]:
        return self._card_div_markdown(f"**{title}**\n{body}")

    @staticmethod
    def _card_note(text: str) -> dict[str, Any]:
        return {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": text,
                }
            ],
        }

    @staticmethod
    def _card_fields(fields: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "tag": "div",
            "fields": fields,
        }

    @staticmethod
    def _card_field(title: str, value: str, is_short: bool = True) -> dict[str, Any]:
        return {
            "is_short": is_short,
            "text": {
                "tag": "lark_md",
                "content": f"**{title}**\n{value}",
            },
        }

    @staticmethod
    def _card_hr() -> dict[str, str]:
        return {"tag": "hr"}
