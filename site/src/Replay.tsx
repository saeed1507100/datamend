import { useEffect, useState } from "react";
import { Graph } from "./Graph";
import type { Check, Scenario, State, Step } from "./types";
import { STATE_HELP, STATE_LABEL } from "./types";

const STEP_MS = 7000;

const PLAIN: Record<Step["key"], { what: string; why: string }> = {
  load: { what: "Ten tables load in order: raw sources first, then staging, then the marts your dashboards read.",
    why: "This is the normal morning. Every box is green." },
  detect: { what: "Two source loads fail and one loses rows. Freshness and row-count checks flag every table that is now wrong.",
    why: "A plain alerting setup would page you 7 times here, once per table." },
  lineage: { what: "datamend walks the lineage graph upstream. Most flagged tables are only late because something they read from is late.",
    why: "7 alerts collapse into 2 root causes. You fix causes, not symptoms." },
  decide: { what: "Each root cause is checked against five safety rules. Two pass and get rerun automatically. One job isn't safe to rerun: it would load duplicates.",
    why: "Unsafe fixes go to a person. Tables below it wait instead of rebuilding on bad data." },
  investigate: { what: "An AI agent reads the job in a sandbox and proposes a one-file patch. Guardrails and a test run on a copy of the warehouse check it.",
    why: "The agent can only propose. Nothing touches production yet." },
  approve: { what: "A person reviews and approves. datamend commits to a branch, reruns the chain, and re-checks every table.",
    why: "Everything is healthy again, and every decision is in the audit log." },
};

const LEGEND: State[] = ["healthy", "stale", "gap", "auto_fix", "suppressed", "needs_approval", "blocked", "ai_proposed", "ai_fix"];

export function Replay({ data }: { data: Scenario }) {
  const [i, setI] = useState(0);
  const [playing, setPlaying] = useState(false);
  const steps = data.steps;
  const step = steps[i];
  const last = steps.length - 1;
  const dur = step.key === "investigate" ? STEP_MS * 1.6 : STEP_MS;

  useEffect(() => {
    if (!playing) return;
    if (i === last) { setPlaying(false); return; }
    const t = setTimeout(() => setI((n) => n + 1), dur);
    return () => clearTimeout(t);
  }, [i, playing, last, dur]);

  const go = (n: number) => { setPlaying(false); setI(Math.max(0, Math.min(last, n))); };
  const toggle = () => {
    if (i === last) { setI(0); setPlaying(true); } else setPlaying(!playing);
  };
  const onKey = (e: React.KeyboardEvent) => {
    if ((e.target as HTMLElement).closest("summary, details")) return;
    if (e.key === "ArrowRight") { e.preventDefault(); go(i + 1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); go(i - 1); }
    else if (e.key === "Home") { e.preventDefault(); go(0); }
    else if (e.key === "End") { e.preventDefault(); go(last); }
    else if (e.key === " " || e.key === "k") {
      if ((e.target as HTMLElement).tagName === "BUTTON") return;
      e.preventDefault(); toggle();
    }
  };
  const plain = PLAIN[step.key];

  return (
    <div className="replay" onKeyDown={onKey} tabIndex={-1} aria-roledescription="replay">
      <div className="replay-head">
        <ol className="stepper" aria-label="Steps">
          {steps.map((s, n) => (
            <li key={s.key}>
              <button className={n === i ? "on" : n < i ? "done" : ""} onClick={() => go(n)}
                aria-current={n === i ? "step" : undefined} aria-label={`Step ${n + 1}: ${s.title}`}>
                <span className="num">{n + 1}</span><span className="lbl">{s.title}</span>
              </button>
            </li>
          ))}
        </ol>
        <div className="controls">
          <button onClick={() => go(i - 1)} disabled={i === 0}>Back</button>
          <button className="play" onClick={toggle} aria-pressed={playing}>
            {i === last ? "Start over" : playing ? "Pause" : i === 0 ? "Play the run" : "Resume"}
          </button>
          <button onClick={() => go(i + 1)} disabled={i === last}>Next</button>
        </div>
      </div>
      <div className="track" aria-hidden="true">
        {steps.map((s, n) => (
          <span key={s.key} className={n < i ? "full" : ""}>
            {n === i && playing && <i key={i} style={{ animationDuration: `${dur}ms` }} />}
            {n === i && !playing && <i className="still" />}
          </span>
        ))}
      </div>
      <p className="hint">Use the <kbd>←</kbd> <kbd>→</kbd> keys to step, <kbd>Space</kbd> to play or pause.</p>
      <div className="replay-body">
        <div className="graph-wrap">
          <div className="graph-scroll"><Graph nodes={data.graph.nodes} edges={data.graph.edges} step={step} /></div>
          <p className="swipe">Swipe the graph sideways to see staging and marts.</p>
          <Legend present={new Set(Object.values(step.states))} />
        </div>
        <div className="panel" key={step.key} aria-live="polite">
          <p className="count">Step {i + 1} of {steps.length}</p>
          <h3>{step.title}</h3>
          <p className="what">{plain.what}</p>
          <p className="takeaway">{plain.why}</p>
          <details className="tech">
            <summary>What the engine recorded</summary>
            <p className="summary">{renderTicks(step.summary)}</p>
          </details>
          <Detail step={step} />
        </div>
      </div>
    </div>
  );
}

function renderTicks(text: string) {
  return text.split(/(`[^`]+`)/).map((part, n) =>
    part.startsWith("`") ? <code key={n}>{part.slice(1, -1)}</code> : part);
}

function Legend({ present }: { present: Set<string> }) {
  return (
    <details className="legend-box" open>
      <summary>What the colors mean</summary>
      <ul className="legend">
        {LEGEND.map((st) => (
          <li key={st} className={present.has(st) ? "here" : ""}>
            <span className={`swatch s-${st}`} /><strong>{STATE_LABEL[st]}</strong><span>{STATE_HELP[st]}</span>
          </li>
        ))}
      </ul>
    </details>
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
                <span className={`verdict v-${d.verdict}`}>{d.verdict === "auto_fix" ? `safe: ${d.action}` : "needs a person"}</span>
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
