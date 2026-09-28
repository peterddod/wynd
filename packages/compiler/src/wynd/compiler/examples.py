"""Example checks (conformance, contradictions, unknown exits), edge-case proposals, questions about examples and
appending confirmed examples to the proto YAML (`$DRAFTS/05 §6.4, §7.4-§7.5, §9.6`).

Examples are spec `Example`s in canonical form (`schemas.canonical`: JSON-mode values, dates as ISO strings, `{tmp}`
values verbatim). Relative `path` values resolve against `ExampleContext.base_dir` (the process dir, or the package
dir of a step-root step). Answer interpretation itself is the session's (`session.interpret`).
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import ValidationError

from wynd.spec.base import RESERVED_EXIT
from wynd.spec.hashing import canonical_json, hash_obj
from wynd.spec.proto_step import Example
from wynd.spec.typelang import TList, TObject, TScalar, TypeNode, render_type

if TYPE_CHECKING:
    from wynd.compiler.calls import CallKind, Proposal
    from wynd.compiler.llm import CompilerLLM
    from wynd.compiler.session import Answer, CompileSession, Question
    from wynd.spec.proto_step import ProtoStep

TMP_PREFIX = "{tmp}"


@dataclass
class ExampleContext:
    """One node's view for example handling: identity, schema and where relative paths resolve."""
    process: str                                  # process id
    node: str                                     # node name in process.yaml
    package: str                                  # step package name
    prefix: str                                   # question id prefix: "" (root process) or "<child-pid>:"
    instruction: str
    inputs: dict[str, TypeNode]
    outputs: dict[str, dict[str, TypeNode]]       # exit -> fields, in declaration order
    base_dir: Path
    job: str = ""                                 # job asking (the session overwrites it)
    now: Callable[[], str] = lambda: ""           # ISO timestamp (the session overwrites it)

    @property
    def exits(self) -> list[str]:
        return list(self.outputs)

    def qid(self, local: str) -> str:
        return f"{self.prefix}{self.node}.{local}"

    @property
    def llm_node(self) -> str:
        """The node as compiler LLM calls name it (`<child-pid>:<node>` in a child process)."""
        return f"{self.prefix}{self.node}"


@dataclass
class ProposalOutcome:
    added: list[Example] = field(default_factory=list)       # confirmed/corrected, canonical, with description
    rejected: list[str] = field(default_factory=list)        # questions rejected (-> compiled.rejected)
    pending: bool = False
    proposed: int = 0
    confirmed: int = 0
    corrected: int = 0
    warnings: list[str] = field(default_factory=list)


# --- LLM calls ---------------------------------------------------------------------------------------------------

def call_llm(llm: CompilerLLM, kind: CallKind, *, node: str, sections: list[tuple[str, Any]],
             **placeholders: Any) -> Any:
    """One compiler call with the kind's tier, thinking and response model (`calls.CALLS`); returns the value."""
    from wynd.compiler.calls import CALLS
    from wynd.compiler.prompts import render, system_prompt

    spec = CALLS[kind]
    result = llm.call(kind, node=node, system=system_prompt(kind, **placeholders), prompt=render(sections),
                      response_model=spec.response_model, tier=spec.tier, thinking=spec.thinking)
    return result.value


def schema_section(ctx: ExampleContext) -> dict[str, Any]:
    """Types and plain language, as shown to the model."""
    from wynd.compiler.schemas import plain_interface

    return {
        "inputs": {name: render_type(node) for name, node in ctx.inputs.items()},
        "outputs": {exit: {name: render_type(node) for name, node in fields.items()}
                    for exit, fields in ctx.outputs.items()},
        "plain": plain_interface(ctx.package, ctx.inputs, ctx.outputs),
    }


# --- checks ----------------------------------------------------------------------------------------------------------

def canonical(example: Example, ctx: ExampleContext) -> Example:
    from wynd.compiler.schemas import canonical as schema_canonical

    return schema_canonical(example, ctx.inputs, ctx.outputs)


def check_examples(examples: Sequence[Example], ctx: ExampleContext) -> list[str]:
    """Conformance and unknown exits (spec E-EXAMPLE), as numbered plain-language problems."""
    from wynd.compiler.schemas import example_problems as conformance

    return conformance(examples, ctx.package, ctx.inputs, ctx.outputs)


def contradictions(examples: Sequence[Example], ctx: ExampleContext) -> list[str]:
    """Pairs of examples with equal canonical inputs but a different exit or different outputs."""
    first: dict[bytes, int] = {}
    problems = []
    for n, example in enumerate(examples, 1):
        c = canonical(example, ctx)
        key = canonical_json(c.inputs)
        if key not in first:
            first[key] = n
            continue
        m = first[key]
        other = canonical(examples[m - 1], ctx)
        if other.exit != c.exit:
            problems.append(f"examples {m} and {n} have the same inputs but expect different exits "
                            f"({other.exit} and {c.exit})")
        elif canonical_json(other.outputs) != canonical_json(c.outputs):
            problems.append(f"examples {m} and {n} have the same inputs but expect different outputs")
    return problems


