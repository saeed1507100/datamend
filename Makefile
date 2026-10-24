.PHONY: demo demo-ai test lint

# The demo works on a copy of examples/ with its own git repo, so the AI's
# fix branch lands there, never in this repository.
export DATAMEND_DB := demo/datamend.duckdb
export DATAMEND_CATALOG := demo/datasets.yaml
export DATAMEND_AI_RUNS := demo/.datamend/ai

demo:
	uv sync
	rm -rf demo && cp -R examples demo
	git -C demo init -q && git -C demo add -A && \
	  git -C demo -c user.name=demo -c user.email=demo@localhost commit -qm "pipelines"
	uv run datamend seed
	uv run datamend inject stale raw_orders
	uv run datamend inject stale raw_payments
	uv run datamend inject gap raw_customers
	uv run datamend run
	uv run datamend audit
	uv run datamend lineage raw_orders
	uv run datamend lineage --mermaid

# After `make demo`: replay the recorded AI investigation, approve it, check again.
demo-ai:
	uv run datamend ai investigate raw_payments:stale --replay examples/ai/raw_payments_stale.json
	uv run datamend ai approve $$(uv run datamend ai list | awk '/proposed/{print $$1}' | tail -1) --by demo-reviewer
	uv run datamend run

test:
	uv run pytest -q

lint:
	uv run ruff check .
