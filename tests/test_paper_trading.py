from __future__ import annotations

from datetime import date

import pytest
from typer.testing import CliRunner

from stockotter_small.cli import app
from stockotter_v2.paper import (
    PaperEventType,
    PositionState,
    apply_eod_rules,
    create_entry_position,
)
from stockotter_v2.storage import Repository


def test_paper_rule_take_profit_trigger() -> None:
    position = create_entry_position(
        ticker="005930",
        entry_price=100.0,
        entry_date=date(2026, 2, 25),
    )

    next_position, events = apply_eod_rules(
        position,
        close=108.0,
        asof=date(2026, 2, 26),
    )

    assert next_position.state == PositionState.PARTIAL_TP
    assert next_position.qty_remaining == pytest.approx(0.5)
    assert next_position.highest_close_since_tp == pytest.approx(108.0)
    assert [event.event_type for event in events] == [PaperEventType.PARTIAL_TP]


def test_paper_rule_trailing_stop_trigger() -> None:
    position = create_entry_position(
        ticker="005930",
        entry_price=100.0,
        entry_date=date(2026, 2, 25),
    )

    position, _ = apply_eod_rules(
        position,
        close=108.0,
        asof=date(2026, 2, 26),
    )
    position, events_day3 = apply_eod_rules(
        position,
        close=112.0,
        asof=date(2026, 2, 27),
    )
    position, events_day4 = apply_eod_rules(
        position,
        close=104.0,
        asof=date(2026, 2, 28),
    )

    assert position.state == PositionState.EXITED
    assert position.qty_remaining == 0.0
    assert events_day3 == []
    assert [event.event_type for event in events_day4] == [PaperEventType.TRAILING_STOP]


def test_paper_rule_stop_loss_trigger() -> None:
    position = create_entry_position(
        ticker="005930",
        entry_price=100.0,
        entry_date=date(2026, 2, 25),
    )

    next_position, events = apply_eod_rules(
        position,
        close=93.0,
        asof=date(2026, 2, 26),
    )

    assert next_position.state == PositionState.EXITED
    assert next_position.exit_price == pytest.approx(93.0)
    assert next_position.qty_remaining == 0.0
    assert [event.event_type for event in events] == [PaperEventType.STOP_LOSS]


def test_paper_rule_state_transition_sequence() -> None:
    position = create_entry_position(
        ticker="005930",
        entry_price=100.0,
        entry_date=date(2026, 2, 25),
    )
    states = [position.state]
    event_types: list[PaperEventType] = []

    for asof, close in [
        (date(2026, 2, 26), 108.0),
        (date(2026, 2, 27), 111.0),
        (date(2026, 2, 28), 103.0),
    ]:
        position, events = apply_eod_rules(position, close=close, asof=asof)
        states.append(position.state)
        event_types.extend(event.event_type for event in events)

    assert states == [
        PositionState.ENTRY,
        PositionState.PARTIAL_TP,
        PositionState.TRAILING,
        PositionState.EXITED,
    ]
    assert event_types == [PaperEventType.PARTIAL_TP, PaperEventType.TRAILING_STOP]


def test_cli_paper_step_persists_position_and_events(tmp_path) -> None:
    prices_path = tmp_path / "daily_close.csv"
    prices_path.write_text(
        "\n".join(
            [
                "ticker,date,close",
                "005930,2026-02-25,100",
                "005930,2026-02-26,108",
                "005930,2026-02-27,112",
                "005930,2026-02-28,104",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "paper.db"
    runner = CliRunner()

    for asof in ["2026-02-25", "2026-02-26", "2026-02-27", "2026-02-28"]:
        result = runner.invoke(
            app,
            [
                "paper",
                "step",
                "--prices",
                str(prices_path),
                "--asof",
                asof,
                "--db-path",
                str(db_path),
            ],
        )
        assert result.exit_code == 0
        assert f"asof={asof}" in result.output

    repo = Repository(db_path)
    position = repo.get_paper_position("005930")
    assert position is not None
    assert position.state == PositionState.EXITED
    assert position.qty_remaining == 0.0

    events = repo.list_paper_events(ticker="005930")
    assert [event.event_type for event in events] == [
        PaperEventType.PARTIAL_TP,
        PaperEventType.TRAILING_STOP,
    ]
