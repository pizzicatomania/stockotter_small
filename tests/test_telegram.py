from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest
import requests
from typer.testing import CliRunner

import stockotter_small.cli as cli_module
import stockotter_v2.storage.repo as repo_module
from stockotter_small.telegram import (
    TelegramAPIError,
    TelegramAuthError,
    TelegramClient,
    build_briefing_candidates,
    build_inline_keyboard_and_actions,
    finalize_callback_execution,
    format_briefing_message,
    parse_callback_update,
    persist_tg_actions,
    process_callback_action,
)
from stockotter_v2.schemas import (
    BrokerOrder,
    Candidate,
    NewsItem,
    OrderIntentStatus,
    OrderSide,
    OrderStatus,
    OrderType,
    TelegramActionStatus,
    TelegramActionType,
)
from stockotter_v2.storage import Repository


class _FakeResponse:
    def __init__(self, *, status_code: int, payload: dict[str, object]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, object]:
        return self._payload


class _FakeSession:
    def __init__(
        self,
        *,
        response: _FakeResponse | None = None,
        error: requests.RequestException | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, object]] = []

    def post(self, url: str, *, json: dict[str, object], timeout: float) -> _FakeResponse:
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response

    def get(self, url: str, *, params: dict[str, object], timeout: float) -> _FakeResponse:
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


class _FakeTelegramSender:
    def __init__(self, *, updates: list[dict[str, object]] | None = None) -> None:
        self.messages: list[str] = []
        self.reply_markups: list[dict[str, object] | None] = []
        self.callback_query_ids: list[str] = []
        self.edits: list[dict[str, object]] = []
        self.updates = updates or []

    def send_message(
        self,
        text: str,
        *,
        reply_markup: dict[str, object] | None = None,
    ) -> object:
        self.messages.append(text)
        self.reply_markups.append(reply_markup)

        class _Result:
            message_id = 777

        return _Result()

    def answer_callback_query(self, callback_query_id: str, *, text: str = "received") -> object:
        _ = text
        self.callback_query_ids.append(callback_query_id)

        class _Result:
            ok = True

        return _Result()

    def edit_message_text(
        self,
        *,
        message_id: int,
        text: str,
        reply_markup: dict[str, object] | None = None,
    ) -> object:
        self.edits.append(
            {
                "message_id": message_id,
                "text": text,
                "reply_markup": reply_markup,
            }
        )

        class _Result:
            ok = True

        return _Result()

    def get_updates(
        self,
        *,
        offset: int | None = None,
        timeout: int = 20,
        allowed_updates: list[str] | None = None,
    ) -> object:
        _ = timeout, allowed_updates
        updates = self.updates
        if offset is not None:
            updates = [
                update
                for update in updates
                if isinstance(update.get("update_id"), int) and update["update_id"] >= offset
            ]

        class _Result:
            def __init__(self, updates: list[dict[str, object]]) -> None:
                self.updates = updates

        return _Result(updates)


class _FakeOrderService:
    def __init__(
        self,
        *,
        buy_order: BrokerOrder | None = None,
        sell_order: BrokerOrder | None = None,
        error: Exception | None = None,
    ) -> None:
        self.buy_order = buy_order
        self.sell_order = sell_order
        self.error = error
        self.buy_calls: list[dict[str, object]] = []
        self.sell_calls: list[dict[str, object]] = []

    def place_buy_market(
        self,
        ticker: str,
        cash_amount: int,
        *,
        confirm: bool = False,
        allow_live: bool = False,
    ) -> BrokerOrder:
        self.buy_calls.append(
            {
                "ticker": ticker,
                "cash_amount": cash_amount,
                "confirm": confirm,
                "allow_live": allow_live,
            }
        )
        if self.error is not None:
            raise self.error
        assert self.buy_order is not None
        return self.buy_order

    def place_sell_market(
        self,
        ticker: str,
        qty: int,
        *,
        confirm: bool = False,
        allow_live: bool = False,
    ) -> BrokerOrder:
        self.sell_calls.append(
            {
                "ticker": ticker,
                "qty": qty,
                "confirm": confirm,
                "allow_live": allow_live,
            }
        )
        if self.error is not None:
            raise self.error
        assert self.sell_order is not None
        return self.sell_order


def test_build_briefing_candidates_and_format_message(monkeypatch, tmp_path) -> None:
    fixed_now = datetime.fromisoformat("2026-03-08T08:30:00+09:00")
    monkeypatch.setattr(repo_module, "now_in_seoul", lambda: fixed_now)
    repo = Repository(tmp_path / "storage.db")
    _seed_briefing_repo(repo)

    candidates = build_briefing_candidates(repo=repo, asof=date(2026, 3, 8), limit=10)
    message = format_briefing_message(asof=date(2026, 3, 8), candidates=candidates)

    assert [candidate.ticker for candidate in candidates] == ["005930", "000660"]
    assert "[StockOtter] Morning Briefing" in message
    assert "asof: 2026-03-08" in message
    assert "1. 005930 | score 0.910" in message
    assert "- 삼성전자, 반도체 수요 회복 기대감에 상승" in message
    assert "2. 000660 | score 0.770" in message


