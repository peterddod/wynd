"""YAML I/O: YAML 1.2 core booleans, duplicate keys, source marks and located diagnostics (PLAN §3.3, §4.1;
$DRAFTS/01 §4.3)."""

import difflib
import re
import types
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any, TypeAliasType, TypeVar, Union, get_args, get_origin

import yaml
from pydantic import BaseModel, ValidationError

from wynd.spec.errors import Diagnostic, Loc, SpecError

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
    # Scalars whose source text is their value on one line (plain, or quoted without escapes), so expression
    # positions inside them map to exact columns.
    exact: set[Loc] = field(default_factory=set)

    def position(self, loc: Loc) -> Mark | None:
        """Mark of the longest recorded prefix of `loc`."""
        loc = tuple(loc)
        while True:
            if loc in self.marks:
                return self.marks[loc]
            if not loc:
                return None
            loc = loc[:-1]

    def expr_position(self, loc: Loc, expr_line: int, expr_col: int) -> tuple[int, int, bool]:
        """(line, column, exact) of an expression position inside the scalar at `loc`; the scalar's start (exact False)
        when the column cannot be mapped."""
        loc = tuple(loc)
        mark = self.marks.get(loc)
        if mark is not None and loc in self.exact and expr_line == 1:
            quoted = 1 if mark.style in ("'", '"') else 0
            return mark.line, mark.column + quoted + expr_col - 1, True
        start = self.position(loc)
        if start is None:
            return 1, 1, False
        return start.line, start.column, False


_HINT_CHARS = "?[]"
_FLOW_HINT = (
    'inside `{ }` or `[ ]` flow collections, quote types such as "date?" or "list[string]"; block style needs no quotes'
)


def parse_yaml(text: str, file: str = "<string>") -> tuple[Any, SourceMap]:
    """Parse one YAML document with marks for every node; SpecError (E-YAML, E-YAML-DUP)."""
    try:
        node = yaml.compose(text, Loader=WyndLoader)
    except yaml.MarkedYAMLError as err:
        raise SpecError([_yaml_error(err, text, file)]) from None
    source = SourceMap(file, {})
    if node is None:
        return None, source
    duplicates: list[Diagnostic] = []
    try:
        data = _build(node, (), source, WyndLoader(""), duplicates)
    except yaml.MarkedYAMLError as err:
        raise SpecError([_yaml_error(err, text, file)]) from None
    if duplicates:
        raise SpecError(duplicates)
    return data, source


def _yaml_error(err: yaml.MarkedYAMLError, text: str, file: str) -> Diagnostic:
    mark = err.problem_mark or err.context_mark
    message = err.problem or str(err)
    if err.context:
        message = f"{message} ({err.context})"
    if mark is not None and 0 <= mark.index < len(text) and text[mark.index] in _HINT_CHARS:
        message = f"{message}; {_FLOW_HINT}"
    line = mark.line + 1 if mark is not None else None
    column = mark.column + 1 if mark is not None else None
    return Diagnostic("error", "E-YAML", message, file=file, line=line, column=column)