def example_problems(examples: Sequence[Example], ctx: ExampleContext) -> list[str]:
    """Everything that stops a node before any LLM call: conformance, unknown exits, then contradictions."""
    return check_examples(examples, ctx) or contradictions(examples, ctx)


def proposal_problem(example: Example, ctx: ExampleContext, existing: Sequence[Example]) -> str | None:
    """Why a proposed example cannot be offered: it does not conform, duplicates an example, names a missing file
    (unless it expects `error`), or expects outputs from a fixture file no existing example uses (the model sees
    only file names, so those outputs are a guess `--accept-proposals` would make permanent). None when it is
    fine."""
    problems = check_examples([example], ctx)
    if problems:
        return problems[0]
    c = canonical(example, ctx)
    for n, other in enumerate(existing, 1):
        if canonical_json(canonical(other, ctx).inputs) == canonical_json(c.inputs):
            return f"it has the same inputs as example {n}"
    if c.exit == RESERVED_EXIT:
        return None
    for name, node in ctx.inputs.items():
        value = c.inputs.get(name)
        if not (isinstance(node, TScalar) and node.name == "path" and isinstance(value, str)):
            continue
        if value.startswith(TMP_PREFIX):
            continue
        if not (ctx.base_dir / value).exists():
            return f"the file {value!r} does not exist"
        if (name, value) not in _used_paths(existing, ctx):
            return f"its outputs depend on the content of {value!r}, which no existing example shows"
    return None


def _used_paths(examples: Sequence[Example], ctx: ExampleContext) -> set[tuple[str, str]]:
    """(field, path) input pairs of examples that expect a declared exit (their outputs show what the file holds
    when read through that field)."""
    return {(name, e.inputs[name]) for e in examples if e.exit != RESERVED_EXIT
            for name, node in ctx.inputs.items()
            if isinstance(node, TScalar) and node.name == "path" and isinstance(e.inputs.get(name), str)}


def parse_proposal(p: Proposal) -> Example:
    """The example a proposal carries; ValueError when its JSON strings are not objects."""
    return _example(p.inputs_json, p.outputs_json, p.exit)


def fixture_files(base_dir: Path, *, limit: int = 200) -> list[str]:
    """Files under the example base dir (relative POSIX paths), offered to proposals that need path inputs."""
    if not base_dir.is_dir():
        return []
    skip = {"steps", "proto", "cassettes", "__pycache__", ".wynd"}
    out = []
    for path in sorted(base_dir.rglob("*")):
        rel = path.relative_to(base_dir)
        if path.is_file() and not skip.intersection(rel.parts[:-1]) and not rel.name.startswith(".") \
                and rel.suffix not in (".py", ".yaml", ".toml", ".pyc"):
            out.append(rel.as_posix())
            if len(out) >= limit:
                break
    return out


# --- questions ------------------------------------------------------------------------------------------------------

def make_question(ctx: ExampleContext, *, local_id: str, kind: str, text: str, detail: str = "",
                  proposed: dict | None = None, expects: str = "text", default: str | None = None) -> Question:
    from wynd.compiler.session import Question

    return Question(
        id=ctx.qid(local_id), kind=kind, process=ctx.process, step=ctx.node, package=ctx.package, text=text,
        detail=detail, proposed=proposed, expects=expects, default=default,
        fingerprint=hash_obj({"kind": kind, "text": text, "proposed": proposed}),
        asked_at=ctx.now(), asked_in_job=ctx.job,
    )


def next_question_id(session: CompileSession, ctx: ExampleContext, stem: str) -> str:
    """`<stem><k>`, k = 1 + the number of already-answered `<stem>` questions of this node (`clarify`,
    `examples`)."""
    head = ctx.qid(stem)
    answered = sum(1 for q in session.data.questions
                   if q.id.startswith(head) and q.id[len(head):].isdigit() and q.status == "answered")
    return f"{stem}{answered + 1}"


def answered_guidance(session: CompileSession, ctx: ExampleContext) -> list[str]:
    """Answered clarifications (`expects: text`) of this node, oldest first: guidance for its whole pipeline."""
    head = ctx.qid("clarify")
    return [q.answer.text.strip() for q in session.data.questions
            if q.id.startswith(head) and q.expects == "text" and q.status == "answered" and q.answer is not None
            and q.answer.text.strip()]


