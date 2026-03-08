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
JSON 리포트의 각 candidate에는 `supporting_items`가 포함되며, 기사별 `raw_text_summary`와
`llm_analysis`(event_type/direction/confidence/horizon/themes/risk_flags)를 확인할 수 있습니다.

캐시/DB/기존 리포트를 지우고 E2E를 새로 실행하려면:

```bash
export GEMINI_API_KEY=...
make e2e
```

## KIS 인증 테스트

`.env` 또는 셸 환경변수에 KIS 값을 설정한 뒤 토큰/인증 상태를 점검할 수 있습니다.

필수 환경변수:

- `KIS_APP_KEY`
- `KIS_APP_SECRET`
- `KIS_ACCOUNT` (`12345678` 또는 `12345678-01` 형식)
- `KIS_ENV` (`paper` 또는 `live`)

실행 예시:

```bash
PYTHONPATH=src .venv/bin/python -m stockotter_small kis auth-test --ticker 005930
PYTHONPATH=src .venv/bin/python -m stockotter_small kis price 005930
PYTHONPATH=src .venv/bin/python -m stockotter_small kis positions
PYTHONPATH=src .venv/bin/python -m stockotter_small kis buy-market 005930 --cash-amount 150000
PYTHONPATH=src .venv/bin/python -m stockotter_small kis buy-limit 005930 --qty 2 --price 70000
PYTHONPATH=src .venv/bin/python -m stockotter_small kis sell-market 005930 --qty 2
PYTHONPATH=src .venv/bin/python -m stockotter_small kis sell-limit 005930 --qty 2 --price 71000
PYTHONPATH=src .venv/bin/python -m stockotter_small kis buy-limit 005930 --qty 1 --price 70000 --confirm --live
```

`kis positions`는 계좌 요약(balance)과 보유 종목 목록(positions)을 함께 출력합니다.
KIS 응답은 pydantic DTO(`KISPriceQuote`, `KISAccountBalance`, `KISPosition`)로 검증되며,
인증 오류/호출 한도 초과는 각각 별도 에러 타입으로 매핑됩니다.
주문 커맨드는 기본적으로 dry-run이며, 실제 모의주문 전송은 `--confirm`일 때만 허용됩니다.
`KIS_ENV=paper`에서는 `--confirm`만 있으면 모의주문 전송이 가능하지만,
`TRADING_DISABLED=1`이면 paper/live 모두 주문 엔드포인트가 차단됩니다.
`KIS_ENV=live`일 때 실제 주문은 `--confirm`과 `--live`가 모두 있어야 하며,
`config`의 `trading` 한도/allowlist와 `TRADING_DISABLED=1` kill-switch를 모두 통과해야 합니다.
실제 전송 결과와 dry-run 내역은 모두 SQLite `orders` 테이블에 저장되며,
저장되는 request/response payload에서는 계좌번호 등 민감 필드를 redaction 합니다.
비밀값은 로그/출력에 노출하지 않습니다.

운영용 `config/config.yaml`의 보수적 live 예시는 아래와 같습니다.
이 파일은 `.gitignore` 대상이므로 로컬 운영 환경에서만 유지하면 됩니다.

```json
"trading": {
  "live_ticker_allowlist": ["005930", "000660"],
  "max_daily_order_count": 2,
  "max_cash_per_order": 300000,
  "max_total_cash_per_day": 500000
}
```

paper 주문 전/후 확인 커맨드는 아래 순서로 묶어서 실행하는 편이 안전합니다.

```bash
# 1) 환경 로드
source .venv/bin/activate
set -a && source .env && set +a

# 2) 주문 전 상태 확인
KIS_ENV=paper PYTHONPATH=src python -m stockotter_small kis positions
KIS_ENV=paper PYTHONPATH=src python -m stockotter_small kis price 005930

# 3) dry-run 확인
KIS_ENV=paper PYTHONPATH=src python -m stockotter_small kis buy-limit 005930 --qty 1 --price 70000 --config config/config.yaml

# 4) 실제 paper 주문 전송
KIS_ENV=paper PYTHONPATH=src python -m stockotter_small kis buy-limit 005930 --qty 1 --price 70000 --confirm --config config/config.yaml

# 5) 주문 후 상태 재확인
KIS_ENV=paper PYTHONPATH=src python -m stockotter_small kis positions
sqlite3 data/storage/stockotter.db "select order_id, environment, side, order_type, ticker, quantity, status, external_order_id, created_at from orders order by created_at desc limit 5;"
```

