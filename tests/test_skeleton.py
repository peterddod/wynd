"""The wave-0 skeleton contract (PLAN §0 rules 2 and 8, §13, §14 W0-CHECK).

Every module named in PLAN exists and imports, with the public names PLAN lists for it; no package ships
`src/wynd/__init__.py`; the W0-complete data and contract types have exactly PLAN's field names (plus the literals,
constants, protocols, seams and signatures PLAN's code blocks fix). Everything checked here stays true after
implementation: units add behaviour, never add/remove/rename fields or public names.
"""

import dataclasses
import functools
import importlib
import importlib.metadata
import importlib.util
import inspect
import subprocess
import sys
import typing
from pathlib import Path

import pytest
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("spec", "runtime", "process", "compiler", "controller", "cli", "kube")

# Module -> public names PLAN lists for it (§4.1, §5.1, §6.1, §7.1 + $DRAFTS/05 §2, §8.1, §9, §11 + $DRAFTS/08 §5.1).
# "" = PLAN names the module but not its names.
MODULES = {
    # spec (§4.1)
    "wynd.spec": "",
    "wynd.spec.base": (
        "SpecModel Name StepKey ExitName FieldName EnvName Alias ProcessId BranchName HashStr DEFAULT_PROVIDER "
        "DEFAULT_TIER DEFAULT_THINKING DEFAULT_MAX_TRAVERSALS TIERS RESERVED_EXIT EXIT_TARGET_PREFIX IGNORE_TARGET "
        "EDGE_KINDS PROTO_TYPES BASES LATENCIES LIMIT_FIELDS"
    ),
    "wynd.spec.errors": "Severity Loc Diagnostic SpecError format_loc",
    "wynd.spec.yamlio": "WyndLoader Mark SourceMap parse_yaml read_yaml parse_model load_model dump_yaml yaml_to_json",
    "wynd.spec.typelang": (
        "TypeNode TScalar TList TObject parse_type render_type TypeSpec type_schema fields_schema ModelSet "
        "build_models normalise_outputs describe_type describe_fields infer_fields"
    ),
    "wynd.spec.schemas": "normalize_schema schema_at is_nullable strip_null ANY MISSING",
    "wynd.spec.interface": (
        "Interface split_output output_adapter interface_from_models interface_from_fields interfaces_equivalent"
    ),
    "wynd.spec.workspace": (
        "WorkspaceConfig StepRoot load_workspace_config WORKSPACE_FILE PROCESS_FILE PROTO_DIR STEPS_DIR "
        "STEP_PROTO_FILE STEP_LOCK_FILE EDGES_LOCK_FILE PROCESS_LOCK_FILE ENV_MANIFEST_FILE STATE_DIR CASSETTES_DIR "
        "UseRef parse_use find_workspace_root RESERVED_PROCESS_SEGMENTS local_step_id root_step_id step_module_name "
        "slug"
    ),
    "wynd.spec.fragments": "EnvVar EnvGroup Mode EnvFragment merge_env_vars merge_fragments",
    "wynd.spec.proto_step": "Example ProtoStep load_proto_step check_proto_step",
    "wynd.spec.process_doc": (
        "ProcessEnv StepRef RetryOverride Limits Branch Edge FinallyStep ProcessDoc is_else ExprSite expression_sites "
        "site_position load_process check_process_doc"
    ),
    "wynd.spec.lockfiles": (
        "StepKind TraceStepKind HARNESS_BUILTINS Tier Thinking Effect RetryPolicy DEFAULT_RETRIES effective_retries "
        "ToolSnapshot McpToolSnapshot McpSnapshot ShellLock StepLock EdgeLockEntry EdgesLock branch_key check_hash "
        "BaseChoice FragmentRecord LockedWheel LockedVenv ProcessLock load_step_lock load_edges_lock "
        "load_process_lock dump_lock"
    ),
    "wynd.spec.plan": "PlanVenv PlanStep PlanNode PlanProcess RunPlan",
    "wynd.spec.env_manifest": "EnvManifest EnvCheck check_env load_env_manifest",
    "wynd.spec.records": (
        "Summary StepErrorCause StepError ProcessErrorCause TracePointer ProcessError STEP_ERROR_SCHEMA"
    ),
    "wynd.spec.context": "ContextRef parse_context_entry",
    "wynd.spec.hashing": (
        "jsonable canonical_json hash_bytes hash_obj proto_hash interface_hash normalize_requirement "
        "dependency_set_hash"
    ),
    "wynd.spec.expr": "",
    "wynd.spec.expr.grammar": "",
    "wynd.spec.expr.nodes": "",
    "wynd.spec.expr.errors": "ExprError ExprSyntaxError EvalError",
    "wynd.spec.expr.scope": "StepState EdgeState Scope new_scope",
    "wynd.spec.expr.evaluator": (
        "parse evaluate evaluate_condition evaluate_value evaluate_with evaluate_limit truthy strict_eq BUILTINS"
    ),
    "wynd.spec.expr.analysis": (
        "Ref references value_references env_names check_expression StepView TypeEnv check_references"
    ),
    "wynd.spec.expr.infer": "infer_type check_assignable",
    # runtime (§5.1)
    "wynd.runtime": "",
    "wynd.runtime.step": "Step DeterministicStep AgenticStep ShellStep ProcessStep step_kind",
    "wynd.runtime.interface": (
        "StepInterface interface_of process_interface StepDescription describe_step describe_process"
    ),
    "wynd.runtime.errors": "StepFailure StepDefinitionError InvalidProcessInputs",
    "wynd.runtime.usage": "Usage ModelInfo",
    "wynd.runtime.policy": "ExecPolicy CassetteConfig build_policy",
    "wynd.runtime.handle": "RuntimeHandle StepTrace StepCache",
    "wynd.runtime.http": "HttpClient HttpResponse HttpError",
    "wynd.runtime.shell": "ShellResult run_shell",
    "wynd.runtime.summary": "summarise project",
    "wynd.runtime.middleware": "run_chain ChainResult AgentCall AgentResult",
    "wynd.runtime.testing": "load_step run_step StepResult expect match_outputs Mismatch sub_tmp",
    "wynd.runtime.lint": "check_step_module LintIssue",
    "wynd.runtime.describe": "PackageDescription",
    "wynd.runtime.ids": "new_id valid_id",
    "wynd.runtime.storage": (
        "WorkspaceStore TraceSink RunRegistry Registry REGISTRY_SECTIONS Stores stores_from_env registry_from_env "
        "STORAGE_ENV StorageConfigError"
    ),
    "wynd.runtime.storage.base": "WorkspaceStore TraceSink RunRegistry Registry",
    "wynd.runtime.storage.models": "RunRecord TestResult",
    "wynd.runtime.storage.local": (
        "FileWorkspaceStore JsonlTraceSink FileRunRegistry FileRegistry EnvRegistry workspace_file trace_jsonl "
        "runs_file registry_file registry_env"
    ),
    "wynd.runtime.worker": "",
    "wynd.runtime.worker.protocol": "PROTOCOL_VERSION InitStep RunStepParams StepTimings RunStepResult",
    "wynd.runtime.worker.loader": "mount_step_package load_step_class",
    "wynd.runtime.worker.server": "WorkerServer main",
    "wynd.runtime.worker.client": "WorkerClient WorkerCrashed StepTimeout WorkerRpcError",
    "wynd.runtime.worker.pool": "Dispatcher WorkerPool InProcessDispatcher",
    "wynd.runtime.trace": "parse_event TraceEmitter TraceNode TraceTree read_trace build_tree",
    "wynd.runtime.executor": "Executor ProcessResult",
    "wynd.runtime.executor.engine": "Executor ProcessResult",
    "wynd.runtime.executor.instance": "Instance StepRecord Deadline RunCtx",
    "wynd.runtime.executor.context": "assemble_context",
    "wynd.runtime.executor.edges": (
        "EdgeVerdict EdgeCheckCall EdgeCheckResult EdgeCheckCause EdgeCheckError EdgeChecker make_edge_check_call"
    ),
    "wynd.runtime.providers": (
        "DEFAULT_PROVIDER ProviderEntry ProviderInfo provider_info available_providers provider_tiers resolve_model "
        "load_provider register_for_tests check_provider"
    ),
    "wynd.runtime.providers.types": (
        "ToolSchema ToolCall ToolResult ToolHandle McpServerRef GenerateRequest GenerateResponse Continuation "
        "AgentRequest AgentResponse ModelProvider AgentProvider ProviderError"
    ),
    "wynd.runtime.providers.claude_code": "ClaudeCodeProvider",
    "wynd.runtime.providers.anthropic": "AnthropicProvider",
    "wynd.runtime.providers.fake": "FakeProvider",
    "wynd.runtime.providers.scripted": "ScriptedModelProvider ScriptedAgentProvider",
    "wynd.runtime.agentic": "",
    "wynd.runtime.agentic.loop": "complete complete_structured StructuredCall ModelLoop AgentLoop",
    "wynd.runtime.agentic.checks": "check_agentic_class is_ellipsis_body describe_agentic",
    "wynd.runtime.agentic.prompt": "SYSTEM_PREAMBLE render_prompt format_validation_errors",
    "wynd.runtime.agentic.schema": "wrap_output_schema unwrap_output strict_compatible",
    "wynd.runtime.agentic.errors": (
        "ProviderError ToolFailure ToolInputError OutputValidationFailed AgentLoopError McpSnapshotMismatch "
        "McpConfigError MissingEnvVar"
    ),
    "wynd.runtime.tools": "",
    "wynd.runtime.tools.decorator": "tool ToolDecl ToolSpec tool_spec step_tools env",
    "wynd.runtime.tools.toolset": "ToolSet is_transient current_runtime handles_for",
    "wynd.runtime.tools.builtins": "http_get web_search workspace_read workspace_write shell now BUILTINS",
    "wynd.runtime.mcp": "McpServer",
    "wynd.runtime.mcp.client": "",
    "wynd.runtime.mcp.snapshot": "list_tools snapshot schema_sha256 verify",
    "wynd.runtime.mcp.entry": "McpServerEntry resolve_env_refs open_client",
    "wynd.runtime.mcp.fake_server": "",
    "wynd.runtime.cassettes": "CassetteSession CassetteMissError CassetteError NO_RECORDING promote",
    "wynd.runtime.cassettes.key": "Normaliser DATETIME_RE request_key",
    "wynd.runtime.cassettes.store": "",
    "wynd.runtime.cassettes.wrap": "CassetteModelProvider CassetteAgentProvider",
    "wynd.runtime.supervisor": "",
    "wynd.runtime.supervisor.main": "main",
    "wynd.runtime.supervisor.schema": "SUPERVISOR_ENV RunRequest RunCreated Run RunSummary ProcessInfo ErrorBody",
    "wynd.runtime.supervisor.runs": "RunManager",
    "wynd.runtime.supervisor.http": "Handler ROUTES",
    "wynd.runtime.supervisor.files": "",
    "wynd.runtime.supervisor.client": "RunApiClient RunApiError",
    "wynd.runtime.bake": "main",
    "wynd.runtime.edges": (
        "EDGE_VERDICT_SCHEMA VERIFIER_INSTRUCTION run_edge_check handle_edge_check WorkerEdgeChecker EdgeVerdict "
        "EdgeCheckCall EdgeCheckResult EdgeCheckCause EdgeCheckError EdgeChecker make_edge_check_call"
    ),
    # process (§6.1)
    "wynd.process": "",
    "wynd.process.errors": (
        "CODES WyndProcessError LoadError ProcessNotFound DesignPhase ValidationFailed ToolMissing GitError "
        "ResolutionError"
    ),
    "wynd.process._proc": "run require_tool",
    "wynd.process.workspace": (
        "Tree WorkingTree CommitTree ProcessEntry StepEntry Workspace load_workspace find_workspace_root"
    ),
    "wynd.process.loader": "ResolvedInterface StepPackage ResolvedStep LoadedProcess",
    "wynd.process.hashing": "tree_hash step_hash process_hash",
    "wynd.process.validation": "ValidationReport validate validate_process format_diagnostic",
    "wynd.process.validation.structure": "",
    "wynd.process.validation.reach": "",
    "wynd.process.validation.cycles": "",
    "wynd.process.validation.bindings": "",
    "wynd.process.validation.dataflow": "NOT_RUN ExitIs ExitIsNot HasRun NotRun",
    "wynd.process.validation.exprcheck": "TypedSite ExprCheckResult check_expr_at",
    "wynd.process.validation.rules": "",
    "wynd.process.validation.typecheck": "check_types",
    "wynd.process.validation.agentic": "check_agentic_edges",
    "wynd.process.fragments": "StepEnv MergedEnv merge_process_fragments",
    "wynd.process.venvs": (
        "RuntimeSource detect_runtime_source VenvGroup venv_groups local_venv_id ensure_local_venv step_python "
        "sync_interfaces"
    ),
    "wynd.process.plan": "build_plan plan_local assign_edge_venvs",
    "wynd.process.local": "run_local",
    "wynd.process.testing": (
        "TestCase SuiteResult TestReport run_tests run_step_suite run_process_examples tests_status "
        "run_test_live_job"
    ),
    "wynd.process.envmanifest": "assemble_env_manifest registry_snapshot",
    "wynd.process.git": (
        "git toplevel prefix rev_parse current_branch is_ancestor merge_base reference_closure closure_head "
        "dirty_paths count_touching log_paths add_worktree remove_worktree worktree_for_branch commit_paths "
        "commit_only merge_ff_only update_ref result_branch set_branch delete_branch commits_touching "
        "IntegrationResult integrate"
    ),
    "wynd.process.jobs": (
        "JobKind JobStatus TERMINAL JobRecord JobUsage JobContext JobOutcome JobHandler JobRunner new_job_record "
        "wait_for"
    ),
    "wynd.process.compile": "StepCompileState CompileState compile_state",
    "wynd.process.artefacts": "BuildInfo ArtefactStore LocalArtefactStore open_artefact_store ImageRegistryEntry",
    "wynd.process.build": "",
    "wynd.process.build.job": "Prepared prepare_build_job finalize_build_job run_build_job",
    "wynd.process.build.resolve": "Resolver UvResolver",
    "wynd.process.build.wheels": "stage_step build_step_wheel",
    "wynd.process.build.variant": "choose_base",
    "wynd.process.build.dockerfile": "render_dockerfile",
    "wynd.process.build.lockfile": "make_process_lock",
    "wynd.process.build.imagebuilder": (
        "ImageBuildRequest ImageBuildResult ImageBuilder BuildxImageBuilder open_image_builder"
    ),
    "wynd.process.base": "base_image_ref render_base_dockerfile build_base publish_base ensure_base",
    "wynd.process.bake": "run_bake_job bake_process",
    "wynd.process.edges_lock": "sync_edge_lock",
    "wynd.process.optimise": "",
    "wynd.process.latency": "latency_warnings",
    # compiler (§7.1; $DRAFTS/05 §2)
    "wynd.compiler": "",
    "wynd.compiler.jobs": "run_compile_job",
    "wynd.compiler.session": (
        "CompileSession SessionData SessionState Question Answer SessionEvent CompileStep CompileOptions"
    ),
    "wynd.compiler.gitops": "restore_paths squash_to",
    "wynd.compiler.report": "CompileReport render_commit_message",
    "wynd.compiler.pipeline": "CompileEnv CompileDeps compile_closure compile_node NodeOutcome",
    "wynd.compiler.decision": "",
    "wynd.compiler.split": "",
    "wynd.compiler.yamledit": "",
    "wynd.compiler.examples": "",
    "wynd.compiler.schemas": "",
    "wynd.compiler.testgen": "",
    "wynd.compiler.codegen": "",
    "wynd.compiler.astcheck": "",
    "wynd.compiler.tools": "",
    "wynd.compiler.lockfile": "CompiledInfo CompiledSplit",
    "wynd.compiler.attempts": "",
    "wynd.compiler.llm": "CompilerLLM ProviderLLM MemoLLM default_llm",
    "wynd.compiler.calls": "",
    "wynd.compiler.prompts": "load render",
    "wynd.compiler.testing": "ScriptedLLM RecordingLLM FakeJobContext",
    # controller (§8.1)
    "wynd.controller": "",
    "wynd.controller.controller": "ControllerContext Controller",
    "wynd.controller.errors": "WyndError ValidationFailed DirtyTree DetachedHead EnvMissing",
    "wynd.controller.models": (
        "Run Usage JobUsage Job Integration BuildResult ProcessStatus Meta ValidationReportDTO EnvCheckDTO "
        "ProviderInfoDTO Issue"
    ),
    "wynd.controller.store": "DocStore FileDocStore",
    "wynd.controller.workspace": "init_workspace new_process_files",
    "wynd.controller.processes": "ProcessService",
    "wynd.controller.status": "derive_status",
    "wynd.controller.envfile": "",
    "wynd.controller.envcheck": "EnvService",
    "wynd.controller.registries": "RegistryService",
    "wynd.controller.runs": "",
    "wynd.controller.runs.service": "RunService",
    "wynd.controller.runs.tree": "render_tree",
    "wynd.controller.runs.image": "",
    "wynd.controller.runs.mirror": "",
    "wynd.controller.git": "GitLock",
    "wynd.controller.jobs": "",
    "wynd.controller.jobs.records": "",
    "wynd.controller.jobs.handlers": "DEFAULT_HANDLERS PHASE_HANDLERS resolve_handler",
    "wynd.controller.jobs.checkout": "Checkout CheckoutBackend WorktreeCheckout CloneCheckout",
    "wynd.controller.jobs.harness": "execute_job JobCancelled",
    "wynd.controller.jobs.inprocess": "InProcessJobRunner",
    "wynd.controller.jobs.subproc": "SubprocessJobRunner",
    "wynd.controller.jobs.worker": "",
    "wynd.controller.jobs.runners": "open_job_runner pid_alive",
    "wynd.controller.jobs.service": "JobService",
    "wynd.controller.jobs.integrate": "",
    "wynd.controller.docker": "",
    "wynd.controller.serving": "ServeService",
    "wynd.controller.base": "build_base publish_base",
    "wynd.controller.compile_view": "session_dto answer_text apply_answers",
    "wynd.controller.design": "DesignService",
    "wynd.controller.search": "",
    "wynd.controller.uploads": "",
    "wynd.controller.oauth": "",
    "wynd.controller.releases": "",
    "wynd.controller.releases.store": "",
    "wynd.controller.releases.cron": "CronExpr",
    "wynd.controller.releases.service": "ReleaseService",
    "wynd.controller.releases.scheduler": "Scheduler",
    "wynd.controller.releases.serving": "ServingBackend DockerServing open_serving_backend",
    "wynd.controller.releases.triggers": "TriggerBackend SchedulerTriggers open_trigger_backend",
    "wynd.controller.chat": "",
    "wynd.controller.chat.store": "",
    "wynd.controller.chat.events": "",
    "wynd.controller.chat.prompt": "",
    "wynd.controller.chat.tools": "",
    "wynd.controller.chat.engine": "ChatService",
    "wynd.controller.api": "create_app serve",
    "wynd.controller.api.app": "",
    "wynd.controller.api.sse": "",
    "wynd.controller.api.models_web": "CompileSession TraceEvent",
    "wynd.controller.api.routes_meta": "",
    "wynd.controller.api.routes_processes": "",
    "wynd.controller.api.routes_jobs": "",
    "wynd.controller.api.routes_runs": "",
    "wynd.controller.api.routes_chats": "",
    "wynd.controller.api.routes_releases": "",
    "wynd.controller.api.routes_registries": "",
    "wynd.controller.api.static": "",
    "wynd.controller.optimise": "optimise_report submit_optimise run_optimise_job router",
    # cli (§9)
    "wynd.cli": "",
    "wynd.cli.main": "app main",
    "wynd.cli.context": "CliState get_controller",
    "wynd.cli.output": "",
    "wynd.cli.inputs": "parse_inputs parse_answers",
    "wynd.cli.commands": "",
    **{
        f"wynd.cli.commands.{name}": "register"
        for name in (
            "workspace", "test", "run", "env", "registries", "jobs", "build", "serve", "compile", "api", "release",
            "search", "optimise",
        )
    },
    # kube (§11; $DRAFTS/08 §5.1)
    "wynd.kube": "",
    "wynd.kube.config": "KubeConfig InstallParams",
    "wynd.kube.client": "KubeClient RestKubeClient KubeApiError RESOURCES",
    "wynd.kube.names": "k8s_name label_value job_name cronjob_name release_name secret_name_for",
    "wynd.kube.envmap": "",
    "wynd.kube.manifests": (
        "render_job render_cronjob render_process_release render_env_template render_install to_yaml"
    ),
    "wynd.kube.runner": "KubeJobRunner",
    "wynd.kube.triggers": "KubeTriggerBackend",
    "wynd.kube.serving": "KubeServingBackend",
    "wynd.kube.checkout": "checkout",
    "wynd.kube.fire": "fire",
    "wynd.kube.cli": "app",
    "wynd.kube.testing": "FakeKubeClient",
}

