import type { Node, State, Step } from "./types";
import { STATE_LABEL } from "./types";

const W = 760, COL = [40, 300, 560], NW = 160, NH = 52, GAP = 22;

export function layout(nodes: Node[]) {
  const byLayer: Record<number, Node[]> = {};
  nodes.forEach((n) => (byLayer[n.layer] ??= []).push(n));
  const rows = Math.max(...Object.values(byLayer).map((l) => l.length));
  const H = rows * (NH + GAP) + GAP;
  const pos: Record<string, { x: number; y: number }> = {};
  Object.entries(byLayer).forEach(([layer, list]) => {
    const offset = (H - list.length * (NH + GAP) + GAP) / 2;
    list.forEach((n, i) => (pos[n.id] = { x: COL[+layer], y: offset + i * (NH + GAP) }));
  });
  return { pos, H };
}

export function Graph({ nodes, edges, step }: { nodes: Node[]; edges: { from: string; to: string }[]; step: Step }) {
  const { pos, H } = layout(nodes);
  const blast = new Set(step.key === "lineage" ? step.roots?.flatMap((r) => r.blast_radius) : []);
  const roots = new Set(step.key === "lineage" ? step.roots?.map((r) => r.dataset) : []);
  const order = step.order ?? [];

  return (
    <svg className="graph" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Lineage graph: ${step.title}`}>
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
          <path d="M0,0 L10,5 L0,10 z" className="arrowhead" />
        </marker>
      </defs>
      {["raw", "staging", "mart"].map((label, i) => (
        <text key={label} x={COL[i] + NW / 2} y={12} className="layer-label" textAnchor="middle">{label}</text>
      ))}
      {edges.map((e) => {
        const a = pos[e.from], b = pos[e.to];
        const x1 = a.x + NW, y1 = a.y + NH / 2, x2 = b.x - 4, y2 = b.y + NH / 2, mx = (x1 + x2) / 2;
        const hot = blast.has(e.to) && (roots.has(e.from) || blast.has(e.from));
        const delay = step.key === "load" ? order.indexOf(e.to) * 0.18 : 0;
        return (
          <path key={`${e.from}-${e.to}-${step.key}`} d={`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`}
            className={`edge ${hot ? "edge-hot" : ""} ${step.key === "load" ? "edge-flow" : ""}`}
            style={{ animationDelay: `${delay}s` }} markerEnd="url(#arrow)" />
        );
      })}
      {nodes.map((n) => {
        const p = pos[n.id];
        const state = step.states[n.id] as State;
        const s = step.stats[n.id];
        const delay = step.key === "load" ? order.indexOf(n.id) * 0.18 : 0;
        return (
          <g key={`${n.id}-${step.key}`} transform={`translate(${p.x},${p.y})`}
            className={`node s-${state} ${roots.has(n.id) ? "is-root" : ""} ${blast.has(n.id) ? "in-blast" : ""} ${step.key === "load" ? "node-load" : ""}`}
            style={{ animationDelay: `${delay}s` }}>
            <title>{`${n.id} — ${STATE_LABEL[state]} · ${s.rows} rows · job ${n.job}`}</title>
            <rect width={NW} height={NH} rx={10} />
            <circle cx={14} cy={18} r={4.5} className="dot" />
            <text x={26} y={22} className="node-name">{n.id}</text>
            <text x={14} y={40} className="node-meta">{STATE_LABEL[state]} · {s.rows} rows</text>
          </g>
        );
      })}
    </svg>
  );
}
