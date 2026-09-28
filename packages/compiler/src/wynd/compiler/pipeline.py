"""Closure iteration, the skip rule and the per-node pipeline (`$DRAFTS/05 §6.3, §7.1–§7.2`, PLAN §7).

Children compile first (post-order over the closure), then the target. A node is re-touched only when its proto hash
changed; hand-written packages whose lock carries the current proto hash are never touched.

The pipeline writes only inside `env.checkout` (packages, protos, `process.yaml`, process cassettes) and
`env.scratch`; it never commits: the job handler makes the WIP commit (awaiting input) or the squash commit. A node
that stops at a question, or fails, leaves no trace: its package dirs are restored to HEAD.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from wynd.compiler.codegen import CodegenContext, Generated
    from wynd.compiler.decision import Decision, LoopResult
    from wynd.compiler.examples import ExampleContext
    from wynd.compiler.llm import CompilerLLM
    from wynd.compiler.session import CompileSession, SessionData, SessionState
    from wynd.compiler.split import RemoveSplit, SplitPlan
    from wynd.compiler.tools import ToolCatalog
    from wynd.process.loader import LoadedProcess, ResolvedStep
    from wynd.process.testing import SuiteResult
    from wynd.runtime.mcp.entry import McpServerEntry
    from wynd.runtime.mcp.snapshot import McpToolSpec
    from wynd.runtime.storage.base import Registry
    from wynd.runtime.usage import Usage
    from wynd.spec.process_doc import ProcessDoc
    from wynd.spec.proto_step import ProtoStep

AGENTIC_SUFFIX = "_agentic"


@dataclass
class CompileDeps:
    """Every boundary of the pipeline, injected so tests can fake it (PLAN §7 item 6)."""
    llm: CompilerLLM
    registry: Registry
    step_python: Callable[[Sequence[str]], Path]              # wynd.process.venvs.step_python
    lock_requirements: Callable[[Sequence[str]], list[str]]   # UvResolver().compile(..., universal=True, python_version="3.12") minus wynd-spec/runtime
    run_step_suite: Callable[..., SuiteResult]                # wynd.process.testing.run_step_suite
    run_process_examples: Callable[..., SuiteResult]          # wynd.process.testing.run_process_examples
    describe: Callable[[Path, Path, str], dict]               # (python, pkg_dir, entrypoint) -> `python -m wynd.runtime.describe` JSON
    list_mcp_tools: Callable[[McpServerEntry], list[McpToolSpec]]   # wynd.runtime.mcp.list_tools
    now: Callable[[], datetime]


@dataclass
class CompileEnv:
    """Everything `CompileSession.next` needs besides the session data."""
    checkout: Path                               # workspace root inside the job checkout (ctx.workspace)
    worktree: Path                               # git top level (ctx.worktree)
    scratch: Path                                # ctx.scratch / "compile" (outside the checkout)
    deps: CompileDeps
    checkpoint: Callable[[SessionData], None]    # -> ctx.save_session
    commit: Callable[[str], str | None]          # -> ctx.commit(message, None)
    log: Callable[[str], None]


@dataclass
class NodeOutcome:
    status: Literal["compiled", "skipped", "awaiting", "failed"]
    reason: str
    split: SplitPlan | RemoveSplit | None = None     # process.yaml change to apply


@dataclass
class Constraints:
    """What the process graph fixes about a node's interface ($DRAFTS/05 §7.2.1)."""
    input_names: list[str]                       # keys of `with:` on every branch targeting this node
    input_types: dict[str, str]                  # entry node: process.inputs types (names must match)
    output_fields: dict[str, list[str]]          # exit -> fields used as steps.<node>.outputs.<f> in edges from <node>.<exit>
    downstream_fields: list[str]                 # fields referenced by other edges (exit unknown)
    whole_output_targets: list[str]              # "validate.fields" for `fields: steps.extract.outputs`


@dataclass
class _Pass:
    """Per-pass state shared by the nodes of one `compile_closure` run."""
    catalog: ToolCatalog
    root_provider: str


# --- the closure ----------------------------------------------------------------------------------------------------

def compile_closure(session: CompileSession, env: CompileEnv) -> SessionState:
    from wynd.compiler.session import SessionState
    from wynd.process.workspace import load_workspace

    data = session.data
    report = data.report
    ws = load_workspace(env.checkout)
    lp = ws.load_process(data.process)
    if not _validation_ok(session, lp, stage="before compiling"):
        return SessionState.FAILED

    order = _post_order(lp)
    nodes = [(p, rs) for p in order for rs in p.steps.values() if rs.ref_kind != "process"]
    changed = sum(1 for _, rs in nodes if rs.package is not None and rs.package.proto is not None
                  and (rs.package.lock is None or rs.package.lock.proto_hash != rs.package.proto_hash))
    session.emit("note", f"Compiling {data.process} at {data.base_commit[:7]}: {len(nodes)} steps, {changed} with "
                         f"changed proto-steps.", process=data.process, step=None)
    state = _pass_state(env, lp)
    report.warnings += [w for w in state.catalog.warnings if w not in report.warnings]

    awaiting = failed = False
    affected: set[str] = set()
    restore: list[Path] = []
    for p in order:
        for rs in p.steps.values():
            if rs.ref_kind == "process":
                _row(session, p, rs, phase="done", decision={"kind": "process", "rule": None, "tier": None,
                                                              "thinking": None, "reason": "compiled as its own "
                                                              f"process ({rs.child})"})
                continue
            if _is_agentic_half(rs):
                continue                                  # handled with its deterministic partner
            out = compile_node(session, env, p, rs)
            match out.status:
                case "awaiting":
                    awaiting = True
                    restore += _node_dirs(env, rs)
                case "failed":
                    failed = True
                    restore += _node_dirs(env, rs)
                case "compiled":
                    affected.add(p.id)
                    if out.split is not None and not _apply_process_change(session, env, p, rs, out.split):
                        failed = True
            env.checkpoint(session.data)
        if not awaiting and _sync_edge_lock(session, env, p):
            affected.add(p.id)                            # its (and its parents') examples record the checks

    if restore:
        _restore(env, restore)
    if awaiting:
        return SessionState.AWAITING_INPUT
    if failed:
        return SessionState.FAILED
    if affected and not _process_phase(session, env, lp, affected):
        return SessionState.FAILED
    fresh = load_workspace(env.checkout).load_process(data.process)
    if not _validation_ok(session, fresh, stage="after compiling"):
        return SessionState.FAILED
    return SessionState.DONE


