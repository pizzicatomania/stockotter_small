# stockotter_small

Minimal bootstrap for `stockotter_small` using `pip`, `pyproject.toml` (PEP 621),
`requirements.txt`, and `requirements-dev.txt`.

## Requirements

- Python 3.11+

## Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
make install
```

This installs runtime and development dependencies from:

- `requirements.txt`
- `requirements-dev.txt`

## Run

```bash
make run
```

or directly:

```bash
PYTHONPATH=src .venv/bin/python -m stockotter_small --help
```

Storage debug smoke test:

```bash
PYTHONPATH=src .venv/bin/python -m stockotter_small debug storage
```

Fetch RSS news for seed tickers (sources from config):

```bash
PYTHONPATH=src .venv/bin/python -m stockotter_small fetch-news --tickers-file data/seed_tickers.txt --hours 24 --config config/config.example.yaml
```

Structure unprocessed news into `structured_events` via Gemini:

```bash
export GEMINI_API_KEY=...
PYTHONPATH=src .venv/bin/python -m stockotter_small llm-structure --since-hours 24
```

기본 모델은 `gemini-2.5-flash`이며, 쿼터/사용량 초과(`RESOURCE_EXHAUSTED`, 429/403) 오류가 발생하면
자동으로 `gemini-2.5-flash-lite`로 fallback 됩니다.

Cluster similar news (TF-IDF cosine) and store into `clusters`:

```bash
PYTHONPATH=src .venv/bin/python -m stockotter_small cluster --since-hours 24
```

Score clustered representative events and export top candidates:

```bash
PYTHONPATH=src .venv/bin/python -m stockotter_small score --since-hours 24 --top 10 --json-out data/candidates_top10.json
```

오늘 후보 TOP N을 한 번에 뽑기 (fetch -> structure -> cluster -> score -> report):

```bash
export GEMINI_API_KEY=...
PYTHONPATH=src .venv/bin/python -m stockotter_small run --tickers-file data/seed_tickers.txt --since-hours 24 --top 10 --json-out data/reports/candidates_top10.json
```

위 커맨드는 표 형태로 결과를 stdout에 출력하고, JSON 리포트를 파일로 저장합니다.

## Config Notes

- `config/config.example.yaml`의 `sources`는 RSS 소스 리스트입니다.
- RSS `url`에 `{ticker}` 템플릿이 있으면 seed ticker별로 URL을 확장해서 수집합니다.
- Gemini API key 환경변수 이름은 `llm.api_key_env`로 설정합니다(기본값 `GEMINI_API_KEY`).

Update paper-trading positions from daily close CSV (`ticker,date,close`) in EOD mode:

```bash
PYTHONPATH=src .venv/bin/python -m stockotter_small paper step --prices data/daily_close.csv --asof 2026-02-28
```

Filter eligible universe from market snapshot CSV:

```bash
PYTHONPATH=src .venv/bin/python -m stockotter_small universe filter --market-snapshot data/kr_snapshot.csv
```

## Lint

```bash
make lint
```

## Test

```bash
make test
```