def _build(node: yaml.Node, loc: Loc, source: SourceMap, loader: WyndLoader, duplicates: list[Diagnostic]) -> Any:
    start, end = node.start_mark, node.end_mark
    style = node.style if isinstance(node, yaml.ScalarNode) else None
    source.marks[loc] = Mark(start.line + 1, start.column + 1, style)
    match node:
        case yaml.MappingNode():
            merged = getattr(node, "wynd_merged", None)  # set on the first visit; aliases revisit the same node
            if merged is None:
                explicit = sum(1 for key, _ in node.value if key.tag != "tag:yaml.org,2002:merge")
                loader.flatten_mapping(node)  # `<<` merge keys: merged pairs first, then the explicit ones
                merged = node.wynd_merged = len(node.value) - explicit
            out: dict[Any, Any] = {}
            explicit_keys: set[Any] = set()
            for index, (key_node, value_node) in enumerate(node.value):
                try:
                    key = loader.construct_object(key_node, deep=True)
                    hash(key)
                except (ValueError, TypeError):
                    raise yaml.constructor.ConstructorError(
                        None, None, "a mapping key must be a scalar", key_node.start_mark
                    ) from None
                if index >= merged:
                    if key in explicit_keys:
                        duplicates.append(
                            Diagnostic(
                                "error", "E-YAML-DUP", f"duplicate key {key!r}", file=source.file,
                                line=key_node.start_mark.line + 1, column=key_node.start_mark.column + 1,
                                loc=(*loc, key),
                            )
                        )
                        continue
                    explicit_keys.add(key)
                out[key] = _build(value_node, (*loc, key), source, loader, duplicates)
            return out
        case yaml.SequenceNode():
            return [_build(item, (*loc, i), source, loader, duplicates) for i, item in enumerate(node.value)]
    try:
        value = loader.construct_object(node, deep=True)
    except (ValueError, TypeError) as err:  # e.g. an impossible date such as 2026-13-45
        raise yaml.constructor.ConstructorError(None, None, f"invalid value {node.value!r}: {err}", start) from None
    quotes = 2 if style in ("'", '"') else 0
    if style in (None, "'", '"') and start.line == end.line and end.column - start.column - quotes == len(node.value):
        source.exact.add(loc)
    return value


def read_yaml(path: Path) -> tuple[Any, SourceMap]:
    return parse_yaml(Path(path).read_text(encoding="utf-8"), str(path))


def parse_model(text: str, model: type[M], file: str = "<string>") -> M:
    """Validate YAML text into `model`; raises SpecError with located diagnostics; sets the model's `_source`.

    Process documents also get their expressions syntax-checked here (the load fails on E-EXPR-SYNTAX)."""
    data, source = parse_yaml(text, file)
    if not isinstance(data, dict):
        raise SpecError([_root_error(data, source)])
    try:
        obj = model.model_validate(data)
    except ValidationError as err:
        raise SpecError(validation_diagnostics(err, data, model, source)) from None
    obj._source = source
    from wynd.spec.process_doc import ProcessDoc, syntax_diagnostics

    if isinstance(obj, ProcessDoc):
        problems = syntax_diagnostics(obj)
        if problems:
            raise SpecError(problems)
    return obj


def load_model(path: Path, model: type[M]) -> M:
    """Load a YAML file into `model`; raises SpecError; sets the model's `_source`."""
    return parse_model(Path(path).read_text(encoding="utf-8"), model, str(path))


def _root_error(data: Any, source: SourceMap) -> Diagnostic:
    got = "an empty document" if data is None else f"a {type(data).__name__}"
    return Diagnostic(
        "error", "E-YAML-ROOT", f"the document must be a mapping, got {got}", file=source.file, line=1, column=1
    )


class _Dumper(yaml.SafeDumper):
    pass