def _post_order(lp: LoadedProcess) -> list[LoadedProcess]:
    """Children before parents (a parent's hash includes its children's), each process once."""
    out: list[LoadedProcess] = []

    def visit(p: LoadedProcess) -> None:
        for child in p.children.values():
            if all(c.id != child.id for c in out):
                visit(child)
        if all(c.id != p.id for c in out):
            out.append(p)

    visit(lp)
    return out


def _pass_state(env: CompileEnv, root: LoadedProcess) -> _Pass:
    """The per-pass state kept on `env`: the tool catalog (discovered once per pass) and the root's provider."""
    state = env.__dict__.get("_wynd_pass")
    if state is None:
        from wynd.compiler.tools import build_catalog

        state = _Pass(catalog=build_catalog(env.deps.registry, env.deps.list_mcp_tools),
                      root_provider=root.doc.effective_provider)
        env.__dict__["_wynd_pass"] = state
    return state


def _validation_ok(session: CompileSession, lp: LoadedProcess, *, stage: str) -> bool:
    from wynd.process.validation import validate

    result = validate(lp)
    errors = [d.format() for d in result.diagnostics if d.severity == "error"]
    warnings = [d.format() for d in result.diagnostics if d.severity == "warning"]
    session.data.report.validation = {"errors": errors, "warnings": warnings}
    if not errors:
        return True
    message = f"process validation failed {stage}: {len(errors)} error{'s' if len(errors) != 1 else ''}"
    session.data.report.error = message
    session.emit("error", message, process=lp.id, step=None, data={"errors": errors})
    return False


def _is_agentic_half(rs: ResolvedStep) -> bool:
    from wynd.compiler.lockfile import compiled_info

    lock = rs.package.lock if rs.package is not None else None
    info = compiled_info(lock) if lock is not None and lock.compiled else None
    return info is not None and info.split is not None and info.split.role == "agentic"


def _node_dirs(env: CompileEnv, rs: ResolvedStep) -> list[Path]:
    if rs.package is None:
        return []
    pkg = env.checkout / rs.package.dir
    return [p for p in (pkg, pkg.parent / (pkg.name + AGENTIC_SUFFIX)) if p.exists()]


def _restore(env: CompileEnv, paths: list[Path]) -> None:
    from wynd.compiler.gitops import restore_paths

    restore_paths(env.worktree, sorted(set(paths)))


def _apply_process_change(session: CompileSession, env: CompileEnv, lp: LoadedProcess, rs: ResolvedStep,
                          change: SplitPlan | RemoveSplit) -> bool:
    """Rewrite `process.yaml` for a split (or its removal); False when the rewrite could not be verified."""
    from wynd.compiler.split import SplitPlan, apply_split, has_split_markers, remove_split
    from wynd.spec.process_doc import ProcessDoc
    from wynd.spec.workspace import PROCESS_FILE
    from wynd.spec.yamlio import parse_model

    path = env.checkout / lp.dir / PROCESS_FILE
    text = path.read_text(encoding="utf-8")
    doc = parse_model(text, ProcessDoc)
    report = session.data.report
    if isinstance(change, SplitPlan):
        try:
            new = apply_split(text, doc, change)
        except ValueError as err:
            message = f"could not add the split of {change.node} to process.yaml: {err}"
            report.step_entry(lp.id, rs.name).warnings.append(message)
            session.emit("error", message, process=lp.id, step=rs.name)
            return False
        path.write_text(new, encoding="utf-8")
        added = parse_model(new, ProcessDoc)
        edges = [e.key for e in added.edges if e.key == f"{change.node}.error"
                 or e.source_step == change.agentic_node]
        entry = report.step_entry(lp.id, rs.name)
        split = (entry.decision or {}).get("split") or {}
        report.process_changes.append({"type": "split", "process": lp.id, "node": change.node,
                                       "added_node": change.agentic_node, "edges_added": edges,
                                       "handled": split.get("handled", []), "deferred": split.get("deferred", [])})
        return True
    markers = has_split_markers(text, change.node)
    new = remove_split(text, doc, change.node)
    if new != text:
        path.write_text(new, encoding="utf-8")
        report.process_changes.append({"type": "unsplit", "process": lp.id, "node": change.node,
                                       "removed_node": change.node + AGENTIC_SUFFIX})
        if not markers:
            report.warnings.append(f"process.yaml of {lp.id} was re-written to remove the split of {change.node} "
                                   f"(comments lost)")
    return True


