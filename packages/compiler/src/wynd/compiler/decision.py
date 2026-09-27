"""The compile decision (rules 1-4, precedence 4 -> (1, 3) -> 2), the generate-test-revise loops, split eligibility
and tier escalation (`$DRAFTS/05 §8`, PLAN §15 item 42).

- Rule 4: the proto declares `exit_codes`, or the `decide` call says the step runs a command -> ShellStep.
- At most half of the examples classified `pure` -> AgenticStep directly (rule 2).
- Otherwise deterministic code is tried first: all examples pass -> rule 1. Strictly more than half handled, none
  wrong and at least one deferral (`NotImplementedError`) -> a two-step split (rule 3) when the graph allows it.
  Anything else falls back to an agentic step (rule 2).
- Agentic loops run at `cheap`; one further loop at `standard` (`max_revisions=1`) if cheap does not pass. A shell
  or agentic loop that cannot pass, or any loop whose revision says the examples are inconsistent, asks a
  clarification (`<node>.clarify<k>`); a step described as a command never falls back to an agent.

The attempt runner is injected (`NodeBuild.run`) so the pipeline decides where files go and how tests run.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Literal

from wynd.compiler.examples import call_llm, make_question, next_question_id, schema_section

if TYPE_CHECKING:
    from wynd.compiler.attempts import AttemptResult
    from wynd.compiler.codegen import CodegenContext, Generated
    from wynd.compiler.examples import ExampleContext
    from wynd.compiler.llm import CompilerLLM
    from wynd.compiler.session import CompileSession
    from wynd.spec.process_doc import ProcessDoc

RunAttempt = Callable[["CodegenContext", "Generated", int], "AttemptResult"]
DecisionKind = Literal["deterministic", "agentic", "shell", "split"]
AGENTIC_SUFFIX = "_agentic"
DEFAULT_THINKING = "low"


@dataclass
class NodeBuild:
    """Everything one node's decision needs. `cg` is the node package's codegen context (its `test_file` generated
    from the canonical examples); the loops derive per-kind contexts from it."""
    session: CompileSession
    ex: ExampleContext
    cg: CodegenContext
    llm: CompilerLLM
    run: RunAttempt                       # (codegen ctx, generated, attempt n) -> AttemptResult
    exit_codes: dict | None = None        # proto.exit_codes (rule 4)
    split_blocker: str | None = None      # why a split is not possible here; None = possible
    process_context: dict[str, Any] = field(default_factory=dict)
    available_tools: dict[str, Any] = field(default_factory=dict)
    max_revisions: int = 3
    proto_hash: str | None = None         # for generated test headers
    step_root: bool = False
    free_text: list[str] = field(default_factory=list)
    attempt_no: int = 0                   # attempts run so far for this node (numbers scratch dirs)
    records: list[dict[str, Any]] = field(default_factory=list)   # report `attempts` entries


@dataclass
class LoopResult:
    kind: str
    tier: str | None
    cg: CodegenContext                    # the context the attempts ran with
    best: Generated
    best_result: AttemptResult
    results: list[AttemptResult]
    diagnoses: list[str] = field(default_factory=list)
    suspects: list[tuple[int, str]] = field(default_factory=list)
    inconsistent: bool = False
    escalate: bool = False

    @property
    def passed(self) -> bool:
        return self.best_result.all_passed


@dataclass
class Decision:
    status: Literal["built", "awaiting", "failed"]
    reason: str
    kind: DecisionKind | None = None
    rule: Literal[1, 2, 3, 4] | None = None
    tier: str | None = None               # agentic (and the agentic half of a split)
    thinking: str | None = None
    main: LoopResult | None = None        # the node package (deterministic half of a split)
    agentic: LoopResult | None = None     # split: the `<package>_agentic` package
    classification: list[dict[str, Any]] = field(default_factory=list)
    handled: list[int] = field(default_factory=list)      # split: example numbers
    deferred: list[int] = field(default_factory=list)
    attempts: list[dict[str, Any]] = field(default_factory=list)

    def record(self) -> dict[str, Any]:
        """The decision record for the report and `compiled.decision` ($DRAFTS/05 §8.4)."""
        out: dict[str, Any] = {"kind": self.kind, "rule": self.rule, "reason": self.reason}
        if self.classification:
            out["classification"] = self.classification
        if self.tier:
            out["tier"] = self.tier
        return out


# --- the decision ---------------------------------------------------------------------------------------------------

def decide_and_build(b: NodeBuild) -> Decision:
    """Rules 4 -> (1, 3) -> 2 for one node ($DRAFTS/05 §8.1)."""
    total = len(b.cg.examples)
    if b.exit_codes is not None:
        return _shell(b, program="", reason="the proto-step declares exit_codes")

    d = call_llm(b.llm, "decide", node=b.ex.llm_node, sections=[
        ("Step", {"name": b.ex.package, "instruction": b.ex.instruction}),
        ("Schema", schema_section(b.ex)),
        ("Examples", _numbered(b)),
        ("Available tools", b.available_tools or "none"),
        ("Process context", b.process_context or "none"),
        ("User guidance", list(b.cg.guidance) or None),
    ])
    classification = [{"example": e.index, "needs": e.needs, "why": e.why} for e in d.examples]
    if d.command:
        return _with(_shell(b, program=d.program, reason=d.summary), classification=classification)

    pure = sum(1 for e in d.examples if e.needs == "pure")
    if pure * 2 <= total:
        why = f"{total - pure} of {total} examples need judgement, knowledge or outside data. {d.summary}".strip()
        return _with(_agentic(b, rule=2, reason=why), classification=classification)

    det = generate_test_revise(b, _kind(b.cg, "deterministic"), max_revisions=b.max_revisions)
    if det.inconsistent:
        return _with(_clarify(b, [det]), classification=classification)
    best = det.best_result
    if best.all_passed:
        return _built(b, kind="deterministic", rule=1, reason=d.summary or "every example follows fixed rules",
                      main=det, classification=classification)
    if split_eligible(best):
        if b.split_blocker is None:
            return _with(_split(b, det), classification=classification)
        _emit(b, "decision", f"deterministic code handled {best.handled}/{total}; a split is not possible here: "
                             f"{b.split_blocker}")
        why = (f"deterministic code handled {best.handled} of {total} examples, but a split is not possible: "
               f"{b.split_blocker}")
        return _with(_agentic(b, rule=2, reason=why), classification=classification)
    why = f"deterministic code handled only {best.handled} of {total} examples"
    return _with(_agentic(b, rule=2, reason=why), classification=classification)


def split_eligible(r: AttemptResult) -> bool:
    """Rule 3: strictly more than half handled, zero wrong answers, at least one deferral."""
    total = r.handled + r.deferred + r.wrong
    return r.wrong == 0 and r.handled * 2 > total and r.deferred >= 1


def split_applicable(doc: ProcessDoc, node: str, *, ref_kind: str, existing_packages: Collection[str] = ()) \
        -> str | None:
    """Why the node cannot be split (None when it can). `doc` must not contain an earlier split of this node, and
    `existing_packages` are the package dirs under the process's `steps/` other than an earlier agentic half."""
    from wynd.spec.expr import references
    from wynd.spec.process_doc import expression_sites
    from wynd.spec.workspace import parse_use

    if ref_kind != "local":
        return "the step comes from a step root and is shared with other processes"
    use = doc.steps[node].use
    if sum(1 for ref in doc.steps.values() if ref.use == use) > 1:
        return "the package is used by several nodes"
    if doc.edge(f"{node}.error") is not None:
        return f"an edge already handles `{node}.error`"
    for site in expression_sites(doc):
        if site.edge is not None and site.edge.partition(".")[0] == node:
            continue
        for ref in references(site.text):
            head = ref.path[0] if ref.path else None
            if (ref.root == "steps" and head == node) or \
                    (ref.root == "edges" and isinstance(head, str) and head.partition(".")[0] == node):
                where = f"edge `{site.edge}`" if site.edge else "a finally step"
                return f"{where} references `{ref}`, which would be missing when the agentic half ran"
    agentic_node = node + AGENTIC_SUFFIX
    package = parse_use(use).target + AGENTIC_SUFFIX
    if agentic_node in doc.steps or package in existing_packages:
        return f"name `{agentic_node}` is already taken"
    return None