def _represent_str(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    if "\n" in value:
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|")
    return dumper.represent_str(value)


_Dumper.add_representer(str, _represent_str)
_Dumper.add_representer(tuple, yaml.SafeDumper.represent_list)


def dump_yaml(data: Any) -> str:
    return yaml.dump(
        data, Dumper=_Dumper, sort_keys=False, default_flow_style=False, allow_unicode=True, width=10**9
    )


def yaml_to_json(text: str, file: str) -> tuple[dict | None, Diagnostic | None]:
    """JSON-safe document (dates -> ISO strings, int keys kept as ints) or the YAML diagnostic."""
    try:
        data, source = parse_yaml(text, file)
    except SpecError as err:
        return None, err.diagnostics[0]
    if not isinstance(data, dict):
        return None, _root_error(data, source)
    return _json_safe(data), None


def _json_safe(value: Any) -> Any:
    match value:
        case dict():
            return {k if isinstance(k, (str, int)) and not isinstance(k, bool) else str(k): _json_safe(v)
                    for k, v in value.items()}
        case list():
            return [_json_safe(v) for v in value]
        case datetime() | date():
            return value.isoformat()
        case bytes():
            return value.decode("utf-8", errors="replace")
    return value


# --- locations -------------------------------------------------------------------------------------------------------

def source_loc(source: SourceMap, loc: Loc) -> Loc:
    """The document-as-written location of a normalised-model `loc`: a flat done-only `outputs` has no exit level, and
    the shorthand `to: x` carries its branch's `with`/`limits` on the edge."""
    loc = tuple(loc)
    if loc in source.marks:
        return loc
    if len(loc) >= 2 and loc[0] == "outputs" and ("outputs", loc[1]) not in source.marks:
        return ("outputs", *loc[2:])
    if len(loc) >= 4 and loc[0] == "edges" and loc[2:4] == ("to", 0) and loc[:4] not in source.marks:
        if loc[4:] == ("step",):
            return loc[:3]
        return (*loc[:2], *loc[4:])
    return loc


def mark_at(source: SourceMap | None, loc: Loc) -> Mark | None:
    """Mark for a normalised-model location (see source_loc), falling back to the longest recorded prefix."""
    if source is None:
        return None
    return source.position(source_loc(source, loc))


def located(source: SourceMap | None, severity: str, code: str, message: str, loc: Loc, **extra: Any) -> Diagnostic:
    """A Diagnostic at `loc` with file/line/column taken from the document's source map when it has one."""
    mark = mark_at(source, loc)
    return Diagnostic(
        severity, code, message, file=source.file if source else None, line=mark.line if mark else None,
        column=mark.column if mark else None, loc=tuple(loc), **extra,
    )


# --- pydantic errors -> located diagnostics --------------------------------------------------------------------------

_CODE = re.compile(r"^(?:[EWIL]-[A-Z0-9-]+|[EWIL]\d{3})$")
_RULES = {
    r"^[a-z][a-z0-9_]*$": "lowercase letters, digits and _, starting with a letter",
    r"^[a-z_][a-z0-9_]*$": "lowercase letters, digits and _",
    r"^[A-Za-z_][A-Za-z0-9_]*$": "letters, digits and _, not starting with a digit",
    r"^[a-z][a-z0-9_-]*$": "lowercase letters, digits, _ and -, starting with a letter",
    r"^[A-Za-z0-9_][A-Za-z0-9_-]*(/[A-Za-z0-9_][A-Za-z0-9_-]*)*$": "path segments of letters, digits, _ and -",
    r"^sha256:[0-9a-f]{64}$": "sha256: followed by 64 lowercase hex digits",
    r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$": "<step>.<exit>",
    r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$": "<module>:<Class>",
}
_NOUNS = {
    "steps": "step name", "entry": "step name", "on_error": "step name", "step": "step name",
    "exits": "exit name", "outputs": "exit name", "inputs": "field name", "with": "field name",
    "vars": "env var name", "env": "env var name", "name": "name", "from": "edge source",
    "entrypoint": "entrypoint", "step_roots": "step-root alias", "process": "process id",
}


def validation_diagnostics(
    err: ValidationError, data: Any, model: type[BaseModel], source: SourceMap
) -> list[Diagnostic]:
    """Map pydantic errors to E-SCHEMA/E-KIND diagnostics, or to the code a validator raised (PydanticCustomError
    whose type is a diagnostic code; an optional `loc` context entry extends the location)."""
    out: list[Diagnostic] = []
    for error in err.errors(include_url=False):
        model_loc = tuple(error["loc"])
        kind = error["type"]
        context = error.get("ctx") or {}
        if _CODE.match(kind):
            code, message = kind, error["msg"]
            model_loc = (*model_loc, *context.get("loc", ()))
        else:
            code, message = _schema_message(error, model_loc, model)
        loc = raw_loc(data, model_loc)
        mark = source.position(loc)
        diagnostic = Diagnostic(
            "error", code, message, file=source.file, line=mark.line if mark else None,
            column=mark.column if mark else None, loc=loc,
        )
        if diagnostic not in out:
            out.append(diagnostic)
    return out


def _schema_message(error: dict, loc: Loc, model: type[BaseModel]) -> tuple[str, str]:
    kind, context = error["type"], error.get("ctx") or {}
    last = loc[-1] if loc else None
    match kind:
        case "extra_forbidden":
            known = _field_names(model, [s for s in loc[:-1] if s != "[key]"])
            close = difflib.get_close_matches(str(last), known, n=1)
            hint = f" (did you mean '{close[0]}'?)" if close else ""
            return "E-SCHEMA", f"unknown field '{last}'{hint}"
        case "missing":
            return "E-SCHEMA", f"missing required field '{last}'"
        case "literal_error" if last == "kind":
            return "E-KIND", f"kind must be {context.get('expected')}"
        case "string_pattern_mismatch":
            rule = _RULES.get(context.get("pattern", ""), f"must match {context.get('pattern')}")
            return "E-SCHEMA", f"{error['input']!r} is not a valid {_noun(loc)} ({rule})"
        case "value_error":
            return "E-SCHEMA", str(error["msg"]).removeprefix("Value error, ")
    return "E-SCHEMA", error["msg"]


def _noun(loc: Loc) -> str:
    segments = [s for s in loc if isinstance(s, str) and s != "[key]"]
    if len(segments) >= 3 and segments[-3] == "outputs":
        return "field name"
    for segment in reversed(segments[:-1] if loc and loc[-1] == "[key]" else segments):
        if segment in _NOUNS:
            return _NOUNS[segment]
    return "name"


def raw_loc(data: Any, loc: Loc) -> Loc:
    """Map a validated-model location onto the document as written: union tags and `[key]` markers are dropped,
    a flat done-only `outputs` has no exit level, and the shorthand `to: x` carries its branch on the edge."""
    out: list[Any] = []
    node, parent = data, None
    for index, segment in enumerate(loc):
        last = index == len(loc) - 1
        if isinstance(node, dict) and segment in node:
            parent, node = node, node[segment]
            out.append(segment)
        elif isinstance(node, list) and isinstance(segment, int) and 0 <= segment < len(node):
            parent, node = node, node[segment]
            out.append(segment)
        elif segment == 0 and out and out[-1] == "to" and isinstance(node, str) and isinstance(parent, dict):
            node = parent
            out.pop()
        elif segment == "[key]":
            continue
        elif last and isinstance(node, dict):
            out.append(segment)
    return tuple(out)


def _field_names(model: Any, loc: list) -> list[str]:
    targets = [model]
    for segment in loc:
        targets = [nxt for target in targets for nxt in _step_into(target, segment)]
    names: list[str] = []
    for target in targets:
        for candidate in _flatten(target):
            if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                names += [info.alias or name for name, info in candidate.model_fields.items()]
    return names


def _flatten(tp: Any) -> list[Any]:
    if isinstance(tp, TypeAliasType):
        return _flatten(tp.__value__)
    origin = get_origin(tp)
    if origin is Annotated:
        return _flatten(get_args(tp)[0])
    if origin in (Union, types.UnionType):
        return [t for arg in get_args(tp) for t in _flatten(arg)]
    return [tp]


def _step_into(tp: Any, segment: Any) -> list[Any]:
    out = []
    for candidate in _flatten(tp):
        origin = get_origin(candidate)
        if isinstance(candidate, type) and issubclass(candidate, BaseModel):
            for name, info in candidate.model_fields.items():
                if segment in (name, info.alias):
                    out.append(info.annotation)
        elif origin in (list, tuple) and isinstance(segment, int):
            out.append(get_args(candidate)[0])
        elif origin is dict:
            out.append(get_args(candidate)[1])
    return out