def test_build_inline_keyboard_and_actions_uses_payload_defaults(monkeypatch, tmp_path) -> None:
    fixed_now = datetime.fromisoformat("2026-03-08T08:30:00+09:00")
    monkeypatch.setattr(repo_module, "now_in_seoul", lambda: fixed_now)
    repo = Repository(tmp_path / "storage.db")
    _seed_briefing_repo(repo)
    candidates = build_briefing_candidates(repo=repo, asof=date(2026, 3, 8), limit=10)

    reply_markup, actions = build_inline_keyboard_and_actions(
        candidates=candidates,
        buy_cash_amount=250000,
        sell_quantity=3,
        now=fixed_now,
    )

    assert len(reply_markup["inline_keyboard"]) == 2
    assert len(actions) == 6
    assert [button["text"] for button in reply_markup["inline_keyboard"][0]] == [
        "BUY",
        "SELL",
        "SKIP",
    ]
    buy_action = next(action for action in actions if action.action_type is TelegramActionType.BUY)
    sell_action = next(
        action for action in actions if action.action_type is TelegramActionType.SELL
    )
    assert buy_action.cash_amount == 250000
    assert buy_action.quantity is None
    assert sell_action.quantity == 3
    assert sell_action.cash_amount is None
    for action in actions:
        assert len(action.action_id.encode("utf-8")) <= 64
        assert action.status is TelegramActionStatus.PENDING


def test_parse_callback_update_reads_message_metadata() -> None:
    envelope = parse_callback_update(
        json.dumps(
            {
                "callback_query": {
                    "id": "cb-001",
                    "data": "A:buy:1234abcd",
                    "message": {
                        "message_id": 321,
                        "text": "briefing body",
                    },
                }
            }
        )
    )

    assert envelope.callback_query_id == "cb-001"
    assert envelope.action_id == "A:buy:1234abcd"
    assert envelope.message_id == 321
    assert envelope.message_text == "briefing body"


def test_telegram_client_send_and_edit_success() -> None:
    session = _FakeSession(
        response=_FakeResponse(
            status_code=200,
            payload={"ok": True, "result": {"message_id": 12345}},
        )
    )
    client = TelegramClient(
        bot_token="secret-telegram-token",
        chat_id="123456",
        session=session,
    )

    send_result = client.send_message("hello briefing")
    edit_result = client.edit_message_text(message_id=12345, text="edited briefing")

    assert send_result.message_id == 12345
    assert edit_result.ok is True
    assert session.calls[0]["json"] == {
        "chat_id": "123456",
        "text": "hello briefing",
        "disable_web_page_preview": True,
    }
    assert session.calls[1]["json"] == {
        "chat_id": "123456",
        "message_id": 12345,
        "text": "edited briefing",
        "disable_web_page_preview": True,
    }


def test_telegram_client_get_updates_success() -> None:
    session = _FakeSession(
        response=_FakeResponse(
            status_code=200,
            payload={
                "ok": True,
                "result": [{"update_id": 101, "callback_query": {"id": "cb-001"}}],
            },
        )
    )
    client = TelegramClient(
        bot_token="secret-telegram-token",
        chat_id="123456",
        session=session,
    )

    result = client.get_updates(offset=100, timeout=15, allowed_updates=["callback_query"])

    assert result.updates == [{"update_id": 101, "callback_query": {"id": "cb-001"}}]
    assert session.calls[0]["params"] == {
        "offset": 100,
        "timeout": 15,
        "allowed_updates": "[\"callback_query\"]",
    }


def test_telegram_client_answer_callback_query_success() -> None:
    session = _FakeSession(
        response=_FakeResponse(
            status_code=200,
            payload={"ok": True, "result": True},
        )
    )
    client = TelegramClient(
        bot_token="secret-telegram-token",
        chat_id="123456",
        session=session,
    )

    result = client.answer_callback_query("callback-123")

    assert result.ok is True
    assert session.calls[0]["json"] == {
        "callback_query_id": "callback-123",
        "text": "received",
    }


def test_telegram_client_auth_error_does_not_leak_secret() -> None:
    session = _FakeSession(
        response=_FakeResponse(
            status_code=401,
            payload={"ok": False, "description": "Unauthorized"},
        )
    )
    client = TelegramClient(
        bot_token="secret-telegram-token",
        chat_id="123456",
        session=session,
    )

    with pytest.raises(TelegramAuthError) as exc_info:
        client.send_message("hello")

    assert "secret-telegram-token" not in str(exc_info.value)
    assert "123456" not in str(exc_info.value)


