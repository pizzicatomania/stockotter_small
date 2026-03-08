from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from stockotter_small.telegram.briefing import BriefingCandidate
from stockotter_v2.schemas import (
    BrokerOrder,
    OrderIntent,
    OrderIntentStatus,
    OrderStatus,
    TelegramAction,
    TelegramActionStatus,
    TelegramActionType,
    now_in_seoul,
)
from stockotter_v2.storage import Repository

_ACTION_PREFIX = "A"
_STATUS_MARKER = "[Telegram Action]"
_VALID_ACTION_TYPES = {
    TelegramActionType.BUY.value,
    TelegramActionType.SELL.value,
    TelegramActionType.SKIP.value,
    TelegramActionType.CONFIRM_BUY.value,
    TelegramActionType.CONFIRM_SELL.value,
    TelegramActionType.CANCEL.value,
}
_TERMINAL_ACTION_STATUSES = {
    TelegramActionStatus.EXECUTED,
    TelegramActionStatus.FAILED,
    TelegramActionStatus.CANCELLED,
    TelegramActionStatus.SKIPPED,
}


@dataclass(frozen=True)
class CallbackEnvelope:
    callback_query_id: str
    action_id: str
    message_id: int
    message_text: str


@dataclass(frozen=True)
class CallbackExecutionRequest:
    trigger_action: TelegramAction
    parent_action: TelegramAction
    intent: OrderIntent


@dataclass(frozen=True)
class CallbackProcessResult:
    action: TelegramAction
    intent: OrderIntent | None
    execution_request: CallbackExecutionRequest | None
    message_text: str
    reply_markup: dict[str, object] | None


@dataclass(frozen=True)
class CallbackFinalizeResult:
    action: TelegramAction
    intent: OrderIntent | None
    message_text: str
    reply_markup: dict[str, object] | None
    order: BrokerOrder | None


def build_inline_keyboard_and_actions(
    *,
    candidates: list[BriefingCandidate],
    buy_cash_amount: int = 100_000,
    sell_quantity: int = 1,
    now: datetime | None = None,
) -> tuple[dict[str, object], list[TelegramAction]]:
    if not candidates:
        raise ValueError("candidates must not be empty")
    if buy_cash_amount < 1:
        raise ValueError("buy_cash_amount must be >= 1")
    if sell_quantity < 1:
        raise ValueError("sell_quantity must be >= 1")

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
                quantity=sell_quantity if action_type is TelegramActionType.SELL else None,
                cash_amount=buy_cash_amount if action_type is TelegramActionType.BUY else None,
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

    message = callback_query.get("message")
    if not isinstance(message, dict):
        raise ValueError("callback update missing message")

    message_id = _parse_message_id(message.get("message_id"))
    message_text = str(message.get("text") or message.get("caption") or "").strip()
    return CallbackEnvelope(
        callback_query_id=callback_query_id,
        action_id=action_id,
        message_id=message_id,
        message_text=message_text,
    )


