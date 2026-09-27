"""The rule-3 two-step split: rewriting `process.yaml` between `# wynd:split <node> begin/end` markers
(`$DRAFTS/05 §10`).

The agentic half gets a new node `<node>_agentic`, an edge `<node>.error -> <node>_agentic` that re-binds the inputs
from the error payload (`steps.<node>.outputs.inputs.<f>`, PLAN §15 item 13), and a copy of every edge leaving
`<node>` with its expressions renamed to the new node. Removing a split deletes the marked blocks, or, when the
markers are gone, the node and its edges structurally (a full re-dump; comments are lost).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from wynd.compiler.yamledit import append_block_items, append_mapping_entries, has_marker, remove_marked

if TYPE_CHECKING:
    from wynd.spec.process_doc import ProcessDoc


@dataclass
class SplitPlan:
    node: str                    # extract
    agentic_node: str            # extract_agentic
    agentic_use: str             # ./steps/extract_invoice_fields_agentic
    input_fields: list[str]      # the Input field names


@dataclass
class RemoveSplit:
    node: str                    # the deterministic node whose earlier split is undone


def split_marker(node: str) -> str:
    return f"wynd:split {node}"


def agentic_node_name(node: str) -> str:
    return f"{node}_agentic"


def has_split_markers(text: str, node: str) -> bool:
    return has_marker(text, split_marker(node))


def apply_split(text: str, doc: ProcessDoc, plan: SplitPlan) -> str:
    """Add the agentic node and its edges (replacing an earlier split of the same node). Raises ValueError when the
    re-parsed document is not exactly the old one plus the planned node and edges."""
    from wynd.spec.process_doc import Edge, ProcessDoc
    from wynd.spec.workspace import parse_use
    from wynd.spec.yamlio import parse_model, parse_yaml

    text = remove_split(text, doc, plan.node)
    before = parse_model(text, ProcessDoc)
    raw, _ = parse_yaml(text)
    node, agentic = plan.node, plan.agentic_node
    if agentic in before.steps:
        raise ValueError(f"name '{agentic}' is already taken")

    edges: list[dict[str, Any]] = [{
        "from": f"{node}.error",
        "to": agentic,
        "with": {f: f"steps.{node}.outputs.inputs.{f}" for f in plan.input_fields},
    }]
    for edge in raw.get("edges") or []:
        source = str(edge.get("from", ""))
        if source.partition(".")[0] != node or source == f"{node}.error":
            continue
        copied = _rename_edge(copy.deepcopy(edge), node, agentic)
        copied["from"] = f"{agentic}.{source.partition('.')[2]}"
        edges.append(copied)

    package = parse_use(before.steps[node].use).target
    note = f"added by wynd compile; recompiling {package} may remove it"
    marker = split_marker(node)
    out = append_mapping_entries(text, "steps", {agentic: {"use": plan.agentic_use}}, marker=marker, note=note)
    out = append_block_items(out, "edges", edges, comment="", marker=marker, note=note)

    after = parse_model(out, ProcessDoc)
    added = [Edge.model_validate(edge) for edge in edges]
    expected_steps = {**_dump_steps(before), agentic: plan.agentic_use}
    if _dump_steps(after) != expected_steps or _dump_edges(after.edges) != _dump_edges([*before.edges, *added]):
        raise ValueError("process.yaml split rewrite did not re-parse to the planned document")
    return out


def remove_split(text: str, doc: ProcessDoc, node: str) -> str:
    """Remove the split of `node`: the marked blocks, or structurally when the markers are gone."""
    if has_split_markers(text, node):
        return remove_marked(text, split_marker(node))
    agentic = agentic_node_name(node)
    if agentic not in doc.steps:
        return text
    from wynd.spec.yamlio import dump_yaml, parse_yaml

    raw, _ = parse_yaml(text)
    handoff = f"{node}.error"
    if not any(str(e.get("from")) == handoff and _split_edge(e, node, agentic) for e in raw.get("edges") or []):
        return text                                   # a node that merely has the name is not a split

    raw["steps"].pop(agentic, None)
    raw["edges"] = [edge for edge in raw.get("edges") or [] if not _split_edge(edge, node, agentic)]
    return dump_yaml(raw)


def rename_step_refs(expr: str, old: str, new: str) -> str:
    """Rename `steps.<old>.*` and `edges["<old>.*"]` via `wynd.spec.expr` reference positions."""
    from wynd.spec.expr import references

    refs = references(expr)
    starts = _line_starts(expr)
    edits: list[tuple[int, int, str]] = []
    for ref in refs:
        if not ref.path or not isinstance(ref.path[0], str):
            continue
        head = ref.path[0]
        if ref.root == "steps" and head == old:
            span = _member_span(expr, starts[ref.line - 1] + ref.column - 1, "steps")
            edits.append((span[0], span[0] + len(old), new))
        elif ref.root == "edges" and head.partition(".")[0] == old and "." in head:
            span = _member_span(expr, starts[ref.line - 1] + ref.column - 1, "edges")
            edits.append((span[0], span[0] + len(old), new))
    out = expr
    for start, end, text in sorted(edits, reverse=True):
        out = out[:start] + text + out[end:]
    expected = [(r.root, _renamed(r, old, new)) for r in refs]
    if [(r.root, r.path) for r in references(out)] != expected:
        raise ValueError(f"could not rename step '{old}' in expression {expr!r}")
    return out


def _dump_steps(doc: ProcessDoc) -> dict[str, str]:
    return {key: ref.use for key, ref in doc.steps.items()}


def _dump_edges(edges: list) -> list[dict]:
    return [edge.model_dump(mode="json", by_alias=True) for edge in edges]


def _split_edge(edge: dict, node: str, agentic: str) -> bool:
    source = str(edge.get("from", ""))
    if source.partition(".")[0] == agentic:
        return True
    if source != f"{node}.error":
        return False
    to = edge.get("to")
    targets = [to] if isinstance(to, str) else [b if isinstance(b, str) else b.get("step") for b in to or []]
    return targets == [agentic]


_EXPR_KEYS = ("when", "with", "limits", "context")


def _rename_edge(edge: dict, old: str, new: str) -> dict:
    for key in _EXPR_KEYS:
        if key in edge:
            edge[key] = _rename_value(edge[key], old, new)
    to = edge.get("to")
    if isinstance(to, list):
        edge["to"] = [item if isinstance(item, str) else _rename_edge(item, old, new) for item in to]
    return edge


def _rename_value(value: Any, old: str, new: str) -> Any:
    match value:
        case str():
            return rename_step_refs(value, old, new)
        case dict():
            return {key: _rename_value(item, old, new) for key, item in value.items()}
        case list():
            return [_rename_value(item, old, new) for item in value]
    return value


def _renamed(ref: Any, old: str, new: str) -> tuple:
    path = ref.path
    if not path or not isinstance(path[0], str):
        return path
    head = path[0]
    if ref.root == "steps" and head == old:
        return (new, *path[1:])
    if ref.root == "edges" and "." in head and head.partition(".")[0] == old:
        return (f"{new}.{head.partition('.')[2]}", *path[1:])
    return path


def _line_starts(text: str) -> list[int]:
    starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def _member_span(expr: str, pos: int, root: str) -> tuple[int, int]:
    """Offsets of the first member after `root` in the chain starting near `pos`: the identifier after `.`, or the
    content of the string literal in `[...]`."""
    i = _skip(expr, pos, " \t\r\n(")
    if not expr.startswith(root, i):
        raise ValueError(f"no '{root}' reference at offset {pos} in {expr!r}")
    i = _skip(expr, i + len(root), " \t\r\n)")
    if expr[i] == ".":
        i = _skip(expr, i + 1, " \t\r\n")
        j = i
        while j < len(expr) and (expr[j].isalnum() or expr[j] == "_"):
            j += 1
        return i, j
    if expr[i] == "[":
        i = _skip(expr, i + 1, " \t\r\n")
        quote = expr[i]
        if quote not in "\"'":
            raise ValueError(f"unexpected subscript in {expr!r}")
        end = expr.index(quote, i + 1)
        return i + 1, end
    raise ValueError(f"unexpected token after '{root}' in {expr!r}")


def _skip(text: str, i: int, chars: str) -> int:
    while i < len(text) and text[i] in chars:
        i += 1
    return i