def test_telegram_client_request_error_does_not_leak_secret() -> None:
    session = _FakeSession(
        error=requests.ConnectionError(
            "failed request to https://api.telegram.org/botsecret-telegram-token/sendMessage"
        )
    )
    client = TelegramClient(
        bot_token="secret-telegram-token",
        chat_id="123456",
        session=session,
    )

    with pytest.raises(TelegramAPIError) as exc_info:
        client.send_message("hello")

    assert "secret-telegram-token" not in str(exc_info.value)
    assert "123456" not in str(exc_info.value)


def test_process_callback_action_buy_requires_confirmation_by_default(tmp_path) -> None:
    fixed_now = datetime.fromisoformat("2026-03-08T08:30:00+09:00")
    repo = Repository(tmp_path / "storage.db")
    _seed_briefing_repo(repo)
    candidates = build_briefing_candidates(repo=repo, asof=date(2026, 3, 8), limit=10)
    _, actions = build_inline_keyboard_and_actions(candidates=candidates, now=fixed_now)
    persist_tg_actions(repo=repo, actions=actions, message_id=555)
    buy_action = next(action for action in actions if action.action_type is TelegramActionType.BUY)

    result = process_callback_action(
        repo=repo,
        action_id=buy_action.action_id,
        callback_query_id="cb-001",
        message_id=555,
        message_text="briefing body",
        environment="paper",
        paper_one_step_enabled=False,
        default_buy_cash_amount=100000,
        default_sell_quantity=1,
        now=fixed_now,
    )

    stored_parent = repo.get_tg_action(buy_action.action_id)
    assert stored_parent is not None
    assert stored_parent.status is TelegramActionStatus.CONFIRM_PENDING
    assert result.execution_request is None
    assert result.intent is not None
    assert result.intent.status is OrderIntentStatus.AWAITING_CONFIRMATION
    assert result.reply_markup is not None
    button_texts = [button["text"] for button in result.reply_markup["inline_keyboard"][0]]
    assert button_texts == ["CONFIRM BUY", "CANCEL"]
    assert "Confirm BUY?" in result.message_text
    stored_actions = repo.list_tg_actions()
    assert any(action.action_type is TelegramActionType.CONFIRM_BUY for action in stored_actions)
    assert any(action.action_type is TelegramActionType.CANCEL for action in stored_actions)


def test_process_callback_action_duplicate_buy_reuses_existing_confirmation_actions(
    tmp_path,
) -> None:
    fixed_now = datetime.fromisoformat("2026-03-08T08:30:00+09:00")
    repo = Repository(tmp_path / "storage.db")
    _seed_briefing_repo(repo)
    candidates = build_briefing_candidates(repo=repo, asof=date(2026, 3, 8), limit=10)
    _, actions = build_inline_keyboard_and_actions(candidates=candidates, now=fixed_now)
    persist_tg_actions(repo=repo, actions=actions, message_id=555)
    buy_action = next(action for action in actions if action.action_type is TelegramActionType.BUY)

    first_result = process_callback_action(
        repo=repo,
        action_id=buy_action.action_id,
        callback_query_id="cb-dup-001",
        message_id=555,
        message_text="briefing body",
        environment="paper",
        paper_one_step_enabled=False,
        default_buy_cash_amount=100000,
        default_sell_quantity=1,
        now=fixed_now,
    )
    child_actions_after_first = repo.list_tg_child_actions(buy_action.action_id)
    confirm_ids_after_first = {
        action.action_id
        for action in child_actions_after_first
        if action.action_type is TelegramActionType.CONFIRM_BUY
    }
    cancel_ids_after_first = {
        action.action_id
        for action in child_actions_after_first
        if action.action_type is TelegramActionType.CANCEL
    }

    second_result = process_callback_action(
        repo=repo,
        action_id=buy_action.action_id,
        callback_query_id="cb-dup-002",
        message_id=555,
        message_text="briefing body",
        environment="paper",
        paper_one_step_enabled=False,
        default_buy_cash_amount=100000,
        default_sell_quantity=1,
        now=fixed_now,
    )
    child_actions_after_second = repo.list_tg_child_actions(buy_action.action_id)
    confirm_ids_after_second = {
        action.action_id
        for action in child_actions_after_second
        if action.action_type is TelegramActionType.CONFIRM_BUY
    }
    cancel_ids_after_second = {
        action.action_id
        for action in child_actions_after_second
        if action.action_type is TelegramActionType.CANCEL
    }

    assert first_result.execution_request is None
    assert second_result.execution_request is None
    assert len(child_actions_after_first) == 2
    assert len(child_actions_after_second) == 2
    assert confirm_ids_after_first == confirm_ids_after_second
    assert cancel_ids_after_first == cancel_ids_after_second
    assert second_result.reply_markup == first_result.reply_markup
    stored_action = repo.get_tg_action(buy_action.action_id)
    assert stored_action is not None
    assert stored_action.status is TelegramActionStatus.CONFIRM_PENDING


