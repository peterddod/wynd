"""YAML I/O: YAML 1.2 core booleans, duplicate keys, source marks and located diagnostics (PLAN §3.3, §4.1;
$DRAFTS/01 §4.3)."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel

from wynd.spec.errors import Diagnostic, Loc

M = TypeVar("M", bound=BaseModel)


class WyndLoader(yaml.SafeLoader):
    """SafeLoader resolving only YAML 1.2 core booleans (true/True/TRUE/false/False/FALSE); yes/no/on/off stay
    strings."""


WyndLoader.yaml_implicit_resolvers = {
    first: [(tag, rx) for tag, rx in resolvers if tag != "tag:yaml.org,2002:bool"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
WyndLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF")
)


@dataclass(frozen=True)
class Mark:
    line: int  # 1-based
    column: int  # 1-based
    style: str | None  # PyYAML scalar style: None (plain), "'", '"', "|", ">"


@dataclass
class SourceMap:
    file: str
    marks: dict[Loc, Mark]

    def position(self, loc: Loc) -> Mark | None:
        """Mark of the longest recorded prefix of `loc`."""
        raise NotImplementedError("PLAN §3.3")

    def expr_position(self, loc: Loc, expr_line: int, expr_col: int) -> tuple[int, int, bool]:
        """(line, column, exact) of an expression position inside the scalar at `loc`."""
        raise NotImplementedError("PLAN §3.3")


def parse_yaml(text: str, file: str = "<string>") -> tuple[Any, SourceMap]:
    raise NotImplementedError("PLAN §3.3")


def read_yaml(path: Path) -> tuple[Any, SourceMap]:
    raise NotImplementedError("PLAN §3.3")


def parse_model(text: str, model: type[M], file: str = "<string>") -> M:
    """Validate YAML text into `model`; raises SpecError with located diagnostics."""
    raise NotImplementedError("PLAN §3.3")


def load_model(path: Path, model: type[M]) -> M:
    """Load a YAML file into `model`; raises SpecError; sets the model's `_source`."""
    raise NotImplementedError("PLAN §3.3")


def dump_yaml(data: Any) -> str:
    raise NotImplementedError("PLAN §3.3")


def yaml_to_json(text: str, file: str) -> tuple[dict | None, Diagnostic | None]:
    """JSON-safe document (dates -> ISO strings, int keys kept as ints) or the YAML diagnostic."""
    raise NotImplementedError("PLAN §4.1")
