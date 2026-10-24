"""Lineage graph: edges point from upstream to downstream."""

from collections.abc import Iterable

import networkx as nx

from datamend.catalog import Catalog


def build(catalog: Catalog) -> nx.DiGraph:
    g = nx.DiGraph()
    for d in catalog.datasets:
        g.add_node(d.name)
        for up in d.upstreams:
            g.add_edge(up, d.name)
    if not nx.is_directed_acyclic_graph(g):
        raise ValueError(f"lineage has a cycle: {nx.find_cycle(g)}")
    return g


def downstream(g: nx.DiGraph, name: str) -> list[str]:
    """Everything affected if `name` fails, in run order."""
    affected = nx.descendants(g, name)
    return [n for n in nx.topological_sort(g) if n in affected]


def root_causes(g: nx.DiGraph, failing: Iterable[str]) -> list[str]:
    """Failing datasets with no failing ancestor."""
    failing = set(failing)
    return sorted(n for n in failing if not (nx.ancestors(g, n) & failing))


STYLES = {
    "auto_fix": "fill:#d3f9d8,stroke:#2b8a3e",
    "suppressed": "fill:#e9ecef,stroke:#868e96",
    "needs_approval": "fill:#ffe3e3,stroke:#c92a2a",
    "blocked": "fill:#fff3bf,stroke:#e67700",
}


def mermaid(g: nx.DiGraph, states: dict[str, str]) -> str:
    """Mermaid flowchart, nodes colored by their latest audit decision."""
    lines = ["flowchart LR"]
    lines += [f"    {u} --> {v}" for u, v in sorted(g.edges)]
    lines += [f"    {n}" for n in sorted(nx.isolates(g))]
    lines += [f"    classDef {state} {style}" for state, style in STYLES.items()]
    lines += [f"    class {n} {s}" for n, s in sorted(states.items()) if s in STYLES]
    return "\n".join(lines)