def test_process_callback_action_paper_one_step_returns_execution_request(tmp_path) -> None:
    fixed_now = datetime.fromisoformat("2026-03-08T08:30:00+09:00")
    repo = Repository(tmp_path / "storage.db")
    _seed_briefing_repo(repo)
    candidates = build_briefing_candidates(repo=repo, asof=date(2026, 3, 8), limit=10)
    _, actions = build_inline_keyboard_and_actions(candidates=candidates, now=fixed_now)
    persist_tg_actions(repo=repo, actions=actions, message_id=555)
    sell_action = next(
        action for action in actions if action.action_type is TelegramActionType.SELL
    )

    result = process_callback_action(
        repo=repo,
        action_id=sell_action.action_id,
        callback_query_id="cb-002",
        message_id=555,
        message_text="briefing body",
        environment="paper",
        paper_one_step_enabled=True,
        default_buy_cash_amount=100000,
        default_sell_quantity=2,
        now=fixed_now,
    )

    assert result.execution_request is not None
    assert result.execution_request.parent_action.action_type is TelegramActionType.SELL
    assert result.intent is not None
    assert result.intent.status is OrderIntentStatus.CREATED
    assert result.intent.is_dry_run is False
    assert result.intent.quantity == 1
    assert result.reply_markup is None
    assert "paper one-step execution started" in result.message_text


def test_finalize_callback_execution_updates_statuses(tmp_path) -> None:
    fixed_now = datetime.fromisoformat("2026-03-08T08:30:00+09:00")
    repo = Repository(tmp_path / "storage.db")
    _seed_briefing_repo(repo)
    candidates = build_briefing_candidates(repo=repo, asof=date(2026, 3, 8), limit=10)
    _, actions = build_inline_keyboard_and_actions(candidates=candidates, now=fixed_now)
    persist_tg_actions(repo=repo, actions=actions, message_id=555)
    buy_action = next(action for action in actions if action.action_type is TelegramActionType.BUY)
    first_result = process_callback_action(
        repo=repo,
        action_id=buy_action.action_id,
        callback_query_id="cb-003",
        message_id=555,
        message_text="briefing body",
        environment="paper",
        paper_one_step_enabled=False,
        default_buy_cash_amount=100000,
        default_sell_quantity=1,
        now=fixed_now,
    )
    confirm_action = next(
        action
        for action in repo.list_tg_actions()
        if action.action_type is TelegramActionType.CONFIRM_BUY
    )
    second_result = process_callback_action(
        repo=repo,
        action_id=confirm_action.action_id,
        callback_query_id="cb-004",
        message_id=555,
        message_text=first_result.message_text,
        environment="paper",
        paper_one_step_enabled=False,
        default_buy_cash_amount=100000,
        default_sell_quantity=1,
        now=fixed_now,
    )

    final_result = finalize_callback_execution(
        repo=repo,
        execution_request=second_result.execution_request,
        message_text=second_result.message_text,
        order=_submitted_order(),
    )

    assert second_result.execution_request is not None
    assert final_result.action.status is TelegramActionStatus.EXECUTED
    assert final_result.intent is not None
    assert final_result.intent.status is OrderIntentStatus.EXECUTED
    assert final_result.order is not None
    assert "status=submitted" in final_result.message_text
    stored_parent = repo.get_tg_action(buy_action.action_id)
    assert stored_parent is not None
    assert stored_parent.status is TelegramActionStatus.EXECUTED


def test_process_callback_action_cancel_marks_intent_cancelled(tmp_path) -> None:
    fixed_now = datetime.fromisoformat("2026-03-08T08:30:00+09:00")
    repo = Repository(tmp_path / "storage.db")
    _seed_briefing_repo(repo)
    candidates = build_briefing_candidates(repo=repo, asof=date(2026, 3, 8), limit=10)
    _, actions = build_inline_keyboard_and_actions(candidates=candidates, now=fixed_now)
    persist_tg_actions(repo=repo, actions=actions, message_id=555)
    buy_action = next(action for action in actions if action.action_type is TelegramActionType.BUY)
    process_callback_action(
        repo=repo,
        action_id=buy_action.action_id,
        callback_query_id="cb-005",
        message_id=555,
        message_text="briefing body",
        environment="paper",
        paper_one_step_enabled=False,
        default_buy_cash_amount=100000,
        default_sell_quantity=1,
        now=fixed_now,
    )
    cancel_action = next(
        action
        for action in repo.list_tg_actions()
        if action.action_type is TelegramActionType.CANCEL
    )

    result = process_callback_action(
        repo=repo,
        action_id=cancel_action.action_id,
        callback_query_id="cb-006",
        message_id=555,
        message_text=(
            "briefing body\n\n[Telegram Action]\n"
            "ticker=005930\naction=buy\n"
            "status=awaiting_confirmation\ndetail=Confirm BUY?"
        ),
        environment="paper",
        paper_one_step_enabled=False,
        default_buy_cash_amount=100000,
        default_sell_quantity=1,
        now=fixed_now,
    )

    assert result.execution_request is None
    assert result.intent is not None
    assert result.intent.status is OrderIntentStatus.CANCELLED
    assert result.action.status is TelegramActionStatus.CANCELLED
    assert "status=cancelled" in result.message_text


