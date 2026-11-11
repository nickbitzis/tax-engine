.PHONY: install test lint fmt run simulate explain lookup nexus returns export reconcile gate golden summary screenshot docker clean

DB ?= data/taxengine.db
PORT ?= 8877

install:
	uv sync --python 3.12

test:
	uv run pytest -q

lint:
	uvx ruff check .

fmt:
	uvx ruff check --fix .

run:
	uv run taxengine --db $(DB) api --port $(PORT)

simulate:
	uv run taxengine simulate

explain:
	uv run taxengine explain G08

lookup:
	uv run taxengine lookup NY 10002 "120 Main St"

nexus:
	uv run taxengine nexus

returns:
	uv run taxengine returns --month 2026-09

export:
	uv run taxengine export TX 2026-03 --out data/exports/return-TX-2026-03.csv

reconcile:
	uv run taxengine reconcile

gate:
	uv run taxengine gate --golden golden/carts.json

golden:
	uv run taxengine golden --out golden/carts.json

summary:
	uv run taxengine summary --out docs/summary.json

screenshot:
	uv run python scripts/screenshot.py --port $(PORT)

docker:
	docker build -t tax-engine:local .

clean:
	rm -rf data .pytest_cache .ruff_cache
