"""Compiler prompts (`$DRAFTS/05 §13.4, §14`): `<name>.md` package data, loaded with `importlib.resources`.

Every prompt file is a `str.format_map` template (literal braces are doubled). The system prompt of every call is
`preamble.md + "\\n\\n" + <call>.md` with its `{placeholders}` filled (`system_prompt`); the user message is
`render(sections)`.
"""

from __future__ import annotations

from importlib import resources

import yaml

TIER_WORDS = {
    "cheap": "a small, fast language model",
    "standard": "a mid-sized language model",
    "strong": "a large language model",
}


class Code(str):
    """A section value rendered as a fenced `python` block."""


def load(name: str) -> str:
    """The raw template text of `prompts/<name>.md`; FileNotFoundError for an unknown name."""
    return resources.files(__name__).joinpath(f"{name}.md").read_text(encoding="utf-8")


def system_prompt(name: str, **values: object) -> str:
    """`preamble.md + "\\n\\n" + <name>.md`, both formatted; a missing placeholder value raises KeyError."""
    return load("preamble").format_map({}).rstrip("\n") + "\n\n" + load(name).format_map(values).rstrip("\n")


def split_note(deferred_summary: str) -> str:
    """The `{split_note}` value of `write_agentic` for the agentic half of a split ("" otherwise)."""
    return load("write_agentic_split_note").format_map({"deferred_summary": deferred_summary})


def runtime_api() -> str:
    """The runtime API reference given to codegen calls, with the builtin catalog rendered from `BUILTINS`."""
    return load("runtime_api").format_map({"builtin_catalog": builtin_catalog()}).rstrip("\n")


def builtin_catalog() -> str:
    """One line per `wynd.runtime.tools.BUILTINS` tool: signature from its input schema, effects, idempotency, env
    and description."""
    from wynd.runtime.tools import BUILTINS

    lines = []
    for spec in BUILTINS:
        props = spec.input_schema.get("properties", {})
        required = set(spec.input_schema.get("required", []))
        params = ", ".join(f"{name}: {_json_type(props[name])}{'' if name in required else '?'}" for name in props)
        facts = [f"effects: {', '.join(spec.effects) or 'none'}", "idempotent" if spec.idempotent else "not idempotent"]
        if spec.env:
            facts.append(f"env: {', '.join(spec.env)}")
        if spec.name == "shell":
            facts.append('list it as shell.allow("<program>", ...), never bare')
        description = " ".join(spec.description.split())
        lines.append(f"  {spec.name}({params}) [{'; '.join(facts)}]: {description}")
    return "\n".join(lines)


def _json_type(schema: dict) -> str:
    match schema:
        case {"type": "array", "items": items}:
            return f"list[{_json_type(items)}]"
        case {"type": str(kind)}:
            return kind
        case {"anyOf": options}:
            return " | ".join(_json_type(option) for option in options)
    return "any"


def render(sections: list[tuple[str, str | dict | list]]) -> str:
    """Each section is `## <Title>` then plain text, or a fenced `yaml` (data: dict or list) or `python` (a `Code`
    string) block. Sections whose value is None are omitted. The output depends only on the input (dicts keep
    their order), so memo keys are stable."""
    parts = []
    for title, value in sections:
        match value:
            case None:
                continue
            case Code():
                body = f"```python\n{value.rstrip()}\n```"
            case str():
                body = value.strip("\n")
            case dict() | list():
                body = f"```yaml\n{_yaml(value).rstrip()}\n```"
            case _:
                raise TypeError(f"section {title!r}: expected str, dict or list, got {type(value).__name__}")
        parts.append(f"## {title}\n\n{body}")
    return "\n\n".join(parts) + "\n"


class _Dumper(yaml.SafeDumper):
    pass


def _str(dumper: yaml.SafeDumper, value: str) -> yaml.Node:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _str)
_Dumper.add_representer(Code, _str)


def _yaml(value: dict | list) -> str:
    return yaml.dump(value, Dumper=_Dumper, sort_keys=False, allow_unicode=True, default_flow_style=False, width=120)
