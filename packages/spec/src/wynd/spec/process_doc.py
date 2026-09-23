"""Process documents: steps, edges, branches, limits, finally, expression sites (PLAN §3.3; $DRAFTS/01 §6.4,
SPEC §6.2, §3.3–§3.5)."""

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, PrivateAttr, field_validator, model_validator
from pydantic_core import PydanticCustomError

from wynd.spec.base import (
    DEFAULT_PROVIDER,
    EXIT_TARGET_PREFIX,
    IGNORE_TARGET,
    RESERVED_EXIT,
    BranchName,
    EnvName,
    ExitName,
    FieldName,
    Name,
    SpecModel,
    StepKey,
)
from wynd.spec.errors import Diagnostic, Loc
from wynd.spec.interface import Interface, interface_from_fields
from wynd.spec.proto_step import (
    AMBIGUOUS_MESSAGE,
    Example,
    authoring_outputs,
    check_examples,
    example_authoring,
    normalise_document,
)
from wynd.spec.typelang import ModelSet, TypeSpec, build_models, outputs_ambiguous, render_type
from wynd.spec.workspace import UseRef, parse_use
from wynd.spec.yamlio import Mark, SourceMap, load_model, located, mark_at, source_loc

# Strings are expressions; other YAML scalars are literals (dates: rewritten to quoted ISO text, PLAN §15 item 70).
type WithValue = str | int | float | bool | datetime | date | None | list[WithValue] | dict[str, WithValue]


def _quote_dates(value: Any) -> Any:
    """A YAML date/datetime literal under `with:` becomes the expression string of its quoted ISO text."""
    match value:
        case datetime() | date():
            return f"'{value.isoformat()}'"
        case dict():
            return {key: _quote_dates(item) for key, item in value.items()}
        case list():
            return [_quote_dates(item) for item in value]
    return value


