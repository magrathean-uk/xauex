# XAUEX Makefile
# Requires: Python 3.11+, Rust/cargo, make
#
# Usage:
#   make              — show this help
#   make test         — run all tests
#   make build        — compile Rust tick_parser extension
#   make install      — install tick_parser .so into current Python env
#   make backtest     — run backtester (set DATE_FROM / DATE_TO)
#   make download     — download tick data (set DATE_FROM / DATE_TO)
#   make auth         — run OAuth2 authentication flow
#   make deploy       — deploy to /opt/xauex (requires root)
#   make setup-dirs   — create runtime dirs + system user (requires root)
#   make logs         — tail the bot log
#   make dashboard    — launch the TUI dashboard
#   make clean        — remove build artefacts

.PHONY: all help test build install backtest download auth deploy setup-dirs logs dashboard clean lint

# ─── Configurable defaults ────────────────────────────────────────────────────
DATE_FROM   ?= 2022-01-01
DATE_TO     ?= 2026-02-28
DATA_DIR    ?= data/dukascopy
OUTPUT      ?=
WORKERS     ?= 4
ENV_FILE    ?= .env
LOG_FILE    ?= /var/log/xauex/xauex.log
PYTHON      ?= python3
LINT_PYTHON ?= .venv/bin/python
MATURIN     ?= maturin

TICK_DIR    := tick_parser
DIST_DIR    := $(TICK_DIR)/dist

# ─── Default target: help ─────────────────────────────────────────────────────
all: help

help:
	@echo ""
	@echo "  XAUEX — Automated XAUUSD Trading Bot"
	@echo "  ────────────────────────────────────────────────────────────"
	@echo "  make test                    Run all tests"
	@echo "  make build                   Compile Rust tick_parser extension"
	@echo "  make install                 Install tick_parser .whl into Python"
	@echo "  make backtest                Run backtester (DATE_FROM / DATE_TO)"
	@echo "  make download                Download tick data (DATE_FROM / DATE_TO)"
	@echo "  make auth                    Run OAuth2 authentication flow"
	@echo "  make setup-dirs              Create /var/lib/xauex etc (needs root)"
	@echo "  make deploy                  Deploy to /opt/xauex (needs root)"
	@echo "  make logs                    Tail the bot log file"
	@echo "  make dashboard               Launch the TUI dashboard"
	@echo "  make clean                   Remove build artefacts"
	@echo ""
	@echo "  Variables (override on command line):"
	@echo "    DATE_FROM=$(DATE_FROM)  DATE_TO=$(DATE_TO)"
	@echo "    DATA_DIR=$(DATA_DIR)    WORKERS=$(WORKERS)"
	@echo "    ENV_FILE=$(ENV_FILE)    LOG_FILE=$(LOG_FILE)"
	@echo ""
	@echo "  Example: make backtest DATE_FROM=2022-01-01 DATE_TO=2026-02-28"
	@echo "           make download DATE_FROM=2022 DATE_TO=2026"
	@echo ""

# ─── Tests ────────────────────────────────────────────────────────────────────
test:
	cd .. && $(PYTHON) -m pytest xauex/tests/ -v

test-fast:
	cd .. && $(PYTHON) -m pytest xauex/tests/ -x -q --tb=short

# ─── Rust extension ───────────────────────────────────────────────────────────
build:
	@echo "Building Rust tick_parser…"
	cd $(TICK_DIR) && $(MATURIN) build --release --out ../$(DIST_DIR)
	@echo "Built: $$(ls $(DIST_DIR)/*.whl)"

install: build
	@echo "Installing tick_parser wheel…"
	$(PYTHON) -m pip install --force-reinstall $(DIST_DIR)/*.whl
	@echo "Done. Verify: python3 -c \"import tick_parser; print(tick_parser.__version__)\""

# ─── Backtest ─────────────────────────────────────────────────────────────────
backtest:
	@echo "Running backtester: $(DATE_FROM) → $(DATE_TO)"
ifdef OUTPUT
	$(PYTHON) -m backtester --from $(DATE_FROM) --to $(DATE_TO) \
		--data-dir $(DATA_DIR) --config $(ENV_FILE) --output $(OUTPUT)
else
	$(PYTHON) -m backtester --from $(DATE_FROM) --to $(DATE_TO) \
		--data-dir $(DATA_DIR) --config $(ENV_FILE)
endif

# ─── Tick data download ───────────────────────────────────────────────────────
download:
	@echo "Downloading XAUUSD tick data: $(DATE_FROM) → $(DATE_TO)"
	$(PYTHON) data/download.py --from $(DATE_FROM) --to $(DATE_TO) \
		--out $(DATA_DIR) --workers $(WORKERS)

# ─── Auth ─────────────────────────────────────────────────────────────────────
auth:
	$(PYTHON) auth.py

# ─── Ops ──────────────────────────────────────────────────────────────────────
setup-dirs:
	sudo ops/setup_dirs.sh

deploy:
	sudo ops/deploy.sh

# ─── Runtime helpers ─────────────────────────────────────────────────────────
logs:
	tail -f $(LOG_FILE)

dashboard:
	$(PYTHON) dashboard.py

lint:
	cd .. && $(LINT_PYTHON) -m ruff check xauex tests
	cd .. && $(LINT_PYTHON) -m mypy --explicit-package-bases --follow-imports=skip \
		xauex/bot/filters/news.py \
		xauex/signal/cftc_cot.py \
		xauex/signal/direct_predictor.py \
		xauex/signal/market_snapshot.py \
		xauex/signal/signal_parser.py \
		--ignore-missing-imports

# ─── Clean ───────────────────────────────────────────────────────────────────
clean:
	rm -rf $(DIST_DIR) $(TICK_DIR)/target
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name '*.pyc' -delete 2>/dev/null || true
	find . -name '*.so' -delete 2>/dev/null || true
	@echo "Clean done."
