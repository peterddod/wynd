"""Compiler prompts (`$DRAFTS/05 §13.4, §14`): `<name>.md` package data, loaded with `importlib.resources`.

The system prompt of every call is `preamble.md + "\\n\\n" + <call>.md` with `{placeholders}` filled by
`str.format_map`; the user message is `render(sections)`.
"""

from __future__ import annotations


def load(name: str) -> str:
    raise NotImplementedError("PLAN §7")


def render(sections: list[tuple[str, str | dict | list]]) -> str:
    """Each section is `## <Title>` then plain text, or a fenced `yaml` (data) or `python` (code) block;
    deterministic output so memo keys are stable."""
    raise NotImplementedError("PLAN §7")
