"""Proto-step documents and examples (PLAN §3.3; $DRAFTS/01 §6.3, SPEC §6.1)."""

from pathlib import Path
from typing import Any, Literal

from wynd.spec.base import ExitName, FieldName, Name, SpecModel
from wynd.spec.errors import Diagnostic
from wynd.spec.fragments import EnvFragment
from wynd.spec.interface import Interface, interface_from_fields
from wynd.spec.typelang import ModelSet, TypeSpec, build_models


class Example(SpecModel):
    inputs: dict[str, Any] = {}
    outputs: dict[str, Any] = {}  # fields of `exit`'s model, without the exit key; subset allowed
    exit: str = "done"  # a declared exit, or "error" (tests that the step fails loudly)
    description: str | None = None  # set by the compiler to the confirmed edge-case question
    env: dict[str, str] = {}  # process examples only: env for that run (values may use {tmp})


class ProtoStep(SpecModel):
    kind: Literal["proto_step"]
    name: Name
    instruction: str  # non-empty after strip
    inputs: dict[FieldName, TypeSpec] = {}
    outputs: dict[ExitName, dict[FieldName, TypeSpec]]  # normalised: one entry per exit
    exits: list[ExitName]  # normalised
    exit_codes: dict[int | Literal["*"], str] | None = None  # shell only; keys 0..255 or "*"; digit strings coerced
    examples: list[Example] = []
    env: EnvFragment = EnvFragment()

    def models(self) -> ModelSet:
        return build_models(self.name, self.inputs, self.outputs)

    def interface(self) -> Interface:
        return interface_from_fields(self.inputs, self.outputs)

    def to_authoring(self) -> dict:
        """Flat outputs and no `exits` when exits == ["done"]; otherwise nested outputs (every exit) + `exits`."""
        raise NotImplementedError("PLAN §3.3")


def load_proto_step(path: Path) -> ProtoStep:
    raise NotImplementedError("PLAN §3.3")


def check_proto_step(p: ProtoStep) -> list[Diagnostic]:
    """Document-only checks: E-EXAMPLE, E-EXIT-CODES, W-PROTO-NO-EXAMPLES, W-PROTO-EXIT-UNTESTED, W-OUTPUTS-AMBIGUOUS."""
    raise NotImplementedError("PLAN §3.3")
