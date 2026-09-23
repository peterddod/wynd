"""Compiler call kinds and their structured-output response models (`$DRAFTS/05 §13.1, §13.3`).

Every call is one structured-output request with no tools. Example data travels as JSON-encoded strings
(`inputs_json`, `outputs_json`) because the strict schema has no free-form objects; the compiler parses and validates
it. Tier and thinking per kind follow `$DRAFTS/05 §13.1` (strong for codegen/decide/proposals, standard for
infer_schema/revise_example).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

CallKind = Literal["infer_schema", "propose_examples", "revise_example", "decide", "write_deterministic",
                   "write_shell", "write_agentic", "revise_code", "revise_agentic"]


class Field(BaseModel):
    name: str
    type: str                  # proto type grammar, validated with the spec type parser
    description: str
    free_text: bool


class ExitFields(BaseModel):
    exit: str
    fields: list[Field]


class InferSchemaResponse(BaseModel):
    inputs: list[Field]
    exits: list[ExitFields]
    problems: list[str]        # contradictions the model found; empty if none


class Proposal(BaseModel):
    question: str
    rationale: str
    exit: str
    inputs_json: str
    outputs_json: str


class ProposeExamplesResponse(BaseModel):
    proposals: list[Proposal]


class ReviseExampleResponse(BaseModel):
    drop: bool
    exit: str
    inputs_json: str
    outputs_json: str
    understood: str


class ExampleNeeds(BaseModel):
    index: int                 # 1-based example number
    needs: Literal["pure", "judgement", "world_knowledge", "external_data"]
    why: str


class DecideResponse(BaseModel):
    examples: list[ExampleNeeds]
    command: bool
    program: str               # "" when command is false
    summary: str


class EnvVar(BaseModel):
    name: str
    description: str


class WriteCodeResponse(BaseModel):          # deterministic and shell
    module_source: str
    deps: list[str]
    system_packages: list[str]
    requires_glibc: bool
    effects: list[Literal["network", "filesystem", "shell"]]
    env_vars: list[EnvVar]
    notes: str


class Suspect(BaseModel):
    example: int
    why: str


class ReviseCodeResponse(WriteCodeResponse):
    diagnosis: str
    verdict: Literal["fixable", "needs_judgement", "examples_inconsistent"]
    suspects: list[Suspect]


class McpUse(BaseModel):
    server: str
    allow: list[str]


class WriteAgenticResponse(BaseModel):
    docstring: str
    context: list[str]
    tools: list[str]           # builtin tool names
    mcp: list[McpUse]
    tool_methods: str          # "" or method source indented 4 spaces
    deps: list[str]            # for tool methods only
    env_vars: list[EnvVar]
    notes: str


class ReviseAgenticResponse(WriteAgenticResponse):
    diagnosis: str
    verdict: Literal["fixable", "examples_inconsistent", "needs_stronger_model"]
    suspects: list[Suspect]