def _sync_edge_lock(session: CompileSession, env: CompileEnv, lp: LoadedProcess) -> bool:
    """M5 hook: refresh `edges.lock.yaml` for the process's agentic branches (written only when changed); -> whether
    it changed, so the process-level record phase re-records the process's examples with the new checks (PLAN §7
    item 7) even when every step is skipped."""
    from wynd.process.edges_lock import sync_edge_lock
    from wynd.spec.lockfiles import dump_lock
    from wynd.spec.process_doc import ProcessDoc
    from wynd.spec.workspace import EDGES_LOCK_FILE, PROCESS_FILE
    from wynd.spec.yamlio import parse_model

    process_dir = env.checkout / lp.dir
    doc = parse_model((process_dir / PROCESS_FILE).read_text(encoding="utf-8"), ProcessDoc)
    lock, changed = sync_edge_lock(process_dir, doc)
    if changed:
        (process_dir / EDGES_LOCK_FILE).write_text(dump_lock(lock), encoding="utf-8")
        session.data.report.process_changes.append({"type": "edges_lock", "process": lp.id,
                                                    "branches": list(lock.edges)})
    return changed


def _process_phase(session: CompileSession, env: CompileEnv, lp: LoadedProcess, affected: set[str]) -> bool:
    """Process examples of every affected closure process (a parent is affected by its children): record into a
    staging dir, promote into `<process dir>/cassettes/`, then verify in replay. A failing example fails the job."""
    import os

    from wynd.process.workspace import load_workspace
    from wynd.runtime.cassettes import promote
    from wynd.spec.workspace import CASSETTES_DIR, slug

    report = session.data.report
    ws = load_workspace(env.checkout)
    venv_root = env.deps.step_python([]).parents[2]
    passed = failed = 0
    cases: list[dict[str, Any]] = []
    for p in _pre_order(ws.load_process(lp.id)):
        if not (_descendants(p) & affected) or not p.doc.examples:
            continue
        process_dir = env.checkout / p.dir
        scratch = env.scratch / "process" / slug(p.id)
        shutil.rmtree(scratch, ignore_errors=True)
        staging = scratch / "record"
        events = scratch / "events.jsonl"                     # metered as recording usage (SPEC §15)
        record_env = {**os.environ, "WYND_EVENTS_FILE": str(events)}
        suite = env.deps.run_process_examples(ws, p.id, mode="record", env=record_env, scratch=scratch / "run",
                                              venv_root=venv_root, record_root=staging, log=env.log)
        report.usage.recording = report.usage.recording + _file_usage(events)
        if suite.passed:
            promote(staging, process_dir / CASSETTES_DIR)
            suite = env.deps.run_process_examples(load_workspace(env.checkout), p.id, mode="replay",
                                                  env=dict(os.environ), scratch=scratch / "replay",
                                                  venv_root=venv_root, log=env.log)
        passed += suite.counts.get("passed", 0)
        failed += len(suite.cases) - suite.counts.get("passed", 0)
        cases += [{"process": p.id, "name": c.name, "outcome": c.outcome, "message": c.message}
                  for c in suite.cases if c.outcome != "passed"]
        if suite.problem:
            cases.append({"process": p.id, "name": "(suite)", "outcome": "error", "message": suite.problem})
        text = f"process examples of {p.id}: {suite.counts.get('passed', 0)}/{len(suite.cases)} passed"
        session.emit("test", text, process=p.id, step=None)
    report.integration_tests = {"passed": passed, "failed": failed, "cases": cases}
    if failed or any(c["name"] == "(suite)" for c in cases):
        report.error = f"{failed or 'some'} process example(s) failed; see integration_tests"
        session.emit("error", report.error, process=lp.id, step=None)
        return False
    return True


def _pre_order(lp: LoadedProcess) -> list[LoadedProcess]:
    return list(lp.closure_processes().values())


def _descendants(lp: LoadedProcess) -> set[str]:
    return set(lp.closure_processes())


# --- one node -------------------------------------------------------------------------------------------------------

def compile_node(session: CompileSession, env: CompileEnv, lp: LoadedProcess, rs: ResolvedStep) -> NodeOutcome:
    """The skip rule, then schema, example checks and proposals, the decision, and the package files
    ($DRAFTS/05 §7.2)."""
    from wynd.compiler.llm import CompilerLLMError
    from wynd.compiler.lockfile import compiled_info

    data = session.data
    node = rs.name
    pkg = rs.package
    entry = data.report.step_entry(lp.id, node)
    if pkg is None:
        return _finish(session, lp, rs, NodeOutcome("failed", f"`{rs.use}` does not resolve to a step package"))
    entry.step, entry.package = Path(pkg.dir).name, pkg.dir
    if pkg.proto is None:
        return _finish(session, lp, rs, NodeOutcome("skipped", "no proto-step (hand-written step)"))
    lock = pkg.lock
    if lock is not None and lock.proto_hash == pkg.proto_hash:
        return _finish(session, lp, rs, NodeOutcome("skipped", "proto-step unchanged"))
    reason = "not compiled yet" if lock is None else "proto-step changed"
    info = compiled_info(lock) if lock is not None and lock.compiled else None
    _row(session, lp, rs, phase="generating")
    try:
        out = _compile(session, env, lp, rs, reason, info)
    except CompilerLLMError as err:
        out = NodeOutcome("failed", str(err))
    return _finish(session, lp, rs, out)