# `python -m` entry modules: located, never executed.
MAIN_MODULES = ("wynd.runtime.worker.__main__", "wynd.runtime.supervisor.__main__")
# Package data copied into artefacts rather than imported (PLAN §6.1 bake row).
TEMPLATE_FILES = ("packages/process/src/wynd/process/templates/bake_main.py",)


def _discovered_modules() -> set[str]:
    """Every module file under packages/*/src/wynd, so a module PLAN does not list must import too (§0 rule 8)."""
    found = set()
    for path in ROOT.glob("packages/*/src/wynd/**/*.py"):
        parts = path.relative_to(ROOT).parts
        rel = parts[parts.index("src") + 1:]
        if "templates" in rel or rel[-1] == "__main__.py":
            continue
        dotted = ".".join(rel)[: -len(".py")]
        found.add(dotted.removesuffix(".__init__"))
    return found


ALL_MODULES = sorted(set(MODULES) | _discovered_modules())


def resolve(ref: str):
    """Resolve "module:Name" or "module:Class.attr"."""
    module, _, name = ref.partition(":")
    obj = importlib.import_module(module)
    for part in name.split("."):
        obj = getattr(obj, part)
    return obj


def field_names(cls) -> tuple[str, ...]:
    if isinstance(cls, type) and issubclass(cls, BaseModel):
        return tuple(cls.model_fields)
    if dataclasses.is_dataclass(cls):
        return tuple(f.name for f in dataclasses.fields(cls))
    raise TypeError(f"{cls!r} is neither a pydantic model nor a dataclass")


