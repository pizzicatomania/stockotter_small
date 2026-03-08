from __future__ import annotations

import json
from datetime import date, datetime

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
    format_briefing_message,
    parse_callback_update,
    persist_tg_actions,
    process_callback_action,
)
from stockotter_v2.schemas import Candidate, NewsItem, TelegramActionStatus, TelegramActionType
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


class _FakeTelegramSender:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.reply_markups: list[dict[str, object] | None] = []
        self.callback_query_ids: list[str] = []

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
    assert "- 삼성전자, AI 메모리 공급 확대 수혜 전망" in message
    assert "2. 000660 | score 0.770" in message


def test_build_inline_keyboard_and_actions_uses_short_action_ids(monkeypatch, tmp_path) -> None:
    fixed_now = datetime.fromisoformat("2026-03-08T08:30:00+09:00")
    monkeypatch.setattr(repo_module, "now_in_seoul", lambda: fixed_now)
    repo = Repository(tmp_path / "storage.db")
    _seed_briefing_repo(repo)
    candidates = build_briefing_candidates(repo=repo, asof=date(2026, 3, 8), limit=10)

    reply_markup, actions = build_inline_keyboard_and_actions(candidates=candidates, now=fixed_now)

    assert len(reply_markup["inline_keyboard"]) == 2
    assert len(actions) == 6
    first_row = reply_markup["inline_keyboard"][0]
    assert [button["text"] for button in first_row] == ["BUY", "SELL", "SKIP"]
    for action in actions:
        assert len(action.action_id.encode("utf-8")) <= 64
        assert action.action_id.startswith(f"A:{action.action_type.value}:")
        assert action.status is TelegramActionStatus.PENDING


def test_build_briefing_candidates_rejects_snapshot_date_mismatch(monkeypatch, tmp_path) -> None:
    fixed_now = datetime.fromisoformat("2026-03-08T08:30:00+09:00")
    monkeypatch.setattr(repo_module, "now_in_seoul", lambda: fixed_now)
    repo = Repository(tmp_path / "storage.db")
    _seed_briefing_repo(repo)

    with pytest.raises(ValueError, match="candidate snapshot date mismatch"):
        build_briefing_candidates(repo=repo, asof=date(2026, 3, 9), limit=10)


def test_telegram_client_send_success() -> None:
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

    result = client.send_message("hello briefing")

    assert result.message_id == 12345
    assert session.calls[0]["json"] == {
        "chat_id": "123456",
        "text": "hello briefing",
        "disable_web_page_preview": True,
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


def test_cli_tg_send_briefing_success(monkeypatch, tmp_path) -> None:
    fixed_now = datetime.fromisoformat("2026-03-08T08:30:00+09:00")
    monkeypatch.setattr(repo_module, "now_in_seoul", lambda: fixed_now)
    repo = Repository(tmp_path / "storage.db")
    _seed_briefing_repo(repo)

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
        ],
    )

    assert result.exit_code == 0
    assert "telegram sent asof=2026-03-08 candidates=2 message_id=777" in result.output
    assert len(fake_sender.messages) == 1
    assert "[StockOtter] Morning Briefing" in fake_sender.messages[0]
    assert "1. 005930 | score 0.910" in fake_sender.messages[0]
    assert fake_sender.reply_markups[0] is not None
    stored_actions = repo.list_tg_actions()
    assert len(stored_actions) == 6
    assert all(action.message_id == 777 for action in stored_actions)


def test_parse_callback_update_and_process_ack_creates_order_intent(monkeypatch, tmp_path) -> None:
    fixed_now = datetime.fromisoformat("2026-03-08T08:30:00+09:00")
    repo = Repository(tmp_path / "storage.db")
    _seed_briefing_repo(repo)
    candidates = build_briefing_candidates(repo=repo, asof=date(2026, 3, 8), limit=10)
    _, actions = build_inline_keyboard_and_actions(candidates=candidates, now=fixed_now)
    persist_tg_actions(repo=repo, actions=actions, message_id=555)
    buy_action = next(action for action in actions if action.action_type is TelegramActionType.BUY)

    payload = json.dumps(
        {
            "callback_query": {
                "id": "cb-001",
                "data": buy_action.action_id,
            }
        },
        ensure_ascii=False,
    )
    envelope = parse_callback_update(payload)
    result = process_callback_action(
        repo=repo,
        action_id=envelope.action_id,
        callback_query_id=envelope.callback_query_id,
        now=fixed_now,
    )

    assert result.action.status is TelegramActionStatus.ACKED
    assert result.created_intent is not None
    assert result.created_intent.is_dry_run is True
    assert result.created_intent.action_id == buy_action.action_id
    assert result.created_intent.action_type is TelegramActionType.BUY
    stored_action = repo.get_tg_action(buy_action.action_id)
    assert stored_action is not None
    assert stored_action.status is TelegramActionStatus.ACKED
    assert stored_action.callback_query_id == "cb-001"
    stored_intent = repo.get_order_intent_by_action(buy_action.action_id)
    assert stored_intent is not None
    assert stored_intent.is_dry_run is True


def test_cli_tg_handle_callback_success(monkeypatch, tmp_path) -> None:
    fixed_now = datetime.fromisoformat("2026-03-08T08:30:00+09:00")
    repo = Repository(tmp_path / "storage.db")
    _seed_briefing_repo(repo)
    candidates = build_briefing_candidates(repo=repo, asof=date(2026, 3, 8), limit=10)
    _, actions = build_inline_keyboard_and_actions(candidates=candidates, now=fixed_now)
    persist_tg_actions(repo=repo, actions=actions, message_id=777)
    sell_action = next(
        action for action in actions if action.action_type is TelegramActionType.SELL
    )

    update_path = tmp_path / "callback.json"
    update_path.write_text(
        json.dumps(
            {
                "callback_query": {
                    "id": "cb-002",
                    "data": sell_action.action_id,
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
        ],
    )

    assert result.exit_code == 0
    assert "telegram callback" in result.output
    assert f"action_id={sell_action.action_id}" in result.output
    assert "type=sell" in result.output
    assert "status=acked" in result.output
    assert fake_sender.callback_query_ids == ["cb-002"]
    stored_intent = repo.get_order_intent_by_action(sell_action.action_id)
    assert stored_intent is not None
    assert stored_intent.is_dry_run is True


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
