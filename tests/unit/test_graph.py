from __future__ import annotations

import pytest

from dbt_hakari.graph import Graph, NodeKind
from tests.helpers import node, toy_graph


def test_view_expands_to_all_leaf_tables():
    graph = toy_graph(n_tests=2)
    assert graph.leaves_of("t0") == {"s0", "s1", "s2"}
    assert graph.table_count("t0") == 3


def test_materialized_view_counts_as_one_table():
    graph = toy_graph(n_tests=2)
    assert graph.leaves_of("t0", frozenset({"V"})) == {"V"}
    assert graph.table_count("t0", materialized=frozenset({"V"})) == 1


def test_leaf_weight_models_wildcard_tables():
    graph = toy_graph(n_tests=1)
    assert graph.table_count("t0", {"s0": 5}) == 5 + 1 + 1


def test_shared_leaf_is_counted_once():
    nodes = [
        node("a", NodeKind.SOURCE),
        node("b", NodeKind.SOURCE),
        node("v1", NodeKind.VIEW_MODEL, ("a", "b")),
        node("v2", NodeKind.VIEW_MODEL, ("a",)),
        node("m", NodeKind.TABLE_MODEL, ("v1", "v2")),
    ]
    graph = Graph({n.uid: n for n in nodes})
    assert graph.table_count("m") == 2


def test_nested_views_are_expanded_and_stop_at_materialized_ones():
    nodes = [
        node("a", NodeKind.SOURCE),
        node("b", NodeKind.SOURCE),
        node("inner", NodeKind.VIEW_MODEL, ("a", "b")),
        node("outer", NodeKind.VIEW_MODEL, ("inner",)),
        node("t", NodeKind.TEST, ("outer",)),
    ]
    graph = Graph({n.uid: n for n in nodes})
    assert graph.leaves_of("t") == {"a", "b"}
    assert graph.leaves_of("t", frozenset({"inner"})) == {"inner"}
    assert graph.leaves_of("t", frozenset({"outer"})) == {"outer"}


def test_ephemeral_models_are_transparent():
    nodes = [
        node("a", NodeKind.SOURCE),
        node("eph", NodeKind.EPHEMERAL_MODEL, ("a",)),
        node("m", NodeKind.TABLE_MODEL, ("eph",)),
    ]
    graph = Graph({n.uid: n for n in nodes})
    assert graph.leaves_of("m") == {"a"}


def test_table_models_are_leaves_for_their_readers():
    nodes = [
        node("a", NodeKind.SOURCE),
        node("m1", NodeKind.TABLE_MODEL, ("a",)),
        node("m2", NodeKind.TABLE_MODEL, ("m1",)),
    ]
    graph = Graph({n.uid: n for n in nodes})
    assert graph.leaves_of("m2") == {"m1"}


def test_unknown_parent_is_rejected():
    with pytest.raises(ValueError):
        Graph({"x": node("x", NodeKind.TEST, ("missing",))})