def _compile(session: CompileSession, env: CompileEnv, lp: LoadedProcess, rs: ResolvedStep, reason: str,
             info: Any) -> NodeOutcome:
    from wynd.compiler.codegen import CodegenContext
    from wynd.compiler.decision import NodeBuild, decide_and_build, split_applicable
    from wynd.compiler.examples import (
        ExampleContext,
        answered_examples,
        answered_guidance,
        append_examples,
        ask_for_examples,
        canonical,
        example_problems,
        fixture_files,
        propose_and_confirm,
    )
    from wynd.compiler.schemas import pascal, plain_interface
    from wynd.compiler.split import remove_split
    from wynd.compiler.testgen import step_tests
    from wynd.spec.hashing import proto_hash
    from wynd.spec.process_doc import ProcessDoc
    from wynd.spec.proto_step import ProtoStep
    from wynd.spec.workspace import PROCESS_FILE, STEP_PROTO_FILE
    from wynd.spec.yamlio import parse_model, parse_yaml

    data = session.data
    state = _pass_state(env, lp)
    pkg = rs.package
    proto = pkg.proto
    node = rs.name
    package = Path(pkg.dir).name
    pkg_dir = env.checkout / pkg.dir
    process_dir = env.checkout / lp.dir
    step_root = rs.ref_kind == "root"
    base_dir = pkg_dir if step_root else process_dir
    source = STEP_PROTO_FILE if step_root else pkg.proto_path.removeprefix(lp.dir + "/")
    proto_path = env.checkout / pkg.proto_path
    proto_text = proto_path.read_text(encoding="utf-8")
    raw, _ = parse_yaml(proto_text)
    doc = parse_model((process_dir / PROCESS_FILE).read_text(encoding="utf-8"), ProcessDoc)
    entry = data.report.step_entry(lp.id, node)
    entry.reason = reason

    ex = ExampleContext(process=lp.id, node=node, package=package,
                        prefix="" if lp.id == data.process else f"{lp.id}:", instruction=proto.instruction,
                        inputs=dict(proto.inputs), outputs={x: dict(f) for x, f in proto.outputs.items()},
                        base_dir=base_dir, job=data.jobs[-1] if data.jobs else data.id,
                        now=lambda: env.deps.now().strftime("%Y-%m-%dT%H:%M:%SZ"))
    guidance = list(dict.fromkeys([*answered_guidance(session, ex), *(info.guidance if info else [])]))

    # 1. schema: presented, never asked
    descriptions: dict[str, str] = {}
    free_text: list[str] = []
    inferred = not ("inputs" in raw and "outputs" in raw)
    if inferred:
        from wynd.compiler.schemas import infer_interface

        schema = infer_interface(
            env.deps.llm, node=ex.llm_node, name=package, instruction=proto.instruction, exits=proto.exits,
            examples=proto.examples, declared_inputs=dict(proto.inputs) if "inputs" in raw else None,
            declared_outputs=ex.outputs if "outputs" in raw else None, constraints=derive_constraints(doc, node),
            previous=pkg.lock.interface if pkg.lock is not None else None, guidance=guidance)
        if schema.problems:
            return _ask_or_fail(session, ex, schema.problems, "the examples do not fit one schema")
        ex.inputs, ex.outputs = schema.inputs, schema.outputs
        descriptions, free_text = schema.descriptions, schema.free_text
    plain = plain_interface(package, ex.inputs, ex.outputs, descriptions=descriptions, free_text=free_text)
    if inferred:
        from wynd.spec.interface import interface_from_fields

        data.inferred_schemas[ex.llm_node] = {
            "interface": interface_from_fields(ex.inputs, ex.outputs).model_dump(mode="json"), "plain": plain}
        session.emit("note", "\n".join(["Inferred from the examples:", *plain]), process=lp.id, step=node)
    entry.schema_ = {"inferred": inferred, "plain": "\n".join(plain)}

    # 2. example checks and edge-case proposals
    examples = [canonical(e, ex) for e in proto.examples]
    problems = example_problems(examples, ex)
    if problems:
        return _ask_or_fail(session, ex, problems, "the proto-step's examples do not fit together")
    given, given_problems = answered_examples(session, ex)
    rejected_before = list(info.rejected) if info else []
    proposals = propose_and_confirm(session, ex, llm=env.deps.llm, examples=[*examples, *given],
                                    rejected=rejected_before, process_context=_process_context(lp, doc, node),
                                    guidance=guidance, max_proposals=data.options.max_proposals)
    entry.warnings += [w for w in proposals.warnings if w not in entry.warnings]
    entry.examples = {"original": len(examples), "proposed": proposals.proposed, "confirmed": proposals.confirmed,
                      "corrected": proposals.corrected, "rejected": len(proposals.rejected)}
    if proposals.pending:
        return NodeOutcome("awaiting", "waiting for answers about proposed examples")
    added = [*given, *proposals.added]
    examples = [*examples, *added]
    if not examples:
        if ask_for_examples(session, ex, given_problems) is None:
            return NodeOutcome("awaiting", "the step has no examples")
        return NodeOutcome("failed", "the step has no usable examples")
    new_text, reformatted = append_examples(proto_text, proto, added, session_id=data.id)
    if reformatted:
        entry.warnings.append("proto-step YAML was reformatted (comments lost)")
    new_proto = parse_model(new_text, ProtoStep)
    new_hash = proto_hash(new_proto)

    # 3. decide, generate, test, revise
    class_name = pascal(package)
    module = pkg_dir / f"{package}.py"
    cg = CodegenContext(
        kind="deterministic", node=ex.llm_node, package=package, class_name=class_name, source=source,
        instruction=proto.instruction, inputs=ex.inputs, outputs=ex.outputs, plain=plain,
        test_file=step_tests(class_name, examples, ex.inputs, source=source, proto_hash=new_hash,
                             step_root=step_root, free_text=free_text),
        examples=examples, catalog=state.catalog, fixture_files=fixture_files(base_dir),
        previous_source=module.read_text(encoding="utf-8") if module.is_file() else None, guidance=guidance,
        goal=doc.goal, upstream=_upstream(lp, doc, node), exit_codes=proto.exit_codes,
    )
    old_partner = info.split.partner if info and info.split else None
    unsplit_doc = parse_model(remove_split((process_dir / PROCESS_FILE).read_text(encoding="utf-8"), doc, node),
                              ProcessDoc)
    siblings = {d.name for d in pkg_dir.parent.iterdir() if d.is_dir()} if pkg_dir.parent.is_dir() else set()
    existing = siblings - {old_partner}
    blocker = split_applicable(unsplit_doc, node, ref_kind=rs.ref_kind, existing_packages=existing)
    runner = _Runner(session, env, lp, rs, new_proto, state)
    build = NodeBuild(
        session=session, ex=ex, cg=cg, llm=env.deps.llm, run=runner, exit_codes=proto.exit_codes,
        split_blocker=blocker, process_context=_process_context(lp, doc, node),
        available_tools=_available_tools(state.catalog), max_revisions=data.options.max_revisions,
        proto_hash=new_hash, step_root=step_root, free_text=free_text,
    )
    _row(session, lp, rs, phase="testing")
    decision = decide_and_build(build)
    entry.attempts = decision.attempts
    if decision.status == "awaiting":
        return NodeOutcome("awaiting", decision.reason)
    if decision.status == "failed":
        return NodeOutcome("failed", decision.reason)

    # 4. finalize: cassettes, locks, files
    rejected = list(dict.fromkeys([*rejected_before, *proposals.rejected]))
    final = _Final(session=session, env=env, lp=lp, rs=rs, proto=new_proto, proto_hash=new_hash,
                   decision=decision, runner=runner, inferred=inferred, free_text=free_text, guidance=guidance,
                   rejected=rejected)
    problem = final.write()
    if problem is not None:
        return NodeOutcome("failed", problem)
    if added:
        proto_path.write_text(new_text, encoding="utf-8")
        data.report.proto_changes.append({"proto": pkg.proto_path, "examples_added": len(added)})
    entry.decision = _report_decision(decision, package)
    if decision.kind == "split":
        from wynd.compiler.split import SplitPlan

        return NodeOutcome("compiled", reason, split=SplitPlan(
            node=node, agentic_node=node + AGENTIC_SUFFIX, agentic_use=f"./steps/{package}{AGENTIC_SUFFIX}",
            input_fields=list(ex.inputs)))
    if old_partner is not None:
        from wynd.compiler.split import RemoveSplit

        shutil.rmtree(pkg_dir.parent / old_partner, ignore_errors=True)
        return NodeOutcome("compiled", reason, split=RemoveSplit(node))
    return NodeOutcome("compiled", reason)


