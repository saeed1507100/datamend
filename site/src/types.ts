export type State =
  | "healthy" | "stale" | "gap" | "auto_fix" | "suppressed"
  | "needs_approval" | "blocked" | "ai_proposed" | "ai_fix";

export interface Node { id: string; layer: number; job: string; cadence_minutes: number; idempotent: boolean; critical: string }
export interface Rule { rule: string; passed: boolean; reason: string }
export interface Check { check?: string; rule?: string; passed: boolean; detail: string }
export interface Decision { incident: string; verdict: string; action: string | null; blast_radius: number; rules: Rule[] }
export interface Ai {
  run_id: string; status: string; status_reason: string; prompt: string; diff: string; score: number;
  model: string; recorded_at: string;
  explanation: { root_cause: string; fix: string; rationale: string; risk: string; confidence?: number };
  guardrails: Check[]; validation: Check[];
  trajectory?: TrajectoryStep[]; trajectory_source?: string;
  usage: { backend: string; tools: string[]; num_turns: number; cost_usd: number; duration_ms: number };
}
export interface Step {
  key: "load" | "detect" | "lineage" | "decide" | "investigate" | "approve";
  title: string; summary: string;
  states: Record<string, State>;
  stats: Record<string, { rows: number; age_minutes: number | null }>;
  order?: string[];
  incidents?: { id: string; dataset: string; kind: string; detail: string }[];
  roots?: { dataset: string; blast_radius: string[] }[];
  decisions?: Decision[];
  ai?: Ai;
  approval?: { approved_by: string; commit: { branch: string; commit: string }; result: string };
}
export interface AuditEntry {
  incident: string; dataset: string; decision: string; action: string | null;
  blast_radius: number | null; rules_passed: number | null; reason: string; result: string; at: string;
}
export type TrajectoryKind = "tool_call" | "message" | "guardrails" | "validation" | "decision";
export interface TrajectoryStep {
  n: number; kind: TrajectoryKind; source: string;
  tool?: string; target?: string; ok?: boolean; result?: string; edit?: { old: string; new: string };
  text?: string; summary?: string; items?: { name: string; passed: boolean }[];
}
export interface FeedbackExample {
  feedback: { run_id: string; step: number; author: string; text: string };
  lesson: { lesson_id: string; scope: string; text: string; step: number; step_summary: string };
  next_prompt_excerpt: string; cli: string[];
}
export interface Scenario {
  feedback_example?: FeedbackExample;
  now: string;
  graph: { nodes: Node[]; edges: { from: string; to: string }[] };
  steps: Step[]; audit: AuditEntry[];
  policy: { max_blast_radius: number; cooldown_minutes: number };
}

export const STATE_LABEL: Record<State, string> = {
  healthy: "up to date", stale: "late", gap: "missing rows", auto_fix: "auto-fixed",
  suppressed: "fixed upstream", needs_approval: "needs a person", blocked: "on hold",
  ai_proposed: "AI fix in review", ai_fix: "AI fix applied",
};

export const STATE_HELP: Record<State, string> = {
  healthy: "Fresh data, expected row count.",
  stale: "Didn't refresh on schedule.",
  gap: "Refreshed, but rows went missing.",
  auto_fix: "Passed every guardrail, so datamend reran it.",
  suppressed: "Healed by fixing its upstream root. No separate action.",
  needs_approval: "Failed a guardrail. Escalated to a human.",
  blocked: "Downstream of an escalation. Waits instead of rebuilding on bad data.",
  ai_proposed: "An agent proposed a patch. It's being checked.",
  ai_fix: "A person approved the patch; it ran and passed checks.",
};
