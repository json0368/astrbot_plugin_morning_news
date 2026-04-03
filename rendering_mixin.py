from __future__ import annotations

from datetime import datetime
import re
from typing import Any

try:
    from .daily_shared import WEEKDAY_CN
except ImportError:
    from daily_shared import WEEKDAY_CN


class RenderingMixin:
    def _render_report_text(self, report_data: dict[str, Any]) -> str:
        lines = [report_data["title"], report_data["date_line"]]

        if report_data.get("weather"):
            lines.extend(["", "天气", report_data["weather"]])

        news = report_data.get("news") or []
        if news:
            lines.extend(["", "新闻速览"])
            self._append_news_lines(lines, news)

        if report_data.get("quote"):
            lines.extend(["", "今日一句", report_data["quote"]])

        if report_data.get("poem"):
            lines.extend(["", "诗词", report_data["poem"]])

        footer = self._footer_text()
        if footer:
            lines.extend(["", footer])

        if len(lines) <= 2:
            lines.extend(["", "今天的外部数据暂时拉取失败，请检查网络、RSS 源或接口配置。"])

        return "\n".join(lines)

    def _render_news_text(self, news_data: dict[str, Any]) -> str:
        lines = [news_data["title"], news_data["date_line"]]

        news = news_data.get("news") or []
        if news:
            lines.append("")
            self._append_news_lines(lines, news)
        else:
            lines.extend(["", "当前没有可用新闻，请检查 RSS 源或接口配置。"])

        footer = self._footer_text()
        if footer:
            lines.extend(["", footer])

        return "\n".join(lines)

    def _render_status_text(self, status_data: dict[str, Any]) -> str:
        lines = [
            f"启用状态: {'开启' if status_data.get('enabled') else '关闭'}",
            f"发送时间: {status_data.get('delivery_time', '')} ({status_data.get('delivery_timezone', '')})",
            f"默认城市: {status_data.get('default_city', '未设置')}",
            f"天气源: {status_data.get('weather_provider', '未知')}",
            f"RSS 源数量: {status_data.get('rss_count', 0)}",
            f"总订阅数: {status_data.get('subscription_count', 0)}",
            f"当前会话已订阅: {'是' if status_data.get('current_subscribed') else '否'}",
            f"飞书卡片: {'开启' if status_data.get('card_enabled') else '关闭'}",
        ]
        if status_data.get("current_subscribed"):
            lines.append(f"当前会话城市: {status_data.get('current_city', '未设置')}")
        return "\n".join(lines)

    def _fallback_report(self) -> str:
        now = datetime.now(self._timezone())
        lines = [
            f"{self.config.get('report_title', '每日晨报')}",
            f"{now:%Y-%m-%d} 星期{WEEKDAY_CN[now.weekday()]}",
            "",
            "晨报暂时生成失败，请检查网络、RSS 源或接口配置。",
        ]
        footer = self._footer_text()
        if footer:
            lines.extend(["", footer])
        return "\n".join(lines)

    def _append_news_lines(self, lines: list[str], news: list[dict[str, str]]):
        for index, item in enumerate(news):
            title = item.get("title", "").strip()
            raw_summary = item.get("summary", "").strip()
            summary = self._news_summary_text(title, raw_summary)
            link = item.get("link", "").strip()

            headline = title or summary
            if not headline:
                continue

            if index > 0:
                lines.append("")

            lines.append(headline)
            if summary:
                lines.append(summary)
            if link:
                lines.append(f"- [来源]({link})")
            else:
                lines.append("- 来源")

    def _news_summary_text(self, title: str, summary: str) -> str:
        summary = summary.strip()
        if not summary:
            return ""

        summary = self._clip_text(summary, 140)
        normalized_title = re.sub(r"\s+", "", title)
        normalized_summary = re.sub(r"\s+", "", summary)

        if not normalized_summary:
            return ""
        if normalized_summary == normalized_title:
            return ""
        if len(normalized_summary) < 12:
            return ""
        if self._is_byline_summary(summary):
            return ""
        return summary

    @staticmethod
    def _is_byline_summary(summary: str) -> bool:
        compact = re.sub(r"\s+", "", summary)
        return bool(
            re.fullmatch(
                r"(?:责任编辑|记者|编辑|作者|文/图|记者站)?[：:]?[\u4e00-\u9fa5A-Za-z]{1,12}",
                compact,
            )
        )

    def _footer_text(self) -> str:
        bot_name = self._bot_display_name()
        if bot_name:
            return f"由 {bot_name} 推送"
        return str(self.config.get("footer", "") or "").strip()