def _ask_or_fail(session: CompileSession, ex: ExampleContext, problems: Sequence[str], what: str) -> NodeOutcome:
    from wynd.compiler.examples import ask_problems

    if ask_problems(session, ex, problems) is None:
        return NodeOutcome("awaiting", f"{what}; asked the owner")
    return NodeOutcome("failed", f"{what}: {'; '.join(problems)}")


# --- attempts -------------------------------------------------------------------------------------------------------

class _Runner:
    """`NodeBuild.run`: one attempt of a codegen context in its package dir (the node's package, or the split's
    `<package>_agentic` sibling)."""

    def __init__(self, session: CompileSession, env: CompileEnv, lp: LoadedProcess, rs: ResolvedStep,
                 proto: ProtoStep, state: _Pass):
        self.session, self.env, self.lp, self.rs, self.proto, self.state = session, env, lp, rs, proto, state
        self.pkg_dir = env.checkout / rs.package.dir

    def dir_of(self, cg: CodegenContext) -> Path:
        return self.pkg_dir.parent / cg.package

    def requirements(self, cg: CodegenContext, generated: Generated) -> list[str]:
        from wynd.compiler.lockfile import merge_deps
        from wynd.runtime.providers import provider_info

        deps = merge_deps(self.proto.env.deps, generated.deps)
        if cg.kind == "agentic":
            deps += [d for d in provider_info(self.state.root_provider).env_fragment.deps if d not in deps]
        return deps

    def __call__(self, cg: CodegenContext, generated: Generated, n: int,
                 mode: Literal["replay", "record"] | None = None) -> Any:
        from wynd.compiler.attempts import run_attempt
        from wynd.compiler.codegen import attempt_files
        from wynd.spec.interface import interface_from_fields
        from wynd.spec.workspace import STEP_LOCK_FILE, slug

        files = attempt_files(cg, generated)
        if cg.kind == "agentic":
            lock = self.provisional_lock(cg, generated)
            if lock is not None:
                files[STEP_LOCK_FILE] = lock
        return run_attempt(
            self.env, self.dir_of(cg), files, kind=cg.kind, entrypoint=f"{cg.package}:{cg.class_name}",
            requirements=self.requirements(cg, generated), expected=interface_from_fields(cg.inputs, cg.outputs),
            mode=mode or ("record" if cg.kind == "agentic" else "replay"), n=n, catalog=self.state.catalog,
            upstream_nodes=list(_upstream(self.lp, self.lp.doc, self.rs.name)),
            effects=None if cg.kind == "agentic" else generated.effects, provider=self.state.root_provider,
            memo=self.session.data.memo.tests, node=f"{slug(self.lp.id)}-{self.rs.name}",
        )

    def provisional_lock(self, cg: CodegenContext, generated: Generated) -> str | None:
        """The attempt's lock with the loop's tier (the runtime reads the tier from it; cassette keys include the
        resolved model)."""
        from wynd.compiler.astcheck import check
        from wynd.compiler.lockfile import build_step_lock, render_step_lock

        static = check(generated.source, class_name=cg.class_name, base="AgenticStep", catalog=self.state.catalog,
                       upstream_nodes=None)
        if static.errors:
            return None
        try:
            lock = build_step_lock(name=cg.package, kind="agentic", entrypoint=f"{cg.package}:{cg.class_name}",
                                   step_id=cg.package, proto=None, proto_hash=None, interface=None, static=static,
                                   catalog=self.state.catalog, tier=cg.tier)
        except ValueError:
            return None
        return render_step_lock(lock)


