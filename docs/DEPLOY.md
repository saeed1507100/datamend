# Deployment plan: showcase site and portfolio

## Status (2026-10-24)

- Vercel project `datamend` (team `saeed-khan`) is live at https://datamend.vercel.app.
- `datamend.saeedkhan.dev` is added to the project. It goes live once Namecheap has
  an `A` record: host `datamend`, value `76.76.21.21`.
- Auto-deploy from `main` needs the Vercel GitHub app to have access to
  `saeed1507100/datamend`. Then run `vercel git connect` from the repo root.
- `vercel.json` at the repo root builds `site/` and skips deploys when neither
  `site/` nor `vercel.json` changed.

## What gets deployed

| Piece | Where | Why |
| --- | --- | --- |
| Showcase site (`site/`) | Vercel, static | Free, fast, preview deploys per PR |
| datamend engine and agent endpoint | Not deployed | The site replays a recorded real run; nothing public can trigger a model call |
| Code | GitHub, `saeed1507100/datamend` | Linked from the site and the portfolio |

The site is static on purpose. A public "run the AI" button would spend money on
every click and invite abuse. The replay shows a real run, labeled as recorded.

## Steps

1. **Create the Vercel project.** Import `saeed1507100/datamend` in Vercel, then set
   **Root Directory** to `site`. Vercel reads `site/vercel.json`: framework Vite,
   build `npm run build`, output `dist`.
2. **Pick the URL.** Use a subdomain of your portfolio, for example
   `datamend.<your-domain>`. Add it under **Settings → Domains**, then add the CNAME
   record that Vercel shows.
3. **Link it from the portfolio.** Add a project card under your portfolio's
   projects section. Suggested copy:
   - Title: datamend
   - One line: Self-healing data pipelines. Auto-fixes safe incidents, sends the rest
     to a guardrailed AI agent and a human, and audits everything.
   - Tags: Python, DuckDB, lineage graphs, Claude Code, Airflow/Prefect/AWS adapters
   - Links: live replay, GitHub
4. **Keep it fresh.** When the engine changes, run `uv run datamend export-scenario`
   and commit `site/src/scenario.json`. Vercel redeploys on push to `main`.
5. **Record a new AI run when the prompt or guardrails change.** Start
   `datamend agent serve`, run `datamend ai investigate raw_payments:stale` on a
   `make demo` workspace, and save it to `examples/ai/raw_payments_stale.json`.

## If you want the site under a path instead

To serve it at `<your-domain>/projects/datamend`:

1. Set `base: "/projects/datamend/"` in `site/vite.config.ts`.
2. Add a rewrite in the portfolio's Vercel project that sends
   `/projects/datamend/:path*` to the datamend deployment's URL.

## Later: a live sandbox

If you want visitors to run the system, not watch a replay, add a small API (for
example on Fly.io or Render) that runs `make demo` plus `make demo-ai` in a throwaway
folder per request. Use the replay agent only, rate-limit it, and keep the real agent
endpoint on your machine.

## Open questions

- **Portfolio URL.** You mentioned "Scan.dev, then projects, then data mint". I read
  that as your portfolio site's projects section, with a datamend page. Confirm the
  domain, and whether you prefer a subdomain or a path.
- **Vercel account.** The Vercel connector in this session needs authorization
  before I can create the project for you.
