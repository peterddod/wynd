"""Exit-aware dataflow (PLAN §6.1, §6.3 pass 6, §15 item 6; owner PROC-VAL).

Lattice: per step key, the set of possible latest exits plus `NOT_RUN`. Branch-level guard atoms parsed from `when:`
refine the state; fixpoint per `$DRAFTS/04 §4.8` gives `WHEN_STATE`/`WITH_STATE` per branch, and `FINAL_STATE`
(the join of every reachable IN/WITH state and every error-handler entry state, where any alias may also be
`NOT_RUN` or `error`) checks `finally[].with`. Produces the `TypedSite` list. No intra-expression refinement.

Soundness notes: `ExitIs` drops `NOT_RUN` (`null == "a"` is false), `ExitIsNot` keeps it; the negation of a branch's
`when` refines the following branches only when the whole condition is one atom and the branch has no `check:` (a
negative verdict falls through without saying anything about the condition).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from wynd.spec.base import RESERVED_EXIT
from wynd.spec.expr.analysis import StepView, TypeEnv
from wynd.spec.expr.errors import ExprSyntaxError
from wynd.spec.expr.evaluator import parse
from wynd.spec.expr.nodes import Binary, Index, ListLit, Lit, Member, Name, Node, Unary
from wynd.spec.records import ProcessError
from wynd.spec.schemas import ANY, MISSING, schema_at

from .bindings import FINALLY_NAMES
from .exprcheck import TypedSite

if TYPE_CHECKING:
    from wynd.spec.errors import Loc
    from wynd.spec.process_doc import Branch, ProcessDoc

    from .structure import StepIface

NOT_RUN = ""                              # "has not run" member of a step's exit set; never an exit name

State = dict[str, frozenset[str]]
PROCESS_ERROR_SCHEMA: dict = ProcessError.model_json_schema()


@dataclass(frozen=True)
class ExitIs:                             # steps.x.exit == "a" | "a" == steps.x.exit | steps.x.exit in ["a", "b"]
    step: str
    exits: frozenset[str]


@dataclass(frozen=True)
class ExitIsNot:                          # steps.x.exit != "a" | not (steps.x.exit == "a") | not (… in [...])
    step: str
    exits: frozenset[str]


@dataclass(frozen=True)
class HasRun:                             # steps.x.runs > 0 | >= 1 | != 0 | steps.x.exit != null
    step: str


@dataclass(frozen=True)
class NotRun:                             # steps.x.runs == 0 | steps.x.exit == null
    step: str


Atom = ExitIs | ExitIsNot | HasRun | NotRun


# --- guard atoms ----------------------------------------------------------------------------------------------------

def _step_member(node: Node, member: str) -> str | None:
    """The step key of `steps.<k>.<member>` / `steps["<k>"].<member>`."""
    match node:
        case Member(obj=Member(obj=Name(id="steps"), name=key), name=name) if name == member:
            return key
        case Member(obj=Index(obj=Name(id="steps"), index=Lit(value=str() as key)), name=name) if name == member:
            return key
    return None


def _is_int(node: Node, value: int) -> bool:
    return isinstance(node, Lit) and type(node.value) is int and node.value == value


def _exit_set(node: Node) -> frozenset[str] | None:
    """The exit names of a string literal or a list of string literals ("" never names an exit)."""
    match node:
        case Lit(value=str() as value):
            return frozenset({value} - {NOT_RUN})
        case ListLit(items=items) if all(isinstance(i, Lit) and isinstance(i.value, str) for i in items):
            return frozenset(i.value for i in items) - {NOT_RUN}
    return None


def negate(atom: Atom) -> Atom:
    match atom:
        case ExitIs(step, exits):
            return ExitIsNot(step, exits)
        case ExitIsNot(step, exits):
            return ExitIs(step, exits)
        case HasRun(step):
            return NotRun(step)
    return HasRun(atom.step)


def _compare(op: str, subject: Node, other: Node) -> Atom | None:
    """An atom for `subject <op> other` where subject is `steps.x.exit` or `steps.x.runs`."""
    step = _step_member(subject, "exit")
    if step is not None and op in ("==", "!="):
        if isinstance(other, Lit) and other.value is None:
            return NotRun(step) if op == "==" else HasRun(step)
        exits = _exit_set(other) if isinstance(other, Lit) else None
        if exits is not None:
            return ExitIs(step, exits) if op == "==" else ExitIsNot(step, exits)
        return None
    step = _step_member(subject, "runs")
    if step is None:
        return None
    match op:
        case "==" if _is_int(other, 0):
            return NotRun(step)
        case "!=" | ">" if _is_int(other, 0):
            return HasRun(step)
        case ">=" if _is_int(other, 1):
            return HasRun(step)
    return None


_MIRROR = {"==": "==", "!=": "!=", "<": ">", "<=": ">="}


def atom(node: Node) -> Atom | None:
    """The guard atom `node` is, if any."""
    match node:
        case Unary(op="not", operand=operand):
            inner = atom(operand)
            return negate(inner) if inner is not None else None
        case Binary(op="in" | "not in" as op, left=left, right=ListLit() as right):
            step, exits = _step_member(left, "exit"), _exit_set(right)
            if step is None or exits is None:
                return None
            return ExitIs(step, exits) if op == "in" else ExitIsNot(step, exits)
        case Binary(op=op, left=left, right=right):
            found = _compare(op, left, right)
            if found is None and op in _MIRROR:
                found = _compare(_MIRROR[op], right, left)
            return found
    return None


def _node(when: str | bool | None) -> Node | None:
    if not isinstance(when, str):
        return None
    try:
        return parse(when)
    except ExprSyntaxError:
        return None


def _conjuncts(node: Node) -> list[Node]:
    match node:
        case Binary(op="and", left=left, right=right):
            return _conjuncts(left) + _conjuncts(right)
    return [node]


def atoms(when: str | bool | None) -> list[Atom]:
    """The conjuncts of the top-level `and` chain of `when` that are atoms (others give no information)."""
    node = _node(when)
    if node is None:
        return []
    return [a for a in map(atom, _conjuncts(node)) if a is not None]


def single(when: str | bool | None) -> Atom | None:
    """The atom `when` is when the whole condition is one atom."""
    node = _node(when)
    return atom(node) if node is not None else None


# --- lattice --------------------------------------------------------------------------------------------------------

def refine_pos(state: State, found: list[Atom]) -> State:
    out = dict(state)
    for a in found:
        if a.step not in out:
            continue
        match a:
            case ExitIs(step, exits):
                out[step] = out[step] & exits
            case ExitIsNot(step, exits):
                out[step] = out[step] - exits
            case HasRun(step):
                out[step] = out[step] - {NOT_RUN}
            case NotRun(step):
                out[step] = out[step] & {NOT_RUN}
    return out


def refine_neg(state: State, branch: Branch) -> State:
    """The state for the branches after `branch`, given `branch` was not taken."""
    if branch.check is not None:
        return state
    found = single(branch.when)
    return refine_pos(state, [negate(found)]) if found is not None else state


def infeasible(state: State) -> bool:
    return any(not exits for exits in state.values())


def join(a: State, b: State) -> State:
    return {key: a.get(key, frozenset()) | b.get(key, frozenset()) for key in a.keys() | b.keys()}


@dataclass(frozen=True)
class Flow:
    inputs: dict[str, State]              # IN per reached step key (absent = not reached)
    when: dict[tuple[int, int], State]    # (edge index, branch index) -> state its `when` is evaluated in
    with_: dict[tuple[int, int], State]   # -> state its `with`/`limits` are evaluated in (branch taken)
    final: State                          # state `finally[].with` is evaluated in


def _taken(state: State, branch: Branch) -> State | None:
    """The state when `branch` is taken, or None when it cannot be."""
    if branch.when is False:
        return None
    taken = refine_pos(state, atoms(branch.when))
    return None if infeasible(taken) else taken


def analyse(doc: ProcessDoc, ifaces: Mapping[str, StepIface]) -> Flow:
    """The fixpoint over the normalised definition `doc` from its entry."""
    keys = list(doc.steps)
    init: State = {key: frozenset({NOT_RUN}) for key in keys}
    by_source: dict[str, list[int]] = {}
    for i, edge in enumerate(doc.edges):
        if edge.source_step in doc.steps:
            by_source.setdefault(edge.source_step, []).append(i)

    inputs: dict[str, State] = {}
    if doc.entry in doc.steps:
        inputs[doc.entry] = init
    work = deque(inputs)
    while work:
        source = work.popleft()
        for i in by_source.get(source, []):
            edge = doc.edges[i]
            rest = {**inputs[source], source: frozenset({edge.source_exit})}
            for branch in edge.to:
                taken = _taken(rest, branch)
                if taken is not None and branch.step in doc.steps:
                    new = taken if branch.step not in inputs else join(inputs[branch.step], taken)
                    if new != inputs.get(branch.step):
                        inputs[branch.step] = new
                        work.append(branch.step)
                rest = refine_neg(rest, branch)

    when: dict[tuple[int, int], State] = {}
    with_: dict[tuple[int, int], State] = {}
    for source, indices in by_source.items():
        if source not in inputs:
            continue
        for i in indices:
            edge = doc.edges[i]
            rest = {**inputs[source], source: frozenset({edge.source_exit})}
            for j, branch in enumerate(edge.to):
                when[i, j] = rest
                taken = _taken(rest, branch)
                if taken is not None:
                    with_[i, j] = taken
                rest = refine_neg(rest, branch)

    final = {key: frozenset({NOT_RUN, RESERVED_EXIT}) for key in keys}
    for state in (*inputs.values(), *with_.values()):
        final = join(final, state)
    ran = {*inputs, doc.on_error, *(item.step for item in doc.finally_)}
    for key in keys:
        exits = ifaces[key].exits if key in ifaces else None
        if key in ran and exits is not None:
            final[key] = final[key] | frozenset(exits)
    return Flow(inputs, when, with_, final)


# --- typed sites ----------------------------------------------------------------------------------------------------

def _exit_schema(iface: StepIface | None, exit: str) -> dict:
    """The schema of one exit; `{}` (anything) when the interface or the exit is unknown."""
    if iface is None or iface.exits is None:
        return {}
    return iface.exits.get(exit, {})


def _view(iface: StepIface | None, exits: frozenset[str]) -> StepView:
    ran = exits - {NOT_RUN}
    order = list(iface.exits) if iface is not None and iface.exits is not None else []
    ordered = [e for e in order if e in ran] + sorted(ran - set(order))
    return StepView(exits={e: _exit_schema(iface, e) for e in ordered}, may_be_unrun=NOT_RUN in exits)


def type_env(doc: ProcessDoc, ifaces: Mapping[str, StepIface], state: State, previous: tuple[str, str] | None,
             process_inputs: dict) -> TypeEnv:
    """The exit-aware scope for a site evaluated in `state`; `previous` = (step, exit) that just completed;
    `process_inputs` = the process Input schema."""
    prev = None
    if previous is not None:
        step, exit = previous
        prev = StepView(exits={exit: _exit_schema(ifaces.get(step), exit)})
    return TypeEnv(
        steps={key: _view(ifaces.get(key), state[key]) for key in doc.steps},
        edges={edge.key: [b.name for b in edge.to] for edge in doc.edges},
        process_inputs=process_inputs,
        previous=prev,
        env_declared=frozenset(doc.env.vars),
    )


def _schema(schema: dict | None, path: Loc) -> dict | None:
    """The sub-schema at `path` (carrying the root's `$defs`, so it stays resolvable), None when unknown or absent."""
    if schema is None:
        return None
    found = schema_at(schema, path)
    if found is ANY or found is MISSING or not isinstance(found, dict):
        return None
    if path and "$defs" in schema and "$defs" not in found:
        return {**found, "$defs": schema["$defs"]}
    return found


def _leaves(value: Any, path: Loc = ()) -> list[tuple[Loc, str]]:
    """(path, expression) of every string leaf of a with-tree."""
    match value:
        case str():
            return [(path, value)]
        case dict():
            return [leaf for key, item in value.items() for leaf in _leaves(item, (*path, key))]
        case list():
            return [leaf for i, item in enumerate(value) for leaf in _leaves(item, (*path, i))]
    return []


def typed_sites(pid: str, doc: ProcessDoc, flow: Flow, ifaces: Mapping[str, StepIface]) -> list[TypedSite]:
    """Every expression of the reachable part of `doc` (normalised) with its exit-aware scope, plus the synthetic
    binding sites (entry, on_error, handler exits, finally by name) for the M3 type checks."""
    sites: list[TypedSite] = []
    interface = doc.interface()
    inputs, outputs = interface.input, interface.outputs
    for i, j in sorted(flow.when):
        edge = doc.edges[i]
        branch = edge.to[j]
        base = ("edges", i, "to", j)
        previous = (edge.source_step, edge.source_exit)
        if isinstance(branch.when, str):
            env = type_env(doc, ifaces, flow.when[i, j], previous, inputs)
            sites.append(TypedSite(pid, (*base, "when"), branch.when, env, "when", None))
        taken = flow.with_.get((i, j))
        if taken is None:
            continue
        env = type_env(doc, ifaces, taken, previous, inputs)
        exit = branch.exit_target
        if exit is not None:
            target, dst = "exit", outputs.get(exit)
        else:
            iface = ifaces.get(branch.step)
            target, dst = "with", iface.input if iface is not None else None
        for path, text in _leaves(branch.with_):
            sites.append(TypedSite(pid, (*base, "with", *path), text, env, target, _schema(dst, path)))
        if branch.limits is not None:
            for key in ("max_traversals", "timeout"):
                value = getattr(branch.limits, key)
                if isinstance(value, str):
                    sites.append(TypedSite(pid, (*base, "limits", key), value, env, "limit", None))

    entry = ifaces.get(doc.entry)
    if entry is not None and entry.input is not None:
        init = type_env(doc, ifaces, {key: frozenset({NOT_RUN}) for key in doc.steps}, None, inputs)
        for name in doc.inputs:
            dst = _schema(entry.input, (name,))
            if dst is not None:
                sites.append(TypedSite(pid, ("inputs", name), None, init, "entry", dst, _schema(inputs, (name,))))

    final = type_env(doc, ifaces, flow.final, None, inputs)
    handler = ifaces.get(doc.on_error) if doc.on_error is not None else None
    if handler is not None and handler.input is not None:
        sites.append(TypedSite(pid, ("on_error",), None, final, "on_error", handler.input, PROCESS_ERROR_SCHEMA))
        for exit, schema in (handler.exits or {}).items():
            if exit in outputs:
                sites.append(TypedSite(pid, ("on_error",), None, final, "handler_exit", outputs[exit], schema))

    for i, item in enumerate(doc.finally_):
        iface = ifaces.get(item.step)
        dst = iface.input if iface is not None else None
        if item.with_:
            for path, text in _leaves(item.with_):
                sites.append(TypedSite(pid, ("finally", i, "with", *path), text, final, "finally",
                                       _schema(dst, path)))
            continue
        for name in (dst or {}).get("properties", {}):
            if name in doc.inputs:
                src = _schema(inputs, (name,))
            elif name in FINALLY_NAMES:
                src = {"type": "string"}
            else:
                continue
            sites.append(TypedSite(pid, ("finally", i), None, final, "finally", _schema(dst, (name,)), src))
    return sites
