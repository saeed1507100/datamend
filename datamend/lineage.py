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
