"""Process documents: steps, edges, branches, limits, finally, expression sites (PLAN §3.3; $DRAFTS/01 §6.4,
SPEC §6.2, §3.3–§3.5)."""

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field

from wynd.spec.base import (
    DEFAULT_PROVIDER,
    EXIT_TARGET_PREFIX,
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
from wynd.spec.proto_step import Example
from wynd.spec.typelang import ModelSet, TypeSpec, build_models
from wynd.spec.workspace import UseRef, parse_use
from wynd.spec.yamlio import Mark

# Strings are expressions; other YAML scalars are literals (dates: rewritten to quoted ISO text, PLAN §15 item 70).
type WithValue = str | int | float | bool | datetime | date | None | list[WithValue] | dict[str, WithValue]


class ProcessEnv(SpecModel):
    base: Literal["debian-slim-python", "alpine-python"] = "debian-slim-python"
    vars: dict[EnvName, str] = {}  # env vars used by expressions: name -> description (manifest)


class StepRef(SpecModel):
    use: str  # validated with parse_use

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


class Branch(SpecModel):
    step: str  # step key | "$exit.<exit>" | "$ignore"
    when: str | bool | None = None  # expression (never prose)
    with_: dict[FieldName, WithValue] = Field(default_factory=dict, alias="with")
    limits: Limits | None = None
    name: BranchName | None = None  # unique within the edge
    check: str | None = None  # M5 agentic edges only: natural-language condition
    context: list[str] | None = None  # M5 agentic edges only: pull context for the check

    @property
    def exit_target(self) -> str | None:
        """"done" for "$exit.done", else None."""
        return self.step.removeprefix(EXIT_TARGET_PREFIX) if self.step.startswith(EXIT_TARGET_PREFIX) else None


class Edge(SpecModel):
    from_: str = Field(alias="from", pattern=r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")  # "<step>.<exit>"
    to: list[Branch]  # normalised: shorthand `to: x` (+ edge-level with/limits/check/context) -> one branch
    kind: Literal["deterministic", "agentic"] = "deterministic"

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
        raise NotImplementedError("PLAN §3.3")


def is_else(b: Branch) -> bool:
    return b.when is None and b.check is None


@dataclass(frozen=True)
class ExprSite:
    loc: Loc  # normalised-model loc, e.g. ("edges", 3, "to", 0, "with", "dest")
    text: str
    role: Literal["when", "with", "limit", "finally_with"]
    edge: str | None  # edge key ("validate.done"), None for finally
    branch: int | None


def expression_sites(doc: ProcessDoc) -> list[ExprSite]:
    """Every expression in the document: string `when`, string leaves of `with`, string limits, finally `with`."""
    raise NotImplementedError("PLAN §3.3")


def site_position(doc: ProcessDoc, loc: Loc) -> Mark | None:
    """SourceMap lookup; for shorthand edges retries with ("to", 0) removed."""
    raise NotImplementedError("PLAN §3.3")


def load_process(path: Path) -> ProcessDoc:
    """Load process.yaml; expression syntax errors fail the load (E-EXPR-SYNTAX)."""
    raise NotImplementedError("PLAN §3.3")


def check_process_doc(doc: ProcessDoc) -> list[Diagnostic]:
    """Document-only rules (PLAN §4.1 notes); graph and cross-document rules belong to wynd.process."""
    raise NotImplementedError("PLAN §4.1")