def ask_problems(session: CompileSession, ctx: ExampleContext, problems: Sequence[str]) -> Answer | None:
    """The clarification asked when the proto's examples do not fit together (the user fixes the proto)."""
    text = (f"The examples of {ctx.package} have problems I cannot resolve by myself. Please fix the proto-step "
            f"and compile again, or tell me how to read them.")
    detail = "\n".join(f"- {p}" for p in problems)
    q = make_question(ctx, local_id=next_question_id(session, ctx, "clarify"), kind="clarification", text=text,
                      detail=detail, expects="text")
    return session.ask(q)


def ask_for_examples(session: CompileSession, ctx: ExampleContext, problems: Sequence[str] = ()) -> Answer | None:
    """The `expects: examples` clarification for a step that has no examples (a step without tests is never
    compiled). `problems` repeats why the previous answer was not usable."""
    text = (f"{ctx.package} has no examples, so it cannot be tested. Please give at least one example as a YAML "
            f"list of {{inputs, exit, outputs}}.")
    detail = "\n".join(f"- {p}" for p in problems)
    q = make_question(ctx, local_id=next_question_id(session, ctx, "examples"), kind="clarification", text=text,
                      detail=detail, expects="examples")
    return session.ask(q)


def answered_examples(session: CompileSession, ctx: ExampleContext) -> tuple[list[Example], list[str]]:
    """Examples given by answered `expects: examples` questions of this node: (canonical examples, problems of the
    latest answer). Problems ask the question again under the next id."""
    head = ctx.qid("examples")
    answers = [q.answer.text for q in session.data.questions
               if q.id.startswith(head) and q.status == "answered" and q.answer is not None]
    if not answers:
        return [], []
    return parse_examples_answer(answers[-1], ctx)


def parse_examples_answer(text: str, ctx: ExampleContext) -> tuple[list[Example], list[str]]:
    """Examples given as a YAML list (or one mapping): (canonical examples, problems)."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as err:
        return [], [f"the answer is not YAML: {err}"]
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list) or not data or not all(isinstance(e, dict) for e in data):
        return [], ["the answer must be a YAML list of examples, each with inputs, exit and outputs"]
    try:
        examples = [Example.model_validate({"inputs": e.get("inputs") or {}, "outputs": e.get("outputs") or {},
                                            "exit": e.get("exit", "done")}) for e in data]
    except ValidationError as err:
        return [], [f"the answer is not a list of examples: {err.errors()[0]['msg']}"]
    problems = example_problems(examples, ctx)
    if problems:
        return [], problems
    return [canonical(e, ctx) for e in examples], []


# --- proposals ------------------------------------------------------------------------------------------------------

def propose_and_confirm(session: CompileSession, ctx: ExampleContext, *, llm: CompilerLLM,
                        examples: Sequence[Example], rejected: Sequence[str], process_context: Mapping[str, Any],
                        guidance: Sequence[str], max_proposals: int) -> ProposalOutcome:
    """Ask the model for edge cases, drop invalid ones (with a warning), and ask the owner about each
    ($DRAFTS/05 §7.5). Every question of the node is asked in one pass; `pending` is set when any is unanswered."""
    from wynd.compiler.schemas import numbered_examples

    sections: list[tuple[str, Any]] = [
        ("Step", {"name": ctx.package, "instruction": ctx.instruction}),
        ("Schema", schema_section(ctx)),
        ("Examples", numbered_examples(examples) or "none"),
        ("Fixture files", fixture_files(ctx.base_dir) or "none"),
        ("Rejected questions", list(rejected) or "none"),
        ("Process context", dict(process_context)),
        ("User guidance", list(guidance) or None),
        ("Maximum proposals", str(max_proposals)),
    ]
    resp = call_llm(llm, "propose_examples", node=ctx.llm_node, sections=sections, max_proposals=max_proposals)
    out = ProposalOutcome()
    valid: list[tuple[Proposal, Example]] = []
    for p in resp.proposals[:max_proposals]:
        try:
            example = parse_proposal(p)
        except ValueError as err:
            out.warnings.append(f"dropped an invalid proposal ({p.question}): {err}")
            continue
        problem = proposal_problem(example, ctx, [*examples, *(e for _, e in valid)])
        if problem is not None:
            out.warnings.append(f"dropped an invalid proposal ({p.question}): {problem}")
            continue
        valid.append((p, canonical(example, ctx)))
    out.proposed = len(valid)

    for i, (p, example) in enumerate(valid, 1):
        proposed = _authoring(example)
        q = make_question(ctx, local_id=f"example{i}", kind="example_proposal", text=p.question,
                          detail=f"Why: {p.rationale}\n\n{_yaml_block(proposed)}", proposed=proposed,
                          expects="decision", default="accept")
        answer = session.ask(q)
        if answer is None:
            out.pending = True
            continue
        match answer.decision:
            case "confirm":
                out.added.append(example.model_copy(update={"description": p.question}))
                out.confirmed += 1
            case "reject":
                out.rejected.append(p.question)
            case _:
                corrected = _correction(session, ctx, llm, example, answer)
                if corrected is None:
                    out.rejected.append(p.question)
                    continue
                out.added.append(corrected.model_copy(update={"description": p.question}))
                out.corrected += 1
    return out


def _correction(session: CompileSession, ctx: ExampleContext, llm: CompilerLLM, proposed: Example,
                answer: Answer) -> Example | None:
    """The corrected example: the structured one if it conforms, else the `revise_example` call's (None = drop)."""
    if answer.example is not None:
        try:
            given = Example.model_validate({k: answer.example[k] for k in ("inputs", "outputs", "exit")
                                            if k in answer.example})
        except ValidationError:
            given = None
        if given is not None and not check_examples([given], ctx):
            return canonical(given, ctx)
    sections = [("Schema", schema_section(ctx)), ("Proposed example", _authoring(proposed)),
                ("The user's answer", answer.text)]
    resp = call_llm(llm, "revise_example", node=ctx.llm_node, sections=sections)
    if resp.drop:
        return None
    try:
        example = _example(resp.inputs_json, resp.outputs_json, resp.exit)
    except ValueError:
        return None
    if check_examples([example], ctx):
        return None
    session.emit("answer", resp.understood, process=ctx.process, step=ctx.node)
    return canonical(example, ctx)


