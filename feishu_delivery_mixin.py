from __future__ import annotations

import importlib
import json
import uuid
from typing import Any

from astrbot.api import logger
from astrbot.api.event import MessageChain


class FeishuDeliveryMixin:
    async def _deliver_payload_to_event(self, event: Any, payload: dict[str, Any]) -> Any | None:
        if self._payload_has_card(payload) and self._is_feishu_event(event):
            event_error: Exception | None = None
            try:
                if await self._send_feishu_card_with_event(event, payload["card"]):
                    self._mark_event_as_handled(event)
                    return None
            except Exception as exc:
                event_error = exc

            if event_error is not None:
                logger.warning(
                    "飞书卡片发送失败，回退纯文本: scope=event event_error=%s",
                    event_error,
                )
        return event.plain_result(self._payload_text(payload))

    async def _deliver_payload_to_subscription(
        self,
        unified_msg_origin: str,
        payload: dict[str, Any],
        subscription: dict[str, Any] | None = None,
    ) -> bool:
        subscription = subscription or {}
        if self._payload_has_card(payload):
            try:
                if await self._send_feishu_card_to_subscription(
                    unified_msg_origin,
                    subscription,
                    payload["card"],
                ):
                    return True
            except Exception as exc:
                logger.warning(
                    "飞书主动卡片发送失败，回退纯文本: scope=subscription target=%s error=%s",
                    unified_msg_origin,
                    exc,
                )

        chain_builder = getattr(self, "_build_message_chain", None)
        if callable(chain_builder):
            chain = chain_builder(payload)
        else:
            chain = MessageChain().message(self._payload_text(payload))
        await self.context.send_message(unified_msg_origin, chain)
        return True

    def _payload_text(self, payload: dict[str, Any]) -> str:
        return str(payload.get("text") or payload.get("content") or "")

    def _payload_has_card(self, payload: dict[str, Any]) -> bool:
        return bool(self._feishu_card_enabled() and payload.get("card"))

    def _feishu_card_enabled(self) -> bool:
        return bool(self.config.get("feishu_card_enabled", True))

    def _mark_event_as_handled(self, event: Any):
        stop_event = getattr(event, "stop_event", None)
        if callable(stop_event):
            try:
                stop_event()
            except Exception:
                pass

        should_call_llm = getattr(event, "should_call_llm", None)
        if callable(should_call_llm):
            try:
                should_call_llm(True)
            except Exception:
                pass

    def _transport_snapshot_from_event(self, event: Any) -> dict[str, str]:
        snapshot: dict[str, str] = {}
        platform_id = self._event_platform_id(event)
        if platform_id:
            snapshot["platform_id"] = platform_id

        target = self._target_from_event(event)
        if target:
            snapshot.update(target)
        return snapshot

    def _is_feishu_event(self, event: Any) -> bool:
        signature_parts = [
            getattr(event.__class__, "__module__", ""),
            getattr(event.__class__, "__name__", ""),
            str(getattr(getattr(event, "platform_meta", None), "name", "") or ""),
            str(getattr(event, "platform_name", "") or ""),
        ]
        signature = " ".join(signature_parts).lower()
        return (
            "lark" in signature
            or "feishu" in signature
            or hasattr(event, "_send_im_message")
            or hasattr(event, "_send_card_message")
        )

    def _event_platform_id(self, event: Any) -> str:
        getter = getattr(event, "get_platform_id", None)
        if callable(getter):
            try:
                value = getter()
                if value:
                    return str(value)
            except Exception:
                pass

        for candidate in (
            getattr(event, "platform_id", None),
            getattr(getattr(event, "platform_meta", None), "id", None),
            getattr(getattr(event, "bot", None), "platform_id", None),
        ):
            if candidate:
                return str(candidate)
        return ""

    def _target_from_event(self, event: Any) -> dict[str, str]:
        message_obj = getattr(event, "message_obj", None)
        raw_message = getattr(message_obj, "raw_message", None)
        if not isinstance(raw_message, dict):
            raw_message = {}

        origin_target = self._target_from_origin(getattr(event, "unified_msg_origin", ""))
        receive_id = self._first_non_empty(
            getattr(event, "receive_id", None),
            getattr(message_obj, "group_id", None),
            getattr(message_obj, "chat_id", None),
            getattr(message_obj, "receive_id", None),
            getattr(message_obj, "session_id", None),
            raw_message.get("chat_id"),
            raw_message.get("open_id"),
            raw_message.get("user_id"),
            origin_target.get("receive_id", ""),
        )
        receive_id_type = self._first_non_empty(
            getattr(event, "receive_id_type", None),
            getattr(message_obj, "receive_id_type", None),
            self._receive_id_type_from_message_obj(message_obj),
            raw_message.get("receive_id_type"),
            self._infer_receive_id_type(receive_id),
            origin_target.get("receive_id_type", ""),
        )
        if receive_id and receive_id_type:
            return {
                "receive_id": str(receive_id),
                "receive_id_type": str(receive_id_type),
            }
        return {}

    def _target_from_subscription(
        self,
        unified_msg_origin: str,
        subscription: dict[str, Any],
    ) -> dict[str, str]:
        target = {
            "platform_id": str(subscription.get("platform_id") or ""),
            "receive_id": str(subscription.get("receive_id") or ""),
            "receive_id_type": str(subscription.get("receive_id_type") or ""),
        }
        if target["receive_id"] and not target["receive_id_type"]:
            target["receive_id_type"] = self._infer_receive_id_type(target["receive_id"])
        if target["platform_id"] and target["receive_id"] and target["receive_id_type"]:
            return target

        fallback = self._target_from_origin(unified_msg_origin)
        if not target["receive_id"]:
            target["receive_id"] = fallback.get("receive_id", "")
        if not target["receive_id_type"]:
            target["receive_id_type"] = fallback.get("receive_id_type", "")
        return target

    def _target_from_origin(self, unified_msg_origin: str) -> dict[str, str]:
        value = str(unified_msg_origin or "").strip()
        if not value:
            return {}

        if "/" in value:
            prefix = value.split("/", 1)[0].strip()
            receive_id_type = self._infer_receive_id_type(prefix)
            if prefix and receive_id_type:
                return {
                    "receive_id": prefix,
                    "receive_id_type": receive_id_type,
                }

        parts = value.split(":")
        if len(parts) >= 3:
            session_id = parts[-1].strip()
            message_scope = parts[-2].strip().lower()
            if session_id:
                receive_id_type = self._infer_receive_id_type(session_id)
                if not receive_id_type:
                    if message_scope == "group":
                        receive_id_type = "chat_id"
                    elif message_scope == "private":
                        receive_id_type = "open_id"
                if receive_id_type:
                    return {
                        "receive_id": session_id,
                        "receive_id_type": receive_id_type,
                    }

        receive_id_type = self._infer_receive_id_type(value)
        if receive_id_type:
            return {
                "receive_id": value,
                "receive_id_type": receive_id_type,
            }
        return {}

    def _receive_id_type_from_message_obj(self, message_obj: Any) -> str:
        if message_obj is None:
            return ""
        group_id = getattr(message_obj, "group_id", None)
        if group_id:
            return "chat_id"
        session_id = getattr(message_obj, "session_id", None)
        if session_id:
            return self._infer_receive_id_type(str(session_id))
        return ""

    @staticmethod
    def _infer_receive_id_type(receive_id: str) -> str:
        value = str(receive_id or "").strip()
        if value.startswith("oc_"):
            return "chat_id"
        if value.startswith("ou_"):
            return "open_id"
        if value.startswith("on_"):
            return "union_id"
        if value.startswith("u_"):
            return "user_id"
        return ""

    @staticmethod
    def _first_non_empty(*values: Any) -> str:
        for value in values:
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

    async def _send_feishu_card_with_event(self, event: Any, card: dict[str, Any]) -> bool:
        target = self._target_from_event(event)
        return await self._send_feishu_card_with_context(
            scope="event",
            platform_id=self._event_platform_id(event),
            receive_id=target.get("receive_id", ""),
            receive_id_type=target.get("receive_id_type", ""),
            card=card,
        )

    async def _send_feishu_card_to_subscription(
        self,
        unified_msg_origin: str,
        subscription: dict[str, Any],
        card: dict[str, Any],
    ) -> bool:
        target = self._target_from_subscription(unified_msg_origin, subscription)
        return await self._send_feishu_card_with_context(
            scope="subscription",
            platform_id=target.get("platform_id", ""),
            receive_id=target.get("receive_id", ""),
            receive_id_type=target.get("receive_id_type", ""),
            card=card,
        )

    async def _send_feishu_card_with_context(
        self,
        *,
        scope: str,
        platform_id: str,
        receive_id: str,
        receive_id_type: str,
        card: dict[str, Any],
    ) -> bool:
        if not receive_id or not receive_id_type:
            self._log_card_capability_fallback(
                scope=scope,
                reason="missing_receive_target",
                platform_id=platform_id,
                receive_id=receive_id,
                receive_id_type=receive_id_type,
            )
            return False
        if not platform_id:
            self._log_card_capability_fallback(
                scope=scope,
                reason="missing_platform_id",
                platform_id=platform_id,
                receive_id=receive_id,
                receive_id_type=receive_id_type,
            )
            return False

        get_platform_inst = getattr(self.context, "get_platform_inst", None)
        if not callable(get_platform_inst):
            self._log_card_capability_fallback(
                scope=scope,
                reason="missing_get_platform_inst",
                platform_id=platform_id,
                receive_id=receive_id,
                receive_id_type=receive_id_type,
            )
            return False

        platform_inst = get_platform_inst(platform_id)
        if not platform_inst:
            self._log_card_capability_fallback(
                scope=scope,
                reason="missing_platform_instance",
                platform_id=platform_id,
                receive_id=receive_id,
                receive_id_type=receive_id_type,
            )
            return False

        lark_api = self._resolve_lark_api_client(platform_inst)
        if lark_api is None:
            self._log_card_capability_fallback(
                scope=scope,
                reason="missing_lark_api",
                platform_id=platform_id,
                receive_id=receive_id,
                receive_id_type=receive_id_type,
            )
            return False

        create_message = self._resolve_lark_message_api(lark_api)
        if create_message is None:
            self._log_card_capability_fallback(
                scope=scope,
                reason="missing_message_api",
                platform_id=platform_id,
                receive_id=receive_id,
                receive_id_type=receive_id_type,
            )
            return False

        request = self._build_lark_card_request(receive_id, receive_id_type, card)
        if request is None:
            self._log_card_capability_fallback(
                scope=scope,
                reason="missing_lark_sdk",
                platform_id=platform_id,
                receive_id=receive_id,
                receive_id_type=receive_id_type,
            )
            return False

        response = await create_message(request)
        if hasattr(response, "success") and callable(response.success) and not response.success():
            logger.warning(
                "飞书卡片发送失败，回退纯文本: scope=%s reason=api_error code=%s msg=%s platform_id=%s receive_id=%s receive_id_type=%s",
                scope,
                getattr(response, "code", ""),
                getattr(response, "msg", ""),
                platform_id,
                receive_id,
                receive_id_type,
            )
            return False
        return True

    @staticmethod
    def _resolve_lark_api_client(platform_inst: Any) -> Any:
        lark_api = getattr(platform_inst, "lark_api", None)
        return lark_api if lark_api is not None else None

    @staticmethod
    def _resolve_lark_message_api(lark_api: Any):
        im = getattr(lark_api, "im", None)
        v1 = getattr(im, "v1", None)
        message = getattr(v1, "message", None)
        create_message = getattr(message, "acreate", None)
        return create_message if callable(create_message) else None

    @staticmethod
    def _build_lark_card_request(receive_id: str, receive_id_type: str, card: dict[str, Any]) -> Any | None:
        try:
            module = importlib.import_module("lark_oapi.api.im.v1")
        except Exception:
            return None

        request_cls = getattr(module, "CreateMessageRequest", None)
        body_cls = getattr(module, "CreateMessageRequestBody", None)
        if request_cls is None or body_cls is None:
            return None
        if not hasattr(request_cls, "builder") or not hasattr(body_cls, "builder"):
            return None

        content = json.dumps(card, ensure_ascii=False)
        body_builder = body_cls.builder()
        request_builder = request_cls.builder()
        try:
            request = (
                request_builder.receive_id_type(receive_id_type)
                .request_body(
                    body_builder
                    .receive_id(receive_id)
                    .content(content)
                    .msg_type("interactive")
                    .uuid(str(uuid.uuid4()))
                    .build()
                )
                .build()
            )
        except Exception:
            return None
        return request

    @staticmethod
    def _log_card_capability_fallback(
        *,
        scope: str,
        reason: str,
        platform_id: str,
        receive_id: str,
        receive_id_type: str,
    ):
        logger.warning(
            "飞书卡片发送能力不足，回退纯文本: scope=%s reason=%s platform_id=%s receive_id=%s receive_id_type=%s",
            scope,
            reason,
            platform_id,
            receive_id,
            receive_id_type,
        )
