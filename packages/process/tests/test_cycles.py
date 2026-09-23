"""Cycles and `max_traversals` (PLAN §6.1, §15 item 8; `$DRAFTS/04 §4.6`, §18.3)."""

import random

import yaml

from wynd.process.validation.cycles import fill_cycles, strongly_connected
from wynd.process.validation.structure import normalise
from wynd.spec.base import DEFAULT_MAX_TRAVERSALS
from wynd.spec.errors import format_loc
from wynd.spec.process_doc import ProcessDoc
from wynd.spec.yamlio import parse_model


def components(nodes, edges) -> set[frozenset[str]]:
    successors: dict[str, list[str]] = {}
    for a, b in edges:
        successors.setdefault(a, []).append(b)
    comp = strongly_connected(nodes, successors)
    groups: dict[int, set[str]] = {}
    for node, number in comp.items():
        groups.setdefault(number, set()).add(node)
    return {frozenset(g) for g in groups.values()}


def test_strongly_connected():
    assert components("abcd", [("a", "b"), ("b", "c"), ("c", "a"), ("c", "d")]) == {
        frozenset("abc"), frozenset("d")}
    assert components("ab", [("a", "a"), ("a", "b")]) == {frozenset("a"), frozenset("b")}
    assert components("abcde", [("a", "b"), ("b", "a"), ("c", "d"), ("d", "e"), ("e", "c"), ("b", "c")]) == {
        frozenset("ab"), frozenset("cde")}
    long_chain = [(str(i), str(i + 1)) for i in range(3000)] + [("3000", "0")]
    assert components([str(i) for i in range(3001)], long_chain) == {frozenset(str(i) for i in range(3001))}


def test_components_do_not_depend_on_visiting_order():
    edges = [("a", "b"), ("b", "c"), ("c", "b"), ("c", "d"), ("d", "e"), ("e", "d"), ("e", "a"), ("f", "a")]
    expected = components("abcdef", edges)
    rng = random.Random(7)
    for _ in range(50):
        nodes = list("abcdef")
        rng.shuffle(nodes)
        shuffled = edges[:]
        rng.shuffle(shuffled)
        assert components(nodes, shuffled) == expected


DOC = {
    "kind": "process",
    "name": "p",
    "entry": "read",
    "inputs": {"text": "string"},
    "outputs": {"result": "string"},
    "steps": {k: {"use": f"./steps/{k}"} for k in ("read", "check", "fix", "save", "poll", "done")},
    "edges": [
        {"from": "read.done", "to": "check"},
        {"from": "check.done", "to": [
            {"step": "save", "when": "steps.check.outputs.ok"},
            {"step": "fix", "name": "retry", "when": "steps.fix.runs < 3", "limits": {"timeout": 5}},
            {"step": "check", "limits": {"max_traversals": 3}},
        ]},
        {"from": "fix.done", "to": "check", "limits": {"max_traversals": "process.inputs.text"}},
        {"from": "check.error", "to": "fix"},
        {"from": "save.done", "to": "poll"},
        {"from": "poll.done", "to": [{"step": "poll", "when": "steps.poll.runs < 5"}, {"step": "done"}]},
        {"from": "done.done", "to": "$exit.done", "with": {"result": '"ok"'}},
    ],
}


def fill(data: dict) -> tuple[ProcessDoc, list]:
    norm = normalise(parse_model(yaml.safe_dump(data, sort_keys=False), ProcessDoc, "p.yaml"))
    return norm, fill_cycles("p", norm)


def limits(norm: ProcessDoc) -> dict[tuple[str, str], tuple]:
    return {
        (edge.from_, branch.step): (branch.limits.max_traversals, branch.limits.timeout) if branch.limits else None
        for edge in norm.edges
        for branch in edge.to
    }


def test_fill_exactly_the_in_scc_branches():
    norm, found = fill(DOC)
    assert limits(norm) == {
        ("read.done", "check"): None,                              # read is not on the cycle
        ("check.done", "save"): None,                              # save leaves the {check, fix} component
        ("check.done", "fix"): (DEFAULT_MAX_TRAVERSALS, 5),        # filled; timeout kept
        ("check.done", "check"): (3, None),                        # explicit value kept, no I201
        ("fix.done", "check"): ("process.inputs.text", None),      # expression kept, no I201
        ("check.error", "fix"): (DEFAULT_MAX_TRAVERSALS, None),
        ("save.done", "poll"): None,
        ("poll.done", "poll"): (DEFAULT_MAX_TRAVERSALS, None),     # self-loop
        ("poll.done", "done"): None,
        ("done.done", "$exit.done"): None,
    }
    assert [(d.code, d.severity, format_loc(d.loc), d.message) for d in found] == [
        ("I201", "info", "edges[1].to[1]", "branch 'check.done[retry]' → 'fix' lies on a cycle; max_traversals "
                                           "defaulted to 10"),
        ("I201", "info", "edges[3].to[0]", "branch 'check.error[0]' → 'fix' lies on a cycle; max_traversals "
                                           "defaulted to 10"),
        ("I201", "info", "edges[5].to[0]", "branch 'poll.done[0]' → 'poll' lies on a cycle; max_traversals "
                                           "defaulted to 10"),
    ]
    assert all(d.file == "p.yaml" and d.line is not None for d in found)


def test_fill_is_independent_of_steps_and_edges_order():
    _, found = fill(DOC)
    expected_limits, expected_messages = limits(fill(DOC)[0]), sorted(d.message for d in found)
    rng = random.Random(2026)
    for _ in range(50):
        data = dict(DOC)
        keys = list(DOC["steps"])
        rng.shuffle(keys)
        data["steps"] = {k: DOC["steps"][k] for k in keys}
        data["edges"] = DOC["edges"][:]
        rng.shuffle(data["edges"])
        norm, found = fill(data)
        assert limits(norm) == expected_limits
        assert sorted(d.message for d in found) == expected_messages


def test_the_written_document_is_never_changed():
    doc = parse_model(yaml.safe_dump(DOC, sort_keys=False), ProcessDoc, "p.yaml")
    before = doc.model_dump()
    norm = normalise(doc)
    fill_cycles("p", norm)
    assert doc.model_dump() == before
    assert norm.model_dump() != before


def test_branches_after_the_else_are_not_in_the_graph():
    data = {**DOC, "edges": [
        {"from": "read.done", "to": [{"step": "check"}, {"step": "read"}]},   # read -> read only after the else
        {"from": "check.done", "to": "$exit.done", "with": {"result": '"ok"'}},
    ]}
    norm, found = fill(data)
    assert found == [] and [b.step for b in norm.edges[0].to] == ["check"]
