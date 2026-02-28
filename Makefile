.PHONY: install lint test run

VENV ?= .venv
PYTHON := $(VENV)/bin/python

$(PYTHON):
	python3 -m venv $(VENV)

install: $(PYTHON)
	$(PYTHON) -m pip install -r requirements-dev.txt

lint: $(PYTHON)
	$(PYTHON) -m ruff check .

test: $(PYTHON)
	PYTHONPATH=src $(PYTHON) -m pytest

run: $(PYTHON)
	PYTHONPATH=src $(PYTHON) -m stockotter_small --help
