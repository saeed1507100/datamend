import raw from "./scenario.json";
import { AgentTrajectory } from "./AgentTrajectory";
import { Diff, Replay } from "./Replay";
import type { Scenario } from "./types";

const data = raw as unknown as Scenario;
const REPO = "https://github.com/saeed1507100/datamend";
const PR = "https://github.com/saeed1507100/datamend-prefect-demo/pull/1";
const ai = data.steps.find((s) => s.key === "investigate")!.ai!;

const PROBLEMS = [
  ["The 3 a.m. stale dashboard", "A source load quietly stops. Nobody notices until the revenue dashboard is wrong in the morning stand-up."],
  ["One failure, nine pages", "One upstream table fails and every table below it alerts. You get paged for 9 tables and have to work out which one matters."],
  ["The rerun that made it worse", "You rerun the failed job by hand. It wasn't idempotent, so now the payments table has duplicate rows."],
];

const HOW = [
  ["Detect", "Freshness and row-count checks run against every dataset in the catalog."],
  ["Trace the root cause", "datamend walks the lineage graph upstream and groups symptoms under the table that actually broke."],
  ["Decide with guardrails", "Five rules decide whether a fix is safe: a known fix exists, the job is idempotent, the blast radius is small, no recent retry, no manual-only tables."],
  ["Fix or escalate", "Safe fixes rerun or backfill automatically. Everything else goes to a person, optionally with an AI-proposed patch that already passed checks."],
  ["Audit", "Every incident, decision, rule result, action, and outcome is logged with its reason."],
];

const RULES = [
  ["registered_fix", "The incident type has a registered fix: late means rerun, missing rows means backfill."],
  ["idempotent", "The job is declared idempotent, so a rerun can't duplicate data."],
  ["blast_radius", `At most ${data.policy.max_blast_radius} downstream datasets are affected.`],
  ["cooldown", `The same fix didn't run in the last ${data.policy.cooldown_minutes} minutes.`],
  ["not_manual", "No affected dataset is tagged critical: manual by its owner."],
];

const NEVER = [
  ["Never merges", "An AI fix leaves as a pull request. A person reviews and merges it, or doesn't."],
  ["Never writes to production to test", "Patches run against a shadow copy of the warehouse. Production stays as it was until a person approves."],
  ["Never touches manual datasets", "If an owner tags a dataset critical: manual, datamend escalates and stops. No rerun, no AI patch."],
];

const INTEGRATIONS = [
  ["Warehouses", ["DuckDB", "Postgres", "Any DB-API driver"]],
  ["Orchestrators", ["Prefect", "Apache Airflow", "AWS Glue", "AWS Step Functions", "Local Python jobs"]],
  ["Lineage", ["datasets.yaml", "OpenLineage events"]],
] as const;

