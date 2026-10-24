import type { Scenario, TrajectoryStep } from "./types";

const ICON: Record<TrajectoryStep["kind"], string> = {
  tool_call: "⌘", message: "✎", guardrails: "⛨", validation: "✓", decision: "→",
};

function title(s: TrajectoryStep): string {
  if (s.kind === "tool_call") return `${s.tool} ${s.target ?? ""}`.trim();
  if (s.kind === "message") return "Explains the fix";
  if (s.kind === "guardrails") return `Guardrails: ${s.summary}`;
  if (s.kind === "validation") return `Validation: ${s.summary}`;
  return `Decision: ${s.summary}`;
}

function Diff({ old, next }: { old: string; next: string }) {
  const lines = [
    ...old.replace(/\n$/, "").split("\n").map((l) => ["del", `- ${l}`]),
    ...next.replace(/\n$/, "").split("\n").map((l) => ["add", `+ ${l}`]),
  ];
  return <pre className="diff"><code>{lines.map(([c, l], i) => <span key={i} className={c}>{l}</span>)}</code></pre>;
}

function Message({ text }: { text: string }) {
  try {
    const m = JSON.parse(text) as Record<string, string>;
    return <dl className="expl">{["root_cause", "fix", "risk"].filter((k) => m[k]).map((k) => (
      <div key={k}><dt>{k.replace("_", " ")}</dt><dd>{m[k]}</dd></div>))}</dl>;
  } catch {
    return <p>{text}</p>;
  }
}

function hasBody(s: TrajectoryStep) {
  return Boolean(s.edit || s.text || s.items?.length || s.result);
}

export function AgentTrajectory({ data }: { data: Scenario }) {
  const ai = data.steps.find((s) => s.key === "investigate")?.ai;
  const steps = ai?.trajectory ?? [];
  if (!steps.length) return null;
  const fb = data.feedback_example;
  const reconstructed = steps.some((s) => s.source === "reconstructed");

  return (
    <section className="band" id="trajectory" aria-labelledby="traj-h">
      <h2 id="traj-h">See every step the agent took, and correct it</h2>
      <p className="section-lede">
        You don't have to trust a black box. Each tool call, edit, and check is recorded. If the agent went wrong, or
        right but not the way you want, you can comment on that exact step. datamend turns your comment into a lesson
        and puts it in the agent's next prompt, so it remembers.
      </p>
      <div className="traj-grid">
        <div>
          {reconstructed && (
            <p className="recon">
              Steps marked "reconstructed" were rebuilt from the run's saved prompt and diff. This run predates live
              capture; new runs record every step as it happens.
            </p>
          )}
          <ol className="timeline">
            {steps.map((s) => {
              const pinned = fb?.feedback.step === s.n;
              return (
                <li key={s.n} className={`tl k-${s.kind}${pinned ? " pinned" : ""}`}>
                  <span className="ico" aria-hidden="true">{ICON[s.kind]}</span>
                  <details open={pinned || s.kind === "decision"}>
                    <summary>
                      <span className="who">#{s.n}</span>
                      <code>{title(s)}</code>
                      {s.ok !== undefined && <span className={`chip${s.ok ? "" : " no"}`}>{s.ok ? "ok" : "failed"}</span>}
                      <span className="who">{s.source === "datamend" ? "datamend" : s.source === "reconstructed" ? "agent · reconstructed" : "agent"}</span>
                    </summary>
                    {hasBody(s) && (
                      <div className="tl-body">
                        {s.result && <p>Result: {s.result}</p>}
                        {s.edit && <Diff old={s.edit.old} next={s.edit.new} />}
                        {s.text && <Message text={s.text} />}
                        {s.items && (
                          <div className="chips">{s.items.map((i) => (
                            <span key={i.name} className={`chip${i.passed ? "" : " no"}`}>{i.passed ? "✓" : "✕"} {i.name}</span>))}
                          </div>
                        )}
                      </div>
                    )}
                    {pinned && fb && (
                      <div className="comment"><b>{fb.feedback.author} commented on this step</b>{fb.feedback.text}</div>
                    )}
                  </details>
                </li>
              );
            })}
          </ol>
        </div>
        {fb && (
          <div className="loop">
            <article>
              <h3><span>1</span>A reviewer comments on step {fb.feedback.step}</h3>
              <p>"{fb.feedback.text}"</p>
            </article>
            <article>
              <h3><span>2</span>It becomes a lesson</h3>
              <p>Saved as <code>{fb.lesson.lesson_id}</code> with scope <code>{fb.lesson.scope}</code>, so it applies to
                future fixes on that dataset. Linked to: {fb.lesson.step_summary}.</p>
            </article>
            <article>
              <h3><span>3</span>The next prompt includes it</h3>
              <pre>{fb.next_prompt_excerpt}</pre>
            </article>
            <article>
              <h3><span>4</span>Do it from the CLI</h3>
              <pre>{fb.cli.join("\n")}</pre>
            </article>
          </div>
        )}
      </div>
    </section>
  );
}