@dataclass
class _Final:
    """Writes the accepted packages: cassettes (agentic), module, tests, pyproject, lock."""
    session: CompileSession
    env: CompileEnv
    lp: LoadedProcess
    rs: ResolvedStep
    proto: ProtoStep
    proto_hash: str
    decision: Decision
    runner: _Runner
    inferred: bool
    free_text: list[str]
    guidance: list[str]
    rejected: list[str]
    n: int = 0                                     # attempt numbers continue after the decision's

    def __post_init__(self) -> None:
        self.n = len(self.decision.attempts)

    def write(self) -> str | None:
        """None when every package was written, else why the node fails."""
        d = self.decision
        split = d.kind == "split"
        main_kind = "deterministic" if split else d.kind
        problem = self.package(d.main, main_kind, split_role="deterministic" if split else None)
        if problem is None and split:
            problem = self.package(d.agentic, "agentic", split_role="agentic")
        return problem

    def package(self, loop: LoopResult, kind: str, *, split_role: str | None) -> str | None:
        from wynd.compiler.astcheck import check
        from wynd.compiler.codegen import BASES
        from wynd.compiler.lockfile import (
            CompiledInfo,
            CompiledSplit,
            build_step_lock,
            locked_deps,
            merge_deps,
            project_name,
            render_pyproject,
            render_step_lock,
        )
        from wynd.spec.interface import Interface
        from wynd.spec.workspace import CASSETTES_DIR, STEP_LOCK_FILE, local_step_id, parse_use

        cg, generated, result = loop.cg, loop.best, loop.best_result
        pkg_dir = self.runner.dir_of(cg)
        entrypoint = f"{cg.package}:{cg.class_name}"
        if kind == "agentic":
            accepted = self.accept_agentic(loop)
            if isinstance(accepted, str):
                return accepted
            generated, result = accepted
        elif (pkg_dir / CASSETTES_DIR).exists():
            shutil.rmtree(pkg_dir / CASSETTES_DIR)          # the step no longer records anything

        static = check(generated.source, class_name=cg.class_name, base=BASES[kind], catalog=self.runner.state.catalog,
                       upstream_nodes=None)
        deps = merge_deps(self.proto.env.deps, generated.deps)
        locked = locked_deps(deps, self.env.deps.lock_requirements)
        interface = Interface.model_validate(result.description["interface"]) if result.description else None
        d = self.decision
        split = None
        if split_role is not None:
            base = Path(self.rs.package.dir).name
            partner = base + AGENTIC_SUFFIX if split_role == "deterministic" else base
            node = self.rs.name if split_role == "deterministic" else self.rs.name + AGENTIC_SUFFIX
            partner_node = self.rs.name + AGENTIC_SUFFIX if split_role == "deterministic" else self.rs.name
            split = CompiledSplit(role=split_role, node=node, partner=partner, partner_node=partner_node,
                                  handled=d.handled if split_role == "deterministic" else [],
                                  deferred=d.deferred if split_role == "deterministic" else [])
        data = self.session.data
        compiled = CompiledInfo(
            session=data.id, job=data.jobs[-1] if data.jobs else data.id, compiler=f"wynd-compiler {_version()}",
            model=f"{_compiler_provider()}/strong", decision=d.record(), inferred_interface=self.inferred,
            free_text=self.free_text, guidance=self.guidance, rejected=self.rejected, split=split,
        )
        step_root = self.rs.ref_kind == "root"
        owner = parse_use(self.rs.use).alias if step_root else self.lp.id
        step_id = self.rs.package.id if cg.package == Path(self.rs.package.dir).name \
            else local_step_id(self.lp.id, cg.package)
        lock = build_step_lock(
            name=cg.package, kind=kind, entrypoint=entrypoint, step_id=step_id, proto=self.proto,
            proto_hash=self.proto_hash, interface=interface, static=static, catalog=self.runner.state.catalog,
            deps=generated.deps, system_packages=generated.system_packages, requires_glibc=generated.requires_glibc,
            effects=generated.effects, env_vars=generated.env_vars, tier=loop.tier if kind == "agentic" else None,
            locked=locked, compiled=compiled,
        )
        pkg_dir.mkdir(parents=True, exist_ok=True)
        (pkg_dir / f"{cg.package}.py").write_text(generated.source, encoding="utf-8")
        (pkg_dir / f"test_{cg.package}.py").write_text(cg.test_file, encoding="utf-8")
        (pkg_dir / "pyproject.toml").write_text(render_pyproject(project_name(owner, cg.package), lock.fragment.deps),
                                               encoding="utf-8")
        (pkg_dir / STEP_LOCK_FILE).write_text(render_step_lock(lock), encoding="utf-8")
        self.report_package(cg, kind, lock, result)
        return None

    def accept_agentic(self, loop: LoopResult) -> tuple[Generated, Any] | str:
        """Promote the accepted attempt's recordings, narrow MCP `allow` to what the tests used, then verify the
        cassettes in replay ($DRAFTS/05 §7.7, §9.4)."""
        from wynd.compiler.attempts import promote_cassettes

        cg, generated, result = loop.cg, loop.best, loop.best_result
        narrowed = self.narrow(loop)
        if narrowed is not None:
            generated, result = narrowed
        pkg_dir = self.runner.dir_of(cg)
        promote_cassettes(result, pkg_dir)
        check = self.runner(cg, generated, self.next_n(), mode="replay")
        if not check.all_passed:
            return (f"the recorded cassettes of {cg.package} do not replay (a runtime problem, not the examples): "
                    f"{check.handled}/{len(cg.examples)} passed")
        return generated, check

    def narrow(self, loop: LoopResult) -> tuple[Generated, Any] | None:
        """Re-render with the MCP tools the record run actually called and re-record once; None to keep as is."""
        from dataclasses import replace

        from wynd.compiler.codegen import render_agentic
        from wynd.compiler.tools import used_tools

        generated, result = loop.best, loop.best_result
        mcp = (generated.capabilities or {}).get("mcp") or []
        if not mcp or result.events_file is None:
            return None
        used = used_tools(result.events_file)
        allow = [(m["server"], [t for t in m["allow"] if (m["server"], t) in used]) for m in mcp]
        allow = [(server, tools) for server, tools in allow if tools]
        if allow == [(m["server"], list(m["allow"])) for m in mcp]:
            return None
        caps = dict(generated.capabilities)
        caps["mcp"] = [{"server": s, "allow": a} for s, a in allow]
        cg = loop.cg
        source = render_agentic(class_name=cg.class_name, source=cg.source, docstring=caps["docstring"],
                                inputs=cg.inputs, outputs=cg.outputs, context=caps["context"], tools=caps["tools"],
                                mcp=allow, tool_methods=caps["tool_methods"])
        candidate = replace(generated, source=source, capabilities=caps)
        rerun = self.runner(cg, candidate, self.next_n())
        entry = self.session.data.report.step_entry(self.lp.id, self.rs.name)
        if rerun.all_passed:
            entry.warnings.append(f"narrowed the MCP allow lists of {cg.package} to the tools its tests used")
            return candidate, rerun
        entry.warnings.append(f"kept the original MCP allow lists of {cg.package}: the narrowed lists failed")
        self.runner(cg, generated, self.next_n())               # restore the accepted attempt's files
        return None

    def next_n(self) -> int:
        self.n += 1
        return self.n

    def report_package(self, cg: CodegenContext, kind: str, lock: Any, result: Any) -> None:
        entry = self.session.data.report.step_entry(self.lp.id, self.rs.name)
        passed = result.handled if result is not None else 0
        tests = entry.tests or {"passed": 0, "failed": 0}
        entry.tests = {"passed": tests["passed"] + passed,
                       "failed": tests["failed"] + (len(cg.examples) - passed)}
        entry.tools = sorted({*entry.tools, *(t.name for t in lock.tools)})
        entry.mcp = [*entry.mcp, *({"server": m.server, "allow": list(m.allow)} for m in lock.mcp)]
        entry.effects = sorted({*entry.effects, *lock.effects})
        entry.env = sorted({*entry.env, *(v.name for v in lock.fragment.vars)})
        deps = entry.deps or {"declared": [], "locked": []}
        entry.deps = {"declared": list(dict.fromkeys([*deps["declared"], *lock.fragment.deps])),
                      "locked": list(dict.fromkeys([*deps["locked"], *lock.locked_deps]))}
        _events_usage(self.session, self.lp.id, self.rs.name, result)


