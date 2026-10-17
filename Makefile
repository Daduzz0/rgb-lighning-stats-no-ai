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
