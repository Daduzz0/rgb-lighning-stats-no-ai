PYTHON ?= python3

.PHONY: help validate prepare test
help:
	@echo "make validate | make prepare | make test (tutti offline, nessun nodo avviato)"

validate:
	$(PYTHON) scripts/baseline.py validate

prepare:
	$(PYTHON) scripts/baseline.py prepare

test:
	$(PYTHON) -m unittest discover -s tests -v