def shape(fn) -> str:
    """Parameter names with `/`, `*` markers (annotations and defaults ignored); `self` dropped."""
    params = [p for p in inspect.signature(fn).parameters.values() if p.name != "self"]
    parts = []
    star = False
    for i, p in enumerate(params):
        match p.kind:
            case p.VAR_POSITIONAL:
                parts.append(f"*{p.name}")
                star = True
                continue
            case p.VAR_KEYWORD:
                parts.append(f"**{p.name}")
                continue
            case p.KEYWORD_ONLY if not star:
                parts.append("*")
                star = True
        parts.append(p.name)
        last_positional_only = p.kind is p.POSITIONAL_ONLY and (
            i + 1 == len(params) or params[i + 1].kind is not p.POSITIONAL_ONLY
        )
        if last_positional_only:
            parts.append("/")
    return ", ".join(parts)


# --- namespace packaging (§1.2) ---------------------------------------------------------------------------------------

def test_no_package_ships_a_wynd_init():
    assert sorted(str(p.relative_to(ROOT)) for p in ROOT.glob("packages/*/src/wynd/__init__.py")) == []


def test_wynd_is_one_namespace_across_all_members():
    import wynd

    assert getattr(wynd, "__file__", None) is None
    paths = {Path(p).resolve() for p in wynd.__path__}
    assert {(ROOT / "packages" / pkg / "src" / "wynd").resolve() for pkg in PACKAGES} <= paths


