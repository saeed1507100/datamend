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
export interface Scenario {
  now: string;
  graph: { nodes: Node[]; edges: { from: string; to: string }[] };
  steps: Step[]; audit: AuditEntry[];
  policy: { max_blast_radius: number; cooldown_minutes: number };
}

export const STATE_LABEL: Record<State, string> = {
  healthy: "healthy", stale: "stale", gap: "missing rows", auto_fix: "auto-fixed",
  suppressed: "fixed via root", needs_approval: "escalated", blocked: "blocked",
  ai_proposed: "AI fix proposed", ai_fix: "AI fix shipped",
};
