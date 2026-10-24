import { useEffect, useState } from "react";
import { Graph } from "./Graph";
import type { Check, Scenario, Step } from "./types";

const STEP_MS = 7000;

export function Replay({ data }: { data: Scenario }) {
  const [i, setI] = useState(0);
  const [playing, setPlaying] = useState(true);
  const steps = data.steps;
  const step = steps[i];

  useEffect(() => {
    if (!playing || i === steps.length - 1) return;
    const t = setTimeout(() => setI((n) => n + 1), step.key === "investigate" ? STEP_MS * 1.6 : STEP_MS);
    return () => clearTimeout(t);
  }, [i, playing, steps.length, step.key]);

  const go = (n: number) => { setPlaying(false); setI(Math.max(0, Math.min(steps.length - 1, n))); };

  return (
    <section className="replay" id="replay" aria-label="Replay of a real run">
      <div className="replay-head">
        <ol className="stepper">
          {steps.map((s, n) => (
            <li key={s.key}>
              <button className={n === i ? "on" : n < i ? "done" : ""} onClick={() => go(n)}
                aria-current={n === i ? "step" : undefined}>
                <span className="num">{n + 1}</span><span className="lbl">{s.title}</span>
              </button>
            </li>
          ))}
        </ol>
        <div className="controls">
          <button onClick={() => go(i - 1)} disabled={i === 0} aria-label="Previous step">←</button>
          <button onClick={() => (i === steps.length - 1 ? (setI(0), setPlaying(true)) : setPlaying(!playing))}>
            {i === steps.length - 1 ? "Replay" : playing ? "Pause" : "Play"}
          </button>
          <button onClick={() => go(i + 1)} disabled={i === steps.length - 1} aria-label="Next step">→</button>
        </div>
      </div>
      {playing && i < steps.length - 1 && <div className="progress" key={`${i}`} style={{ animationDuration: `${step.key === "investigate" ? STEP_MS * 1.6 : STEP_MS}ms` }} />}
      <div className="replay-body">
        <div className="graph-wrap">
          <Graph nodes={data.graph.nodes} edges={data.graph.edges} step={step} />
          <Legend />
        </div>
        <div className="panel" key={step.key}>
          <p className="eyebrow">Step {i + 1} of {steps.length}</p>
          <h3>{step.title}</h3>
          <p className="summary">{renderTicks(step.summary)}</p>
          <Detail step={step} />
        </div>
      </div>
    </section>
  );
}

function renderTicks(text: string) {
  return text.split(/(`[^`]+`)/).map((part, n) =>
    part.startsWith("`") ? <code key={n}>{part.slice(1, -1)}</code> : part);
}

function Legend() {
  const items: [string, string][] = [["healthy", "healthy"], ["stale", "failing"], ["auto_fix", "auto-fixed"],
    ["needs_approval", "escalated"], ["blocked", "blocked"], ["ai_proposed", "AI"]];
  return (
    <ul className="legend">
      {items.map(([s, l]) => <li key={s}><span className={`swatch s-${s}`} />{l}</li>)}
    </ul>
  );
}

function Checks({ items }: { items: Check[] }) {
  return (
    <ul className="checks">
      {items.map((c) => (
        <li key={c.check ?? c.rule} className={c.passed ? "pass" : "fail"}>
          <span className="mark" aria-label={c.passed ? "passed" : "failed"}>{c.passed ? "✓" : "✕"}</span>
          <code>{c.check ?? c.rule}</code><span className="why">{c.detail}</span>
        </li>
      ))}
    </ul>
  );
}

function Detail({ step }: { step: Step }) {
  switch (step.key) {
    case "load":
      return <p className="note">Jobs run upstream first: {step.order?.slice(0, 4).join(", ")}, then staging, then marts.</p>;
    case "detect":
      return (
        <ul className="incidents">
          {step.incidents?.map((x) => (
            <li key={x.id}><code>{x.id}</code><span>{x.detail}</span></li>
          ))}
        </ul>
      );
    case "lineage":
      return (
        <div className="roots">
          {step.roots?.map((r) => (
            <div key={r.dataset} className="root">
              <code className="root-name">{r.dataset}</code>
              <span>blast radius {r.blast_radius.length}: {r.blast_radius.join(", ")}</span>
            </div>
          ))}
        </div>
      );
    case "decide":
      return (
        <div className="decisions">
          {step.decisions?.map((d) => (
            <details key={d.incident} open={d.verdict !== "auto_fix"}>
              <summary>
                <code>{d.incident}</code>
                <span className={`verdict v-${d.verdict}`}>{d.verdict === "auto_fix" ? `auto-fix → ${d.action}` : "needs approval"}</span>
              </summary>
              <Checks items={d.rules.map((r) => ({ rule: r.rule, passed: r.passed, detail: r.reason }))} />
            </details>
          ))}
        </div>
      );
    case "investigate": {
      const ai = step.ai!;
      return (
        <div className="ai">
          <dl className="expl">
            <dt>Root cause</dt><dd>{ai.explanation.root_cause}</dd>
            <dt>Fix</dt><dd>{ai.explanation.fix}</dd>
          </dl>
          <Diff text={ai.diff} />
          <div className="two">
            <div><h4>Guardrails</h4><Checks items={ai.guardrails} /></div>
            <div><h4>Shadow validation · score {ai.score.toFixed(2)}</h4><Checks items={ai.validation} /></div>
          </div>
          <p className="note">Recorded run: {ai.usage.num_turns} turns, {(ai.usage.duration_ms / 1000).toFixed(0)} s, ${ai.usage.cost_usd.toFixed(3)}, tools {ai.usage.tools.join(", ")} only.</p>
        </div>
      );
    }
    case "approve":
      return (
        <ul className="incidents">
          <li><code>approved by</code><span>{step.approval?.approved_by}</span></li>
          <li><code>branch</code><span>{step.approval?.commit.branch} @ {step.approval?.commit.commit}</span></li>
          <li><code>post-checks</code><span>{step.approval?.result}</span></li>
        </ul>
      );
  }
}

export function Diff({ text }: { text: string }) {
  return (
    <pre className="diff" aria-label="Patch"><code>
      {text.split("\n").filter((l) => !l.startsWith("---") && !l.startsWith("+++")).map((l, n) => (
        <span key={n} className={l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : l.startsWith("@@") ? "hunk" : ""}>{l || " "}</span>
      ))}
    </code></pre>
  );
}