def process_callback_action(
    *,
    repo: Repository,
    action_id: str,
    callback_query_id: str,
    message_id: int,
    message_text: str,
    environment: str,
    paper_one_step_enabled: bool,
    default_buy_cash_amount: int,
    default_sell_quantity: int,
    now: datetime | None = None,
) -> CallbackProcessResult:
    action = repo.get_tg_action(action_id)
    if action is None:
        raise ValueError(f"telegram action not found action_id={action_id}")
    if action.message_id is not None and action.message_id != message_id:
        raise ValueError(
            "telegram callback message_id mismatch "
            f"action_message_id={action.message_id} callback_message_id={message_id}"
        )

    current_time = now or now_in_seoul()
    normalized_environment = environment.strip().lower() or "paper"
    if action.status in _TERMINAL_ACTION_STATUSES:
        return CallbackProcessResult(
            action=action,
            intent=_get_related_intent(repo=repo, action=action),
            execution_request=None,
            message_text=_render_status_message(
                message_text=message_text,
                ticker=action.ticker,
                action_type=action.action_type,
                status_label=action.status.value,
                detail="already processed",
            ),
            reply_markup=None,
        )

    if action.action_type is TelegramActionType.SKIP:
        repo.update_tg_action_status(
            action_id=action.action_id,
            status=TelegramActionStatus.SKIPPED,
            callback_query_id=callback_query_id,
        )
        skipped_action = _require_action(repo, action.action_id)
        return CallbackProcessResult(
            action=skipped_action,
            intent=None,
            execution_request=None,
            message_text=_render_status_message(
                message_text=message_text,
                ticker=skipped_action.ticker,
                action_type=skipped_action.action_type,
                status_label="skipped",
                detail="candidate skipped",
            ),
            reply_markup=None,
        )

    if action.action_type is TelegramActionType.CANCEL:
        return _process_cancel_action(
            repo=repo,
            action=action,
            callback_query_id=callback_query_id,
            message_text=message_text,
        )

    if action.action_type in {TelegramActionType.CONFIRM_BUY, TelegramActionType.CONFIRM_SELL}:
        return _process_confirm_action(
            repo=repo,
            action=action,
            callback_query_id=callback_query_id,
            message_text=message_text,
            current_time=current_time,
            default_buy_cash_amount=default_buy_cash_amount,
            default_sell_quantity=default_sell_quantity,
        )

    if action.action_type not in {TelegramActionType.BUY, TelegramActionType.SELL}:
        raise ValueError(f"unsupported telegram action type: {action.action_type.value}")

    if normalized_environment == "paper" and paper_one_step_enabled:
        repo.update_tg_action_status(
            action_id=action.action_id,
            status=TelegramActionStatus.ACKED,
            callback_query_id=callback_query_id,
        )
        parent_action = _require_action(repo, action.action_id)
        intent = _upsert_order_intent(
            repo=repo,
            action=parent_action,
            status=OrderIntentStatus.CREATED,
            note="telegram paper one-step execution requested",
            is_dry_run=False,
            current_time=current_time,
            default_buy_cash_amount=default_buy_cash_amount,
            default_sell_quantity=default_sell_quantity,
        )
        return CallbackProcessResult(
            action=parent_action,
            intent=intent,
            execution_request=CallbackExecutionRequest(
                trigger_action=parent_action,
                parent_action=parent_action,
                intent=intent,
            ),
            message_text=_render_status_message(
                message_text=message_text,
                ticker=parent_action.ticker,
                action_type=parent_action.action_type,
                status_label="executing",
                detail="paper one-step execution started",
            ),
            reply_markup=None,
        )

    if action.status is TelegramActionStatus.CONFIRM_PENDING:
        parent_action = _require_action(repo, action.action_id)
        intent = _upsert_order_intent(
            repo=repo,
            action=parent_action,
            status=OrderIntentStatus.AWAITING_CONFIRMATION,
            note="awaiting telegram confirmation",
            is_dry_run=True,
            current_time=current_time,
            default_buy_cash_amount=default_buy_cash_amount,
            default_sell_quantity=default_sell_quantity,
        )
        confirm_action, cancel_action = _get_or_create_confirmation_actions(
            repo=repo,
            parent_action=parent_action,
            current_time=current_time,
            default_buy_cash_amount=default_buy_cash_amount,
            default_sell_quantity=default_sell_quantity,
        )
        return CallbackProcessResult(
            action=parent_action,
            intent=intent,
            execution_request=None,
            message_text=_render_status_message(
                message_text=message_text,
                ticker=parent_action.ticker,
                action_type=parent_action.action_type,
                status_label="awaiting_confirmation",
                detail=f"Confirm {parent_action.action_type.value.upper()}?",
            ),
            reply_markup=_build_confirmation_reply_markup(
                confirm_action=confirm_action,
                cancel_action=cancel_action,
            ),
        )

    repo.update_tg_action_status(
        action_id=action.action_id,
        status=TelegramActionStatus.CONFIRM_PENDING,
        callback_query_id=callback_query_id,
    )
    parent_action = _require_action(repo, action.action_id)
    intent = _upsert_order_intent(
        repo=repo,
        action=parent_action,
        status=OrderIntentStatus.AWAITING_CONFIRMATION,
        note="awaiting telegram confirmation",
        is_dry_run=True,
        current_time=current_time,
        default_buy_cash_amount=default_buy_cash_amount,
        default_sell_quantity=default_sell_quantity,
    )
    confirm_action, cancel_action = _get_or_create_confirmation_actions(
        repo=repo,
        parent_action=parent_action,
        current_time=current_time,
        default_buy_cash_amount=default_buy_cash_amount,
        default_sell_quantity=default_sell_quantity,
    )
    return CallbackProcessResult(
        action=parent_action,
        intent=intent,
        execution_request=None,
        message_text=_render_status_message(
            message_text=message_text,
            ticker=parent_action.ticker,
            action_type=parent_action.action_type,
            status_label="awaiting_confirmation",
            detail=f"Confirm {parent_action.action_type.value.upper()}?",
        ),
        reply_markup=_build_confirmation_reply_markup(
            confirm_action=confirm_action,
            cancel_action=cancel_action,
        ),
    )


