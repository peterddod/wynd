"""wynd.spec: document formats, type language, runtime records and hashing (the shared leaf).

Public names are re-exported lazily: a leaf module is imported on first access, so a broken leaf only breaks its own
importers. Expression-language names live in `wynd.spec.expr`.
"""

import importlib

__version__ = "0.1.0"

_MODULES = {
    ".base": (
        "SpecModel", "Name", "StepKey", "ExitName", "FieldName", "EnvName", "Alias", "ProcessId", "BranchName",
        "HashStr", "DEFAULT_PROVIDER", "DEFAULT_TIER", "DEFAULT_THINKING", "DEFAULT_MAX_TRAVERSALS", "TIERS",
        "RESERVED_EXIT", "EXIT_TARGET_PREFIX", "IGNORE_TARGET", "EDGE_KINDS", "PROTO_TYPES", "BASES", "LATENCIES",
        "LIMIT_FIELDS",
    ),
    ".errors": ("Severity", "Loc", "Diagnostic", "SpecError", "format_loc"),
    ".yamlio": (
        "WyndLoader", "Mark", "SourceMap", "parse_yaml", "read_yaml", "parse_model", "load_model", "dump_yaml",
        "yaml_to_json",
    ),
    ".typelang": (
        "TypeNode", "TScalar", "TList", "TObject", "parse_type", "render_type", "TypeSpec", "type_schema",
        "fields_schema", "ModelSet", "build_models", "normalise_outputs", "describe_type", "describe_fields",
        "infer_fields",
    ),
    ".schemas": ("normalize_schema", "schema_at", "is_nullable", "strip_null", "ANY", "MISSING"),
    ".interface": (
        "Interface", "split_output", "output_adapter", "interface_from_models", "interface_from_fields",
        "interfaces_equivalent",
    ),
    ".workspace": (
        "WorkspaceConfig", "StepRoot", "load_workspace_config", "WORKSPACE_FILE", "PROCESS_FILE", "PROTO_DIR",
        "STEPS_DIR", "STEP_PROTO_FILE", "STEP_LOCK_FILE", "EDGES_LOCK_FILE", "PROCESS_LOCK_FILE", "ENV_MANIFEST_FILE",
        "STATE_DIR", "CASSETTES_DIR", "UseRef", "parse_use", "find_workspace_root", "RESERVED_PROCESS_SEGMENTS",
        "local_step_id", "root_step_id", "step_module_name", "slug",
    ),
    ".fragments": ("EnvVar", "EnvGroup", "Mode", "EnvFragment", "merge_env_vars", "merge_fragments"),
    ".proto_step": ("Example", "ProtoStep", "load_proto_step", "check_proto_step"),
    ".process_doc": (
        "ProcessEnv", "StepRef", "RetryOverride", "Limits", "Branch", "Edge", "FinallyStep", "ProcessDoc", "is_else",
        "ExprSite", "expression_sites", "site_position", "load_process", "check_process_doc",
    ),
    ".lockfiles": (
        "StepKind", "TraceStepKind", "HARNESS_BUILTINS", "Tier", "Thinking", "Effect", "RetryPolicy",
        "DEFAULT_RETRIES", "effective_retries", "ToolSnapshot", "McpToolSnapshot", "McpSnapshot", "ShellLock",
        "StepLock", "EdgeLockEntry", "EdgesLock", "branch_key", "check_hash", "BaseChoice", "FragmentRecord",
        "LockedWheel", "LockedVenv", "ProcessLock", "load_step_lock", "load_edges_lock", "load_process_lock",
        "dump_lock",
    ),
    ".plan": ("PlanVenv", "PlanStep", "PlanNode", "PlanProcess", "RunPlan"),
    ".env_manifest": ("EnvManifest", "EnvCheck", "check_env", "load_env_manifest"),
    ".records": (
        "Summary", "StepErrorCause", "StepError", "ProcessErrorCause", "TracePointer", "ProcessError",
        "STEP_ERROR_SCHEMA",
    ),
    ".context": ("ContextRef", "parse_context_entry"),
    ".hashing": (
        "jsonable", "canonical_json", "hash_bytes", "hash_obj", "proto_hash", "interface_hash",
        "normalize_requirement", "dependency_set_hash",
    ),
}
_EXPORTS = {name: module for module, names in _MODULES.items() for name in names}
__all__ = list(_EXPORTS)


def __getattr__(name: str):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_EXPORTS})