def test_cli_tg_send_briefing_success(monkeypatch, tmp_path) -> None:
    fixed_now = datetime.fromisoformat("2026-03-08T08:30:00+09:00")
    monkeypatch.setattr(repo_module, "now_in_seoul", lambda: fixed_now)
    repo = Repository(tmp_path / "storage.db")
    _seed_briefing_repo(repo)
    config_path = _write_config(tmp_path, telegram_paper_one_step_enabled=False)

    fake_sender = _FakeTelegramSender()
    monkeypatch.setattr(
        cli_module.TelegramClient,
        "from_env",
        staticmethod(lambda: fake_sender),
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "tg",
            "send-briefing",
            "--asof",
            "2026-03-08",
            "--db-path",
            str(repo.db_path),
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 0
    assert "telegram sent asof=2026-03-08 candidates=2 message_id=777" in result.output
    stored_actions = repo.list_tg_actions()
    assert len(stored_actions) == 6
    buy_action = next(
        action for action in stored_actions if action.action_type is TelegramActionType.BUY
    )
    sell_action = next(
        action for action in stored_actions if action.action_type is TelegramActionType.SELL
    )
    assert buy_action.cash_amount == 100000
    assert sell_action.quantity == 1


def test_cli_tg_handle_callback_first_step_edits_message_without_order(
    monkeypatch, tmp_path
) -> None:
    fixed_now = datetime.fromisoformat("2026-03-08T08:30:00+09:00")
    repo = Repository(tmp_path / "storage.db")
    _seed_briefing_repo(repo)
    candidates = build_briefing_candidates(repo=repo, asof=date(2026, 3, 8), limit=10)
    _, actions = build_inline_keyboard_and_actions(candidates=candidates, now=fixed_now)
    persist_tg_actions(repo=repo, actions=actions, message_id=777)
    buy_action = next(action for action in actions if action.action_type is TelegramActionType.BUY)
    config_path = _write_config(tmp_path, telegram_paper_one_step_enabled=False)

    update_path = tmp_path / "callback.json"
    update_path.write_text(
        json.dumps(
            {
                "callback_query": {
                    "id": "cb-007",
                    "data": buy_action.action_id,
                    "message": {
                        "message_id": 777,
                        "text": "briefing body",
                    },
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    fake_sender = _FakeTelegramSender()
    monkeypatch.setattr(
        cli_module.TelegramClient,
        "from_env",
        staticmethod(lambda: fake_sender),
    )
    monkeypatch.setattr(
        cli_module,
        "_build_order_service",
        lambda **_: (_ for _ in ()).throw(AssertionError("order service should not be built")),
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "tg",
            "handle-callback",
            "--update-json",
            str(update_path),
            "--db-path",
            str(repo.db_path),
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 0
    assert "status=confirm_pending" in result.output
    assert fake_sender.callback_query_ids == ["cb-007"]
    assert len(fake_sender.edits) == 1
    assert "Confirm BUY?" in fake_sender.edits[0]["text"]
    assert fake_sender.edits[0]["reply_markup"] is not None


def test_cli_tg_handle_callback_confirm_executes_order_and_edits_message(
    monkeypatch, tmp_path
) -> None:
    fixed_now = datetime.fromisoformat("2026-03-08T08:30:00+09:00")
    repo = Repository(tmp_path / "storage.db")
    _seed_briefing_repo(repo)
    candidates = build_briefing_candidates(repo=repo, asof=date(2026, 3, 8), limit=10)
    _, actions = build_inline_keyboard_and_actions(candidates=candidates, now=fixed_now)
    persist_tg_actions(repo=repo, actions=actions, message_id=777)
    buy_action = next(action for action in actions if action.action_type is TelegramActionType.BUY)
    config_path = _write_config(tmp_path, telegram_paper_one_step_enabled=False)
    fake_sender = _FakeTelegramSender()
    monkeypatch.setattr(
        cli_module.TelegramClient,
        "from_env",
        staticmethod(lambda: fake_sender),
    )

    first_update = tmp_path / "callback-first.json"
    first_update.write_text(
        json.dumps(
            {
                "callback_query": {
                    "id": "cb-008",
                    "data": buy_action.action_id,
                    "message": {
                        "message_id": 777,
                        "text": "briefing body",
                    },
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    runner = CliRunner()
    first_result = runner.invoke(
        cli_module.app,
        [
            "tg",
            "handle-callback",
            "--update-json",
            str(first_update),
            "--db-path",
            str(repo.db_path),
            "--config",
            str(config_path),
        ],
    )
    assert first_result.exit_code == 0
    confirm_action = next(
        action
        for action in repo.list_tg_actions()
        if action.action_type is TelegramActionType.CONFIRM_BUY
    )

    fake_service = _FakeOrderService(buy_order=_submitted_order())
    monkeypatch.setattr(cli_module, "_build_order_service", lambda **_: fake_service)

    second_update = tmp_path / "callback-second.json"
    second_update.write_text(
        json.dumps(
            {
                "callback_query": {
                    "id": "cb-009",
                    "data": confirm_action.action_id,
                    "message": {
                        "message_id": 777,
                        "text": fake_sender.edits[-1]["text"],
                    },
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    second_result = runner.invoke(
        cli_module.app,
        [
            "tg",
            "handle-callback",
            "--update-json",
            str(second_update),
            "--db-path",
            str(repo.db_path),
            "--config",
            str(config_path),
        ],
    )

    assert second_result.exit_code == 0
    assert "status=executed" in second_result.output
    assert fake_service.buy_calls == [
        {
            "ticker": "005930",
            "cash_amount": 100000,
            "confirm": True,
            "allow_live": False,
        }
    ]
    assert "status=submitted" in fake_sender.edits[-1]["text"]
    stored_intent = repo.get_order_intent_by_action(buy_action.action_id)
    assert stored_intent is not None
    assert stored_intent.status is OrderIntentStatus.EXECUTED


def test_cli_tg_handle_callback_confirm_reports_execution_error(monkeypatch, tmp_path) -> None:
    fixed_now = datetime.fromisoformat("2026-03-08T08:30:00+09:00")
    repo = Repository(tmp_path / "storage.db")
    _seed_briefing_repo(repo)
    candidates = build_briefing_candidates(repo=repo, asof=date(2026, 3, 8), limit=10)
    _, actions = build_inline_keyboard_and_actions(candidates=candidates, now=fixed_now)
    persist_tg_actions(repo=repo, actions=actions, message_id=777)
    sell_action = next(
        action for action in actions if action.action_type is TelegramActionType.SELL
    )
    config_path = _write_config(tmp_path, telegram_paper_one_step_enabled=True)
    fake_sender = _FakeTelegramSender()
    monkeypatch.setattr(
        cli_module.TelegramClient,
        "from_env",
        staticmethod(lambda: fake_sender),
    )
    fake_service = _FakeOrderService(
        error=ValueError("order endpoints are disabled by TRADING_DISABLED")
    )
    monkeypatch.setattr(cli_module, "_build_order_service", lambda **_: fake_service)

    update_path = tmp_path / "callback-error.json"
    update_path.write_text(
        json.dumps(
            {
                "callback_query": {
                    "id": "cb-010",
                    "data": sell_action.action_id,
                    "message": {
                        "message_id": 777,
                        "text": "briefing body",
                    },
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "tg",
            "handle-callback",
            "--update-json",
            str(update_path),
            "--db-path",
            str(repo.db_path),
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 0
    assert "status=failed" in result.output
    assert fake_service.sell_calls == [
        {
            "ticker": "005930",
            "qty": 1,
            "confirm": True,
            "allow_live": False,
        }
    ]
    assert "status=failed" in fake_sender.edits[-1]["text"]
    assert "TRADING_DISABLED" in fake_sender.edits[-1]["text"]
    stored_intent = repo.get_order_intent_by_action(sell_action.action_id)
    assert stored_intent is not None
    assert stored_intent.status is OrderIntentStatus.REJECTED


def test_cli_tg_poll_callbacks_processes_first_step_and_saves_offset(
    monkeypatch, tmp_path
) -> None:
    fixed_now = datetime.fromisoformat("2026-03-08T08:30:00+09:00")
    repo = Repository(tmp_path / "storage.db")
    _seed_briefing_repo(repo)
    candidates = build_briefing_candidates(repo=repo, asof=date(2026, 3, 8), limit=10)
    _, actions = build_inline_keyboard_and_actions(candidates=candidates, now=fixed_now)
    persist_tg_actions(repo=repo, actions=actions, message_id=777)
    buy_action = next(action for action in actions if action.action_type is TelegramActionType.BUY)
    config_path = _write_config(tmp_path, telegram_paper_one_step_enabled=False)
    offset_file = tmp_path / "telegram.offset"

    fake_sender = _FakeTelegramSender(
        updates=[
            {
                "update_id": 1001,
                "callback_query": {
                    "id": "cb-poll-001",
                    "data": buy_action.action_id,
                    "message": {
                        "message_id": 777,
                        "text": "briefing body",
                    },
                },
            }
        ]
    )
    monkeypatch.setattr(
        cli_module.TelegramClient,
        "from_env",
        staticmethod(lambda: fake_sender),
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "tg",
            "poll-callbacks",
            "--db-path",
            str(repo.db_path),
            "--config",
            str(config_path),
            "--offset-file",
            str(offset_file),
            "--once",
        ],
    )

    assert result.exit_code == 0
    assert "update_id=1001 telegram callback" in result.output
    assert "status=confirm_pending" in result.output
    assert fake_sender.callback_query_ids == ["cb-poll-001"]
    assert fake_sender.edits[-1]["reply_markup"] is not None
    assert offset_file.read_text(encoding="utf-8").strip() == "1002"
    stored_action = repo.get_tg_action(buy_action.action_id)
    assert stored_action is not None
    assert stored_action.status is TelegramActionStatus.CONFIRM_PENDING


def test_cli_tg_poll_callbacks_confirm_executes_order(monkeypatch, tmp_path) -> None:
    fixed_now = datetime.fromisoformat("2026-03-08T08:30:00+09:00")
    repo = Repository(tmp_path / "storage.db")
    _seed_briefing_repo(repo)
    candidates = build_briefing_candidates(repo=repo, asof=date(2026, 3, 8), limit=10)
    _, actions = build_inline_keyboard_and_actions(candidates=candidates, now=fixed_now)
    persist_tg_actions(repo=repo, actions=actions, message_id=777)
    buy_action = next(action for action in actions if action.action_type is TelegramActionType.BUY)
    config_path = _write_config(tmp_path, telegram_paper_one_step_enabled=False)
    offset_file = tmp_path / "telegram.offset"

    first_result = process_callback_action(
        repo=repo,
        action_id=buy_action.action_id,
        callback_query_id="cb-seed-001",
        message_id=777,
        message_text="briefing body",
        environment="paper",
        paper_one_step_enabled=False,
        default_buy_cash_amount=100000,
        default_sell_quantity=1,
        now=fixed_now,
    )
    confirm_action = next(
        action
        for action in repo.list_tg_actions()
        if action.action_type is TelegramActionType.CONFIRM_BUY
    )

    fake_sender = _FakeTelegramSender(
        updates=[
            {
                "update_id": 2001,
                "callback_query": {
                    "id": "cb-poll-002",
                    "data": confirm_action.action_id,
                    "message": {
                        "message_id": 777,
                        "text": first_result.message_text,
                    },
                },
            }
        ]
    )
    monkeypatch.setattr(
        cli_module.TelegramClient,
        "from_env",
        staticmethod(lambda: fake_sender),
    )
    fake_service = _FakeOrderService(buy_order=_submitted_order())
    monkeypatch.setattr(cli_module, "_build_order_service", lambda **_: fake_service)

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "tg",
            "poll-callbacks",
            "--db-path",
            str(repo.db_path),
            "--config",
            str(config_path),
            "--offset-file",
            str(offset_file),
            "--once",
        ],
    )

    assert result.exit_code == 0
    assert "update_id=2001 telegram callback" in result.output
    assert "status=executed" in result.output
    assert fake_sender.callback_query_ids == ["cb-poll-002"]
    assert fake_service.buy_calls == [
        {
            "ticker": "005930",
            "cash_amount": 100000,
            "confirm": True,
            "allow_live": False,
        }
    ]
    assert "status=submitted" in fake_sender.edits[-1]["text"]
    assert offset_file.read_text(encoding="utf-8").strip() == "2002"
    stored_action = repo.get_tg_action(buy_action.action_id)
    stored_intent = repo.get_order_intent_by_action(buy_action.action_id)
    assert stored_action is not None
    assert stored_action.status is TelegramActionStatus.EXECUTED
    assert stored_intent is not None
    assert stored_intent.status is OrderIntentStatus.EXECUTED


def test_cli_tg_poll_callbacks_duplicate_buy_updates_remain_idempotent(
    monkeypatch, tmp_path
) -> None:
    fixed_now = datetime.fromisoformat("2026-03-08T08:30:00+09:00")
    repo = Repository(tmp_path / "storage.db")
    _seed_briefing_repo(repo)
    candidates = build_briefing_candidates(repo=repo, asof=date(2026, 3, 8), limit=10)
    _, actions = build_inline_keyboard_and_actions(candidates=candidates, now=fixed_now)
    persist_tg_actions(repo=repo, actions=actions, message_id=777)
    buy_action = next(action for action in actions if action.action_type is TelegramActionType.BUY)
    config_path = _write_config(tmp_path, telegram_paper_one_step_enabled=False)
    offset_file = tmp_path / "telegram.offset"

    fake_sender = _FakeTelegramSender(
        updates=[
            {
                "update_id": 3001,
                "callback_query": {
                    "id": "cb-poll-dup-001",
                    "data": buy_action.action_id,
                    "message": {
                        "message_id": 777,
                        "text": "briefing body",
                    },
                },
            },
            {
                "update_id": 3002,
                "callback_query": {
                    "id": "cb-poll-dup-002",
                    "data": buy_action.action_id,
                    "message": {
                        "message_id": 777,
                        "text": "briefing body",
                    },
                },
            },
        ]
    )
    monkeypatch.setattr(
        cli_module.TelegramClient,
        "from_env",
        staticmethod(lambda: fake_sender),
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "tg",
            "poll-callbacks",
            "--db-path",
            str(repo.db_path),
            "--config",
            str(config_path),
            "--offset-file",
            str(offset_file),
            "--once",
        ],
    )

    assert result.exit_code == 0
    assert "update_id=3001 telegram callback" in result.output
    assert "update_id=3002 telegram callback" in result.output
    assert fake_sender.callback_query_ids == ["cb-poll-dup-001", "cb-poll-dup-002"]
    child_actions = repo.list_tg_child_actions(buy_action.action_id)
    assert len(child_actions) == 2
    assert {
        action.action_type for action in child_actions
    } == {TelegramActionType.CONFIRM_BUY, TelegramActionType.CANCEL}
    assert offset_file.read_text(encoding="utf-8").strip() == "3003"


def _write_config(tmp_path: Path, *, telegram_paper_one_step_enabled: bool) -> Path:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "timezone": "Asia/Seoul",
                "sources": [
                    {
                        "name": "google-news",
                        "type": "rss",
                        "enabled": True,
                        "url": "https://example.com/rss",
                    }
                ],
                "caching": {
                    "enabled": True,
                    "directory": "data/cache",
                    "ttl_minutes": 60,
                },
                "llm": {
                    "provider": "gemini",
                    "model": "gemini-2.5-flash",
                    "fallback_model": "gemini-2.5-flash-lite",
                    "api_key_env": "GEMINI_API_KEY",
                    "temperature": 0.0,
                    "max_retries": 1,
                    "prompt_template": None,
                },
                "news_quality": {
                    "enabled": True,
                    "ticker_map_path": "data/ticker_map.json",
                    "noise_patterns": ["광고"],
                    "min_title_length": 10,
                    "drop_duplicate_titles": True,
                },
                "scoring": {
                    "min_score": 0.0,
                    "weights": {},
                },
                "universe": {
                    "market": "KR",
                    "tickers": [],
                    "max_candidates": 20,
                    "min_price": 1000.0,
                    "max_price": 100000.0,
                    "min_value_traded_5d_avg": 10000000000.0,
                    "exclude_managed": True,
                },
                "trading": {
                    "live_ticker_allowlist": ["005930"],
                    "max_daily_order_count": 3,
                    "max_cash_per_order": 500000,
                    "max_total_cash_per_day": 1000000,
                    "telegram_paper_one_step_enabled": telegram_paper_one_step_enabled,
                    "telegram_default_buy_cash_amount": 100000,
                    "telegram_default_sell_quantity": 1,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return config_path


def _submitted_order() -> BrokerOrder:
    return BrokerOrder(
        order_id="order-001",
        broker="kis",
        environment="paper",
        ticker="005930",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=1,
        cash_amount=100000,
        status=OrderStatus.SUBMITTED,
        is_dry_run=False,
        request_payload={},
        response_payload={"msg1": "주문 전송 완료 되었습니다."},
        external_order_id="0001234567",
        external_order_time="103000",
        note="submitted",
        created_at="2026-03-08T09:00:00+09:00",
        updated_at="2026-03-08T09:00:01+09:00",
        submitted_at="2026-03-08T09:00:01+09:00",
    )


def _seed_briefing_repo(repo: Repository) -> None:
    repo.upsert_news_item(
        NewsItem(
            id="news-005930-1",
            source="unit-test",
            title="삼성전자, 반도체 수요 회복 기대감에 상승",
            url="https://example.com/news-005930-1",
            published_at="2026-03-08T07:30:00+09:00",
            raw_text="기사 본문 1",
            tickers_mentioned=["005930"],
        )
    )
    repo.upsert_news_item(
        NewsItem(
            id="news-005930-2",
            source="unit-test",
            title="삼성전자, AI 메모리 공급 확대 수혜 전망",
            url="https://example.com/news-005930-2",
            published_at="2026-03-08T08:00:00+09:00",
            raw_text="기사 본문 2",
            tickers_mentioned=["005930"],
        )
    )
    repo.upsert_news_item(
        NewsItem(
            id="news-000660-1",
            source="unit-test",
            title="SK하이닉스, HBM 투자 확대 기대",
            url="https://example.com/news-000660-1",
            published_at="2026-03-08T08:10:00+09:00",
            raw_text="기사 본문 3",
            tickers_mentioned=["000660"],
        )
    )
    repo.replace_candidates(
        [
            Candidate(
                ticker="005930",
                score=0.91,
                reasons=["반도체 업황 회복"],
                supporting_news_ids=["news-005930-1", "news-005930-2"],
                themes=["semiconductor"],
                risk_flags=[],
            ),
            Candidate(
                ticker="000660",
                score=0.77,
                reasons=["HBM 수요 확대"],
                supporting_news_ids=["news-000660-1"],
                themes=["semiconductor"],
                risk_flags=[],
            ),
        ]
    )