# --- loops -----------------------------------------------------------------------------------------------------------

def generate_test_revise(b: NodeBuild, cg: CodegenContext, *, max_revisions: int) -> LoopResult:
    """Attempt 1 from `write_*`, then up to `max_revisions` revisions while tests fail ($DRAFTS/05 §8.2)."""
    from wynd.compiler.codegen import revise, write

    total = len(cg.examples)
    tier = cg.tier if cg.kind == "agentic" else None
    generated = write(b.llm, cg)
    runs: list[tuple[Generated, AttemptResult]] = [(generated, _run(b, cg, generated, tier))]
    out = LoopResult(kind=cg.kind, tier=tier, cg=cg, best=generated, best_result=runs[0][1], results=[])
    attempts = max_revisions + 1
    for i in range(2, attempts + 1):
        previous, result = runs[-1]
        if result.all_passed:
            break
        rev = revise(b.llm, cg, previous, result.failures, out.diagnoses, attempt=i, attempts=attempts)
        diagnosis = rev.diagnosis or ""
        _emit(b, "test", f"attempt {i - 1}: {result.handled}/{total} passed; {diagnosis}".rstrip("; "))
        out.diagnoses.append(diagnosis)
        b.records[-1]["diagnosis"] = diagnosis
        if rev.verdict == "examples_inconsistent":
            out.inconsistent, out.suspects = True, list(rev.suspects)
            break
        if cg.kind == "deterministic" and rev.verdict == "needs_judgement" and result.wrong == 0:
            break
        if cg.kind == "agentic" and rev.verdict == "needs_stronger_model":
            out.escalate = True
            break
        runs.append((rev, _run(b, cg, rev, tier)))
    index = max(range(len(runs)), key=lambda k: score(cg.kind, runs[k][1], k))
    out.best, out.best_result = runs[index]
    out.results = [r for _, r in runs]
    return out