paper 주문 실행 전 체크리스트:

1. `.venv`가 활성화되어 있는지 확인합니다.
2. `KIS_ENV=paper`가 설정되어 있는지 확인합니다.
3. `TRADING_DISABLED`가 비어 있거나 `0`인지 확인합니다.
4. 먼저 `--confirm` 없이 dry-run으로 주문 내용을 확인합니다.
5. `PYTHONPATH=src .venv/bin/python -m stockotter_small kis positions`로 계좌/보유 상태를 먼저 확인합니다.
6. 실제 전송 시에는 `--confirm --config config/config.yaml`를 함께 명시합니다.

live 주문 전/후 확인 커맨드는 아래 순서로 묶어서 실행하는 편이 안전합니다.

```bash
# 1) 환경 로드
source .venv/bin/activate
set -a && source .env && set +a

# 2) 주문 전 상태 확인
KIS_ENV=live PYTHONPATH=src python -m stockotter_small kis positions
KIS_ENV=live PYTHONPATH=src python -m stockotter_small kis price 005930

# 3) dry-run 확인
KIS_ENV=live PYTHONPATH=src python -m stockotter_small kis buy-limit 005930 --qty 1 --price 70000 --config config/config.yaml

# 4) 실제 live 주문 전송
KIS_ENV=live PYTHONPATH=src python -m stockotter_small kis buy-limit 005930 --qty 1 --price 70000 --confirm --live --config config/config.yaml

# 5) 주문 후 상태 재확인
KIS_ENV=live PYTHONPATH=src python -m stockotter_small kis positions
sqlite3 data/storage/stockotter.db "select order_id, environment, side, order_type, ticker, quantity, status, external_order_id, created_at from orders order by created_at desc limit 5;"
```

live 주문 실행 전 체크리스트:

1. `.venv`가 활성화되어 있는지 확인합니다.
2. `KIS_ENV=live`가 설정되어 있는지 확인합니다.
3. `TRADING_DISABLED`가 비어 있거나 `0`인지 확인합니다.
4. 주문할 ticker가 `config/config.yaml`의 `trading.live_ticker_allowlist`에 포함되는지 확인합니다.
5. 주문 금액이 `trading.max_cash_per_order` 이하인지 확인합니다.
6. 오늘 누적 매수 금액이 `trading.max_total_cash_per_day`를 넘지 않는지 확인합니다.
7. 오늘 실주문 건수가 `trading.max_daily_order_count` 미만인지 확인합니다.
8. 먼저 `--confirm` 없이 dry-run으로 주문 내용을 확인합니다.
9. `PYTHONPATH=src .venv/bin/python -m stockotter_small kis positions`로 계좌/보유 상태를 먼저 확인합니다.
10. 실제 전송 시에는 `--confirm --live --config config/config.yaml`를 함께 명시합니다.

## Config Notes

- `config/config.example.yaml`의 `sources`는 RSS 소스 리스트입니다.
- RSS `url`에 `{ticker}` 템플릿이 있으면 seed ticker별로 URL을 확장해서 수집합니다.
- RSS `url`에 `{stock_name}` 또는 `{stock_name_urlencoded}`를 쓰면
  `data/ticker_map.json`의 종목명 기준으로 소스를 확장합니다.
- Gemini API key 환경변수 이름은 `llm.api_key_env`로 설정합니다(기본값 `GEMINI_API_KEY`).
- `trading.live_ticker_allowlist`가 비어 있지 않으면 live 주문 ticker는 이 목록에 포함되어야 합니다.
- `trading.max_daily_order_count`, `trading.max_cash_per_order`,
  `trading.max_total_cash_per_day`는 live 주문 안전 한도입니다.

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