@pytest.mark.parametrize("pkg", PACKAGES)
def test_package_version_matches_its_distribution(pkg):
    assert importlib.import_module(f"wynd.{pkg}").__version__ == importlib.metadata.version(f"wynd-{pkg}")


# --- every module imports, with its public names ----------------------------------------------------------------------

@pytest.mark.parametrize("module", ALL_MODULES)
def test_module_imports(module):
    importlib.import_module(module)


@pytest.mark.parametrize("module", MAIN_MODULES)
def test_main_module_exists(module):
    assert importlib.util.find_spec(module) is not None


@pytest.mark.parametrize("path", TEMPLATE_FILES)
def test_template_module_compiles(path):
    source = (ROOT / path).read_text()
    compile(source, path, "exec")


@pytest.mark.parametrize("module", [m for m, names in MODULES.items() if names])
def test_module_public_names(module):
    mod = importlib.import_module(module)
    assert [name for name in MODULES[module].split() if not hasattr(mod, name)] == []


# --- lazy package re-exports (§0 rule 8, §4.2, §5.1, §6.1, §7.1, §8.1) -----------------------------------------------

def _names(*modules: str) -> set[str]:
    return {name for module in modules for name in MODULES[module].split()}


SPEC_LEAVES = [m for m in MODULES if m.startswith("wynd.spec.") and not m.startswith("wynd.spec.expr")]
EXPR_LEAVES = [m for m in MODULES if m.startswith("wynd.spec.expr.")]
BUILD_LEAVES = [m for m in MODULES if m.startswith("wynd.process.build.")]
CONTROLLER_ERRORS = (
    "WyndError Invalid ValidationFailed Unauthorized OutOfScope NotFound NotAWorkspace Conflict DirtyTree "
    "RevisionConflict NotBuilt EnvMissing EnvUnbound JobState DetachedHead TurnInProgress VersionMismatch "
    "DesignLocked Unavailable"
)
EXPORTS = {
    "wynd.spec": _names(*SPEC_LEAVES),
    "wynd.spec.expr": _names(*EXPR_LEAVES),
    "wynd.runtime": set(
        "Step DeterministicStep AgenticStep ShellStep ProcessStep ShellResult tool env McpServer StepError "
        "ProcessError Summary RuntimeHandle".split()
    ),
    "wynd.process": set(
        "find_workspace_root load_workspace Workspace LoadedProcess ResolvedStep StepPackage ResolvedInterface "
        "validate validate_process ValidationReport plan_local run_local run_tests tests_status "
        "assemble_env_manifest compile_state CompileState reference_closure closure_head dirty_paths integrate "
        "IntegrationResult JobKind JobStatus JobRecord JobContext JobOutcome JobUsage JobRunner open_artefact_store "
        "ArtefactStore BuildInfo ImageRegistryEntry".split()
    ),
    "wynd.process.build": _names(*BUILD_LEAVES),
    "wynd.compiler": set(
        "run_compile_job CompileSession SessionData SessionState Question Answer SessionEvent CompileStep "
        "CompileOptions CompileReport ScriptedLLM RecordingLLM FakeJobContext".split()
    ),
    "wynd.controller": {"Controller", "ControllerContext", *CONTROLLER_ERRORS.split()},
    "wynd.kube": set(),
}


@pytest.mark.parametrize("package", EXPORTS)
def test_package_reexports_resolve(package):
    mod = importlib.import_module(package)
    assert sorted(EXPORTS[package] - set(mod.__all__)) == []
    for name in mod.__all__:
        getattr(mod, name)


