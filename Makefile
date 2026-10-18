PYTHON ?= python3
PROJECT ?= rgb-stats
CONFIG ?= state/$(PROJECT)/config.json
DATASET ?= 2000
RUN_ID ?= run-$(shell date -u +%Y%m%dT%H%M%SZ)
LAYOUT ?= history

.PHONY: help validate prepare test up down status logs test-live replay
help:
	@echo "Offline: make validate prepare test"
	@echo "Docker regtest: make up | make down | make status | make logs"
	@echo "Suite transazioni reali: make test-live RUN_ID=suite-01"
	@echo "Workload: make replay DATASET=2000 RUN_ID=baseline-2000-01"
	@echo "Confronto AIR-GB (20 nodi): benchmark-up | benchmark-run AI_RUN=... | benchmark-compare AI_RUN=... BASELINE_RUN=..."

validate:
	$(PYTHON) scripts/baseline.py validate

prepare:
	$(PYTHON) scripts/baseline.py prepare

test:
	$(PYTHON) -m unittest discover -s tests -v

up:
	$(PYTHON) scripts/lab.py up --project $(PROJECT) --layout $(LAYOUT)

down:
	$(PYTHON) scripts/lab.py down --project $(PROJECT)

status:
	$(PYTHON) scripts/lab.py status --project $(PROJECT)

logs:
	$(PYTHON) scripts/lab.py logs --project $(PROJECT)

test-live:
	$(PYTHON) scripts/transaction_suite.py --config $(CONFIG) --run-id $(RUN_ID)

replay:
	$(PYTHON) scripts/baseline.py replay --config $(CONFIG) --dataset $(DATASET) --run-id $(RUN_ID)

# AIR-GB comparison profile: independent 20-node topology and native single-path RLN.
NODE ?= node
BENCHMARK_PROJECT ?= rgb-stats-benchmark
BENCHMARK_CONFIG ?= state/$(BENCHMARK_PROJECT)/benchmark.json
BENCHMARK_LABEL ?= machine-a
BENCHMARK_OUT ?= results/benchmark/$(RUN_ID)
AI_RUN ?=
BASELINE_RUN ?= $(BENCHMARK_OUT)
BENCHMARK_REPORT ?= results/benchmark/comparison-$(RUN_ID).json

.PHONY: benchmark-up benchmark-down benchmark-status benchmark-logs benchmark-run benchmark-compare benchmark-contract-tests
benchmark-up:
	$(PYTHON) benchmark/setup.py up --project "$(BENCHMARK_PROJECT)" --label "$(BENCHMARK_LABEL)"

benchmark-down:
	$(PYTHON) benchmark/setup.py down --project "$(BENCHMARK_PROJECT)"

benchmark-status:
	$(PYTHON) benchmark/setup.py status --project "$(BENCHMARK_PROJECT)"

benchmark-logs:
	$(PYTHON) benchmark/setup.py logs --project "$(BENCHMARK_PROJECT)"

benchmark-run:
	$(NODE) benchmark/run.mjs --config "$(BENCHMARK_CONFIG)" $(if $(AI_RUN),--reference "$(AI_RUN)",) --out "$(BENCHMARK_OUT)"

benchmark-compare:
	@test -n "$(AI_RUN)" || { echo "Set AI_RUN to the AIR-GB run directory"; exit 1; }
	$(NODE) benchmark/compare.mjs --ai "$(AI_RUN)" --baseline "$(BASELINE_RUN)" --out "$(BENCHMARK_REPORT)"

benchmark-contract-tests:
	$(NODE) --test benchmark/benchmark.test.mjs benchmark/stock.test.mjs
	$(PYTHON) -m unittest discover -s tests -p test_benchmark_single_path.py -v