def _events_usage(session: CompileSession, pid: str, node: str, result: Any) -> None:
    """The step's own model calls while its tests recorded (`model.call` events of `WYND_EVENTS_FILE`)."""
    path = getattr(result, "events_file", None)
    if path is None or not Path(path).is_file():
        return
    total = _file_usage(Path(path))
    report = session.data.report
    entry = report.step_entry(pid, node)
    entry.usage.recording = entry.usage.recording + total
    report.usage.recording = report.usage.recording + total


def _file_usage(path: Path) -> Usage:
    """The usage of the non-replayed `model.call` events of a `WYND_EVENTS_FILE` (zero when there is none)."""
    import json

    from wynd.runtime.usage import Usage

    total = Usage()
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "model.call" and event.get("cassette") != "replay" and event.get("usage"):
            total = total + Usage.model_validate({**event["usage"], "calls": 1})
    return total


# --- report and session rows ----------------------------------------------------------------------------------------

def _finish(session: CompileSession, lp: LoadedProcess, rs: ResolvedStep, out: NodeOutcome) -> NodeOutcome:
    entry = session.data.report.step_entry(lp.id, rs.name)
    entry.action = out.status
    entry.reason = out.reason
    phase = {"compiled": "done", "skipped": "skipped", "awaiting": "awaiting_input", "failed": "failed"}[out.status]
    decision = None
    if out.status == "compiled" and entry.decision:
        d = entry.decision
        split = d.get("split")
        decision = {"kind": "deterministic" if d["kind"] == "split" else d["kind"], "rule": d.get("rule"),
                    "tier": d.get("tier"), "thinking": "low" if d.get("tier") else None,
                    "reason": d.get("why") or "", "split": split}
    tests = entry.tests
    counts = {"passed": tests["passed"], "failed": tests["failed"], "total": tests["passed"] + tests["failed"]} \
        if tests and out.status == "compiled" else None
    _row(session, lp, rs, phase=phase, decision=decision, tests=counts, attempts=len(entry.attempts),
         skipped_reason=out.reason if out.status == "skipped" else None)
    if out.status == "failed":
        session.emit("error", f"{entry.step or rs.name}: {out.reason}", process=lp.id, step=rs.name)
    elif out.status == "skipped":
        session.emit("note", f"{entry.step or rs.name}: skipped ({out.reason})", process=lp.id, step=rs.name)
    return out


