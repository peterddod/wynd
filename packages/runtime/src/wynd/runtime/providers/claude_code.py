"""The `claude-code` AgentProvider over claude-agent-sdk (PLAN §3.15, §1.4; `$DRAFTS/03 §7`).

`claude_agent_sdk` is imported only inside function bodies: the runtime never depends on it; the provider's env
fragment declares it for the step venvs that need it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, ClassVar, Literal

from wynd.runtime.providers.types import AgentRequest, AgentResponse
from wynd.spec.fragments import EnvFragment, EnvGroup, EnvVar

SERVER = "wynd"
EFFORT = {"none": "low", "low": "low", "medium": "medium", "high": "high"}
BUILTIN_EFFECTS = {"Read": ["filesystem"], "Glob": ["filesystem"], "Grep": ["filesystem"], "Edit": ["filesystem"],
                   "Write": ["filesystem"], "NotebookEdit": ["filesystem"], "Bash": ["shell"],
                   "WebFetch": ["network"], "WebSearch": ["network"]}


class ClaudeCodeProvider:
    name: ClassVar[str] = "claude-code"
    kind: ClassVar[Literal["model", "agent"]] = "agent"
    default_tiers: ClassVar[dict[str, str]] = {"cheap": "haiku", "standard": "sonnet", "strong": "opus"}
    env_fragment: ClassVar[EnvFragment] = EnvFragment(
        deps=["claude-agent-sdk>=0.2.157,<0.3"],
        requires="glibc",
        vars=[
            EnvVar(
                name="CLAUDE_CODE_OAUTH_TOKEN",
                description="Claude Code subscription token (dev key, `claude setup-token`). Locally the logged-in "
                "`claude` CLI is used when neither this nor ANTHROPIC_API_KEY is set.",
                secret=True,
                required=False,
                one_of="claude-code-auth",
                used_by=["provider:claude-code"],
            ),
            EnvVar(
                name="ANTHROPIC_API_KEY",
                description="Anthropic API key; alternative to CLAUDE_CODE_OAUTH_TOKEN (takes precedence if both are "
                "set).",
                secret=True,
                required=False,
                one_of="claude-code-auth",
                used_by=["provider:claude-code"],
            ),
        ],
        groups={
            "claude-code-auth": EnvGroup(
                description="Claude Code authentication: one of CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY "
                "(locally the logged-in `claude` CLI suffices).",
                modes=["image"],
            )
        },
    )

    def __init__(self, tiers: Mapping[str, str], *, query_fn: Callable[..., Any] | None = None) -> None:
        raise NotImplementedError("PLAN §3.15")

    def tiers(self) -> dict[str, str]:
        raise NotImplementedError("PLAN §3.15")

    def run(self, req: AgentRequest) -> AgentResponse:
        raise NotImplementedError("PLAN §3.15")
