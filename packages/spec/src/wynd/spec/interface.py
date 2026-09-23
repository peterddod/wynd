"""Interface snapshots and the Output rules (PLAN §3.4; $DRAFTS/01 §6.10)."""

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, TypeAdapter

from wynd.spec.base import RESERVED_EXIT, ExitName, SpecModel
from wynd.spec.records import STEP_ERROR_SCHEMA
from wynd.spec.typelang import TypeNode


class Interface(SpecModel):  # the step/process contract snapshot
    input: dict[str, Any]  # JSON Schema of Input
    outputs: dict[ExitName, dict[str, Any]]  # exit -> JSON Schema of that exit's model, incl. "exit" const

    @property
    def exits(self) -> list[str]:
        return list(self.outputs)

    def with_error(self) -> dict[str, dict]:
        """Outputs plus the implicit `error` exit."""
        return {**self.outputs, RESERVED_EXIT: STEP_ERROR_SCHEMA}


def split_output(output: Any) -> dict[str, type[BaseModel]]:
    """Exit -> model for a model or a union of models; TypeError naming the class on a rule violation."""
    raise NotImplementedError("PLAN §3.4")


def output_adapter(output: Any) -> TypeAdapter:
    """TypeAdapter discriminated on "exit"."""
    raise NotImplementedError("PLAN §3.4")


def interface_from_models(input_model: type[BaseModel], output: Any) -> Interface:
    raise NotImplementedError("PLAN §3.4")


def interface_from_fields(
    inputs: Mapping[str, TypeNode], outputs: Mapping[str, Mapping[str, TypeNode]]
) -> Interface:
    raise NotImplementedError("PLAN §3.4")


def interfaces_equivalent(a: Interface, b: Interface) -> list[str]:
    """Human-readable differences; [] when equal modulo titles/defaults/additionalProperties."""
    raise NotImplementedError("PLAN §3.4")
