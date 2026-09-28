"""The `fake` AgentProvider (PLAN §3.15, §15 item 63; script format `$DRAFTS/03 §14.5`).

Test-only and inert unless selected: answers from `WYND_FAKE_PROVIDER_SCRIPT` (inline JSON when the value's first
non-space character is `{` or `[`, else a file path), zero usage, model id `fake`, no network I/O.

Script: `{"responses": [{"match": {"input": {...}}, "output": {...}, "note": "..."}, ...]}` (or the bare list). The
first response whose `match.input` is a subset of the request input wins (nested objects are matched as subsets,
everything else by equality; a missing `match` matches everything). A miss raises `ProviderError(kind=
"invalid_request")`. The script is read on every call, so a test can change it between runs.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar, Literal

from wynd.runtime.providers.types import AgentRequest, AgentResponse, ProviderError
from wynd.runtime.usage import Usage
from wynd.spec.fragments import EnvFragment, EnvVar

SCRIPT_VAR = "WYND_FAKE_PROVIDER_SCRIPT"
MODEL_ID = "fake"


class FakeProvider:
    name: ClassVar[str] = "fake"
    kind: ClassVar[Literal["model", "agent"]] = "agent"
    default_tiers: ClassVar[dict[str, str]] = {"cheap": "fake", "standard": "fake", "strong": "fake"}
    env_fragment: ClassVar[EnvFragment] = EnvFragment(
        vars=[
            EnvVar(
                name="WYND_FAKE_PROVIDER_SCRIPT",
                description="Fake provider script: inline JSON (starting with { or [) or a file path.",
                required=False,
                used_by=["provider:fake"],
            ),
        ],
    )

    def __init__(self, tiers: Mapping[str, str]) -> None:
        self._tiers = dict(tiers)

    def tiers(self) -> dict[str, str]:
        return dict(self._tiers)

    def run(self, req: AgentRequest) -> AgentResponse:
        if req.cancel is not None and req.cancel.is_set():
            raise ProviderError("cancelled", kind="transport", retryable=False)
        responses, source = _load_script(os.environ.get(SCRIPT_VAR))
        for entry in responses:
            if _is_subset((entry.get("match") or {}).get("input") or {}, req.input):
                return AgentResponse(
                    structured_output=entry.get("output"),
                    usage=Usage(cost_usd=0.0, calls=1),
                    model_id=MODEL_ID,
                    transcript=[],
                    session={},
                    note=entry.get("note", ""),
                )
        shown = json.dumps(req.input, sort_keys=True, ensure_ascii=False, default=str)[:500]
        raise ProviderError(f"fake provider: no scripted response in {source} matches input {shown}",
                            kind="invalid_request", retryable=False)


def _load_script(value: str | None) -> tuple[list[dict[str, Any]], str]:
    """-> (responses, source description). Problems with the script itself are `ProviderError(kind="unavailable")`
    (a configuration error, not a model error)."""
    if not value or not value.strip():
        raise ProviderError(f"fake provider: {SCRIPT_VAR} is not set", kind="unavailable", retryable=False)
    if value.lstrip()[0] in "{[":
        text, source = value, f"inline {SCRIPT_VAR}"
    else:
        source = value
        try:
            text = Path(value).read_text(encoding="utf-8")
        except OSError as e:
            raise ProviderError(f"fake provider: cannot read {SCRIPT_VAR} file {value}: {e}",
                                kind="unavailable", retryable=False) from None
    try:
        data = json.loads(text)
    except ValueError as e:
        raise ProviderError(f"fake provider: {source} is not valid JSON: {e}", kind="unavailable",
                            retryable=False) from None
    responses = data.get("responses") if isinstance(data, dict) else data
    if not isinstance(responses, list) or not all(isinstance(r, dict) for r in responses):
        raise ProviderError(f'fake provider: {source} must be {{"responses": [...]}} or a list of responses',
                            kind="unavailable", retryable=False)
    return responses, source


def _is_subset(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and _is_subset(v, actual[k]) for k, v in expected.items())
    return expected == actual
