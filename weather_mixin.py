from __future__ import annotations

import asyncio
import http.client
import ipaddress
import json
import socket
import ssl
from typing import Any
from urllib.parse import quote_plus, urlparse

import httpx
from astrbot.api import logger

from .daily_shared import WEATHER_CODE_MAP


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, tls_hostname: str, connect_ip: str, port: int, timeout: float):
        context = ssl.create_default_context()
        super().__init__(host=connect_ip, port=port, timeout=timeout, context=context)
        self._tls_hostname = tls_hostname
        self._connect_ip = connect_ip

    def connect(self):
        sock = socket.create_connection((self._connect_ip, self.port), self.timeout, self.source_address)
        self.sock = self._context.wrap_socket(sock, server_hostname=self._tls_hostname)


class _PinnedHttpResponse:
    def __init__(self, status_code: int, headers: dict[str, str], body: bytes):
        self.status_code = status_code
        self.headers = headers
        self.text = self._decode_body(body, headers)

    @staticmethod
    def _decode_body(body: bytes, headers: dict[str, str]) -> str:
        content_type = str(headers.get("content-type", "") or "")
        encoding = "utf-8"
        for part in content_type.split(";")[1:]:
            part = part.strip()
            if part.lower().startswith("charset="):
                encoding = part.split("=", 1)[1].strip() or "utf-8"
                break
        try:
            return body.decode(encoding, errors="replace")
        except LookupError:
            return body.decode("utf-8", errors="replace")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise ValueError(f"custom weather api request failed with status {self.status_code}")

    def json(self) -> Any:
        return json.loads(self.text)


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
        fields = self._extract_uapi_weather_fields(data, city_name)
        return self._render_weather_summary(fields)

    async def _fetch_open_meteo_weather_summary(self, client: httpx.AsyncClient, city_name: str) -> str | None:
        geo = await self._fetch_city_geo(client, city_name)
        if not geo:
            return f"{city_name}: 暂时无法解析城市坐标。"

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
        fields = self._extract_open_meteo_weather_fields(data, geo, city_name)
        return self._render_weather_summary(fields)

    def _extract_uapi_weather_fields(self, data: dict[str, Any], city_name: str) -> dict[str, Any]:
        return {
            "location_name": (
                self._clean_text(str(data.get("city", "") or ""))
                or self._clean_text(str(data.get("province", "") or ""))
                or city_name
            ),
            "weather_text": self._clean_text(str(data.get("weather", "") or "")) or "未知天气",
            "temp_min": self._safe_float(data.get("temp_min")),
            "temp_max": self._safe_float(data.get("temp_max")),
            "current_temp": self._safe_float(data.get("temperature")),
            "humidity": self._text_value(data.get("humidity")),
            "wind_text": self._weather_wind_text(data.get("wind_direction"), data.get("wind_power")),
            "feels_like": self._safe_float(data.get("feels_like")),
            "aqi_text": self._text_value(data.get("aqi")),
            "aqi_category": self._clean_text(str(data.get("aqi_category", "") or "")),
            "rain_probability": None,
            "sunrise": "",
            "sunset": "",
        }

    def _extract_open_meteo_weather_fields(
        self,
        data: dict[str, Any],
        geo: dict[str, Any],
        city_name: str,
    ) -> dict[str, Any]:
        current = data.get("current", {}) or {}
        daily = data.get("daily", {}) or {}
        weather_code = self._safe_int(self._first_or_default(daily.get("weather_code"), current.get("weather_code")))
        return {
            "location_name": geo.get("display_name") or city_name,
            "weather_text": WEATHER_CODE_MAP.get(weather_code, "未知天气") if weather_code is not None else "未知天气",
            "temp_min": self._safe_float(self._first_or_default(daily.get("temperature_2m_min"))),
            "temp_max": self._safe_float(self._first_or_default(daily.get("temperature_2m_max"))),
            "current_temp": self._safe_float(current.get("temperature_2m")),
            "humidity": "",
            "wind_text": "",
            "feels_like": None,
            "aqi_text": "",
            "aqi_category": "",
            "rain_probability": self._safe_float(self._first_or_default(daily.get("precipitation_probability_max"))),
            "sunrise": self._format_time(self._first_or_default(daily.get("sunrise"), "")),
            "sunset": self._format_time(self._first_or_default(daily.get("sunset"), "")),
        }

    def _render_weather_summary(self, fields: dict[str, Any]) -> str:
        location_name = str(fields.get("location_name") or "").strip()
        weather_text = str(fields.get("weather_text") or "未知天气").strip() or "未知天气"
        parts = [f"{location_name}: {weather_text}"]

        temp_min = fields.get("temp_min")
        temp_max = fields.get("temp_max")
        current_temp = fields.get("current_temp")
        humidity = str(fields.get("humidity") or "").strip()
        wind_text = str(fields.get("wind_text") or "").strip()
        feels_like = fields.get("feels_like")
        rain_probability = fields.get("rain_probability")
        sunrise = str(fields.get("sunrise") or "").strip()
        sunset = str(fields.get("sunset") or "").strip()
        aqi_text = str(fields.get("aqi_text") or "").strip()
        aqi_category = str(fields.get("aqi_category") or "").strip()

        if temp_min is not None and temp_max is not None:
            parts.append(f"{round(temp_min)}~{round(temp_max)}°C")
        if current_temp is not None:
            parts.append(f"当前 {round(current_temp)}°C")
        if humidity:
            parts.append(f"湿度 {humidity}%")
        if wind_text:
            parts.append(wind_text)
        if feels_like is not None:
            parts.append(f"体感 {round(feels_like)}°C")
        if rain_probability is not None:
            parts.append(f"降水概率 {round(rain_probability)}%")
        if sunrise:
            parts.append(f"日出 {sunrise}")
        if sunset:
            parts.append(f"日落 {sunset}")
        if aqi_text:
            aqi_summary = f"AQI {aqi_text}"
            if aqi_category:
                aqi_summary = f"{aqi_summary} {aqi_category}"
            parts.append(aqi_summary)
        return "，".join(parts)

    def _weather_wind_text(self, direction: Any, power: Any) -> str:
        wind_direction = self._clean_text(str(direction or ""))
        wind_power = self._clean_text(str(power or ""))
        return " ".join(part for part in (wind_direction, wind_power) if part)

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
        target = await self._validate_custom_weather_url(request_url)
        headers = self._custom_weather_headers()
        if str(getattr(self, "config", {}).get("http_proxy", "") or "").strip():
            logger.warning("custom weather pinned request ignores http_proxy to prevent DNS rebinding: host=%s", target["hostname"])
        response = await self._fetch_pinned_custom_weather_response(request_url, headers, target)
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

    async def _fetch_pinned_custom_weather_response(
        self,
        request_url: str,
        headers: dict[str, str],
        target: dict[str, Any],
    ) -> _PinnedHttpResponse:
        return await asyncio.to_thread(
            self._perform_pinned_https_get,
            request_url,
            headers,
            target,
            self._custom_weather_timeout_seconds(),
        )

    def _perform_pinned_https_get(
        self,
        request_url: str,
        headers: dict[str, str],
        target: dict[str, Any],
        timeout: int,
    ) -> _PinnedHttpResponse:
        del request_url
        connection = _PinnedHTTPSConnection(
            tls_hostname=target["hostname"],
            connect_ip=target["resolved_ip"],
            port=int(target["port"]),
            timeout=float(timeout),
        )
        request_headers = self._sanitize_pinned_request_headers(headers)
        try:
            connection.putrequest("GET", str(target["request_path"]), skip_host=True, skip_accept_encoding=True)
            connection.putheader("Host", str(target["hostname"]))
            connection.putheader("Accept", "*/*")
            connection.putheader("Accept-Encoding", "identity")
            connection.putheader("Connection", "close")
            for name, value in request_headers.items():
                connection.putheader(name, value)
            connection.endheaders()
            response = connection.getresponse()
            body = self._read_limited_response_body(response, self._custom_weather_max_response_bytes())
            response_headers = {key.lower(): value for key, value in response.getheaders()}
            return _PinnedHttpResponse(response.status, response_headers, body)
        finally:
            connection.close()

    def _custom_weather_timeout_seconds(self) -> int:
        config = getattr(self, "config", {}) or {}
        return max(int(config.get("http_timeout_seconds", 15) or 15), 5)

    def _dns_resolution_timeout_seconds(self) -> int:
        return max(1, min(self._custom_weather_timeout_seconds(), 10))

    def _custom_weather_max_response_bytes(self) -> int:
        return 262144

    @staticmethod
    def _read_limited_response_body(response: Any, max_bytes: int) -> bytes:
        chunks: list[bytes] = []
        total = 0
        chunk_size = min(65536, max_bytes + 1)
        while True:
            chunk = response.read(chunk_size)
            if not chunk:
                return b"".join(chunks)
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"custom weather api response too large: {total} bytes")
            chunks.append(chunk)

    @staticmethod
    def _sanitize_pinned_request_headers(headers: dict[str, str]) -> dict[str, str]:
        sanitized: dict[str, str] = {}
        for name, value in (headers or {}).items():
            key = str(name or "").strip()
            if not key:
                continue
            lowered = key.lower()
            if lowered in {"host", "connection", "accept-encoding", "content-length"}:
                continue
            sanitized[key] = str(value)
        return sanitized

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
        infos = await asyncio.wait_for(
            loop.getaddrinfo(
                hostname,
                None,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            ),
            timeout=self._dns_resolution_timeout_seconds(),
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

    async def _validate_custom_weather_url(self, request_url: str) -> dict[str, Any]:
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

        resolved_ips: set[str]
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            try:
                resolved_ips = await self._resolve_hostname_ips(hostname)
            except asyncio.TimeoutError as exc:
                raise ValueError(f"custom weather api hostname resolution timed out: {hostname}") from exc
            except OSError as exc:
                raise ValueError(f"custom weather api hostname resolution failed: {hostname}") from exc
            if not resolved_ips:
                raise ValueError(f"custom weather api hostname resolved to no addresses: {hostname}")
            for ip_text in resolved_ips:
                self._ensure_public_ip(ip_text)
        else:
            self._ensure_public_ip(hostname)
            resolved_ips = {hostname}

        resolved_ip = sorted(resolved_ips)[0]
        return {
            "hostname": hostname,
            "port": parsed.port or 443,
            "resolved_ip": resolved_ip,
            "request_path": self._request_path_from_url(parsed),
        }

    @staticmethod
    def _request_path_from_url(parsed) -> str:
        path = parsed.path or "/"
        if parsed.query:
            return f"{path}?{parsed.query}"
        return path

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
