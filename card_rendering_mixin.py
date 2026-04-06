from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit


class CardRenderingMixin:
    def _render_report_card(self, report_data: dict[str, Any]) -> dict[str, Any]:
        news = report_data.get("news") or []
        weather_summary = self._parse_weather_summary(report_data.get("weather", ""))

        overview_fields = [
            self._card_field("\u65e5\u671f", report_data["date_line"]),
            self._card_field("\u65b0\u95fb\u6761\u6570", f"{len(news)} \u6761"),
        ]
        if weather_summary.get("city"):
            overview_fields.append(self._card_field("\u57ce\u5e02", weather_summary["city"]))

        elements: list[dict[str, Any]] = [self._card_fields(overview_fields)]

        if report_data.get("weather"):
            elements.extend(self._build_weather_section_elements(weather_summary, report_data["weather"]))

        if news:
            elements.extend([self._card_hr(), self._card_section_title("\u65b0\u95fb\u901f\u89c8")])
            elements.extend(self._build_news_card_elements(news))

        if report_data.get("quote"):
            elements.extend(
                [
                    self._card_hr(),
                    self._card_section_title("\u4eca\u65e5\u4e00\u53e5"),
                    self._card_markdown_text(report_data["quote"]),
                ]
            )

        if report_data.get("poem"):
            elements.extend(
                [
                    self._card_hr(),
                    self._card_section_title("\u8bd7\u8bcd"),
                    self._card_markdown_text(report_data["poem"]),
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
                    self._card_field("\u65e5\u671f", news_data["date_line"]),
                    self._card_field("\u65b0\u95fb\u6761\u6570", f"{len(news)} \u6761"),
                ]
            )
        ]

        elements.append(self._card_hr())
        if news:
            elements.extend(self._build_news_card_elements(news))
        else:
            elements.append(
                self._card_section(
                    "\u63d0\u793a",
                    "\u5f53\u524d\u6ca1\u6709\u53ef\u7528\u65b0\u95fb\uff0c\u8bf7\u68c0\u67e5 RSS \u6e90\u6216\u63a5\u53e3\u914d\u7f6e\u3002",
                )
            )

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
            elements.append(
                self._card_fields([
                    self._card_field("\u57ce\u5e02", city.strip() or "\u672a\u6307\u5b9a\u57ce\u5e02")
                ])
            )

        detail_text = self._weather_detail_text(weather_summary, weather_text)
        if detail_text:
            elements.extend(
                [
                    self._card_hr(),
                    self._card_section_title("\u5929\u6c14\u6982\u89c8"),
                    self._card_markdown_text(detail_text),
                ]
            )

        footer = self._footer_text()
        if footer:
            elements.extend([self._card_hr(), self._card_note(footer)])
        return self._build_card("\u5929\u6c14\u67e5\u8be2", "turquoise", elements)

    def _render_status_card(self, status_data: dict[str, Any]) -> dict[str, Any]:
        fields = [
            self._card_field("\u542f\u7528\u72b6\u6001", "\u5f00\u542f" if status_data.get("enabled") else "\u5173\u95ed"),
            self._card_field(
                "\u53d1\u9001\u65f6\u95f4",
                f"{status_data.get('delivery_time', '')} ({status_data.get('delivery_timezone', '')})",
            ),
            self._card_field("\u9ed8\u8ba4\u57ce\u5e02", status_data.get("default_city", "\u672a\u8bbe\u7f6e")),
            self._card_field("\u5929\u6c14\u6e90", status_data.get("weather_provider", "unknown")),
            self._card_field("RSS \u6e90\u6570\u91cf", str(status_data.get("rss_count", 0))),
            self._card_field("\u603b\u8ba2\u9605\u6570", str(status_data.get("subscription_count", 0))),
            self._card_field("\u5f53\u524d\u4f1a\u8bdd\u5df2\u8ba2\u9605", "\u662f" if status_data.get("current_subscribed") else "\u5426"),
            self._card_field("\u98de\u4e66\u5361\u7247", "\u5f00\u542f" if status_data.get("card_enabled") else "\u5173\u95ed"),
        ]
        if status_data.get("current_subscribed"):
            fields.append(self._card_field("\u5f53\u524d\u4f1a\u8bdd\u57ce\u5e02", status_data.get("current_city", "\u672a\u8bbe\u7f6e")))

        elements = [self._card_fields(fields)]

        footer = self._footer_text()
        if footer:
            elements.extend([self._card_hr(), self._card_note(footer)])

        return self._build_card("\u6668\u62a5\u72b6\u6001", "grey", elements)

    def _build_weather_section_elements(
        self,
        weather_summary: dict[str, Any],
        raw_weather_text: str,
    ) -> list[dict[str, Any]]:
        elements = [self._card_hr(), self._card_section_title("\u5929\u6c14\u6982\u89c8")]
        weather_fields = self._weather_metric_fields(weather_summary)
        if weather_fields:
            elements.append(self._card_fields(weather_fields))

        detail_text = self._weather_detail_text(weather_summary, raw_weather_text)
        if detail_text:
            elements.append(self._card_markdown_text(detail_text))
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

            elements.append(self._card_div_markdown(f"**{index + 1}. {self._escape_card_text(headline)}**"))
            if summary:
                elements.append(self._card_markdown_text(summary))

            meta_fields = []
            if source:
                meta_fields.append(self._card_field("\u6765\u6e90", source))
            safe_link = self._safe_card_link(link)
            if safe_link:
                meta_fields.append(
                    self._card_field(
                        "\u539f\u6587",
                        f"[\u67e5\u770b\u8be6\u60c5]({safe_link})",
                        escape_value=False,
                    )
                )
            if meta_fields:
                elements.append(self._card_fields(meta_fields))

            if index != len(news) - 1:
                elements.append(self._card_hr())
        return elements or [self._card_section("\u63d0\u793a", "\u5f53\u524d\u6ca1\u6709\u53ef\u5c55\u793a\u7684\u65b0\u95fb\u5185\u5bb9\u3002")]

    def _parse_weather_summary(self, weather_text: str, city_hint: str = "") -> dict[str, Any]:
        raw_text = str(weather_text or "").strip()
        city = city_hint.strip()
        detail = raw_text
        if ":" in raw_text:
            possible_city, possible_detail = raw_text.split(":", 1)
            if possible_city.strip():
                city = possible_city.strip()
                detail = possible_detail.strip()

        segments = [self._clean_card_text(part) for part in re.split(r"[,\uFF0C]", detail) if self._clean_card_text(part)]
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
            if not parsed["current"] and segment.lower().startswith(("\u5f53\u524d", "current")):
                parsed["current"] = segment.replace("\u5f53\u524d", "", 1).replace("current", "", 1).strip() or segment
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
            fields.append(self._card_field("\u57ce\u5e02", weather_summary["city"]))
        if weather_summary.get("condition"):
            fields.append(self._card_field("\u5929\u6c14", weather_summary["condition"]))
        if weather_summary.get("range"):
            fields.append(self._card_field("\u6e29\u5ea6", weather_summary["range"]))
        if weather_summary.get("current"):
            fields.append(self._card_field("\u5f53\u524d", weather_summary["current"]))
        if weather_summary.get("air"):
            fields.append(self._card_field("\u7a7a\u6c14", weather_summary["air"]))
        return fields

    def _weather_detail_text(self, weather_summary: dict[str, Any], fallback_text: str) -> str:
        extras = [segment for segment in weather_summary.get("extras", []) if segment]
        if extras:
            return ", ".join(extras)
        return str(fallback_text or "").strip()

    @staticmethod
    def _looks_like_weather_metric(segment: str) -> bool:
        value = segment.strip()
        lowered = value.lower()
        if not value:
            return False
        if value.startswith(("\u5f53\u524d", "\u6e7f\u5ea6", "\u4f53\u611f", "AQI")):
            return True
        if lowered.startswith(("current", "humidity", "feels", "aqi")):
            return True
        if ("\u98ce" in value or "wind" in lowered) and len(value) <= 24:
            return True
        return CardRenderingMixin._is_temperature_range(value)

    @staticmethod
    def _is_temperature_range(segment: str) -> bool:
        return bool(re.search(r"-?\d+\s*(?:~|\uFF5E|-|\u81F3)\s*-?\d+\s*[\u00B0A-Za-z\u2103\u2109]*", segment))

    @staticmethod
    def _clean_card_text(text: str) -> str:
        return " ".join(str(text).split())

    def _escape_card_text(self, text: Any) -> str:
        value = self._clean_card_text(text)
        if not value:
            return ""
        value = value.replace("\\", "\\\\")
        for marker in ("`", "*", "_", "[", "]", "(", ")", "<", ">"):
            value = value.replace(marker, f"\\{marker}")
        value = value.replace("@", "@\u200b")
        return value

    def _safe_card_link(self, url: str) -> str:
        sanitizer = getattr(self, "_safe_markdown_link_url", None)
        if callable(sanitizer):
            return sanitizer(url)

        value = str(url or "").strip()
        if not value:
            return ""
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            return ""
        if any(char.isspace() for char in parts.netloc):
            return ""
        path = quote(parts.path or "/", safe="/%:@!$&'*+,;=-._~")
        query = quote(parts.query, safe="=&%:@!$'*,;+-._~")
        fragment = quote(parts.fragment, safe="%:@!$&'*,;=+-._~")
        return urlunsplit((parts.scheme, parts.netloc, path, query, fragment))

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
                    "content": " ".join(str(title).split()),
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

    def _card_markdown_text(self, text: str) -> dict[str, Any]:
        return self._card_div_markdown(self._escape_card_text(text))

    def _card_section_title(self, title: str) -> dict[str, Any]:
        return self._card_div_markdown(f"**{self._escape_card_text(title)}**")

    def _card_section(self, title: str, body: str) -> dict[str, Any]:
        return self._card_div_markdown(f"**{self._escape_card_text(title)}**\n{self._escape_card_text(body)}")

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

    def _card_field(self, title: str, value: str, is_short: bool = True, *, escape_value: bool = True) -> dict[str, Any]:
        safe_title = self._escape_card_text(title)
        safe_value = self._escape_card_text(value) if escape_value else value
        return {
            "is_short": is_short,
            "text": {
                "tag": "lark_md",
                "content": f"**{safe_title}**\n{safe_value}",
            },
        }

    @staticmethod
    def _card_hr() -> dict[str, str]:
        return {"tag": "hr"}