def score(kind: str, r: AttemptResult, index: int) -> tuple:
    if kind == "deterministic":
        return (r.all_passed, r.wrong == 0, r.handled, -index)
    return (r.all_passed, r.handled, -index)


def _run(b: NodeBuild, cg: CodegenContext, generated: Generated, tier: str | None) -> AttemptResult:
    b.attempt_no += 1
    result = b.run(cg, generated, b.attempt_no)
    b.records.append({"kind": cg.kind, "package": cg.package, "tier": tier, "n": b.attempt_no,
                      "handled": result.handled, "deferred": result.deferred, "wrong": result.wrong,
                      "duration_ms": result.duration_ms, "diagnosis": None})
    return result


def _shell(b: NodeBuild, *, program: str, reason: str) -> Decision:
    cg = replace(_kind(b.cg, "shell"), program=program, exit_codes=b.exit_codes)
    loop = generate_test_revise(b, cg, max_revisions=b.max_revisions)
    if loop.passed and not loop.inconsistent:
        return _built(b, kind="shell", rule=4, reason=reason, main=loop)
    return _clarify(b, [loop])


def _agentic(b: NodeBuild, *, rule: Literal[2, 3], reason: str, cg: CodegenContext | None = None,
             earlier: list[LoopResult] | None = None) -> Decision:
    """The agentic loop at cheap, then once at standard; the tier that passed is recorded with its reason."""
    base = cg or _kind(b.cg, "agentic")
    cheap = generate_test_revise(b, replace(base, tier="cheap"), max_revisions=b.max_revisions)
    loops = [*(earlier or []), cheap]
    if cheap.passed:
        return _agentic_built(b, rule, reason, cheap)
    if cheap.inconsistent:
        return _clarify(b, loops)
    total = len(base.examples)
    failed = _failed_examples(cheap.best_result)
    standard = generate_test_revise(b, replace(base, tier="standard"), max_revisions=1)
    loops.append(standard)
    if standard.passed:
        why = (f"{reason}; cheap failed examples {', '.join(map(str, failed))} after {len(cheap.results)} "
               f"attempts; standard passed")
        _emit(b, "decision", f"{b.ex.package}: escalated to the standard tier (cheap handled "
                             f"{cheap.best_result.handled}/{total})")
        return _agentic_built(b, rule, why, standard)
    return _clarify(b, loops)


def _agentic_built(b: NodeBuild, rule: Literal[2, 3], reason: str, loop: LoopResult) -> Decision:
    return _built(b, kind="agentic", rule=rule, reason=reason, main=loop, tier=loop.tier)


def _split(b: NodeBuild, det: LoopResult) -> Decision:
    """Rule 3: the deterministic half keeps the node; `<package>_agentic` gets every example."""
    from wynd.compiler.testgen import step_tests

    total = len(b.cg.examples)
    cases = det.best_result.cases
    handled = sorted(n for n, c in cases.items() if c == "handled")
    deferred = sorted(n for n, c in cases.items() if c == "deferred")
    package = b.cg.package + AGENTIC_SUFFIX
    class_name = b.cg.class_name + "Agentic"
    half = replace(_kind(b.cg, "agentic"), package=package, class_name=class_name, deferred=deferred,
                   test_file=step_tests(class_name, b.cg.examples, b.cg.inputs, source=b.cg.source,
                                        proto_hash=b.proto_hash, step_root=b.step_root, free_text=b.free_text))
    why = (f"deterministic code handled {len(handled)} of {total} examples and deferred {len(deferred)}; the "
           f"deferred inputs go to {package} via {b.ex.node}.error")
    agentic = _agentic(b, rule=3, reason=why, cg=half, earlier=[det])
    if agentic.status != "built":
        return agentic

    # the deterministic half's tests: handled examples as given, deferred ones must hand off (exit error)
    det_cg = replace(det.cg, test_file=step_tests(b.cg.class_name, b.cg.examples, b.cg.inputs, source=b.cg.source,
                                                  proto_hash=b.proto_hash, step_root=b.step_root,
                                                  free_text=b.free_text, deferred=deferred, partner=package))
    check = _run(b, det_cg, det.best, None)
    if not check.all_passed:
        return Decision(status="failed", reason=f"the deterministic half of the split of {b.ex.package} failed its "
                                                f"hand-off tests ({check.handled}/{total} passed)",
                        attempts=list(b.records))
    main = replace(det, cg=det_cg, best_result=check)
    _emit(b, "split", f"{b.ex.package} was split in two. Deterministic code handles {len(handled)} of {total} "
                      f"examples and hands the other {len(deferred)} to a new agentic step, {package}, through "
                      f"{b.ex.node}.error.", data={"handled": handled, "deferred": deferred})
    out = _built(b, kind="split", rule=3, reason=why, main=main, tier=agentic.tier)
    out.agentic, out.handled, out.deferred = agentic.main, handled, deferred
    return out


