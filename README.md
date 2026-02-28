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

Fetch Naver Finance news for seed tickers:

```bash
PYTHONPATH=src .venv/bin/python -m stockotter_small fetch-news --tickers-file data/seed_tickers.txt --hours 24
```

Structure unprocessed news into `structured_events` via Gemini:

```bash
export GEMINI_API_KEY=...
PYTHONPATH=src .venv/bin/python -m stockotter_small llm-structure --since-hours 24
```

Cluster similar news (TF-IDF cosine) and store into `clusters`:

```bash
PYTHONPATH=src .venv/bin/python -m stockotter_small cluster --since-hours 24
```

Score clustered representative events and export top candidates:

```bash
PYTHONPATH=src .venv/bin/python -m stockotter_small score --since-hours 24 --top 10 --json-out data/candidates_top10.json
```

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
