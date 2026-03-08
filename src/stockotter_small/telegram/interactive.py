from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from stockotter_small.telegram.briefing import BriefingCandidate
from stockotter_v2.schemas import (
    OrderIntent,
    TelegramAction,
    TelegramActionStatus,
    TelegramActionType,
    now_in_seoul,
)
from stockotter_v2.storage import Repository

_ACTION_PREFIX = "A"
_VALID_ACTION_TYPES = {
    TelegramActionType.BUY.value,
    TelegramActionType.SELL.value,
    TelegramActionType.SKIP.value,
}


@dataclass(frozen=True)
class CallbackEnvelope:
    callback_query_id: str
    action_id: str


@dataclass(frozen=True)
class CallbackProcessResult:
    action: TelegramAction
    created_intent: OrderIntent | None


def build_inline_keyboard_and_actions(
    *,
    candidates: list[BriefingCandidate],
    now: datetime | None = None,
) -> tuple[dict[str, object], list[TelegramAction]]:
    if not candidates:
        raise ValueError("candidates must not be empty")

    created_at = now or now_in_seoul()
    inline_keyboard: list[list[dict[str, str]]] = []
    actions: list[TelegramAction] = []
    for candidate in candidates:
        row: list[dict[str, str]] = []
        for action_type in (
            TelegramActionType.BUY,
            TelegramActionType.SELL,
            TelegramActionType.SKIP,
        ):
            action_id = _build_action_id(action_type)
            action = TelegramAction(
                action_id=action_id,
                action_type=action_type,
                ticker=candidate.ticker,
                created_at=created_at,
                status=TelegramActionStatus.PENDING,
            )
            actions.append(action)
            row.append(
                {
                    "text": action_type.value.upper(),
                    "callback_data": action_id,
                }
            )
        inline_keyboard.append(row)
    return {"inline_keyboard": inline_keyboard}, actions


def persist_tg_actions(
    *,
    repo: Repository,
    actions: list[TelegramAction],
    message_id: int | None,
) -> None:
    for action in actions:
        repo.insert_tg_action(action.model_copy(update={"message_id": message_id}))


def parse_callback_update(raw_payload: str) -> CallbackEnvelope:
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid callback update JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("callback update payload must be an object")

    callback_query = payload.get("callback_query")
    if not isinstance(callback_query, dict):
        raise ValueError("callback update missing callback_query")

    callback_query_id = str(callback_query.get("id") or "").strip()
    action_id = str(callback_query.get("data") or "").strip()
    if not callback_query_id:
        raise ValueError("callback query id is missing")
    _validate_action_id(action_id)
    return CallbackEnvelope(
        callback_query_id=callback_query_id,
        action_id=action_id,
    )


def process_callback_action(
    *,
    repo: Repository,
    action_id: str,
    callback_query_id: str,
    now: datetime | None = None,
) -> CallbackProcessResult:
    action = repo.get_tg_action(action_id)
    if action is None:
        raise ValueError(f"telegram action not found action_id={action_id}")

    repo.update_tg_action_ack(
        action_id=action_id,
        callback_query_id=callback_query_id,
    )
    acked_action = repo.get_tg_action(action_id)
    assert acked_action is not None

    if acked_action.action_type is TelegramActionType.SKIP:
        return CallbackProcessResult(action=acked_action, created_intent=None)

    existing_intent = repo.get_order_intent_by_action(action_id)
    if existing_intent is not None:
        return CallbackProcessResult(action=acked_action, created_intent=existing_intent)

    created_at = now or now_in_seoul()
    intent = OrderIntent(
        intent_id=_build_order_intent_id(created_at=created_at, action_id=action_id),
        action_id=action_id,
        action_type=acked_action.action_type,
        ticker=acked_action.ticker,
        quantity=acked_action.quantity,
        cash_amount=acked_action.cash_amount,
        is_dry_run=True,
        note="created from telegram callback; dry-run only",
        created_at=created_at,
    )
    repo.insert_order_intent(intent)
    return CallbackProcessResult(action=acked_action, created_intent=intent)


def _build_action_id(action_type: TelegramActionType) -> str:
    return f"{_ACTION_PREFIX}:{action_type.value}:{uuid4().hex[:8]}"


def _build_order_intent_id(*, created_at: datetime, action_id: str) -> str:
    return f"intent-{created_at.strftime('%Y%m%d%H%M%S%f')}-{action_id.split(':')[-1]}"


def _validate_action_id(action_id: str) -> None:
    parts = action_id.split(":")
    if len(parts) != 3:
        raise ValueError("invalid callback action_id format")
    prefix, action_type, suffix = parts
    if prefix != _ACTION_PREFIX:
        raise ValueError("invalid callback action_id prefix")
    if action_type not in _VALID_ACTION_TYPES:
        raise ValueError("invalid callback action type")
    if len(suffix) != 8 or any(character not in "0123456789abcdef" for character in suffix):
        raise ValueError("invalid callback action suffix")
