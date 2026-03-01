# stockotter_small

Minimal bootstrap for `stockotter_small` using `pip`, `pyproject.toml` (PEP 621),
`requirements.txt`, and `requirements-dev.txt`.

## Requirements

- Python 3.11+

## Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
cp .env.example .env
make install
```

`make` 타겟(`install`, `lint`, `test`, `run`, `e2e`)은 실행 시 `.env`를 자동으로 로드합니다.

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

StructuredEvent 스키마는 enum 제약을 사용하며, 후처리에서 동의어를 canonical 값으로 정규화합니다.
분류 불확실 시 `event_type=\"UNKNOWN\"` + low confidence 전략을 사용합니다.

Cluster similar news (TF-IDF cosine) and store into `clusters`:

```bash
PYTHONPATH=src .venv/bin/python -m stockotter_small cluster --since-hours 24
```

StructuredEvent 추출 품질 평가(오프라인, recorded output 사용):

```bash
PYTHONPATH=src .venv/bin/python -m stockotter_small llm-eval \
  --dataset 'data/llm_eval/*.json' \
  --report out/llm_eval.json \
  --mode recorded
```

기존 프롬프트 baseline(`baseline_output`) 대비 개선 프롬프트(`recorded_output`) 비교:

```bash
PYTHONPATH=src .venv/bin/python -m stockotter_small llm-eval \
  --dataset 'data/llm_eval/p1_1_subset.json' \
  --recorded-field recorded_output \
  --compare-baseline \
  --report out/llm_eval_compare.json \
  --mode recorded
```

평가 데이터 샘플 포맷:

```json
{
  "news_id": "eval-0001",
  "title": "삼성전자, 실적 가이던스 상향",
  "snippet": "분기 전망 개선",
  "raw_text": "기사 본문...",
  "expected": {
    "event_type": "earnings_guidance",
    "direction": "positive",
    "horizon": "short_term",
    "risk_flags": []
  },
  "recorded_output": {
    "event_type": "earnings_guidance",
    "direction": "positive",
    "horizon": "short_term",
    "risk_flags": []
  }
}
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

캐시/DB/기존 리포트를 지우고 E2E를 새로 실행하려면:

```bash
export GEMINI_API_KEY=...
make e2e
```

## Config Notes

- `config/config.example.yaml`의 `sources`는 RSS 소스 리스트입니다.
- RSS `url`에 `{ticker}` 템플릿이 있으면 seed ticker별로 URL을 확장해서 수집합니다.
- RSS `url`에 `{stock_name}` 또는 `{stock_name_urlencoded}`를 쓰면
  `data/ticker_map.json`의 종목명 기준으로 소스를 확장합니다.
- Gemini API key 환경변수 이름은 `llm.api_key_env`로 설정합니다(기본값 `GEMINI_API_KEY`).

## Google RSS 품질 안정화

- Google RSS 링크(`news.google.com/rss/articles/...`)는 canonical URL 정규화 후 저장합니다.
- `data/ticker_map.json`의 종목명 사전으로 제목/요약 기반 ticker 매핑을 수행합니다.
- 노이즈 타이틀(광고/협찬/추천주/짧은 제목/중복 제목 해시)은 LLM 처리 전 단계에서 제외합니다.
- 클러스터링 직전 제목 정규화 기반 exact dedupe를 한 번 더 수행해 중복 기사 영향을 줄입니다.

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
