# AI investigation, with guardrails

When an incident is escalated because a fix isn't provably safe, datamend can ask an
AI agent to investigate. The agent's output is a proposal, not a change, until it
passes every guardrail and validation check, and a person approves it.

```
investigate:  gate → sandbox → agent → static guardrails → shadow validation → proposed
approve:      re-check → git branch → apply → rerun → post-checks → audit
```

## Run it

```bash
make demo                                    # raw_payments escalates: job not idempotent
export DATAMEND_AGENT_TOKEN=$(openssl rand -hex 16)
uv run datamend agent serve &                # Messages-API-shaped endpoint on 127.0.0.1:8787
uv run datamend ai investigate raw_payments:stale
uv run datamend ai show <run_id>             # explanation, guardrails, checks, diff, score
uv run datamend ai approve <run_id> --by you  # or: ai reject <run_id> --by you --reason "..."
```

To see the flow without calling a model, `make demo-ai` replays a recorded real run
from `examples/ai/raw_payments_stale.json`.

## The agent endpoint

`datamend agent serve` accepts `POST /v1/messages` in the Anthropic Messages API
shape and returns a Messages API response. Behind it, it runs the local Claude Code
CLI headless:

```
claude -p <prompt> --output-format json --model <model>
  --tools Read,Glob,Grep,Edit --disallowedTools Bash,Write,WebFetch,WebSearch,...
  --permission-mode acceptEdits --max-turns N --max-budget-usd B
  --setting-sources project --strict-mcp-config --no-session-persistence
```

- It binds to `127.0.0.1` and requires a bearer token.
- It runs only in folders under the workspace root. `metadata.workspace` picks the folder.
- The agent can read and edit files. It can't run a shell, browse the web, or use MCP servers.
- `--bare` adds stricter isolation, and needs `ANTHROPIC_API_KEY` auth.

Because the endpoint speaks the Messages API shape, you can swap it for the hosted
Claude API (or the Claude Agent SDK) without changing the fixer.

## Guardrails

| Stage | Rule | Why |
| --- | --- | --- |
| Gate | Only incidents escalated for `ai.eligible_rules` (default: `idempotent`) | An owner opt-out, a wide blast radius, or a cooldown is a human decision |
| Sandbox | The agent edits a copy of the pipeline folder | It never touches live code or data |
| Static | `single_file`: only the incident's job file changed | No drive-by edits |
| Static | `diff_size`: at most `ai.max_diff_lines` | Small changes are reviewable |
| Static | `static_safety`: allowlisted imports, no `open`/`exec`/`eval`, SQL in `con.execute` is a literal with no `DROP`, `TRUNCATE`, `ATTACH`, `COPY`, `ALTER`, ... | No exfiltration, no schema damage, no SQL built at runtime |
| Static | `explained`: root cause, fix, rationale, and risk as JSON | A reviewer can see why |
| Validation | `runs_clean`, `idempotent_rerun`, `idempotent_backfill`, `unique_ids`, `schema_stable`, `volume`, `post_checks` on a shadow copy | The fix must work, be safe to rerun, and not change the data's shape |
| Score | Passed checks / all checks ≥ `ai.min_score` (default 1.0) | Partial credit isn't enough |
| Human | `ai.apply: approve` (default) | A person signs off |
| Apply | Re-check that the job file hasn't changed since the AI read it | No applying over someone's hotfix |
| Apply | Commit to `datamend/ai-fix-<run>` before writing files; nothing is written if any step fails | All or nothing |

Every run is recorded in the `ai_runs` table, whatever its outcome (`refused`,
`rejected`, `failed`, `proposed`, `applied`): the prompt, the response, the
explanation, the diff, each guardrail and check with its detail, the score, token use
and cost, who approved, the commit, and the post-fix checks. The main audit log gets
`ai_proposed` and `ai_fix` entries.

## Configuration

```yaml
ai:
  endpoint: http://127.0.0.1:8787
  model: sonnet
  eligible_rules: [idempotent]
  max_diff_lines: 40
  max_turns: 12
  max_budget_usd: 1.0
  min_score: 1.0
  apply: approve        # or auto: apply as soon as everything passes
  git_branch: true
  git_push: false
```

## What the guardrails caught while building this

The first real runs were rejected, and each rejection was correct to fail closed:

1. `static_safety` matched "Load" in a docstring as the SQL keyword `LOAD`. Now it
   scans only SQL passed to `con.execute`, and that SQL must be a literal.
2. The validator's row checksum used SQL that DuckDB rejects, so the patch scored 0.
3. Post-checks read live tables instead of the shadow copy, so they reported stale data.

In each case, nothing shipped, and the failure is in the audit trail.
