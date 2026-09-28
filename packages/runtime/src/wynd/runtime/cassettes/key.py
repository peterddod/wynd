"""Cassette keys (PLAN §3.16; `$DRAFTS/03 §11.2`): `sha256(Normaliser.apply(canonical_json(canonical_request)))`,
with `canonical_json` = `wynd.spec.hashing.canonical_json`.

A canonical request holds every field that influences model behaviour and nothing volatile. Model and agent requests
include provider, tier, thinking and the resolved `model_id` (PLAN §15 item 39: a tier remap is a request change).
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from wynd.spec.hashing import canonical_json

if TYPE_CHECKING:
    from wynd.runtime.providers.types import AgentRequest, GenerateRequest

DATETIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})")


class Normaliser:
    """Replaces literals (longest first) with placeholders, then `DATETIME_RE` with `<datetime>`.

    Recorded responses get the literal replacement only (`replace_literals`) and are brought back into the replaying
    run's terms with `restore`, so a replayed message or tool input naming the workspace or an example tmp dir names
    this run's, and the next request of the conversation keys exactly as it did when recorded.
    """

    def __init__(self, literals: Mapping[str, str]) -> None:
        self._literals = {value: placeholder for value, placeholder in literals.items() if value}
        self._order = sorted(self._literals, key=len, reverse=True)
        self._values: dict[str, str] = {}          # placeholder -> the first literal naming it (restore target)
        for value, placeholder in self._literals.items():
            self._values.setdefault(placeholder, value)

    @classmethod
    def for_run(cls, run_id: str, workspace: Path, extra: Mapping[str, str] = {}) -> Normaliser:
        """The run workspace (and its realpath) -> `<workspace>`, each `extra` literal (and, for an absolute path, its
        realpath) -> its placeholder, the run id -> `<run.id>`."""
        literals: dict[str, str] = {}              # the given form of each literal first: it is what `restore` writes
        for value, placeholder in extra.items():
            literals[value] = placeholder
            if os.path.isabs(value):
                literals[os.path.realpath(value)] = placeholder
        literals[str(workspace)] = "<workspace>"
        literals[os.path.realpath(workspace)] = "<workspace>"
        literals[run_id] = "<run.id>"
        return cls(literals)

    def apply(self, text: str) -> str:
        return DATETIME_RE.sub("<datetime>", self.replace_literals(text))

    def replace_literals(self, text: str) -> str:
        for value in self._order:
            text = text.replace(value, self._literals[value])
        return text

    def restore(self, text: str) -> str:
        for placeholder, value in self._values.items():
            text = text.replace(placeholder, value)
        return text


def normalised_json(canonical_request: dict[str, Any], normaliser: Normaliser) -> str:
    """The text the key is computed over; `json.loads` of it is the request stored in entries and miss dumps."""
    return normaliser.apply(canonical_json(canonical_request).decode())


def request_key(canonical_request: dict[str, Any], normaliser: Normaliser) -> str:
    return hashlib.sha256(normalised_json(canonical_request, normaliser).encode()).hexdigest()


def generate_request(req: GenerateRequest, *, provider: str, tier: str | None) -> dict[str, Any]:
    return {
        "v": 1,
        "kind": "generate",
        "provider": provider,
        "tier": tier,
        "thinking": req.thinking,
        "model_id": req.model_id,
        "system": req.system,
        "messages": req.messages,
        "tools": [asdict(t) for t in req.tools],
        "output_schema": req.output_schema,
    }


def agent_request(req: AgentRequest, *, provider: str, tier: str | None) -> dict[str, Any]:
    """The first call of an agent attempt (no continuation)."""
    return {
        "v": 1,
        "kind": "agent",
        "provider": provider,
        "tier": tier,
        "thinking": req.thinking,
        "model_id": req.model_id,
        "instruction": req.instruction,
        "context": req.context,
        "input": req.input,
        "prompt": req.prompt,
        "output_schema": req.output_schema,
        "tools": [{"name": h.name, "description": h.description, "input_schema": h.input_schema} for h in req.tools],
        "mcp": [{"name": s.name, "allow": list(s.allow)} for s in req.mcp_servers],
        "builtin_tools": list(req.builtin_tools),
        "max_turns": req.max_turns,
        "workspace": str(req.workspace),
    }


def continue_request(previous: str | None, message: str) -> dict[str, Any]:
    """An agent continuation, chained on the previous call's key (the session transcript is volatile, the chain is
    not)."""
    return {"v": 1, "kind": "agent_continue", "previous": previous, "message": message}


def tool_request(name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    return {"v": 1, "kind": "tool", "tool": name, "arguments": dict(arguments)}
