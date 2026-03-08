from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import requests


class TelegramClientError(RuntimeError):
    """Base error for Telegram sender failures."""


class TelegramAuthError(TelegramClientError):
    """Authentication/authorization failure."""


class TelegramAPIError(TelegramClientError):
    """Telegram API request failure."""


@dataclass(frozen=True)
class TelegramSendResult:
    message_id: int | None


@dataclass(frozen=True)
class TelegramCallbackAckResult:
    ok: bool


@dataclass(frozen=True)
class TelegramEditResult:
    ok: bool


@dataclass(frozen=True)
class TelegramGetUpdatesResult:
    updates: list[dict[str, Any]]


class TelegramClient:
    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        timeout_seconds: float = 10.0,
        session: requests.Session | None = None,
    ) -> None:
        normalized_token = bot_token.strip()
        normalized_chat_id = chat_id.strip()
        if not normalized_token:
            raise ValueError("TELEGRAM_BOT_TOKEN must not be empty")
        if not normalized_chat_id:
            raise ValueError("TELEGRAM_CHAT_ID must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")

        self.bot_token = normalized_token
        self.chat_id = normalized_chat_id
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()

    @classmethod
    def from_env(cls) -> TelegramClient:
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        missing = [
            key
            for key, value in {
                "TELEGRAM_BOT_TOKEN": bot_token,
                "TELEGRAM_CHAT_ID": chat_id,
            }.items()
            if not value
        ]
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
        return cls(bot_token=bot_token, chat_id=chat_id)

    def send_message(
        self,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> TelegramSendResult:
        normalized_text = text.strip()
        if not normalized_text:
            raise ValueError("telegram message text must not be empty")

        payload = {
            "chat_id": self.chat_id,
            "text": normalized_text,
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        try:
            response = self.session.post(
                self._build_endpoint("sendMessage"),
                json=payload,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise TelegramAPIError("telegram request failed") from exc

        payload = _safe_json(response)
        description = _extract_description(payload)
        if response.status_code in {401, 403}:
            raise TelegramAuthError(
                f"telegram auth failed status={response.status_code} detail={description}"
            )
        if response.status_code >= 400:
            raise TelegramAPIError(
                f"telegram sendMessage failed status={response.status_code} detail={description}"
            )
        if not payload.get("ok", False):
            raise TelegramAPIError(f"telegram sendMessage rejected detail={description}")

        result = payload.get("result")
        message_id: int | None = None
        if isinstance(result, dict):
            raw_message_id = result.get("message_id")
            if isinstance(raw_message_id, int):
                message_id = raw_message_id
        return TelegramSendResult(message_id=message_id)

    def answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: str = "received",
    ) -> TelegramCallbackAckResult:
        normalized_id = callback_query_id.strip()
        if not normalized_id:
            raise ValueError("callback_query_id must not be empty")

        try:
            response = self.session.post(
                self._build_endpoint("answerCallbackQuery"),
                json={
                    "callback_query_id": normalized_id,
                    "text": text,
                },
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise TelegramAPIError("telegram callback acknowledgement failed") from exc

        payload = _safe_json(response)
        description = _extract_description(payload)
        if response.status_code in {401, 403}:
            raise TelegramAuthError(
                f"telegram auth failed status={response.status_code} detail={description}"
            )
        if response.status_code >= 400:
            raise TelegramAPIError(
                "telegram answerCallbackQuery failed "
                f"status={response.status_code} detail={description}"
            )
        if not payload.get("ok", False):
            raise TelegramAPIError(
                f"telegram callback acknowledgement rejected detail={description}"
            )
        return TelegramCallbackAckResult(ok=True)

    def edit_message_text(
        self,
        *,
        message_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> TelegramEditResult:
        normalized_text = text.strip()
        if message_id <= 0:
            raise ValueError("message_id must be > 0")
        if not normalized_text:
            raise ValueError("telegram edited message text must not be empty")

        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "message_id": message_id,
            "text": normalized_text,
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        try:
            response = self.session.post(
                self._build_endpoint("editMessageText"),
                json=payload,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise TelegramAPIError("telegram editMessageText failed") from exc

        response_payload = _safe_json(response)
        description = _extract_description(response_payload)
        if response.status_code in {401, 403}:
            raise TelegramAuthError(
                f"telegram auth failed status={response.status_code} detail={description}"
            )
        if response.status_code >= 400:
            raise TelegramAPIError(
                "telegram editMessageText failed "
                f"status={response.status_code} detail={description}"
            )
        if not response_payload.get("ok", False):
            raise TelegramAPIError(f"telegram editMessageText rejected detail={description}")
        return TelegramEditResult(ok=True)

    def get_updates(
        self,
        *,
        offset: int | None = None,
        timeout: int = 20,
        allowed_updates: list[str] | None = None,
    ) -> TelegramGetUpdatesResult:
        if offset is not None and offset < 0:
            raise ValueError("offset must be >= 0")
        if timeout < 0:
            raise ValueError("timeout must be >= 0")

        params: dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        if allowed_updates is not None:
            params["allowed_updates"] = json.dumps(allowed_updates, ensure_ascii=False)

        try:
            response = self.session.get(
                self._build_endpoint("getUpdates"),
                params=params,
                timeout=max(self.timeout_seconds, float(timeout) + 5.0),
            )
        except requests.RequestException as exc:
            raise TelegramAPIError("telegram getUpdates failed") from exc

        payload = _safe_json(response)
        description = _extract_description(payload)
        if response.status_code in {401, 403}:
            raise TelegramAuthError(
                f"telegram auth failed status={response.status_code} detail={description}"
            )
        if response.status_code >= 400:
            raise TelegramAPIError(
                f"telegram getUpdates failed status={response.status_code} detail={description}"
            )
        if not payload.get("ok", False):
            raise TelegramAPIError(f"telegram getUpdates rejected detail={description}")

        raw_results = payload.get("result")
        if not isinstance(raw_results, list):
            raise TelegramAPIError("telegram getUpdates returned non-list result")
        updates = [item for item in raw_results if isinstance(item, dict)]
        return TelegramGetUpdatesResult(updates=updates)

    def _build_endpoint(self, method_name: str) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}/{method_name}"


def _safe_json(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _extract_description(payload: dict[str, Any]) -> str:
    raw_description = payload.get("description")
    if isinstance(raw_description, str) and raw_description.strip():
        return raw_description.strip()
    return "unknown"
