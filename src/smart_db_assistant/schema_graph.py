"""Foreign-key graph and join-path completion.

Problem (from milestone feedback): top-k retrieval returns the tables most
*similar* to the question. A 5-table join often runs through bridge tables
(e.g. order_items between customers/orders and products) that share no words
with the question, so they are never retrieved and the SQL cannot be written.

Fix: treat tables as nodes and FKs as undirected edges, and connect the
retrieved tables with the smallest set of extra tables - an approximate
Steiner tree (Takahashi-Matsuyama): start from the highest-ranked table and
repeatedly attach the nearest remaining retrieved table via its shortest path.

Expansion strategies compared in experiment 1:

    none      top-k only
    fk1       top-k + every direct FK neighbour (both directions) - the naive fix
    joinpath  top-k + only the bridge tables on shortest connecting paths
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .schema import SchemaMetadata

EXPANSIONS = ("none", "fk1", "joinpath")


def fk_graph(schema: SchemaMetadata) -> dict[str, set[str]]:
    g: dict[str, set[str]] = {name: set() for name in schema.tables}
    for t in schema.tables.values():
        for fk in t.foreign_keys:
            if fk.ref_table in g and fk.ref_table != t.name:
                g[t.name].add(fk.ref_table)
                g[fk.ref_table].add(t.name)
    return g


def shortest_path(graph: dict[str, set[str]], sources: set[str], target: str,
                  max_hops: int | None = None) -> list[str] | None:
    """BFS from any node in `sources` to `target`. Returns the path including both ends."""
    if target in sources:
        return [target]
    prev: dict[str, str | None] = {s: None for s in sources}
    depth = {s: 0 for s in sources}
    queue = deque(sorted(sources))
    while queue:
        node = queue.popleft()
        if max_hops is not None and depth[node] >= max_hops:
            continue
        for nb in sorted(graph.get(node, ())):
            if nb in prev:
                continue
            prev[nb], depth[nb] = node, depth[node] + 1
            if nb == target:
                path = [nb]
                while prev[path[-1]] is not None:
                    path.append(prev[path[-1]])
                return path[::-1]
            queue.append(nb)
    return None


@dataclass
class Expansion:
    tables: list[str]                              # final selection, ranked tables first
    connectors: list[str] = field(default_factory=list)   # tables added by expansion
    unconnected: list[str] = field(default_factory=list)  # retrieved but no path found


def join_path_completion(schema: SchemaMetadata, ranked: list[str],
                         max_hops: int | None = 4) -> Expansion:
    if not ranked:
        return Expansion([])
    g = fk_graph(schema)
    tree = {ranked[0]}
    remaining = [t for t in ranked[1:] if t not in tree]
    connectors: list[str] = []
    unconnected: list[str] = []
    while remaining:
        best = None
        for t in remaining:                      # nearest remaining terminal
            p = shortest_path(g, tree, t, max_hops)
            if p is not None and (best is None or len(p) < len(best[1])):
                best = (t, p)
        if best is None:                          # rest are unreachable
            unconnected.extend(remaining)
            tree.update(remaining)
            break
        t, path = best
        for node in path:
            if node not in tree:
                tree.add(node)
                if node not in ranked:
                    connectors.append(node)
        remaining.remove(t)
    return Expansion(list(dict.fromkeys(ranked)) + connectors, connectors, unconnected)


def fk1_expansion(schema: SchemaMetadata, ranked: list[str]) -> Expansion:
    g = fk_graph(schema)
    extra = [nb for t in ranked for nb in sorted(g.get(t, ())) if nb not in ranked]
    extra = list(dict.fromkeys(extra))
    return Expansion(list(ranked) + extra, extra)


def expand(schema: SchemaMetadata, ranked: list[str], strategy: str) -> Expansion:
    if strategy == "none":
        return Expansion(list(ranked))
    if strategy == "fk1":
        return fk1_expansion(schema, ranked)
    if strategy == "joinpath":
        return join_path_completion(schema, ranked)
    raise ValueError(f"unknown expansion {strategy!r}; choose from {EXPANSIONS}")