def finalize_callback_execution(
    *,
    repo: Repository,
    execution_request: CallbackExecutionRequest,
    message_text: str,
    order: BrokerOrder | None,
    error_message: str | None = None,
) -> CallbackFinalizeResult:
    parent_action_id = execution_request.parent_action.action_id
    trigger_action_id = execution_request.trigger_action.action_id
    is_success = order is not None and order.status is OrderStatus.SUBMITTED
    action_status = TelegramActionStatus.EXECUTED if is_success else TelegramActionStatus.FAILED
    intent_status = OrderIntentStatus.EXECUTED if is_success else OrderIntentStatus.REJECTED
    detail = _resolve_execution_detail(order=order, error_message=error_message)

    for current_action_id in {parent_action_id, trigger_action_id}:
        repo.update_tg_action_status(action_id=current_action_id, status=action_status)
    repo.update_order_intent(
        action_id=parent_action_id,
        status=intent_status,
        is_dry_run=False,
        note=detail,
    )

    finalized_action = _require_action(repo, parent_action_id)
    finalized_intent = repo.get_order_intent_by_action(parent_action_id)
    status_label = "submitted" if is_success else "failed"
    return CallbackFinalizeResult(
        action=finalized_action,
        intent=finalized_intent,
        message_text=_render_status_message(
            message_text=message_text,
            ticker=finalized_action.ticker,
            action_type=finalized_action.action_type,
            status_label=status_label,
            detail=detail,
        ),
        reply_markup=None,
        order=order,
    )


def _process_cancel_action(
    *,
    repo: Repository,
    action: TelegramAction,
    callback_query_id: str,
    message_text: str,
) -> CallbackProcessResult:
    if not action.parent_action_id:
        raise ValueError("cancel action missing parent_action_id")

    repo.update_tg_action_status(
        action_id=action.action_id,
        status=TelegramActionStatus.CANCELLED,
        callback_query_id=callback_query_id,
    )
    repo.update_tg_action_status(
        action_id=action.parent_action_id,
        status=TelegramActionStatus.CANCELLED,
    )

    intent = repo.get_order_intent_by_action(action.parent_action_id)
    if intent is not None:
        repo.update_order_intent(
            action_id=action.parent_action_id,
            status=OrderIntentStatus.CANCELLED,
            note="telegram confirmation cancelled",
        )
        intent = repo.get_order_intent_by_action(action.parent_action_id)

    parent_action = _require_action(repo, action.parent_action_id)
    cancel_action = _require_action(repo, action.action_id)
    return CallbackProcessResult(
        action=cancel_action,
        intent=intent,
        execution_request=None,
        message_text=_render_status_message(
            message_text=message_text,
            ticker=parent_action.ticker,
            action_type=parent_action.action_type,
            status_label="cancelled",
            detail="telegram confirmation cancelled",
        ),
        reply_markup=None,
    )


def _process_confirm_action(
    *,
    repo: Repository,
    action: TelegramAction,
    callback_query_id: str,
    message_text: str,
    current_time: datetime,
    default_buy_cash_amount: int,
    default_sell_quantity: int,
) -> CallbackProcessResult:
    if not action.parent_action_id:
        raise ValueError("confirm action missing parent_action_id")

    repo.update_tg_action_status(
        action_id=action.action_id,
        status=TelegramActionStatus.ACKED,
        callback_query_id=callback_query_id,
    )
    trigger_action = _require_action(repo, action.action_id)
    parent_action = _require_action(repo, action.parent_action_id)
    intent = _upsert_order_intent(
        repo=repo,
        action=parent_action,
        status=OrderIntentStatus.CREATED,
        note="telegram confirmation acknowledged; executing order",
        is_dry_run=False,
        current_time=current_time,
        default_buy_cash_amount=default_buy_cash_amount,
        default_sell_quantity=default_sell_quantity,
    )
    return CallbackProcessResult(
        action=trigger_action,
        intent=intent,
        execution_request=CallbackExecutionRequest(
            trigger_action=trigger_action,
            parent_action=parent_action,
            intent=intent,
        ),
        message_text=_render_status_message(
            message_text=message_text,
            ticker=parent_action.ticker,
            action_type=parent_action.action_type,
            status_label="executing",
            detail=f"executing {parent_action.action_type.value.upper()} order",
        ),
        reply_markup=None,
    )


