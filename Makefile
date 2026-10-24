.PHONY: demo test lint

demo:
	uv sync
	rm -f datamend.duckdb
	uv run datamend seed
	uv run datamend inject stale raw_orders
	uv run datamend inject stale raw_payments
	uv run datamend run
	uv run datamend audit
	uv run datamend lineage raw_orders

test:
	uv run pytest -q

lint:
	uv run ruff check .