# --- appending to the proto ----------------------------------------------------------------------------------------

def append_examples(text: str, proto: ProtoStep, added: Sequence[Example], *, session_id: str) -> tuple[str, bool]:
    """(new proto YAML, reformatted). Surgical append with comments kept, verified by re-parse against the old model
    with the examples extended; otherwise a full re-dump of `to_authoring()` (comments lost, `reformatted=True`)."""
    from wynd.compiler.yamledit import append_block_items
    from wynd.spec.proto_step import ProtoStep
    from wynd.spec.yamlio import dump_yaml, parse_model

    if not added:
        return text, False
    items = [_authoring_item(e, proto) for e in added]
    expected = proto.model_copy(update={"examples": [*proto.examples, *(Example.model_validate(i) for i in items)]})
    try:
        new = append_block_items(text, "examples", items,
                                 comment=f"confirmed during wynd compile (session {session_id})")
        if parse_model(new, ProtoStep).model_dump(mode="json") == expected.model_dump(mode="json"):
            return new, False
    except (ValueError, yaml.YAMLError):
        pass
    return dump_yaml(expected.to_authoring()), True


def _authoring_item(example: Example, proto: ProtoStep) -> dict[str, Any]:
    """Key order inputs, outputs, exit, description; date fields as date objects so they dump unquoted."""
    item: dict[str, Any] = {"inputs": _dates(example.inputs, proto.inputs)}
    outputs = _dates(example.outputs, proto.outputs.get(example.exit, {}))
    if outputs:
        item["outputs"] = outputs
    item["exit"] = example.exit
    if example.description:
        item["description"] = example.description
    return item


def _dates(values: Mapping[str, Any], fields: Mapping[str, TypeNode]) -> dict[str, Any]:
    return {name: _date_value(value, fields.get(name)) for name, value in values.items()}


def _date_value(value: Any, node: TypeNode | None) -> Any:
    match node:
        case TScalar(name="date") if isinstance(value, str):
            try:
                return dt.date.fromisoformat(value)
            except ValueError:
                return value
        case TScalar(name="datetime") if isinstance(value, str):
            try:
                return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return value
        case TList(item=item) if isinstance(value, list):
            return [_date_value(v, item) for v in value]
        case TObject(fields=fields) if isinstance(value, dict):
            return _dates(value, dict(fields))
    return value


def _example(inputs_json: str, outputs_json: str, exit: str) -> Example:
    inputs, outputs = json.loads(inputs_json or "{}"), json.loads(outputs_json or "{}")
    if not isinstance(inputs, dict) or not isinstance(outputs, dict):
        raise ValueError("inputs_json and outputs_json must be JSON objects")
    try:
        return Example(inputs=inputs, outputs=outputs, exit=exit)
    except ValidationError as err:
        raise ValueError(str(err)) from None


def _authoring(example: Example) -> dict[str, Any]:
    return {"inputs": dict(example.inputs), "outputs": dict(example.outputs), "exit": example.exit}


def _yaml_block(example: Mapping[str, Any]) -> str:
    text = yaml.safe_dump(dict(example), sort_keys=False, allow_unicode=True, width=100)
    return f"```yaml\n{text}```"