export function App() {
  return (
    <>
      <a className="skip" href="#main">Skip to content</a>
      <header className="top">
        <a className="brand" href="#">datamend</a>
        <nav aria-label="Sections">
          <a href="#problem">Problem</a><a href="#how">How it works</a><a href="#replay">Replay</a><a href="#proof">Proof</a>
          <a className="gh" href={REPO}>GitHub</a>
        </nav>
      </header>

      <main id="main">
        <section className="hero">
          <h1>Your pipelines break at 3 a.m. datamend fixes the safe ones and wakes you only for the rest.</h1>
          <p className="lede">
            It finds the one upstream table that actually failed, reruns it only when a rerun can't corrupt data,
            and hands everything else to you with a tested fix proposal and a full audit trail.
          </p>
          <div className="cta">
            <a className="btn primary" href="#replay">Watch a real incident replay</a>
            <a className="btn" href={REPO}>View on GitHub</a>
          </div>
        </section>

        <section className="band" id="problem">
          <h2>The problem</h2>
          <div className="problems">
            {PROBLEMS.map(([t, d]) => <article key={t}><h3>{t}</h3><p>{d}</p></article>)}
          </div>
        </section>

        <section className="band" id="how">
          <h2>How it works</h2>
          <ol className="how">
            {HOW.map(([t, d]) => <li key={t}><h3>{t}</h3><p>{d}</p></li>)}
          </ol>
          <details className="rules-box">
            <summary>The five guardrail rules, in full</summary>
            <ul className="rules">
              {RULES.map(([r, d]) => <li key={r}><code>{r}</code><span>{d}</span></li>)}
            </ul>
            <p className="note">If any rule fails, the incident goes to a person with the failed rule as the reason.</p>
          </details>
        </section>

        <section className="band" id="replay" aria-labelledby="replay-h">
          <h2 id="replay-h">Replay a real run</h2>
          <p className="section-lede">
            This is a recorded run of the real engine on ten synthetic tables. Step through it to see 7 alerts become
            2 root causes, 2 automatic fixes, and 1 AI-assisted fix that a person approved.
          </p>
          <Replay data={data} />
        </section>

        <AgentTrajectory data={data} />

        <section className="band" id="proof">
          <h2>Proof</h2>
          <p className="section-lede">Numbers from real Claude Code runs, not estimates.</p>
          <div className="proof">
            <article>
              <h3>Local fix: non-idempotent payments load</h3>
              <dl className="nums">
                <div><dt>Agent turns</dt><dd>6</dd></div><div><dt>Time</dt><dd>21 s</dd></div>
                <div><dt>Cost</dt><dd>$0.03</dd></div><div><dt>Validation</dt><dd>7/7</dd></div>
              </dl>
            </article>
            <article>
              <h3>Prefect fix: <code>'$2,369.53'</code> ValueError</h3>
              <dl className="nums">
                <div><dt>Agent turns</dt><dd>12</dd></div><div><dt>Time</dt><dd>39 s</dd></div>
                <div><dt>Cost</dt><dd>$0.09</dd></div><div><dt>Production</dt><dd>untouched</dd></div>
              </dl>
            </article>
            <article className="pr">
              <h3>A real pull request</h3>
              <p>
                The first attempt broke the repo's own test. Shadow validation caught it and fed the failure back.
                The second attempt passed. datamend opened the PR and stopped there.
              </p>
              <a className="btn" href={PR}>Open PR #1 on GitHub</a>
            </article>
          </div>
          <div className="ai-card">
            <div className="ai-why">
              <h3>What the agent wrote in the replayed run</h3>
              <dl>
                <dt>Root cause</dt><dd>{ai.explanation.root_cause}</dd>
                <dt>Why this fix</dt><dd>{ai.explanation.rationale}</dd>
                <dt>Risk, in its own words</dt><dd>{ai.explanation.risk}</dd>
              </dl>
            </div>
            <Diff text={ai.diff} />
          </div>
        </section>

        <section className="band" id="never">
          <h2>What it will never do</h2>
          <ul className="never">
            {NEVER.map(([t, d]) => <li key={t}><h3>{t}</h3><p>{d}</p></li>)}
          </ul>
        </section>

        <section className="band" id="integrations">
          <h2>Works with what you run</h2>
          <p className="section-lede">datamend needs table stats and a way to trigger a job. Pick adapters in one YAML block.</p>
          <div className="adapters">
            {INTEGRATIONS.map(([kind, list]) => (
              <div key={kind} className="adapter"><h3>{kind}</h3><ul>{list.map((x) => <li key={x}>{x}</li>)}</ul></div>
            ))}
            <pre className="yaml">{`platform:
  warehouse:    {type: postgres, dsn_env: WAREHOUSE_DSN}
  orchestrator: {type: prefect, url: https://prefect.internal/api}`}</pre>
          </div>
        </section>

        <section className="band" id="audit">
          <h2>The audit log from this run</h2>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Incident</th><th>Decision</th><th>Action</th><th>Reason</th><th>Result</th></tr></thead>
              <tbody>
                {data.audit.map((e, n) => (
                  <tr key={n}>
                    <td><code>{e.incident}</code></td>
                    <td><span className={`pill p-${e.decision}`}>{e.decision.replace(/_/g, " ")}</span></td>
                    <td><code>{e.action ?? "none"}</code></td>
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
          The replay comes from <code>datamend export-scenario</code>, which runs the real system on synthetic data.
          The AI step replays a Claude Code run recorded on {ai.recorded_at.slice(0, 10)}.
        </p>
        <p><a href={REPO}>Source on GitHub</a>, MIT license. Built by Saeed Khan.</p>
      </footer>
    </>
  );
}