def _create_confirmation_actions(
    *,
    repo: Repository,
    parent_action: TelegramAction,
    current_time: datetime,
    default_buy_cash_amount: int,
    default_sell_quantity: int,
) -> tuple[TelegramAction, TelegramAction]:
    quantity, cash_amount = _resolve_action_payload(
        action=parent_action,
        default_buy_cash_amount=default_buy_cash_amount,
        default_sell_quantity=default_sell_quantity,
    )
    confirm_type = (
        TelegramActionType.CONFIRM_BUY
        if parent_action.action_type is TelegramActionType.BUY
        else TelegramActionType.CONFIRM_SELL
    )
    confirm_action = TelegramAction(
        action_id=_build_action_id(confirm_type),
        action_type=confirm_type,
        ticker=parent_action.ticker,
        quantity=quantity,
        cash_amount=cash_amount,
        parent_action_id=parent_action.action_id,
        created_at=current_time,
        status=TelegramActionStatus.PENDING,
        message_id=parent_action.message_id,
    )
    cancel_action = TelegramAction(
        action_id=_build_action_id(TelegramActionType.CANCEL),
        action_type=TelegramActionType.CANCEL,
        ticker=parent_action.ticker,
        quantity=quantity,
        cash_amount=cash_amount,
        parent_action_id=parent_action.action_id,
        created_at=current_time,
        status=TelegramActionStatus.PENDING,
        message_id=parent_action.message_id,
    )
    repo.insert_tg_action(confirm_action)
    repo.insert_tg_action(cancel_action)
    return confirm_action, cancel_action


def _get_or_create_confirmation_actions(
    *,
    repo: Repository,
    parent_action: TelegramAction,
    current_time: datetime,
    default_buy_cash_amount: int,
    default_sell_quantity: int,
) -> tuple[TelegramAction, TelegramAction]:
    existing = _find_confirmation_actions(repo=repo, parent_action=parent_action)
    if existing is not None:
        return existing
    return _create_confirmation_actions(
        repo=repo,
        parent_action=parent_action,
        current_time=current_time,
        default_buy_cash_amount=default_buy_cash_amount,
        default_sell_quantity=default_sell_quantity,
    )


def _find_confirmation_actions(
    *,
    repo: Repository,
    parent_action: TelegramAction,
) -> tuple[TelegramAction, TelegramAction] | None:
    confirm_type = (
        TelegramActionType.CONFIRM_BUY
        if parent_action.action_type is TelegramActionType.BUY
        else TelegramActionType.CONFIRM_SELL
    )
    child_actions = repo.list_tg_child_actions(parent_action.action_id)
    confirm_action: TelegramAction | None = None
    cancel_action: TelegramAction | None = None
    for child_action in child_actions:
        if (
            child_action.action_type is confirm_type
            and child_action.status is not TelegramActionStatus.CANCELLED
            and confirm_action is None
        ):
            confirm_action = child_action
        if (
            child_action.action_type is TelegramActionType.CANCEL
            and child_action.status is not TelegramActionStatus.CANCELLED
            and cancel_action is None
        ):
            cancel_action = child_action
    if confirm_action is None or cancel_action is None:
        return None
    return confirm_action, cancel_action