@pytest.mark.parametrize("package", EXPORTS)
def test_package_init_imports_no_leaf(package):
    code = (
        f"import sys, {package}\n"
        f"print(sorted(m for m in sys.modules if m.startswith('{package}.')))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "[]"


EMPTY_INITS = (
    "packages/runtime/src/wynd/runtime/worker/__init__.py",
    "packages/controller/src/wynd/controller/runs/__init__.py",
    "packages/controller/src/wynd/controller/jobs/__init__.py",
    "packages/controller/src/wynd/controller/releases/__init__.py",
    "packages/controller/src/wynd/controller/chat/__init__.py",
    "packages/cli/src/wynd/cli/commands/__init__.py",
)


@pytest.mark.parametrize("path", EMPTY_INITS)
def test_w0_subpackage_init_is_empty(path):
    assert (ROOT / path).read_text().strip() == ""


def test_process_validate_is_the_function():
    process = importlib.import_module("wynd.process")
    validation = importlib.import_module("wynd.process.validation")
    assert process.validate is validation.validate
    assert inspect.isfunction(process.validate)


# --- W0-complete types: exact field names (PLAN code blocks) ----------------------------------------------------------

FIELDS = {
    # spec
    "wynd.spec.errors:Diagnostic": "severity code message file line column loc process span snippet",
    "wynd.spec.workspace:WorkspaceConfig": "process_roots step_roots cassette_warn_mb",
    "wynd.spec.workspace:StepRoot": "path url",
    "wynd.spec.workspace:UseRef": "form target alias",
    "wynd.spec.proto_step:Example": "inputs outputs exit description env",
    "wynd.spec.proto_step:ProtoStep": "kind name instruction inputs outputs exits exit_codes examples env",
    "wynd.spec.process_doc:ProcessEnv": "base vars",
    "wynd.spec.process_doc:StepRef": "use",
    "wynd.spec.process_doc:RetryOverride": "run validation tool",
    "wynd.spec.process_doc:Limits": "max_traversals timeout retries",
    "wynd.spec.process_doc:Branch": "step when with_ limits name check context",
    "wynd.spec.process_doc:Edge": "from_ to kind",
    "wynd.spec.process_doc:FinallyStep": "step with_",
    "wynd.spec.process_doc:ProcessDoc": (
        "kind name goal latency provider env entry inputs outputs exits examples steps edges on_error finally_"
    ),
    "wynd.spec.process_doc:ExprSite": "loc text role edge branch",
    "wynd.spec.expr.scope:StepState": "runs exit outputs summary",
    "wynd.spec.expr.scope:EdgeState": "taken names",
    "wynd.spec.expr.scope:Scope": "steps edges process_inputs env run_id previous clock",
    "wynd.spec.lockfiles:RetryPolicy": "run validation tool",
    "wynd.spec.lockfiles:ToolSnapshot": "name source effects idempotent env allow",
    "wynd.spec.lockfiles:McpToolSnapshot": "name description input_schema output_schema idempotent",
    "wynd.spec.lockfiles:McpSnapshot": "server allow tools hash",
    "wynd.spec.lockfiles:ShellLock": "exit_codes",
    "wynd.spec.lockfiles:StepLock": (
        "wynd name kind entrypoint proto_hash provider tier thinking builtin_tools max_turns retries effects tools "
        "mcp context shell fragment locked_deps interface compiled"
    ),
    "wynd.spec.lockfiles:EdgeLockEntry": "check_hash provider tier thinking retries timeout_s",
    "wynd.spec.lockfiles:EdgesLock": "wynd edges",
    "wynd.spec.lockfiles:BaseChoice": "requested variant version image reason",
    "wynd.spec.lockfiles:FragmentRecord": "source deps system requires",
    "wynd.spec.lockfiles:LockedWheel": "dir hash wheel",
    "wynd.spec.lockfiles:LockedVenv": "id inputs requirements",
    "wynd.spec.lockfiles:ProcessLock": (
        "wynd process commit source_sha process_hash runtime_version platform base system_packages fragments wheels "
        "venvs plan"
    ),
    "wynd.spec.records:Summary": "step exit key_outputs note",
    "wynd.spec.records:StepError": "exit cause message type traceback inputs partial_outputs attempts child",
    "wynd.spec.records:TracePointer": "run_id uri seq",
    "wynd.spec.records:ProcessError": (
        "run_id process step cause message edge inputs partial_outputs step_error handler_error detail trace "
        "workspace"
    ),
    "wynd.spec.fragments:EnvVar": "name description secret required default one_of used_by",
    "wynd.spec.fragments:EnvGroup": "description modes",
    "wynd.spec.fragments:EnvFragment": "deps system requires vars groups",
    "wynd.spec.env_manifest:EnvManifest": "wynd process commit vars groups",
    "wynd.spec.env_manifest:EnvCheck": "ok diagnostics",
    "wynd.spec.plan:PlanVenv": "id python steps",
    "wynd.spec.plan:PlanStep": "id kind entrypoint venv package_dir lock",
    "wynd.spec.plan:PlanNode": "step process",
    "wynd.spec.plan:PlanProcess": "id dir definition nodes edges_lock",
    "wynd.spec.plan:RunPlan": "wynd mode root commit provider venv_root venvs steps processes edge_venvs",
    "wynd.spec.interface:Interface": "input outputs",
    "wynd.spec.context:ContextRef": "kind step path",
    # runtime
    "wynd.runtime.usage:Usage": (
        "input_tokens output_tokens cache_read_tokens cache_write_tokens cost_usd latency_ms calls"
    ),
    "wynd.runtime.usage:ModelInfo": "provider model_id tier thinking",
    "wynd.runtime.policy:CassetteConfig": "mode dir record_dir literals",
    "wynd.runtime.policy:ExecPolicy": (
        "kind retries provider model_id tier thinking builtin_tools max_turns effects mcp"
    ),
    "wynd.runtime.worker.protocol:InitStep": "id entrypoint package_dir kind interface_hash context exit_codes",
    "wynd.runtime.worker.protocol:RunStepParams": (
        "run_id step_path step_run step_id inputs context workspace policy cassette"
    ),
    "wynd.runtime.worker.protocol:StepTimings": "started_at ended_at worker_ms pre_ms run_ms post_ms",
    "wynd.runtime.worker.protocol:RunStepResult": (
        "exit outputs summary attempts validation_failures timings usage model replayed"
    ),
    "wynd.runtime.providers.types:ToolSchema": "name description input_schema",
    "wynd.runtime.providers.types:ToolCall": "id name arguments",
    "wynd.runtime.providers.types:ToolResult": "text is_error",
    "wynd.runtime.providers.types:ToolHandle": "name description input_schema invoke local",
    "wynd.runtime.providers.types:McpServerRef": "name allow",
    "wynd.runtime.providers.types:GenerateRequest": "model_id thinking system messages tools output_schema",
    "wynd.runtime.providers.types:GenerateResponse": (
        "message text tool_calls structured_output stop usage model_id cost_basis raw"
    ),
    "wynd.runtime.providers.types:Continuation": "session message",
    "wynd.runtime.providers.types:AgentRequest": (
        "model_id thinking instruction context input output_schema tools mcp_servers workspace max_turns "
        "builtin_tools continuation prompt on_event cancel"
    ),
    "wynd.runtime.providers.types:AgentResponse": (
        "structured_output usage model_id transcript session note tool_calls startup_ms cost_basis"
    ),
    "wynd.runtime.providers:ProviderInfo": "name kind default_tiers env_fragment",
    "wynd.runtime.providers:ProviderEntry": "tiers",
    "wynd.runtime.mcp.entry:McpServerEntry": (
        "name transport url headers command env auth_env timeout_s description oauth"
    ),
    "wynd.runtime.storage.models:RunRecord": (
        "id kind process status created_at updated_at started_at finished_at ref mode inputs exit outputs error "
        "trace workspace duration_ms usage trace_bytes workspace_bytes meta"
    ),
    "wynd.runtime.storage.models:TestResult": "commit key passed counts ran_at job_id report",
    "wynd.runtime.storage:Stores": "workspaces traces runs registry",
    "wynd.runtime.shell:ShellResult": "returncode stdout stderr",
    "wynd.runtime.executor.engine:ProcessResult": (
        "run_id process exit outputs error status trace workspace duration_ms usage finally_errors"
    ),
    "wynd.runtime.executor.edges:EdgeVerdict": "take reason",
    "wynd.runtime.executor.edges:EdgeCheckCall": (
        "run_id process process_goal edge branch branch_key source_step target check context bindings lock "
        "provider model_id"
    ),
    "wynd.runtime.executor.edges:EdgeCheckResult": (
        "verdict attempts validation_failures provider tier model_id usage replayed duration_ms"
    ),
    # process
    "wynd.process.jobs:JobUsage": (
        "input_tokens output_tokens cache_read_tokens cache_write_tokens cost_usd latency_ms calls by"
    ),
    "wynd.process.jobs:JobRecord": (
        "id kind job_kind process ref base_commit target_branch workspace_rel inputs status runner handler attempt "
        "created_at updated_at started_at finished_at duration_ms cpu_ms pid host result_branch result_commit "
        "artefacts report session questions usage error integration chat_id"
    ),
    "wynd.process.jobs:JobContext": (
        "job inputs session worktree workspace workspace_root state_dir scratch runs registry log commit "
        "save_session"
    ),
    "wynd.process.jobs:JobOutcome": "status commit artefacts report session questions error usage",
    "wynd.process.git:IntegrationResult": (
        "mode branch target head skipped_commits conflicts reason at pr_url"
    ),
    "wynd.process.loader:ResolvedInterface": "interface source precise",
    "wynd.process.loader:StepPackage": "id dir proto_path proto proto_hash lock phase stale interface",
    "wynd.process.loader:ResolvedStep": "name use ref_kind package child",
    "wynd.process.loader:LoadedProcess": "id dir path doc source steps children diagnostics",
    "wynd.process.validation:ValidationReport": "process diagnostics normalized env_refs",
    "wynd.process.validation.exprcheck:TypedSite": "process loc expr env target dst_schema src_schema",
    "wynd.process.validation.exprcheck:ExprCheckResult": "errors warnings scope",
    "wynd.process.artefacts:BuildInfo": (
        "process commit source_sha job_id process_hash image image_id image_digest pushed base manifest bake "
        "created_at dir"
    ),
    "wynd.process.artefacts:ImageRegistryEntry": "name url username_env password_env insecure default",
    "wynd.process.testing:TestCase": "name outcome message duration_ms",
    "wynd.process.testing:SuiteResult": "subject hash passed counts cases problem",
    "wynd.process.testing:TestReport": (
        "process commit process_hash mode passed suites started_at finished_at recorded"
    ),
    # controller (§8, §3.21 amendments 1, 4, 13)
    "wynd.controller.controller:ControllerContext": (
        "root state_dir subdir env stores artefacts docs runner git_lock load_provider clock"
    ),
    "wynd.controller.models:Issue": "severity code message file loc span",
    "wynd.controller.models:ValidationReportDTO": "ok issues",
    "wynd.controller.models:EnvCheckDTO": "ok missing unbound issues",
    "wynd.controller.models:ProviderInfoDTO": "name kind tiers ready message",
    "wynd.controller.api.models_web:TraceEvent": "v seq ts run_id type",
}


@pytest.mark.parametrize("ref", FIELDS)
def test_fields(ref):
    cls = resolve(ref)
    assert field_names(cls) == tuple(FIELDS[ref].split())
    if issubclass(cls, BaseModel):
        assert cls.__pydantic_complete__, f"{ref} has unresolved annotations"


# Web DTOs whose PLAN amendments name only some fields (§3.21 amendments 3, 10-12).
DTO_FIELDS = {
    "wynd.controller.models:Job": "kind process_id branch result_commit usage",
    "wynd.controller.models:Run": "status finished_at usage",
    "wynd.controller.models:BuildResult": "build_dir",
    "wynd.controller.models:ProcessStatus": "tests",
    "wynd.controller.models:Meta": "latency",
}


@pytest.mark.parametrize("ref", DTO_FIELDS)
def test_dto_amendment_fields(ref):
    assert sorted(set(DTO_FIELDS[ref].split()) - set(resolve(ref).model_fields)) == []


def test_yaml_keyword_aliases():
    doc = importlib.import_module("wynd.spec.process_doc")
    aliases = {
        (cls.__name__, name): cls.model_fields[name].alias
        for cls, name in (
            (doc.Branch, "with_"), (doc.Edge, "from_"), (doc.FinallyStep, "with_"), (doc.ProcessDoc, "finally_")
        )
    }
    assert aliases == {
        ("Branch", "with_"): "with",
        ("Edge", "from_"): "from",
        ("FinallyStep", "with_"): "with",
        ("ProcessDoc", "finally_"): "finally",
    }


def test_job_usage_extends_usage():
    assert issubclass(resolve("wynd.process.jobs:JobUsage"), resolve("wynd.runtime.usage:Usage"))


def test_records_mutual_reference_is_resolved():
    records = importlib.import_module("wynd.spec.records")
    child = records.ProcessError(run_id="run_1", process="p", step=None, cause="internal", message="boom")
    err = records.StepError(cause="child_process", message="child failed", child=child)
    assert err.child == child
    assert err.exit == "error"
    assert resolve("wynd.spec.lockfiles:ProcessLock").model_fields["plan"].annotation is resolve(
        "wynd.spec.plan:RunPlan"
    )


# --- literals and constants -------------------------------------------------------------------------------------------

LITERALS = {
    "wynd.spec.errors:Severity": ("error", "warning", "info"),
    "wynd.spec.lockfiles:StepKind": ("deterministic", "agentic", "shell"),
    "wynd.spec.lockfiles:TraceStepKind": ("deterministic", "agentic", "shell", "process"),
    "wynd.spec.lockfiles:Tier": ("cheap", "standard", "strong"),
    "wynd.spec.lockfiles:Thinking": ("none", "low", "medium", "high"),
    "wynd.spec.lockfiles:Effect": ("network", "filesystem", "shell"),
    "wynd.spec.records:StepErrorCause": (
        "input_validation", "exception", "hook", "output_validation", "tool", "transport", "model", "config",
        "cassette_miss", "shell_exit", "child_process", "worker_crash", "import",
    ),
    "wynd.spec.records:ProcessErrorCause": (
        "step_error", "unrouted_exit", "ignored_exit", "no_branch_matched", "max_traversals", "timeout",
        "expression_error", "invalid_process_outputs", "edge_check", "internal",
    ),
    "wynd.spec.fragments:Mode": ("local", "image"),
    "wynd.process.jobs:JobKind": ("compile", "test_live", "build", "bake", "optimise"),
    "wynd.process.jobs:JobStatus": ("queued", "running", "awaiting_input", "succeeded", "failed", "cancelled"),
    "wynd.runtime.executor.edges:EdgeCheckCause": (
        "validation", "transport", "timeout", "config", "model", "cassette_miss",
    ),
    "wynd.process.git:IntegrationResult.mode": ("fast_forward", "rebased", "pr_branch", "noop"),
    "wynd.runtime.storage.models:RunRecord.status": ("queued", "running", "succeeded", "failed"),
    "wynd.controller.models:Issue.severity": ("error", "warning", "info"),
    "wynd.controller.models:Run.status": ("queued", "running", "succeeded", "failed"),
    "wynd.controller.models:ProcessStatus.tests": ("passed", "failed", "unknown"),
}


def literal_args(ref: str) -> tuple:
    module, _, name = ref.partition(":")
    owner, _, field = name.partition(".")
    obj = getattr(importlib.import_module(module), owner)
    if field:
        obj = obj.model_fields[field].annotation
    return typing.get_args(obj)


@pytest.mark.parametrize("ref", LITERALS)
def test_literal_values(ref):
    assert literal_args(ref) == LITERALS[ref]


CONSTANTS = {
    "wynd.spec.base:DEFAULT_PROVIDER": "claude-code",
    "wynd.spec.base:DEFAULT_TIER": "cheap",
    "wynd.spec.base:DEFAULT_THINKING": "low",
    "wynd.spec.base:DEFAULT_MAX_TRAVERSALS": 10,
    "wynd.spec.base:TIERS": {"cheap", "standard", "strong"},
    "wynd.spec.base:RESERVED_EXIT": "error",
    "wynd.spec.base:EXIT_TARGET_PREFIX": "$exit.",
    "wynd.spec.base:IGNORE_TARGET": "$ignore",
    "wynd.spec.base:EDGE_KINDS": ("deterministic", "agentic"),
    "wynd.spec.base:PROTO_TYPES": {"string", "number", "integer", "boolean", "date", "datetime", "path", "object"},
    "wynd.spec.base:BASES": {"debian-slim-python", "alpine-python"},
    "wynd.spec.base:LATENCIES": ("fast", "normal"),
    "wynd.spec.base:LIMIT_FIELDS": ("max_traversals", "timeout", "retries"),
    "wynd.spec.workspace:WORKSPACE_FILE": "wynd.yaml",
    "wynd.spec.workspace:PROCESS_FILE": "process.yaml",
    "wynd.spec.workspace:PROTO_DIR": "proto",
    "wynd.spec.workspace:STEPS_DIR": "steps",
    "wynd.spec.workspace:STEP_PROTO_FILE": "proto.yaml",
    "wynd.spec.workspace:STEP_LOCK_FILE": "step.lock.yaml",
    "wynd.spec.workspace:EDGES_LOCK_FILE": "edges.lock.yaml",
    "wynd.spec.workspace:PROCESS_LOCK_FILE": "process.lock.yaml",
    "wynd.spec.workspace:ENV_MANIFEST_FILE": "process.env.yaml",
    "wynd.spec.workspace:STATE_DIR": ".wynd",
    "wynd.spec.workspace:CASSETTES_DIR": "cassettes",
    "wynd.spec.workspace:RESERVED_PROCESS_SEGMENTS": {
        "design", "compile", "build", "builds", "interface", "status", "validate", "test", "test-live", "bake",
        "optimise", "history", "env", "files",
    },
    "wynd.spec.lockfiles:HARNESS_BUILTINS": ("Read", "Glob", "Grep", "WebFetch", "WebSearch"),
    "wynd.runtime.providers:DEFAULT_PROVIDER": "claude-code",
    "wynd.runtime.worker.protocol:PROTOCOL_VERSION": 1,
    "wynd.runtime.storage:REGISTRY_SECTIONS": ("mcp", "providers", "registries"),
    "wynd.process.jobs:TERMINAL": {"succeeded", "failed", "cancelled"},
}


@pytest.mark.parametrize("ref", CONSTANTS)
def test_constants(ref):
    value, expected = resolve(ref), CONSTANTS[ref]
    if isinstance(expected, set):
        assert set(value) == expected
        return
    assert value == expected


def test_default_retries():
    retries = resolve("wynd.spec.lockfiles:DEFAULT_RETRIES")
    assert {kind: policy.model_dump() for kind, policy in retries.items()} == {
        "deterministic": {"run": 0, "validation": 0, "tool": 0},
        "shell": {"run": 0, "validation": 0, "tool": 0},
        "agentic": {"run": 2, "validation": 2, "tool": 1},
    }


def test_supervisor_env():
    env = {v.name: v for v in resolve("wynd.runtime.supervisor.schema:SUPERVISOR_ENV")}
    assert set(env) == {
        "WYND_RUN_API_TOKEN", "WYND_HOST", "WYND_PORT", "WYND_MAX_CONCURRENT_RUNS", "WYND_MAX_QUEUED_RUNS",
        "WYND_RUN_RETENTION", "WYND_DRAIN_TIMEOUT_S", "WYND_MAX_BODY_MB",
    }
    assert [name for name, v in env.items() if v.required or v.used_by != ["runtime"]] == []
    assert [name for name, v in env.items() if v.secret] == ["WYND_RUN_API_TOKEN"]
    assert [name for name, v in env.items() if v.default is None] == ["WYND_RUN_API_TOKEN"]
    assert {name: env[name].default for name in ("WYND_HOST", "WYND_PORT", "WYND_MAX_CONCURRENT_RUNS",
                                                  "WYND_MAX_QUEUED_RUNS")} == {
        "WYND_HOST": "0.0.0.0", "WYND_PORT": "8080", "WYND_MAX_CONCURRENT_RUNS": "4", "WYND_MAX_QUEUED_RUNS": "64",
    }


# --- protocols and signatures (PLAN code blocks) ----------------------------------------------------------------------

PROTOCOLS = {
    "wynd.runtime.storage.base:WorkspaceStore": {"open": "run_id", "close": "run_id, *, keep", "locate": "run_id"},
    "wynd.runtime.storage.base:TraceSink": {
        "write": "event", "close": "run_id", "read": "run_id", "uri": "run_id",
    },
    "wynd.runtime.storage.base:RunRegistry": {
        "create": "record", "update": "id, patch", "get": "id", "list": "*, kind, process, status, limit",
        "put_test_result": "result", "get_test_result": "commit, key",
    },
    "wynd.runtime.storage.base:Registry": {
        "list": "section", "get": "section, name", "put": "section, name, entry", "remove": "section, name",
        "put_secret": "name, value", "secrets": "", "location": "",
    },
    "wynd.runtime.providers.types:ModelProvider": {"name": None, "generate": "req", "tiers": ""},
    "wynd.runtime.providers.types:AgentProvider": {"name": None, "run": "req", "tiers": ""},
    "wynd.runtime.executor.edges:EdgeChecker": {"check": "call, *, cassette, workspace, timeout_s, on_event"},
    "wynd.process.jobs:JobRunner": {
        "name": None, "submit": "kind, ref, inputs", "status": "job_id", "logs": "job_id, offset",
        "artefacts": "job_id", "cancel": "job_id", "requeue": "job_id, ref, inputs",
    },
    "wynd.process.artefacts:ArtefactStore": {
        "put_build": "staged_dir, info", "get_build": "pid, commit", "list_builds": "pid", "local_dir": "pid, commit",
    },
}


def protocol_members(proto) -> dict[str, str | None]:
    """Annotated attributes -> None; methods -> their parameter shape."""
    members: dict[str, str | None] = {name: None for name in vars(proto).get("__annotations__", {})}
    for name, value in vars(proto).items():
        if not name.startswith("_") and inspect.isfunction(value):
            members[name] = shape(value)
    return members


@pytest.mark.parametrize("ref", PROTOCOLS)
def test_protocol_members(ref):
    assert protocol_members(resolve(ref)) == PROTOCOLS[ref]


SIGNATURES = {
    "wynd.spec.errors:format_loc": "loc",
    "wynd.spec.interface:split_output": "output",
    "wynd.spec.interface:output_adapter": "output",
    "wynd.spec.interface:interface_from_models": "input_model, output",
    "wynd.spec.interface:interface_from_fields": "inputs, outputs",
    "wynd.spec.interface:interfaces_equivalent": "a, b",
    "wynd.spec.process_doc:is_else": "b",
    "wynd.spec.expr.scope:Scope.complete": "step, exit, outputs, summary",
    "wynd.spec.expr.scope:Scope.take": "edge, branch",
    "wynd.spec.expr.scope:new_scope": "doc, *, inputs, env, run_id, clock",
    "wynd.spec.lockfiles:effective_retries": "policy, override",
    "wynd.spec.lockfiles:branch_key": "edge_from, index, name",
    "wynd.spec.lockfiles:check_hash": "check, context",
    "wynd.spec.lockfiles:load_edges_lock": "path",
    "wynd.spec.fragments:merge_env_vars": "vars",
    "wynd.spec.fragments:merge_fragments": "frags",
    "wynd.spec.env_manifest:check_env": "manifest, environ, *, mode",
    "wynd.spec.workspace:parse_use": "text",
    "wynd.runtime.errors:StepFailure.__init__": "cause, message, *, partial_outputs, usage, attempts",
    "wynd.runtime.policy:build_policy": "kind, lock, *, default_provider, registry, environ, retry_override",
    "wynd.runtime.storage:stores_from_env": "env, *, data_dir",
    "wynd.runtime.storage:registry_from_env": "env",
    "wynd.runtime.providers:check_provider": "name",
    "wynd.runtime.tools.decorator:tool": "fn, /, *, name, effects, idempotent, env",
    "wynd.runtime.testing:load_step": "step_dir, cls",
    "wynd.runtime.testing:run_step": "step, input, *, mode, cassettes, lock, context, workspace, retry",
    "wynd.runtime.testing:expect": "result, *, exit, outputs, present",
    "wynd.runtime.testing:match_outputs": "expected, actual, *, present",
    "wynd.runtime.testing:sub_tmp": "value, tmp",
    "wynd.runtime.worker.pool:WorkerPool.__init__": "plan, *, env, cwd",
    "wynd.runtime.executor.engine:Executor.__init__": "plan, dispatcher, stores, *, env, edge_checker",
    "wynd.runtime.executor.engine:Executor.validate_inputs": "inputs",
    "wynd.runtime.executor.engine:Executor.run": (
        "inputs, *, run_id, cassette_mode, cassette_root, record_root, cassette_literals, metadata, on_event"
    ),
    "wynd.runtime.executor.edges:make_edge_check_call": "instance, edge, branch_index, branch, bindings",
    "wynd.runtime.edges:WorkerEdgeChecker.__init__": "pool, plan",
    "wynd.runtime.supervisor.client:RunApiClient.__init__": "base_url, *, token, timeout_s",
    "wynd.process.local:run_local": (
        "ws, pid, inputs, *, env, stores, run_id, on_event, cassette_mode, cassette_root, record_root, "
        "cassette_literals, metadata, venv_root, log"
    ),
    "wynd.process.testing:run_tests": "ws, pid, *, mode, commit, runs, venv_root, scratch, env, log",
    "wynd.process.testing:run_step_suite": (
        "pkg_dir, *, python, mode, env, junit, basetemp, record_dir, timeout_s"
    ),
    "wynd.process.testing:run_process_examples": "ws, pid, *, mode, env, scratch, venv_root, record_root, log",
    "wynd.process.testing:tests_status": "runs, ws, pid, commit",
    "wynd.process.testing:run_test_live_job": "ctx",
    "wynd.process.git:integrate": "ws_root, *, process_id, base_sha, branch, target_branch, scratch_dir",
    "wynd.process.git:result_branch": "kind, pid, job_id",
    "wynd.process.jobs:new_job_record": "kind, ref, inputs, *, ws_root, runner, handler",
    "wynd.process.jobs:wait_for": "runner, job_id, *, poll_s, timeout_s",
    "wynd.process.validation.typecheck:check_types": "lp, sites",
    "wynd.process.validation.agentic:check_agentic_edges": "lp, edges_lock",
    "wynd.process.edges_lock:sync_edge_lock": "process_dir, doc",
    "wynd.controller.controller:Controller.open": "root, *, environ, load_dotenv",
    "wynd.controller.jobs.harness:execute_job": "job_id, *, phase, checkout, stores, registry, handlers",
}


@pytest.mark.parametrize("ref", SIGNATURES)
def test_signature(ref):
    assert shape(resolve(ref)) == SIGNATURES[ref]


# --- single definitions (a contract type is defined once and re-exported) --------------------------------------------

SAME_OBJECT = {
    "wynd.runtime.agentic.errors:ProviderError": "wynd.runtime.providers.types:ProviderError",
    "wynd.controller.models:Usage": "wynd.runtime.usage:Usage",
    "wynd.controller.models:JobUsage": "wynd.process.jobs:JobUsage",
    "wynd.controller.models:Integration": "wynd.process.git:IntegrationResult",
    "wynd.controller.models:ProcessError": "wynd.spec.records:ProcessError",
    **{
        f"wynd.runtime.edges:{name}": f"wynd.runtime.executor.edges:{name}"
        for name in (
            "EdgeVerdict", "EdgeCheckCall", "EdgeCheckResult", "EdgeCheckCause", "EdgeCheckError", "EdgeChecker",
            "make_edge_check_call",
        )
    },
}


@pytest.mark.parametrize("ref", SAME_OBJECT)
def test_single_definition(ref):
    assert resolve(ref) is resolve(SAME_OBJECT[ref])


# --- exceptions -------------------------------------------------------------------------------------------------------

def test_provider_error_attributes():
    cls = resolve("wynd.runtime.providers.types:ProviderError")
    err = cls("down", kind="transport", retryable=True)
    assert (err.kind, err.retryable, err.tool_called, err.status) == ("transport", True, False, None)
    kinds = typing.get_type_hints(cls.__init__)["kind"]
    assert typing.get_args(kinds) == ("transport", "auth", "unavailable", "invalid_request", "refusal", "max_turns")


def test_edge_check_error_attributes():
    err = resolve("wynd.runtime.executor.edges:EdgeCheckError")("timeout", "took too long")
    assert (err.cause, err.message) == ("timeout", "took too long")


def test_step_failure_attributes():
    err = resolve("wynd.runtime.errors:StepFailure")("config", "no provider", attempts=2)
    assert (err.cause, err.message, err.partial_outputs, err.usage, err.attempts) == (
        "config", "no provider", None, None, 2,
    )


# (class, base, code, http, exit): $DRAFTS/06 §5.2 with PLAN §8.1's EnvMissing 412.
CONTROLLER_ERROR_TABLE = [
    ("WyndError", "Exception", "error", 500, 1),
    ("Invalid", "WyndError", "invalid", 422, 2),
    ("ValidationFailed", "WyndError", "validation_failed", 422, 1),
    ("Unauthorized", "WyndError", "unauthorized", 401, 3),
    ("OutOfScope", "WyndError", "out_of_scope", 403, 3),
    ("NotFound", "WyndError", "not_found", 404, 3),
    ("NotAWorkspace", "NotFound", "not_a_workspace", 404, 3),
    ("Conflict", "WyndError", "conflict", 409, 3),
    ("DirtyTree", "Conflict", "dirty_tree", 409, 3),
    ("RevisionConflict", "Conflict", "revision_conflict", 409, 3),
    ("NotBuilt", "Conflict", "not_built", 409, 3),
    ("EnvMissing", "Conflict", "env_missing", 412, 3),
    ("EnvUnbound", "Conflict", "env_unbound", 409, 3),
    ("JobState", "Conflict", "job_state", 409, 3),
    ("DetachedHead", "Conflict", "detached_head", 409, 3),
    ("TurnInProgress", "Conflict", "turn_in_progress", 409, 3),
    ("VersionMismatch", "Conflict", "version_mismatch", 409, 3),
    ("DesignLocked", "WyndError", "design_locked", 423, 3),
    ("Unavailable", "WyndError", "unavailable", 503, 3),
]


def test_controller_error_hierarchy():
    errors = importlib.import_module("wynd.controller.errors")
    actual = [
        (name, getattr(errors, name).__bases__[0].__name__, getattr(errors, name).code, getattr(errors, name).http,
         getattr(errors, name).exit)
        for name, *_ in CONTROLLER_ERROR_TABLE
    ]
    assert actual == CONTROLLER_ERROR_TABLE


def test_wynd_error_stores_message_details_hint():
    err = resolve("wynd.controller.errors:DirtyTree")("dirty", details={"paths": ["a"]}, hint="commit first")
    assert (str(err), err.message, err.details, err.hint) == ("dirty", "dirty", {"paths": ["a"]}, "commit first")


# --- controller and cli skeleton (§8, §9) -----------------------------------------------------------------------------

SERVICES = (
    "wynd.controller.processes:ProcessService",
    "wynd.controller.design:DesignService",
    "wynd.controller.jobs.service:JobService",
    "wynd.controller.runs.service:RunService",
    "wynd.controller.envcheck:EnvService",
    "wynd.controller.serving:ServeService",
    "wynd.controller.uploads:UploadService",
    "wynd.controller.registries:RegistryService",
    "wynd.controller.releases.service:ReleaseService",
    "wynd.controller.chat.engine:ChatService",
)


@pytest.mark.parametrize("ref", SERVICES)
def test_service_is_constructed_from_ctx_and_ctl(ref):
    params = [p for p in inspect.signature(resolve(ref).__init__).parameters.values() if p.name != "self"]
    assert [p.name for p in params[:2]] == ["ctx", "ctl"]
    assert [p.name for p in params[2:] if p.default is inspect.Parameter.empty] == []


def test_controller_context_is_frozen_with_lazy_backends():
    cls = resolve("wynd.controller.controller:ControllerContext")
    assert cls.__dataclass_params__.frozen
    assert isinstance(vars(cls)["serving"], functools.cached_property)
    assert isinstance(vars(cls)["triggers"], functools.cached_property)


def test_optimise_router_is_an_api_router():
    from fastapi import APIRouter

    assert isinstance(resolve("wynd.controller.optimise:router"), APIRouter)


@pytest.mark.parametrize("module", [m for m in MODULES if m.startswith("wynd.cli.commands.")])
def test_cli_command_module_registers(module):
    assert shape(resolve(f"{module}:register")) == "app"


def test_cli_root_app_help_and_version():
    import typer
    from typer.testing import CliRunner

    app = resolve("wynd.cli.main:app")
    assert isinstance(app, typer.Typer)
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    assert "--workspace" in result.output
    assert "--version" in result.output
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert resolve("wynd.cli:__version__") in result.output