def _row(session: CompileSession, lp: LoadedProcess, rs: ResolvedStep, *, phase: str,
         decision: dict | None = None, tests: dict | None = None, attempts: int | None = None,
         skipped_reason: str | None = None) -> None:
    from wynd.compiler.session import CompileStep, StepDecision, TestCounts

    package = Path(rs.package.dir).name if rs.package is not None else None
    steps = session.data.steps
    row = next((s for s in steps if s.process == lp.id and s.step == rs.name), None)
    if row is None:
        row = CompileStep(process=lp.id, step=rs.name, use=rs.use, package=package, phase=phase)
        steps.append(row)
    row.phase = phase
    if decision is not None:
        row.decision = StepDecision.model_validate(decision)
    if tests is not None:
        row.tests = TestCounts.model_validate(tests)
    if attempts is not None:
        row.attempts = attempts
    row.skipped_reason = skipped_reason


def _report_decision(decision: Decision, package: str) -> dict[str, Any]:
    out: dict[str, Any] = {"kind": decision.kind, "rule": decision.rule, "why": decision.reason,
                           "classification": decision.classification, "tier": decision.tier}
    if decision.kind == "split":
        out["split"] = {"deterministic": package, "agentic": package + AGENTIC_SUFFIX,
                        "handled": decision.handled, "deferred": decision.deferred}
    return out


# --- graph context --------------------------------------------------------------------------------------------------

def derive_constraints(doc: ProcessDoc, node: str) -> Constraints:
    """Field names the process graph fixes for `node`, from every `with:`/`when:` expression ($DRAFTS/05 §7.2.1)."""
    from wynd.spec.expr import references
    from wynd.spec.process_doc import expression_sites
    from wynd.spec.typelang import render_type

    input_names: list[str] = []
    for edge in doc.edges:
        for branch in edge.to:
            if branch.step == node:
                input_names += [f for f in branch.with_ if f not in input_names]
    input_types = {f: render_type(t) for f, t in doc.inputs.items()} if doc.entry == node else {}
    output_fields: dict[str, list[str]] = {}
    downstream: list[str] = []
    whole: list[str] = []
    for site in expression_sites(doc):
        own = site.edge is not None and site.edge.partition(".")[0] == node
        for ref in references(site.text):
            if ref.root != "steps" or not ref.path or ref.path[0] != node or len(ref.path) < 2 \
                    or ref.path[1] != "outputs":
                continue
            if len(ref.path) == 2:
                if site.role == "with" and site.edge is not None:          # loc: edges, i, to, j, with, field
                    target = doc.edges[site.loc[1]].to[site.loc[3]].step
                    whole.append(f"{target}.{site.loc[5]}")
                continue
            name = ref.path[2]
            if not isinstance(name, str):
                continue
            if own:
                fields = output_fields.setdefault(site.edge.partition(".")[2], [])
                if name not in fields:
                    fields.append(name)
            elif name not in downstream:
                downstream.append(name)
    return Constraints(input_names, input_types, output_fields, downstream, whole)


def _upstream(lp: LoadedProcess, doc: ProcessDoc, node: str) -> dict[str, str]:
    """Nodes with an edge into `node` -> their instruction (when they have a proto)."""
    names = [e.source_step for e in doc.edges if any(b.step == node for b in e.to)]
    return {n: _instruction(lp, n) for n in dict.fromkeys(names) if n in doc.steps}


def _downstream(lp: LoadedProcess, doc: ProcessDoc, node: str) -> dict[str, str]:
    names = [b.step for e in doc.edges if e.source_step == node for b in e.to if b.step in doc.steps]
    return {n: _instruction(lp, n) for n in dict.fromkeys(names)}


def _instruction(lp: LoadedProcess, node: str) -> str:
    rs = lp.steps.get(node)
    if rs is None or rs.package is None or rs.package.proto is None:
        return ""
    return rs.package.proto.instruction.strip()


def _process_context(lp: LoadedProcess, doc: ProcessDoc, node: str) -> dict[str, Any]:
    return {"goal": doc.goal or "", "upstream": _upstream(lp, doc, node), "downstream": _downstream(lp, doc, node)}


def _available_tools(catalog: ToolCatalog) -> dict[str, Any]:
    return {"builtins": [b.name for b in catalog.builtins],
            "mcp": {s.name: s.entry.description for s in catalog.mcp}}


def _version() -> str:
    from wynd.compiler import __version__

    return __version__


def _compiler_provider() -> str:
    import os

    return os.environ.get("WYND_COMPILER_PROVIDER") or "claude-code"