def _built(b: NodeBuild, *, kind: DecisionKind, rule: Literal[1, 2, 3, 4], reason: str, main: LoopResult,
           tier: str | None = None, classification: list | None = None) -> Decision:
    decision = Decision(status="built", reason=reason, kind=kind, rule=rule, tier=tier,
                        thinking=DEFAULT_THINKING if tier else None, main=main, classification=classification or [],
                        attempts=list(b.records))
    tier_text = f" ({tier} tier)" if tier else ""
    _emit(b, "decision", f"{b.ex.package}: {kind} (rule {rule}){tier_text}. {reason}",
          data={"rule": rule, "kind": kind, "tier": tier})
    return decision


def _clarify(b: NodeBuild, loops: list[LoopResult]) -> Decision:
    """The clarification asked when no loop passes, or a revision found the examples inconsistent."""
    text = clarification_text(b.ex.package, loops)
    q = make_question(b.ex, local_id=next_question_id(b.session, b.ex, "clarify"), kind="clarification", text=text,
                      expects="text")
    answer = b.session.ask(q)
    if answer is not None and answer.text.strip():
        # answered already (a pre-answer): compile again right away with it as guidance
        cg = replace(b.cg, guidance=[*b.cg.guidance, answer.text.strip()])
        return decide_and_build(replace(b, cg=cg, attempt_no=b.attempt_no, records=b.records))
    return Decision(status="awaiting", reason=f"{b.ex.package} could not pass its examples; asked {q.id}",
                    attempts=list(b.records))


def clarification_text(package: str, loops: list[LoopResult]) -> str:
    """The deterministic question text of `$DRAFTS/05 §8.2` built from the last diagnosis and the suspects."""
    attempts = sum(len(loop.results) for loop in loops)
    parts = []
    for kind in ("deterministic", "shell", "agentic"):
        of_kind = [loop for loop in loops if loop.kind == kind]
        if not of_kind:
            continue
        total = len(of_kind[0].cg.examples)
        best = max(loop.best_result.handled for loop in of_kind)
        if kind == "agentic":
            tiers = " and ".join(dict.fromkeys(str(loop.tier) for loop in of_kind))
            label = f"agentic at {tiers} tier{'s' if len(of_kind) > 1 else ''}"
        else:
            label = kind
        parts.append(f"{label}: {best}/{total} at best")
    lines = [f"I could not make {package} pass its examples after {attempts} attempt{'s' if attempts != 1 else ''}",
             f"({'; '.join(parts)})."]
    diagnosis = next((d for loop in reversed(loops) for d in reversed(loop.diagnoses) if d), "")
    if diagnosis:
        lines.append(f"What went wrong: {diagnosis}")
    suspects = [s for loop in loops for s in loop.suspects]
    if suspects:
        lines.append("These examples look inconsistent:")
        lines += [f"  - Example {n}: {why}" for n, why in suspects]
    lines.append('Answer with guidance (for example "treat credit notes as not_an_invoice"); it will be used when '
                 "this step")
    lines.append("is compiled again. To change an example itself, edit the proto-step and compile again.")
    return "\n".join(lines)


# --- helpers --------------------------------------------------------------------------------------------------------

def _kind(cg: CodegenContext, kind: str) -> CodegenContext:
    return replace(cg, kind=kind)


def _with(decision: Decision, **fields: Any) -> Decision:
    for key, value in fields.items():
        if not getattr(decision, key):
            setattr(decision, key, value)
    return decision


def _failed_examples(r: AttemptResult) -> list[int]:
    return sorted(n for n, c in r.cases.items() if c != "handled")


def _numbered(b: NodeBuild) -> Any:
    from wynd.compiler.schemas import numbered_examples

    return numbered_examples(b.cg.examples)


def _emit(b: NodeBuild, type: str, text: str, data: dict | None = None) -> None:
    b.session.emit(type, text, process=b.ex.process, step=b.ex.node, data=data)