def _upsert_order_intent(
    *,
    repo: Repository,
    action: TelegramAction,
    status: OrderIntentStatus,
    note: str,
    is_dry_run: bool,
    current_time: datetime,
    default_buy_cash_amount: int,
    default_sell_quantity: int,
) -> OrderIntent:
    quantity, cash_amount = _resolve_action_payload(
        action=action,
        default_buy_cash_amount=default_buy_cash_amount,
        default_sell_quantity=default_sell_quantity,
    )
    existing = repo.get_order_intent_by_action(action.action_id)
    intent = OrderIntent(
        intent_id=(
            existing.intent_id
            if existing is not None
            else _build_order_intent_id(created_at=current_time, action_id=action.action_id)
        ),
        action_id=action.action_id,
        action_type=action.action_type,
        ticker=action.ticker,
        quantity=quantity,
        cash_amount=cash_amount,
        is_dry_run=is_dry_run,
        status=status,
        note=note,
        created_at=current_time if existing is None else existing.created_at,
    )
    repo.insert_order_intent(intent)
    stored_intent = repo.get_order_intent_by_action(action.action_id)
    assert stored_intent is not None
    return stored_intent


def _get_related_intent(*, repo: Repository, action: TelegramAction) -> OrderIntent | None:
    lookup_action_id = action.parent_action_id or action.action_id
    return repo.get_order_intent_by_action(lookup_action_id)


def _resolve_action_payload(
    *,
    action: TelegramAction,
    default_buy_cash_amount: int,
    default_sell_quantity: int,
) -> tuple[int | None, int | None]:
    if action.action_type in {TelegramActionType.BUY, TelegramActionType.CONFIRM_BUY}:
        return None, action.cash_amount or default_buy_cash_amount
    if action.action_type in {TelegramActionType.SELL, TelegramActionType.CONFIRM_SELL}:
        return action.quantity or default_sell_quantity, None
    return action.quantity, action.cash_amount


def _build_confirmation_reply_markup(
    *,
    confirm_action: TelegramAction,
    cancel_action: TelegramAction,
) -> dict[str, object]:
    confirm_label = (
        "CONFIRM BUY"
        if confirm_action.action_type is TelegramActionType.CONFIRM_BUY
        else "CONFIRM SELL"
    )
    return {
        "inline_keyboard": [
            [
                {
                    "text": confirm_label,
                    "callback_data": confirm_action.action_id,
                },
                {
                    "text": "CANCEL",
                    "callback_data": cancel_action.action_id,
                },
            ]
        ]
    }


def _render_status_message(
    *,
    message_text: str,
    ticker: str,
    action_type: TelegramActionType,
    status_label: str,
    detail: str,
) -> str:
    base_text = _strip_status_section(message_text)
    detail_text = " ".join(detail.split()) if detail else "-"
    lines = [
        base_text,
        "",
        _STATUS_MARKER,
        f"ticker={ticker}",
        f"action={_display_action_type(action_type)}",
        f"status={status_label}",
        f"detail={detail_text}",
    ]
    return "\n".join(line for line in lines if line != "" or base_text)


def _strip_status_section(message_text: str) -> str:
    marker_index = message_text.find(f"\n\n{_STATUS_MARKER}")
    if marker_index >= 0:
        return message_text[:marker_index].rstrip()
    if message_text.startswith(_STATUS_MARKER):
        return ""
    return message_text.strip()


def _resolve_execution_detail(*, order: BrokerOrder | None, error_message: str | None) -> str:
    if error_message:
        return " ".join(error_message.split())
    if order is None:
        return "order execution failed"
    if order.status is OrderStatus.SUBMITTED:
        external_order_id = order.external_order_id or "-"
        return (
            f"order_status={order.status.value} order_id={order.order_id} "
            f"external_order_id={external_order_id}"
        )

    response_message = str(order.response_payload.get("message") or "").strip()
    if response_message:
        return response_message
    if order.note.strip():
        return " ".join(order.note.split())
    return f"order_status={order.status.value}"


def _display_action_type(action_type: TelegramActionType) -> str:
    if action_type in {TelegramActionType.CONFIRM_BUY, TelegramActionType.BUY}:
        return TelegramActionType.BUY.value
    if action_type in {TelegramActionType.CONFIRM_SELL, TelegramActionType.SELL}:
        return TelegramActionType.SELL.value
    return action_type.value


def _require_action(repo: Repository, action_id: str) -> TelegramAction:
    action = repo.get_tg_action(action_id)
    if action is None:
        raise ValueError(f"telegram action not found action_id={action_id}")
    return action


def _parse_message_id(raw_value: object) -> int:
    if isinstance(raw_value, int):
        return raw_value
    if isinstance(raw_value, str) and raw_value.strip().isdigit():
        return int(raw_value.strip())
    raise ValueError("callback update missing message.message_id")


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
