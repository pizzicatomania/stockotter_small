# CODEX_RULES — StockOtter Small

## Project Goal
KR market only.
News-driven idea generation.
Low-price stocks.
Simple rule-based scoring.
LLM used only for structured extraction / clustering / theme extraction.

## Hard Constraints
1. Do NOT add features outside MVP scope.
2. No real trading integration.
3. No complex backtesting framework.
4. No vector DB / heavy infra.
5. Deterministic pipeline.

## Architecture Principles
- Clear module boundaries.
- No circular imports.
- Pydantic for schemas.
- SQLite for metadata.
- File cache for raw content.
- CLI-first design.

## Code Quality
- Python 3.11+
- Type hints required
- pytest required for core logic
- logging module (no print)
- ruff-compatible code style

## LLM Usage Constraints
- JSON output only.
- Validate with schema.
- Never let LLM invent tickers.
- Retry once on invalid JSON.

## Simplicity Rule
If a design feels “clever”, it is probably too complex.
Prefer boring, explicit code.

# 텍스트 출력 룰
모든 응답 및 주석은 한국어로