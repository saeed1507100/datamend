.PHONY: demo test lint

demo:
	uv sync
	rm -f datamend.duckdb
	uv run datamend seed
	uv run datamend inject stale raw_orders
	uv run datamend inject stale raw_payments
	uv run datamend inject gap raw_customers
	uv run datamend run
	uv run datamend audit
	uv run datamend lineage raw_orders
	uv run datamend lineage --mermaid

test:
	uv run pytest -q

lint:
	uv run ruff check .