def _quote_with(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    return {key: _quote_dates(value) if key in ("with", "with_") else value for key, value in data.items()}


class ProcessEnv(SpecModel):
    base: Literal["debian-slim-python", "alpine-python"] = "debian-slim-python"
    vars: dict[EnvName, str] = {}  # env vars used by expressions: name -> description (manifest)


class StepRef(SpecModel):
    use: str  # validated with parse_use

    @field_validator("use")
    @classmethod
    def _use(cls, value: str) -> str:
        try:
            parse_use(value)
        except ValueError as err:
            raise PydanticCustomError("E-USE", "{message}", {"message": str(err)}) from None
        return value

    @property
    def use_ref(self) -> UseRef:
        return parse_use(self.use)


class RetryOverride(SpecModel):  # branch-level, field-wise override of the target's lockfile RetryPolicy
    run: int | None = Field(None, ge=0)
    validation: int | None = Field(None, ge=0)
    tool: int | None = Field(None, ge=0)


class Limits(SpecModel):
    max_traversals: int | str | None = None  # int >= 1 or expression; filled 10 on cycle branches (validator)
    timeout: float | str | None = None  # seconds (> 0) for the target step's run, or expression
    retries: RetryOverride | None = None  # field-wise override of the target's lockfile RetryPolicy

    @field_validator("max_traversals", mode="before")
    @classmethod
    def _max_traversals(cls, value: Any) -> Any:
        if isinstance(value, bool) or (isinstance(value, int) and value < 1):
            raise PydanticCustomError("E-SCHEMA", "max_traversals must be an integer >= 1 or an expression")
        return value

    @field_validator("timeout", mode="before")
    @classmethod
    def _timeout(cls, value: Any) -> Any:
        if isinstance(value, bool) or (isinstance(value, (int, float)) and value <= 0):
            raise PydanticCustomError("E-SCHEMA", "timeout must be a number of seconds > 0 or an expression")
        return value


class Branch(SpecModel):
    step: str  # step key | "$exit.<exit>" | "$ignore"
    when: str | bool | None = None  # expression (never prose)
    with_: dict[FieldName, WithValue] = Field(default_factory=dict, alias="with")
    limits: Limits | None = None
    name: BranchName | None = None  # unique within the edge
    check: str | None = None  # M5 agentic edges only: natural-language condition
    context: list[str] | None = None  # M5 agentic edges only: pull context for the check

    @model_validator(mode="before")
    @classmethod
    def _dates(cls, data: Any) -> Any:
        return _quote_with(data)

    @property
    def exit_target(self) -> str | None:
        """"done" for "$exit.done", else None."""
        return self.step.removeprefix(EXIT_TARGET_PREFIX) if self.step.startswith(EXIT_TARGET_PREFIX) else None


_LIFTED = ("with", "limits", "check", "context")


def _to_error(message: str, loc: Loc = ("to",)) -> PydanticCustomError:
    return PydanticCustomError("E-TO", "{message}", {"message": message, "loc": loc})


class Edge(SpecModel):
    from_: str = Field(alias="from", pattern=r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")  # "<step>.<exit>"
    to: list[Branch]  # normalised: shorthand `to: x` (+ edge-level with/limits/check/context) -> one branch
    kind: Literal["deterministic", "agentic"] = "deterministic"

    @model_validator(mode="before")
    @classmethod
    def _normalise_to(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        lifted = {key: data.pop(key) for key in _LIFTED if key in data}
        to = data.get("to")
        match to:
            case str():
                data["to"] = [{"step": to, **lifted}]
                return data
            case None:
                raise _to_error("to: is required: a step key, $exit.<name>, $ignore or a list of branches", ())
            case list() if not to:
                raise _to_error("to: must not be an empty list")
            case list():
                if lifted:
                    keys = "/".join(f"{key}:" for key in lifted)
                    raise _to_error(f"{keys} go on each branch when to: is a list", (next(iter(lifted)),))
                data["to"] = [{"step": item} if isinstance(item, str) else item for item in to]
                return data
        raise _to_error("to: must be a step key, $exit.<name>, $ignore or a list of branches (got a mapping)"
                        if isinstance(to, dict) else f"to: must be a step key or a list of branches (got {to!r})")

    @property
    def key(self) -> str:
        """The edge key used by `edges["..."]`, e.g. "validate.done"."""
        return self.from_

    @property
    def source_step(self) -> str:
        return self.from_.split(".", 1)[0]

    @property
    def source_exit(self) -> str:
        return self.from_.split(".", 1)[1]


class FinallyStep(SpecModel):
    step: StepKey
    with_: dict[FieldName, WithValue] = Field(default_factory=dict, alias="with")  # "name" string items accepted

    @model_validator(mode="before")
    @classmethod
    def _shorthand(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"step": data}
        return _quote_with(data)


class ProcessDoc(SpecModel):
    kind: Literal["process"]
    name: Name
    goal: str | None = None
    latency: Literal["fast", "normal"] | None = None
    provider: str | None = None  # process default; None -> DEFAULT_PROVIDER ("claude-code")
    env: ProcessEnv = ProcessEnv()
    entry: StepKey
    inputs: dict[FieldName, TypeSpec] = {}
    outputs: dict[ExitName, dict[FieldName, TypeSpec]]  # normalised as for protos
    exits: list[ExitName]  # normalised
    examples: list[Example] = []
    steps: dict[StepKey, StepRef] = Field(min_length=1)
    edges: list[Edge] = []
    on_error: StepKey | None = None
    finally_: list[FinallyStep] = Field(default_factory=list, alias="finally")

    _outputs_ambiguous: bool = PrivateAttr(default=False)

    @model_validator(mode="wrap")
    @classmethod
    def _normalise(cls, data: Any, handler: Any) -> "ProcessDoc":
        if not isinstance(data, dict):
            return handler(data)
        ambiguous = outputs_ambiguous(data.get("outputs"), data.get("exits"))
        doc = handler(normalise_document(data))
        doc._outputs_ambiguous = ambiguous
        return doc

    @property
    def effective_provider(self) -> str:
        return self.provider or DEFAULT_PROVIDER

    def models(self) -> ModelSet:
        return build_models(self.name, self.inputs, self.outputs)

    def interface(self) -> Interface:
        """The ONLY process-interface builder (runtime process_interface wraps it)."""
        return interface_from_fields(self.inputs, self.outputs)

    def edge(self, key: str) -> Edge | None:
        return next((e for e in self.edges if e.key == key), None)

    def to_authoring(self) -> dict:
        """Shorthand `to: x` for one plain branch; flat outputs when exits == [done]; `finally` items without `with`
        as plain strings."""
        outputs, exits = authoring_outputs(self.exits, self.outputs)
        out: dict[str, Any] = {"kind": self.kind, "name": self.name}
        for key in ("goal", "latency", "provider"):
            if getattr(self, key) is not None:
                out[key] = getattr(self, key)
        env = self.env.model_dump(mode="python", exclude_defaults=True)
        if env:
            out["env"] = env
        out["entry"] = self.entry
        out["inputs"] = {name: render_type(node) for name, node in self.inputs.items()}
        out["outputs"] = outputs
        if exits is not None and "done" not in exits:  # nested outputs with a `done` key need no `exits:`
            out["exits"] = exits
        out["examples"] = [example_authoring(example) for example in self.examples]
        out["steps"] = {key: {"use": ref.use} for key, ref in self.steps.items()}
        out["edges"] = [_edge_authoring(edge) for edge in self.edges]
        if self.on_error is not None:
            out["on_error"] = self.on_error
        if self.finally_:
            out["finally"] = [item.step if not item.with_ else {"step": item.step, "with": item.with_}
                              for item in self.finally_]
        return out


def _branch_authoring(branch: Branch) -> dict:
    out: dict[str, Any] = {"step": branch.step}
    if branch.name is not None:
        out["name"] = branch.name
    if branch.when is not None:
        out["when"] = branch.when
    if branch.check is not None:
        out["check"] = branch.check
    if branch.context is not None:
        out["context"] = list(branch.context)
    if branch.with_:
        out["with"] = branch.with_
    if branch.limits is not None:
        out["limits"] = branch.limits.model_dump(mode="python", exclude_none=True)
    return out


def _edge_authoring(edge: Edge) -> dict:
    out: dict[str, Any] = {"from": edge.from_}
    if edge.kind != "deterministic":
        out["kind"] = edge.kind
    if len(edge.to) == 1 and edge.to[0].when is None and edge.to[0].name is None:
        branch = _branch_authoring(edge.to[0])
        out["to"] = branch.pop("step")
        out.update(branch)
        return out
    out["to"] = [_branch_authoring(branch) for branch in edge.to]
    return out


def is_else(b: Branch) -> bool:
    return b.when is None and b.check is None


@dataclass(frozen=True)
class ExprSite:
    loc: Loc  # normalised-model loc, e.g. ("edges", 3, "to", 0, "with", "dest")
    text: str
    role: Literal["when", "with", "limit", "finally_with"]
    edge: str | None  # edge key ("validate.done"), None for finally
    branch: int | None


def _leaf_sites(value: Any, loc: Loc, role: str, edge: str | None, branch: int | None) -> list[ExprSite]:
    match value:
        case str():
            return [ExprSite(loc, value, role, edge, branch)]
        case dict():
            return [s for key, item in value.items() for s in _leaf_sites(item, (*loc, key), role, edge, branch)]
        case list():
            return [s for i, item in enumerate(value) for s in _leaf_sites(item, (*loc, i), role, edge, branch)]
    return []


def expression_sites(doc: ProcessDoc) -> list[ExprSite]:
    """Every expression in the document: string `when`, string leaves of `with`, string limits, finally `with`."""
    sites: list[ExprSite] = []
    for i, edge in enumerate(doc.edges):
        for j, branch in enumerate(edge.to):
            base = ("edges", i, "to", j)
            if isinstance(branch.when, str):
                sites.append(ExprSite((*base, "when"), branch.when, "when", edge.key, j))
            sites += _leaf_sites(branch.with_, (*base, "with"), "with", edge.key, j)
            if branch.limits is not None:
                for key in ("max_traversals", "timeout"):
                    value = getattr(branch.limits, key)
                    if isinstance(value, str):
                        sites.append(ExprSite((*base, "limits", key), value, "limit", edge.key, j))
    for i, item in enumerate(doc.finally_):
        sites += _leaf_sites(item.with_, ("finally", i, "with"), "finally_with", None, None)
    return sites


def site_position(doc: ProcessDoc, loc: Loc) -> Mark | None:
    """SourceMap lookup; for shorthand edges retries with ("to", 0) removed."""
    return mark_at(doc._source, loc)


def load_process(path: Path) -> ProcessDoc:
    """Load process.yaml; expression syntax errors fail the load (E-EXPR-SYNTAX)."""
    return load_model(path, ProcessDoc)


def _expr_offset_position(text: str, offset: int) -> tuple[int, int]:
    line = text.count("\n", 0, offset) + 1
    return line, offset - (text.rfind("\n", 0, offset) + 1) + 1


def _expr_diagnostic(
    source: SourceMap | None, site: ExprSite, severity: str, code: str, message: str,
    position: tuple[int, int] | None, **extra: Any,
) -> Diagnostic:
    """A diagnostic for an expression, located at its exact column when the scalar allows it, else at the scalar
    start with "(expression L:C)" and a caret snippet."""
    if source is None or position is None:
        return located(source, severity, code, message, site.loc, **extra)
    expr_line, expr_col = position
    line, column, exact = source.expr_position(source_loc(source, site.loc), expr_line, expr_col)
    if not exact:
        message = f"{message} (expression {expr_line}:{expr_col})"
    if extra.get("snippet") is None:
        lines = site.text.splitlines() or [""]
        text_line = lines[expr_line - 1] if 0 < expr_line <= len(lines) else lines[-1]
        extra["snippet"] = f"{text_line}\n{' ' * (expr_col - 1)}^"
    return Diagnostic(severity, code, message, file=source.file, line=line, column=column, loc=site.loc, **extra)


def syntax_diagnostics(doc: ProcessDoc) -> list[Diagnostic]:
    """E-EXPR-SYNTAX for every expression site that does not parse (run when a process document is loaded)."""
    from wynd.spec.expr.errors import ExprSyntaxError
    from wynd.spec.expr.evaluator import parse

    out = []
    for site in expression_sites(doc):
        try:
            parse(site.text)
        except ExprSyntaxError as err:
            out.append(_expr_diagnostic(doc._source, site, "error", "E-EXPR-SYNTAX", err.message,
                                        (err.line, err.column), span=getattr(err, "span", None)))
    return out


def _site_findings(doc: ProcessDoc) -> list[Diagnostic]:
    from wynd.spec.expr.analysis import check_expression

    out = []
    for site in expression_sites(doc):
        for found in check_expression(site.text):
            if found.span is not None:
                position = _expr_offset_position(site.text, found.span[0])
            elif found.line is not None:
                position = (found.line, found.column or 1)
            else:
                position = None
            out.append(_expr_diagnostic(doc._source, site, found.severity, found.code, found.message, position,
                                        span=found.span, snippet=found.snippet))
    return out


def check_process_doc(doc: ProcessDoc) -> list[Diagnostic]:
    """Document-only rules (PLAN §4.1 notes); graph and cross-document rules belong to wynd.process."""
    source: SourceMap | None = doc._source
    out: list[Diagnostic] = []

    def add(severity: str, code: str, message: str, loc: Loc) -> None:
        out.append(located(source, severity, code, message, loc))

    steps = doc.steps
    declared = ", ".join(steps)
    if doc.entry not in steps:
        add("error", "E-ENTRY", f"entry '{doc.entry}' is not a key of steps ({declared})", ("entry",))
    handlers: list[tuple[str, str, Loc]] = []
    if doc.on_error is not None:
        handlers.append(("on_error", doc.on_error, ("on_error",)))
    handlers += [("finally", item.step, ("finally", i, "step")) for i, item in enumerate(doc.finally_)]
    for role, key, loc in handlers:
        if key not in steps:
            add("error", "E-STEP-UNKNOWN", f"{role} step '{key}' is not a key of steps ({declared})", loc)

    first_edge: dict[str, int] = {}
    targets: set[str] = set()
    for i, edge in enumerate(doc.edges):
        loc = ("edges", i)
        if edge.source_step not in steps:
            add("error", "E-STEP-UNKNOWN", f"edge from '{edge.from_}': '{edge.source_step}' is not a key of steps "
                f"({declared})", (*loc, "from"))
        if edge.from_ in first_edge:
            add("error", "E-EDGE-DUP", f"'{edge.from_}' already has an edge (edges[{first_edge[edge.from_]}]); "
                "one edge per exit", (*loc, "from"))
        else:
            first_edge[edge.from_] = i
        names: dict[str, int] = {}
        else_index: int | None = None
        for j, branch in enumerate(edge.to):
            bloc = (*loc, "to", j)
            if else_index is not None:
                add("warning", "W-BRANCH-UNREACHABLE", f"branch {j} of '{edge.from_}' comes after the else branch "
                    f"{else_index} (no when:) and is never taken", bloc)
            target = branch.exit_target
            if branch.step == IGNORE_TARGET:
                if len(edge.to) != 1 or not is_else(branch) or branch.with_ or branch.limits is not None:
                    add("error", "E-IGNORE-FORM", f"{IGNORE_TARGET} must be the single unconditioned branch of an "
                        "edge, without with: or limits:", (*bloc, "step"))
            elif target is not None:
                if target == RESERVED_EXIT:
                    add("error", "E-EXIT-TARGET", f"'{branch.step}' is not a routable target: the process error "
                        "exit is produced only by the error handler", (*bloc, "step"))
                elif target not in doc.exits:
                    add("error", "E-EXIT-TARGET", f"'{branch.step}' is not a declared process exit "
                        f"({', '.join(doc.exits)})", (*bloc, "step"))
                if branch.limits is not None:
                    add("warning", "W-LIMITS-EXIT", f"limits: on the '{branch.step}' branch have no effect",
                        (*bloc, "limits"))
            elif branch.step not in steps:
                add("error", "E-STEP-UNKNOWN", f"branch target '{branch.step}' is not a key of steps ({declared}), "
                    f"{EXIT_TARGET_PREFIX}<name> or {IGNORE_TARGET}", (*bloc, "step"))
            else:
                targets.add(branch.step)
            if branch.name is not None:
                if branch.name in names:
                    add("error", "E-BRANCH-NAME", f"branch name '{branch.name}' is used by branches "
                        f"{names[branch.name]} and {j} of '{edge.from_}'", (*bloc, "name"))
                else:
                    names[branch.name] = j
            if else_index is None and is_else(branch):
                else_index = j

    sources = {edge.source_step for edge in doc.edges}
    for role, key, loc in handlers:
        if key == doc.entry:
            add("error", "E-HANDLER-ROLE", f"{role} step '{key}' must not be the entry step", loc)
        if key in targets:
            add("error", "E-HANDLER-ROLE", f"{role} step '{key}' must not be a branch target", loc)
        if key in sources:
            add("error", "E-HANDLER-ROLE", f"{role} step '{key}' must not be the from of an edge: its exits are not "
                "routed", loc)

    out += _site_findings(doc)
    out += check_examples(doc.examples, doc.models(), doc.exits, source)
    if doc._outputs_ambiguous:
        add("warning", "W-OUTPUTS-AMBIGUOUS", AMBIGUOUS_MESSAGE, ("outputs",))
    return out
