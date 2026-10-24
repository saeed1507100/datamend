import raw from "./scenario.json";
import { Diff, Replay } from "./Replay";
import type { Scenario } from "./types";

const data = raw as unknown as Scenario;
const REPO = "https://github.com/saeed1507100/datamend";
const ai = data.steps.find((s) => s.key === "investigate")!.ai!;

const RULES = [
  ["registered_fix", "The incident type has a registered fix: stale → rerun, missing rows → backfill."],
  ["idempotent", "The job is declared idempotent, so a rerun can't duplicate data."],
  ["blast_radius", `The blast radius is at most ${data.policy.max_blast_radius} downstream datasets.`],
  ["cooldown", `The same fix didn't run in the last ${data.policy.cooldown_minutes} minutes.`],
  ["not_manual", "No dataset in the blast radius is owner-tagged critical: manual."],
];

const AI_FLOW = [
  ["Gate", "Only incidents escalated for an AI-eligible rule (here: idempotent). Owner opt-outs, wide blast radius, and cooldowns stay with people."],
  ["Sandbox", "The agent works on a copy of the pipeline folder. It never sees credentials or live data."],
  ["Locked-down agent", "Claude Code runs headless behind a Messages-API-shaped endpoint with Read, Glob, Grep, and Edit only. No shell, web, or MCP."],
  ["Static guardrails", "One file changed, a small diff, allowlisted imports, literal SQL with no DDL or admin statements, and a structured explanation."],
  ["Shadow validation", "The patch runs twice on a copy of the warehouse. Reruns and backfills must change nothing; ids stay unique; schema and volume hold; downstream checks pass."],
  ["Human approval", "A person approves. datamend commits to a branch, applies, reruns, and re-checks. Nothing is applied if any step fails."],
];

const ADAPTERS = [
  ["Warehouse", ["DuckDB", "Postgres", "Any DB-API: Redshift, SQLite, …"]],
  ["Orchestrator", ["Local Python jobs", "Apache Airflow (REST)", "Prefect (REST)", "AWS Glue", "AWS Step Functions"]],
  ["Lineage", ["datasets.yaml", "OpenLineage run events"]],
] as const;

export function App() {
  return (
    <>
      <header className="top">
        <a className="brand" href="#">datamend</a>
        <nav>
          <a href="#replay">Replay</a><a href="#ai">AI</a><a href="#adapters">Plug in</a><a href="#audit">Audit</a>
          <a className="gh" href={REPO}>GitHub</a>
        </nav>
      </header>

      <main>
        <section className="hero">
          <p className="eyebrow">Self-healing data pipelines</p>
          <h1>Auto-fix the safe data incidents.<br />Escalate the rest.<br /><span>Audit everything.</span></h1>
          <p className="lede">
            datamend watches dataset freshness and quality, walks the lineage graph to find the root cause and
            blast radius, fixes what is provably safe, and hands the rest to a guardrailed AI agent and a person.
          </p>
          <div className="cta">
            <a className="btn primary" href="#replay">Watch a real run</a>
            <a className="btn" href={REPO}>Read the code</a>
          </div>
          <ul className="facts">
            <li><strong>{data.graph.nodes.length}</strong> datasets, 3 layers</li>
            <li><strong>{data.steps[1].incidents?.length}</strong> incidents → <strong>{data.steps[2].roots?.length}</strong> root causes</li>
            <li><strong>2</strong> auto-fixed, <strong>1</strong> fixed by AI + human</li>
            <li><strong>{data.audit.length}</strong> audit entries</li>
          </ul>
        </section>

        <Replay data={data} />

        <section className="band">
          <h2>Five rules before any automatic fix</h2>
          <p className="section-lede">An allowlist, not a denylist. If any rule fails, the incident goes to a person with the failed rule as the reason.</p>
          <ol className="rules">
            {RULES.map(([r, d]) => <li key={r}><code>{r}</code><span>{d}</span></li>)}
          </ol>
        </section>

        <section className="band" id="ai">
          <h2>AI that has to prove its fix</h2>
          <p className="section-lede">
            When a fix isn't provably safe, an agent investigates. Its output is a proposal, not a change, until it passes every check
            and a person signs off.
          </p>
          <ol className="flow">
            {AI_FLOW.map(([t, d], n) => <li key={t}><span className="n">{n + 1}</span><h4>{t}</h4><p>{d}</p></li>)}
          </ol>
          <div className="ai-card">
            <div className="ai-why">
              <h3>From the recorded run</h3>
              <dl>
                <dt>Root cause</dt><dd>{ai.explanation.root_cause}</dd>
                <dt>Why this fix</dt><dd>{ai.explanation.rationale}</dd>
                <dt>Risk, in its own words</dt><dd>{ai.explanation.risk}</dd>
              </dl>
            </div>
            <div>
              <Diff text={ai.diff} />
              <p className="note">
                Model <code>{ai.model}</code> · {ai.usage.num_turns} turns · {(ai.usage.duration_ms / 1000).toFixed(0)} s ·
                ${ai.usage.cost_usd.toFixed(3)} · validation score {ai.score.toFixed(2)}
              </p>
            </div>
          </div>
        </section>

        <section className="band" id="adapters">
          <h2>Plug into the platform you already run</h2>
          <p className="section-lede">datamend needs two things from a platform: table stats and a way to run a job. Pick adapters in one YAML block.</p>
          <div className="adapters">
            {ADAPTERS.map(([kind, list]) => (
              <div key={kind} className="adapter"><h4>{kind}</h4><ul>{list.map((x) => <li key={x}>{x}</li>)}</ul></div>
            ))}
            <pre className="yaml">{`platform:
  warehouse:    {type: postgres, dsn_env: WAREHOUSE_DSN}
  orchestrator: {type: airflow, url: https://airflow.internal}

datasets:
  - {name: raw_orders, job: load_raw_orders,   # DAG id
     cadence_minutes: 60, checks: {min_rows: 100}}`}</pre>
          </div>
        </section>

        <section className="band" id="audit">
          <h2>The audit trail</h2>
          <p className="section-lede">Every incident, decision, action, and result, with the reason. This is the log from the run above.</p>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Incident</th><th>Decision</th><th>Action</th><th>Reason</th><th>Result</th></tr></thead>
              <tbody>
                {data.audit.map((e, n) => (
                  <tr key={n}>
                    <td><code>{e.incident}</code></td>
                    <td><span className={`pill p-${e.decision}`}>{e.decision}</span></td>
                    <td><code>{e.action ?? "—"}</code></td>
                    <td>{e.reason}</td>
                    <td>{e.result}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </main>

      <footer>
        <p>
          Everything on this page comes from <code>datamend export-scenario</code>, which runs the real system on synthetic data.
          The AI step replays a real Claude Code run recorded on {ai.recorded_at.slice(0, 10)}.
        </p>
        <p><a href={REPO}>github.com/saeed1507100/datamend</a> · MIT</p>
      </footer>
    </>
  );
}
