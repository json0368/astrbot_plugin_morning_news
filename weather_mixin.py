from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Any
from urllib.parse import quote_plus, urlparse

import httpx
from astrbot.api import logger

try:
    from .daily_shared import WEATHER_CODE_MAP
except ImportError:
    from daily_shared import WEATHER_CODE_MAP


class WeatherMixin:
    async def _fetch_weather_summary(self, client: httpx.AsyncClient, city_name: str) -> str | None:
        provider = self._weather_provider()
        if provider == "uapi":
            try:
                return await self._fetch_uapi_weather_summary(client, city_name)
            except Exception as exc:
                logger.warning("uapi weather failed, falling back to open-meteo: city=%s error=%s", city_name, exc)
                return await self._fetch_open_meteo_weather_summary(client, city_name)
        if provider == "custom":
            try:
                return await self._fetch_custom_weather_summary(client, city_name)
            except Exception as exc:
                logger.warning("custom weather failed, falling back to open-meteo: city=%s error=%s", city_name, exc)
        return await self._fetch_open_meteo_weather_summary(client, city_name)

    async def _fetch_uapi_weather_summary(self, client: httpx.AsyncClient, city_name: str) -> str | None:
        response = await client.get(
            "https://uapis.cn/api/v1/misc/weather",
            params={
                "city": city_name,
                "forecast": "true",
                "extended": "true",
            },
        )
        response.raise_for_status()
        data = response.json()

        location_name = (
            self._clean_text(str(data.get("city", "") or ""))
            or self._clean_text(str(data.get("province", "") or ""))
            or city_name
        )
        weather_text = self._clean_text(str(data.get("weather", "") or "")) or "\u672a\u77e5\u5929\u6c14"
        parts = [f"{location_name}: {weather_text}"]

        temp_min_value = self._safe_float(data.get("temp_min"))
        temp_max_value = self._safe_float(data.get("temp_max"))
        current_temp_value = self._safe_float(data.get("temperature"))
        humidity_text = self._text_value(data.get("humidity"))
        wind_direction = self._clean_text(str(data.get("wind_direction", "") or ""))
        wind_power = self._clean_text(str(data.get("wind_power", "") or ""))
        feels_like_value = self._safe_float(data.get("feels_like"))
        aqi_text_value = self._text_value(data.get("aqi"))
        aqi_category = self._clean_text(str(data.get("aqi_category", "") or ""))

        if temp_min_value is not None and temp_max_value is not None:
            parts.append(f"{round(temp_min_value)}~{round(temp_max_value)}\u00b0C")
        if current_temp_value is not None:
            parts.append(f"\u5f53\u524d {round(current_temp_value)}\u00b0C")
        if humidity_text:
            parts.append(f"\u6e7f\u5ea6 {humidity_text}%")
        if wind_direction or wind_power:
            wind_text = " ".join(part for part in [wind_direction, wind_power] if part)
            if wind_text:
                parts.append(wind_text)
        if feels_like_value is not None:
            parts.append(f"\u4f53\u611f {round(feels_like_value)}\u00b0C")
        if aqi_text_value:
            aqi_text = f"AQI {aqi_text_value}"
            if aqi_category:
                aqi_text = f"{aqi_text} {aqi_category}"
            parts.append(aqi_text)

        return "\uff0c".join(parts)

    async def _fetch_open_meteo_weather_summary(self, client: httpx.AsyncClient, city_name: str) -> str | None:
        geo = await self._fetch_city_geo(client, city_name)
        if not geo:
            return f"{city_name}: \u6682\u65f6\u65e0\u6cd5\u89e3\u6790\u57ce\u5e02\u5750\u6807\u3002"

        response = await client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": geo["latitude"],
                "longitude": geo["longitude"],
                "current": "temperature_2m,weather_code",
                "daily": (
                    "weather_code,temperature_2m_max,temperature_2m_min,"
                    "precipitation_probability_max,sunrise,sunset"
                ),
                "forecast_days": 1,
                "timezone": self._timezone_name(),
            },
        )
        response.raise_for_status()
        data = response.json()

        current = data.get("current", {})
        daily = data.get("daily", {})

        weather_code = self._safe_int(self._first_or_default(daily.get("weather_code"), current.get("weather_code")))
        max_temp = self._safe_float(self._first_or_default(daily.get("temperature_2m_max")))
        min_temp = self._safe_float(self._first_or_default(daily.get("temperature_2m_min")))
        rain_prob_value = self._safe_float(self._first_or_default(daily.get("precipitation_probability_max")))
        sunrise = self._format_time(self._first_or_default(daily.get("sunrise"), ""))
        sunset = self._format_time(self._first_or_default(daily.get("sunset"), ""))
        current_temp = self._safe_float(current.get("temperature_2m"))

        location_name = geo.get("display_name") or city_name
        weather_text = WEATHER_CODE_MAP.get(weather_code, "\u672a\u77e5\u5929\u6c14") if weather_code is not None else "\u672a\u77e5\u5929\u6c14"
        parts = [f"{location_name}: {weather_text}"]

        if min_temp is not None and max_temp is not None:
            parts.append(f"{round(min_temp)}~{round(max_temp)}\u00b0C")
        if current_temp is not None:
            parts.append(f"\u5f53\u524d {round(current_temp)}\u00b0C")
        if rain_prob_value is not None:
            parts.append(f"\u964d\u6c34\u6982\u7387 {round(rain_prob_value)}%")
        if sunrise:
            parts.append(f"\u65e5\u51fa {sunrise}")
        if sunset:
            parts.append(f"\u65e5\u843d {sunset}")

        return "\uff0c".join(parts)

    async def _fetch_custom_weather_summary(self, client: httpx.AsyncClient, city_name: str) -> str | None:
        template = self._custom_weather_api_url()
        if not template:
            raise ValueError("missing custom_weather_api_url")

        geo: dict[str, Any] | None = None
        if any(token in template for token in ("{latitude}", "{longitude}", "{display_name}")):
            geo = await self._fetch_city_geo(client, city_name)

        values = {
            "city": city_name,
            "city_urlencoded": quote_plus(city_name),
            "timezone": self._timezone_name(),
            "latitude": "" if not geo else str(geo.get("latitude", "")),
            "longitude": "" if not geo else str(geo.get("longitude", "")),
            "display_name": city_name if not geo else str(geo.get("display_name") or city_name),
        }
        request_url = self._fill_url_template(template, values)
        await self._validate_custom_weather_url(request_url)

        response = await client.get(
            request_url,
            headers=self._custom_weather_headers(),
            follow_redirects=False,
        )
        response.raise_for_status()

        response_path = self._custom_weather_response_path()
        if response_path:
            data = response.json()
            value = self._extract_data_by_path(data, response_path)
            text = self._text_value(value)
            if text:
                return self._clip_text(text, 300)
            raise ValueError(f"custom weather response path not found: {response_path}")

        content_type = str(response.headers.get("content-type", "") or "").lower()
        if "json" in content_type:
            guessed = self._guess_weather_text_from_json(response.json())
            if guessed:
                return self._clip_text(guessed, 300)
            raise ValueError("custom weather JSON response requires custom_weather_response_path")

        text = self._clean_text(response.text)
        return self._clip_text(text, 300) if text else None

    def _custom_weather_allowed_domains(self) -> set[str]:
        config = getattr(self, "config", {}) or {}
        raw = str(config.get("custom_weather_allowed_domains", "") or "")
        return {
            line.strip().lower().rstrip(".")
            for line in raw.splitlines()
            if line.strip()
        }

    @staticmethod
    def _hostname_allowed(hostname: str, allowed_domains: set[str]) -> bool:
        if not allowed_domains:
            return False
        return any(hostname == domain or hostname.endswith("." + domain) for domain in allowed_domains)

    async def _resolve_hostname_ips(self, hostname: str) -> set[str]:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(
            hostname,
            None,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
        return {
            sockaddr[0]
            for *_, sockaddr in infos
            if isinstance(sockaddr, tuple) and sockaddr
        }

    @staticmethod
    def _ensure_public_ip(ip_text: str):
        ip = ipaddress.ip_address(ip_text)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError(f"custom weather api cannot target local/private IP: {ip}")

    async def _validate_custom_weather_url(self, request_url: str):
        parsed = urlparse(str(request_url or "").strip())
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("custom weather api url must use https and include a hostname")
        if parsed.port not in (None, 443):
            raise ValueError("custom weather api url must use port 443")

        hostname = parsed.hostname.strip().lower().rstrip(".")
        if hostname == "localhost":
            raise ValueError("custom weather api url cannot target localhost")

        allowed_domains = self._custom_weather_allowed_domains()
        if not allowed_domains:
            raise ValueError("custom weather allowed domains must be configured")
        if not self._hostname_allowed(hostname, allowed_domains):
            raise ValueError(f"custom weather api hostname is not allowlisted: {hostname}")

        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            try:
                resolved_ips = await self._resolve_hostname_ips(hostname)
            except OSError as exc:
                raise ValueError(f"custom weather api hostname resolution failed: {hostname}") from exc
            if not resolved_ips:
                raise ValueError(f"custom weather api hostname resolved to no addresses: {hostname}")
            for ip_text in resolved_ips:
                self._ensure_public_ip(ip_text)
        else:
            self._ensure_public_ip(hostname)

    async def _fetch_city_geo(self, client: httpx.AsyncClient, city_name: str) -> dict[str, Any] | None:
        cache_key = city_name.strip().casefold()
        cached = self._geo_cache.pop(cache_key, None)
        if cached is not None:
            self._geo_cache[cache_key] = cached
            return cached.copy()

        response = await client.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={
                "name": city_name,
                "count": 1,
                "language": "zh",
                "format": "json",
            },
        )
        response.raise_for_status()
        data = response.json()
        results = data.get("results") or []
        if not results:
            return None

        item = results[0]
        latitude = self._safe_float(item.get("latitude"))
        longitude = self._safe_float(item.get("longitude"))
        if latitude is None or longitude is None:
            logger.warning("geocoding result missing coordinates: city=%s item=%s", city_name, item)
            return None

        display_name = self._clean_text(str(item.get("name", city_name) or city_name))
        admin1 = self._clean_text(str(item.get("admin1") or ""))
        country = self._clean_text(str(item.get("country") or ""))
        if admin1 and admin1 != display_name:
            display_name = f"{display_name}, {admin1}"
        if country:
            display_name = f"{display_name}, {country}"

        result = {
            "latitude": latitude,
            "longitude": longitude,
            "display_name": display_name,
        }
        self._remember_geo_cache(cache_key, result)
        return result.copy()
