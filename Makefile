.PHONY: install lint test run e2e

VENV ?= .venv
PYTHON := $(VENV)/bin/python
ENV_FILE ?= .env
ENV_EXPORT = set -a; [ -f $(ENV_FILE) ] && . ./$(ENV_FILE); set +a
CONFIG ?= config/config.yaml
TICKERS_FILE ?= data/seed_tickers.txt
SINCE_HOURS ?= 72
TOP ?= 10
DB_PATH ?= data/storage/stockotter.db
CACHE_DIR ?= data/cache/raw
JSON_OUT ?= data/reports/candidates_top10.json

$(PYTHON):
	python3 -m venv $(VENV)

install: $(PYTHON)
	$(ENV_EXPORT); $(PYTHON) -m pip install -r requirements-dev.txt

lint: $(PYTHON)
	$(ENV_EXPORT); $(PYTHON) -m ruff check .

test: $(PYTHON)
	$(ENV_EXPORT); PYTHONPATH=src $(PYTHON) -m pytest

run: $(PYTHON)
	$(ENV_EXPORT); PYTHONPATH=src $(PYTHON) -m stockotter_small --help

e2e: $(PYTHON)
	rm -rf $(CACHE_DIR) $(DB_PATH) $(JSON_OUT)
	$(ENV_EXPORT); PYTHONPATH=src $(PYTHON) -m stockotter_small run \
		--tickers-file $(TICKERS_FILE) \
		--since-hours $(SINCE_HOURS) \
		--top $(TOP) \
		--db-path $(DB_PATH) \
		--cache-dir $(CACHE_DIR) \
		--config $(CONFIG) \
		--sleep-seconds 0 \
		--json-out $(JSON_OUT)
