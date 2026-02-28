from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

REQUIRED_COLUMNS = ("ticker", "price", "value_traded_5d_avg", "is_managed")
TRUE_VALUES = {"1", "true", "t", "yes", "y"}
FALSE_VALUES = {"0", "false", "f", "no", "n"}


@dataclass(slots=True)
class UniverseFilterResult:
    eligible_tickers: list[str]
    total_rows: int
    excluded_counts: dict[str, int]


def filter_market_snapshot(
    market_snapshot_path: Path,
    *,
    min_price: float,
    max_price: float,
    min_value_traded_5d_avg: float,
    exclude_managed: bool,
) -> UniverseFilterResult:
    excluded_counts: defaultdict[str, int] = defaultdict(int)
    eligible_tickers: list[str] = []
    seen: set[str] = set()
    total_rows = 0

    with market_snapshot_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("market snapshot csv is empty")

        missing_columns = [column for column in REQUIRED_COLUMNS if column not in reader.fieldnames]
        if missing_columns:
            missing_text = ", ".join(missing_columns)
            raise ValueError(f"market snapshot csv missing required columns: {missing_text}")

        for row in reader:
            total_rows += 1

            ticker = (row.get("ticker") or "").strip()
            if not ticker:
                _bump(excluded_counts, "missing_ticker")
                continue

            raw_price = row.get("price")
            price = _parse_float(raw_price)
            if price is None:
                _bump(
                    excluded_counts,
                    "missing_price" if _is_missing(raw_price) else "invalid_price",
                )
                continue

            raw_value_traded = row.get("value_traded_5d_avg")
            value_traded_5d_avg = _parse_float(raw_value_traded)
            if value_traded_5d_avg is None:
                _bump(
                    excluded_counts,
                    "missing_value_traded_5d_avg"
                    if _is_missing(raw_value_traded)
                    else "invalid_value_traded_5d_avg",
                )
                continue

            raw_is_managed = row.get("is_managed")
            is_managed = _parse_bool(raw_is_managed)
            if is_managed is None:
                _bump(
                    excluded_counts,
                    "missing_is_managed" if _is_missing(raw_is_managed) else "invalid_is_managed",
                )
                continue

            if exclude_managed and is_managed:
                _bump(excluded_counts, "managed_stock")
                continue

            if price < min_price or price > max_price:
                _bump(excluded_counts, "price_out_of_range")
                continue

            if value_traded_5d_avg < min_value_traded_5d_avg:
                _bump(excluded_counts, "below_min_value_traded_5d_avg")
                continue

            if ticker in seen:
                _bump(excluded_counts, "duplicate_ticker")
                continue

            seen.add(ticker)
            eligible_tickers.append(ticker)

    return UniverseFilterResult(
        eligible_tickers=eligible_tickers,
        total_rows=total_rows,
        excluded_counts=dict(sorted(excluded_counts.items())),
    )


def _is_missing(value: object | None) -> bool:
    if value is None:
        return True
    return str(value).strip() == ""


def _parse_float(value: object | None) -> float | None:
    if _is_missing(value):
        return None

    text = str(value).strip().replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def _parse_bool(value: object | None) -> bool | None:
    if isinstance(value, bool):
        return value
    if _is_missing(value):
        return None

    text = str(value).strip().lower()
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return False
    return None


def _bump(counter: defaultdict[str, int], key: str) -> None:
    counter[key] += 1
