# Wynd design 01: the `wynd.spec` package (shared leaf)

Package `packages/spec`, distribution `wynd-spec`, import path `wynd.spec`, licence Apache-2.0. It is installed in every image venv. Dependencies are `pydantic>=2`, `pyyaml>=6` and `lark>=1.2` only; all three have musllinux wheels or are pure Python, so the package also works on the Alpine base.

`wynd.spec` owns these things:

- every YAML/JSON document format;
- the type mini-language and the dynamic pydantic models built from it;
- the step-interface (JSON Schema) snapshot helpers;
- the expression language: grammar, evaluator and static analysis;
- the small runtime records (`Summary`, `StepError`, `ProcessError`) that other formats embed;
- canonical serialisation and hashing.

It performs no I/O beyond reading a file it is given or walking a directory it is given. It never touches the network, Docker, git or an LLM.

The spikes live under `.../scratchpad/spikes/`:

- `ns/` is the packaging spike.
- `expr/grammar.lark`, `expr/expr.py` and `expr/test_expr.py` hold the grammar, a reference evaluator and the reference extractor, with 126 passing cases.
- `expr/yamltypes.py` holds the YAML-with-marks loader, the type language and the dynamic models.

The implementer should start from these files. They are verified, and every behaviour in §7 is pinned by `test_expr.py`.

---

## 1. Spec clauses this package is responsible for

| Clause | What `wynd.spec` provides |
|---|---|
| 3.1 | `Output` is a discriminated union with one model per exit and `exit: Literal[...]`; the plain-model shorthand must be `done`. Provided by `split_output`, `output_adapter`, `interface_from_models`, and `build_models` for YAML-declared shapes. |
| 3.2 (ProcessStep) | `use: process:<id>` parsing (`parse_use`). A process's `interface()` is the ProcessStep contract. Process docs expose `env.base`, `provider` and `latency` so the validator can warn when a child's values differ from the parent's. |
| 3.3 | `ProcessDoc`: `inputs`, `outputs` (nested by exit), `examples`, `entry`. There is no `$entry`. The by-name entry-binding check itself belongs to `process`. |
| 3.4 | `Edge`/`Branch`/`Limits` models covering `from`, `to` (shorthand and list), `when`, `with`, `limits`, branch `name` and `kind`. Also the loc→line mapping for diagnostics. |
| 3.4.1 | The whole expression language: Lark grammar, tree-walking evaluator (no `eval`), references, counters, builtins, reference-validity analysis (M1) and type inference (M3). |
| 3.5 | The implicit `error` exit is reserved. `$exit.error` is always a valid target. `ProcessError`, `StepError` and `ProcessErrorOutput` records. `RetryPolicy` in the lockfile with per-kind defaults and a per-branch `limits.retries` override. `on_error` and `finally` fields. |
| 3.6 step 5 / 3.7 | The `Summary` record shape. Parsing and validation of `context` entries (`parse_context_entry`). |
| 3.8 | Tool snapshot fields (`effects`, `idempotent`, `env`) and MCP snapshots (`allow` required, tool schemas, snapshot hash). |
| 3.9 | Lock fields `provider`, `tier` and `thinking`; tier names; the process-level provider default (`claude-code`); env fragments for providers (`EnvFragment`). |
| 4 | `EnvFragment` (`deps`, `system`, `requires: glibc`, `vars`); `env.base`; the base choice and reason plus the `wynd-base` version in `process.lock.yaml`; the env manifest model; `check_env` (used by `wynd env check`, and runnable in-image as a startup probe). |
| 5.1 | `wynd.yaml` (`process_roots`, `step_roots`, reserved `url:`); layout file-name constants; the three `use:` forms; `find_workspace_root`. |
| 6.1 | `ProtoStep`: flat vs nested outputs, `exits`, `exit_codes`, examples, `env`. |
| 6.2 | `ProcessDoc`, including every construct in the example. |
| 6.3 | `StepLock` (`step.lock.yaml`). |
| 6.4 | `ProcessLock` (`process.lock.yaml`) and `EnvManifest` (`process.env.yaml`). |
| 6.5 | `proto_hash` (the recompile trigger); `step_hash` and `process_hash` (RunRegistry keys; parent hashes include child hashes). |
| 7 (cassettes) | `canonical_json`, `hash_obj`, `normalise_volatile`; the evaluator records the volatile values it produces. |
| 8 (loader) | "parse YAML via spec": `load_*` functions with line-numbered diagnostics. |
| 9 (schema inference, plain language) | `infer_fields` (M3) and `describe_type`/`describe_fields`. |
| 13 | No `eval`; `Output` is a union; knobs live in lockfiles; tier-to-model mapping never appears in a lockfile (the lock models have no model-id field). |
| 15 | Apache-2.0 LICENSE and metadata. |

---

## 2. Packaging (verified by spike)

The spike at `scratchpad/spikes/ns` confirmed the following with uv 0.10.7 and uv-managed CPython 3.12.12:

- `uv sync` installs both members editable (through `_editable_impl_wynd_spec.pth`, which points at `packages/spec/src`).
- `import wynd.spec, wynd.runtime` works, and `wynd.__path__` is a `_NamespacePath` spanning both `src/wynd` directories.
- Source edits show up without re-syncing.
- The console script `wynd-supervisor` runs.
- Built wheels contain only `wynd/spec/...` or `wynd/runtime/...`, plus `dist-info/licenses/LICENSE` and `License-Expression: Apache-2.0`.
- Non-editable wheel installs into a fresh venv also import correctly.
- An exact pin `wynd-spec==0.1.0` between workspace members resolves.

### 2.1 Root `pyproject.toml` (workspace, virtual)

```toml
[project]
name = "wynd-workspace"
version = "0.0.0"
requires-python = ">=3.12"
dependencies = [
  "wynd-spec", "wynd-runtime", "wynd-process", "wynd-compiler",
  "wynd-controller", "wynd-cli", "wynd-kube",
]

[dependency-groups]
dev = ["pytest>=8"]

[tool.uv]
package = false

[tool.uv.sources]
wynd-spec = { workspace = true }
wynd-runtime = { workspace = true }
# ... one line per member

[tool.uv.workspace]
members = ["packages/*"]
exclude = ["packages/web"]        # REQUIRED: uv errors on a glob member without pyproject.toml

[tool.pytest.ini_options]
addopts = ["--import-mode=importlib"]   # REQUIRED: tests/test_*.py basenames collide across packages
testpaths = ["packages"]               # NOT examples/: compiled-step tests import step deps (pypdf, ...) that
                                       # exist only in their venvs; they run via `wynd test`, which integration
                                       # tests in packages/cli drive against examples/invoices
markers = [
  "live: needs real LLM access (set WYND_LIVE=1)",
  "docker: needs Docker/BuildKit (set WYND_DOCKER=1)",
]
```

Put `.python-version` containing `3.12` at the root.

### 2.2 `packages/spec/pyproject.toml`

```toml
[project]
name = "wynd-spec"
version = "0.1.0"                  # lockstep with wynd-runtime and wynd-base
description = "Wynd document formats, expression language and hashing"
requires-python = ">=3.12"
license = "Apache-2.0"
license-files = ["LICENSE"]
dependencies = ["pydantic>=2.7", "pyyaml>=6", "lark>=1.2"]

[build-system]
requires = ["hatchling>=1.26"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/wynd"]            # REQUIRED: auto-detection looks for src/wynd_spec and fails
```

Other members use the same pattern. For example, `packages/runtime/pyproject.toml` carries `dependencies = ["wynd-spec==0.1.0"]`, `[tool.uv.sources] wynd-spec = { workspace = true }` and `[project.scripts] wynd-supervisor = "wynd.runtime.supervisor:main"`.

**Rules for every package.** There must be no `src/wynd/__init__.py`, and no `wynd/__init__.py` in any wheel. A stray one shadows every other member. Each package gets a test that asserts `not (Path(wynd.spec.__file__).parents[1] / "__init__.py").exists()`, with the path adjusted per package.

### 2.3 Root `conftest.py` (verified)

```python
import os
import pytest

_GATES = {"live": "WYND_LIVE", "docker": "WYND_DOCKER"}

def pytest_collection_modifyitems(config, items):
    for item in items:
        for marker, var in _GATES.items():
            if marker in item.keywords and os.environ.get(var) != "1":
                item.add_marker(pytest.mark.skip(reason=f"set {var}=1 to run"))
```

### 2.4 Dependency-direction check (verified)

`uv run --isolated --package wynd-spec --with pytest pytest packages/spec` runs the spec tests in an environment containing only `wynd-spec` and its dependencies. An accidental `import wynd.runtime` then fails. Recommend one such line per package in CI (`scripts/check.sh`).

As a cheap static guard, `tests/test_leaf.py` parses every module under `src/wynd/spec` with `ast` and fails on imports of any `wynd.*` other than `wynd.spec`, and on any non-stdlib import outside {pydantic, pydantic_core, yaml, lark, typing_extensions}.

---

## 3. Module map

```
packages/spec/
  pyproject.toml  LICENSE (Apache-2.0 text)  README.md
  src/wynd/spec/
    __init__.py        curated re-exports of the public API (§9) + __version__ (importlib.metadata)
    base.py            SpecModel base class; identifier types; defaults/constants (DEFAULT_PROVIDER, TIERS, ...)
    errors.py          Diagnostic, SpecError, format_loc
    yamlio.py          WyndLoader (YAML 1.2 booleans, duplicate-key detection), parse_yaml/read_yaml -> (data, SourceMap),
                       SourceMap, load_model/parse_model (pydantic errors -> located Diagnostics), dump_yaml
    typelang.py        type mini-language: TypeNode, parse_type, render_type, type_schema, fields_schema,
                       build_models/ModelSet, normalise_outputs, describe_type/describe_fields, infer_fields (M3)
    schemas.py         JSON Schema helpers: normalize_schema, schema_at (path walk), nullable/strip_null,
                       check_assignable (M3)
    interface.py       Interface model, split_output, output_adapter, interface_from_models, interface_from_fields,
                       interfaces_equivalent (M3)
    workspace.py       WorkspaceConfig, StepRoot, layout constants, UseRef/parse_use, find_workspace_root
    fragments.py       EnvVar, EnvFragment, merge_env_vars, merge_fragments
    proto_step.py      Example, ProtoStep, load_proto_step, check_proto_step
    process_doc.py     ProcessEnv, StepRef, RetryOverride, Limits, Branch, Edge, FinallyStep, ProcessDoc,
                       ExprSite/expression_sites, load_process, check_process_doc
    lockfiles.py       RetryPolicy (+DEFAULT_RETRIES, effective_retries), ToolSnapshot, McpToolSnapshot, McpSnapshot,
                       ShellLock, StepLock, EdgeLock, EdgesLock, WyndBase, BaseChoice, LockedUnit, LockedProcess,
                       LockedPackage, VenvPlan, ProcessLock, load_* helpers
    env_manifest.py    EnvManifest, EnvCheck, check_env
    records.py         Summary, StepError, ProcessError, ProcessErrorOutput, STEP_ERROR_SCHEMA
    context.py         ContextRef, parse_context_entry
    hashing.py         jsonable, canonical_json, hash_bytes, hash_obj, proto_hash, step_hash, process_hash,
                       interface_hash, normalise_requirement, dependency_set_hash, normalise_volatile
    expr/
      __init__.py      public expression API (re-exports)
      grammar.py       GRAMMAR (verbatim §7.1) + module-level Lark LALR parser
      nodes.py         AST dataclasses, Lark->AST transformer, string-literal unescaping
      errors.py        ExprError, ExprSyntaxError, EvalError
      scope.py         StepState, EdgeState, Scope, new_scope, utc_now
      evaluator.py     parse (lru_cache), evaluate, evaluate_condition, evaluate_value, evaluate_with, truthy,
                       strict_eq, BUILTINS/ARITY
      analysis.py      Ref, references, value_references, env_names, check_expression, StepView, TypeEnv,
                       check_references (M1)
      infer.py         infer_type (M3)
  tests/               see §12
```

The module is called `process_doc.py`, not `process.py`, so that `wynd.spec.process` is not confused with the `wynd.process` package.

---

## 4. Foundations

### 4.1 `SpecModel` and identifiers (`base.py`)

```python
class SpecModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    _source: "SourceMap | None" = PrivateAttr(default=None)

    @property
    def source(self) -> "SourceMap | None": ...     # set by load_model; None for in-code instances
```

All document models subclass `SpecModel`. `extra="forbid"` means a YAML typo is reported as an unknown field.

| Type alias (`Annotated[str, StringConstraints(pattern=...)]`) | Pattern | Used for | Extra rule (validator) |
|---|---|---|---|
| `Name` | `^[a-z][a-z0-9_]*$` | proto-step `name`, process `name`, step package dir | |
| `StepKey` | `^[a-z_][a-z0-9_]*$` | keys of process `steps:` | |
| `ExitName` | `^[a-z_][a-z0-9_]*$` | exits | `error` is rejected where exits are *declared* |
| `FieldName` | `^[A-Za-z_][A-Za-z0-9_]*$` | input/output fields | not a Python keyword, not `exit`, no leading `_`, no `model_` prefix |
| `EnvName` | `^[A-Za-z_][A-Za-z0-9_]*$` | env vars | |
| `Alias` | `^[a-z][a-z0-9_-]*$` | step-root alias | `!= "process"` |
| `ProcessId` | `^[A-Za-z0-9_][A-Za-z0-9_-]*(/[A-Za-z0-9_][A-Za-z0-9_-]*)*$` | process ids (relative path under a process root) | |
| `BranchName` | `^[A-Za-z_][A-Za-z0-9_]*$` | branch `name:` | |
| `HashStr` | `^sha256:[0-9a-f]{64}$` | all hashes | |

Constants:

```python
DEFAULT_PROVIDER = "claude-code"
DEFAULT_TIER = "cheap"
DEFAULT_THINKING = "low"
DEFAULT_MAX_TRAVERSALS = 10
TIERS = ("cheap", "standard", "strong")
RESERVED_EXIT = "error"
EXIT_TARGET_PREFIX = "$exit."
```

### 4.2 Diagnostics (`errors.py`)

```python
Severity = Literal["error", "warning"]
Loc = tuple[str | int, ...]

@dataclass(frozen=True)
class Diagnostic:
    severity: Severity
    code: str                      # stable, e.g. "E-EXPR-SYNTAX"; tests assert on codes, not prose
    message: str
    file: str | None = None
    line: int | None = None        # 1-based
    column: int | None = None      # 1-based
    loc: Loc = ()                  # document path, e.g. ("edges", 3, "to", 0, "with", "dest")
    snippet: str | None = None     # optional extra lines (expression + caret)

    def format(self) -> str: ...
    def located(self, source: "SourceMap | None", loc: Loc,
                expr_line: int | None = None, expr_col: int | None = None) -> "Diagnostic": ...

class SpecError(ValueError):
    def __init__(self, diagnostics: list[Diagnostic]): ...
    diagnostics: list[Diagnostic]  # str(err) == "\n".join(d.format() for d in diagnostics)

def format_loc(loc: Loc) -> str    # ("edges",3,"to",0,"with","dest") -> "edges[3].to[0].with.dest"
```

`format()` produces `"{file}:{line}:{column}: {severity}[{code}] {format_loc(loc)}: {message}"`, dropping any part that is absent. When a snippet exists it follows on the next lines, indented four spaces. For example:

```
processes/process_supplier_invoice/process.yaml:47:17: error[E-EXPR-SYNTAX] edges[3].to[0].with.dest: unexpected 'elze'; expected an operator, 'elif' or 'else' (expression 1:62)
    if steps.validate.outputs.fields.total > 10000 then env.REVIEW_DIR elze env.RECORDS_DIR
                                                                       ^
```

### 4.3 YAML I/O (`yamlio.py`), algorithm verified by spike

```python
class WyndLoader(yaml.SafeLoader): ...   # YAML 1.2 core booleans only: true/True/TRUE/false/False/FALSE
                                         # (yes/no/on/off/y/n stay strings; verified)

@dataclass(frozen=True)
class Mark:
    line: int
    column: int
    style: str | None                    # PyYAML scalar style: None (plain), "'", '"', "|", ">"

@dataclass
class SourceMap:
    file: str
    marks: dict[Loc, Mark]
    def position(self, loc: Loc) -> Mark | None: ...                        # longest matching prefix
    def expr_position(self, loc: Loc, expr_line: int, expr_col: int) -> tuple[int, int, bool]: ...
        # -> (line, col, exact). exact when the scalar is plain or single-quoted/double-quoted on one line
        #    and expr_line == 1: col = mark.column + expr_col - 1 (+1 for quoted). Otherwise the scalar start,
        #    and the message carries "(expression L:C)"

def parse_yaml(text: str, file: str = "<string>") -> tuple[Any, SourceMap]
def read_yaml(path: Path) -> tuple[Any, SourceMap]                           # UTF-8; file = str(path)
def parse_model(text: str, model: type[M], file: str = "<string>") -> M     # raises SpecError
def load_model(path: Path, model: type[M]) -> M                             # raises SpecError; sets ._source
def dump_yaml(data: Any) -> str
```

**Loading algorithm.**

1. `yaml.compose(text, Loader=WyndLoader)`. On `MarkedYAMLError`, return `E-YAML` with `problem_mark`. When the problem character is `?`, `[` or `]`, append a hint: *"inside `{ }` or `[ ]` flow collections, quote types such as `"date?"` or `"list[string]"`; block style needs no quotes"*. The spike showed that PyYAML rejects `{x: date?}` and `{x: list[string]}`.
2. Walk the node tree. Construct each key and scalar with `loader.construct_object(node, deep=True)`. Record `marks[loc] = Mark(start_mark.line+1, start_mark.column+1, node.style)` for every node. A duplicate key in a mapping is `E-YAML-DUP` at the second key's position.
3. A document root that is not a mapping is `E-YAML-ROOT`.
4. `model.model_validate(data)`. Map each pydantic error to a `Diagnostic` with `code="E-SCHEMA"`, `loc=err["loc"]` and its position from `SourceMap.position`. Messages by pydantic error type:
   - `extra_forbidden` becomes ``unknown field 'whn' (did you mean 'when'?)``. The suggestion comes from `difflib.get_close_matches` against the field names of the model class at `loc[:-1]`, resolved by walking annotations through `dict`/`list`/`Union`. When the walk fails, there is no suggestion.
   - `missing` becomes ``missing required field 'entry'``.
   - `literal_error` on `kind` becomes ``kind must be 'process'`` (code `E-KIND`).
   - `string_pattern_mismatch` becomes ``'Read' is not a valid step name (lowercase letters, digits and _)``, with the wording chosen per identifier type.
   - Everything else keeps pydantic's `msg`.
   - Errors raised by the type-language `BeforeValidator` are coded `E-TYPE`; the `ValueError` text becomes the message.
5. Run model-specific post-load checks that must fail loading: expression syntax for process docs (§6.4) and context-entry syntax for step locks. They go through the same `Diagnostic` path.
6. Set `model._source = source_map`.

**Dumping.** `dump_yaml(data)` uses a `SafeDumper` subclass with `sort_keys=False`, `default_flow_style=False`, `allow_unicode=True` and `width=10**9`, so expressions are never re-wrapped. Strings containing `\n` are written in literal block style `|`. Empty mappings and lists are written as `{}` and `[]`. Callers pass a plain `dict`: lockfiles pass `model.model_dump(mode="json", by_alias=True, exclude_none=True)`, and authored docs pass `doc.to_authoring()` (§6.3 and §6.4).

PyYAML discards comments, so any tool that rewrites `process.yaml` loses them. See §13.

---

## 5. Type mini-language (`typelang.py`)

### 5.1 Syntax

A type is one of three YAML shapes:

- a **string** in the grammar below;
- a **mapping**, meaning a nested object whose values are types;
- a **one-item sequence** `[T]`, meaning a list of `T` where `T` may be a mapping. Use this for lists of records such as line items.

```
type_string := base "?"?
base        := scalar | "list[" type_string "]"
scalar      := "string" | "number" | "integer" | "boolean" | "date" | "datetime" | "path" | "object"
```

- Whitespace inside the brackets is allowed (`list[ string ]`). The canonical rendering has none.
- `?` makes the field optional: it may be absent or null, its default is `None`, and it is not in `required`. Items can be optional too, as in `list[date?]`.
- Nested mappings and the `[T]` sequence form cannot carry `?`. Use `object?` or `list[...]?` for an optional collection. This is an interpretation; see §11.
- `{}` as a field type is a closed empty object. As an exit's output shape it means "no fields".

```python
@dataclass(frozen=True)
class TScalar: name: str; optional: bool = False
@dataclass(frozen=True)
class TList:   item: "TypeNode"; optional: bool = False
@dataclass(frozen=True)
class TObject: fields: tuple[tuple[str, "TypeNode"], ...]; optional: bool = False
TypeNode = TScalar | TList | TObject

def parse_type(spec: str | Mapping | Sequence) -> TypeNode     # ValueError with a precise message
def render_type(node: TypeNode) -> str | dict | list           # canonical YAML form (inverse of parse_type)
TypeSpec = Annotated[Any, BeforeValidator(parse_type), PlainSerializer(render_type),
                     WithJsonSchema({"type": ["string", "object", "array"]})]
```

Error messages, all pinned by tests:

- `unknown type 'strng' (expected one of boolean, date, datetime, integer, number, object, path, string, list[...])`
- `invalid type 'list[string': missing ']'`
- `invalid type 'string??': unexpected '?'`
- `a list type is written as a one-item list: [ <type> ]`
- `expected a type name, got 5`

### 5.2 Mapping

| Type | JSON Schema (`type_schema`) | Python (`build_models`) | Plain language (`describe_type`) |
|---|---|---|---|
| `string` | `{"type":"string"}` | `str` | text |
| `number` | `{"type":"number"}` | `float` | a number |
| `integer` | `{"type":"integer"}` | `int` | a whole number |
| `boolean` | `{"type":"boolean"}` | `bool` | yes/no |
| `date` | `{"type":"string","format":"date"}` | `datetime.date` | a date (YYYY-MM-DD) |
| `datetime` | `{"type":"string","format":"date-time"}` | `datetime.datetime` | a date and time |
| `path` | `{"type":"string","format":"path"}` | `pathlib.Path` | a file path |
| `object` | `{"type":"object"}` (open) | `dict[str, Any]` | a set of named values |
| `list[T]` / `[T]` | `{"type":"array","items":S(T)}` | `list[P(T)]` | a list of <T> |
| mapping | `{"type":"object","properties":{...},"required":[...],"additionalProperties":false}` | generated model `<Parent><Field>` (list items: `<Parent><Field>Item`), `extra="forbid"` | a group with fields a, b, c |
| `T?` | `{"anyOf":[S(T),{"type":"null"}]}` and not required | `Optional[P(T)] = None` | … (optional) |

These formats match what pydantic emits for `date`, `datetime` and `Path`. The spike checked this, so schemas derived from YAML and schemas derived from code compare cleanly after `normalize_schema`.

```python
def type_schema(node: TypeNode) -> dict
def fields_schema(fields: Mapping[str, TypeNode], exit: str | None = None) -> dict
    # object schema; with exit: properties start with {"exit": {"const": exit}} and "exit" is first in required

@dataclass(frozen=True)
class ModelSet:
    input: type[BaseModel]                       # <Pascal(name)>Input, extra="forbid"
    outputs: dict[str, type[BaseModel]]          # exit -> <Pascal(name)><Pascal(exit)> with exit: Literal[exit] = exit
    output: Any                                  # single model, or Annotated[Union[...], Field(discriminator="exit")]
    adapter: TypeAdapter                         # validates {"exit": ..., **fields} for any declared exit

def build_models(name: str, inputs: Mapping[str, TypeNode],
                 outputs: Mapping[str, Mapping[str, TypeNode]]) -> ModelSet
```

Generated models use pydantic lax mode, which is the default. JSON strings become `date`, ints become `float`, and `"12.5"` becomes `12.5`. This is intended: values arrive as JSON envelopes.

### 5.3 Normalising `outputs` and `exits` (proto-steps and processes)

```python
def normalise_outputs(outputs: Mapping | None, exits: Sequence[str] | None
                      ) -> tuple[list[str], dict[str, dict[str, Any]]]   # raises ValueError (E-OUTPUTS)
```

The function runs as a `model_validator(mode="before")` on both `ProtoStep` and `ProcessDoc`. The raw values of the result are then parsed by `TypeSpec`.

| `exits:` | `outputs:` | Result |
|---|---|---|
| absent | absent or `{}` | `exits=[done]`, `done: {}` |
| absent | has a `done` key **and** every value is a mapping | nested by exit; `exits` = keys in author order |
| absent | anything else | flat: `exits=[done]`, `done: <outputs>`. If every value is a mapping but there is no `done` key, add warning `W-OUTPUTS-AMBIGUOUS`: "outputs looks nested by exit but has no `done`; declare `exits:` to nest" (reported by `check_*`). |
| declared | absent or `{}` | every exit gets `{}` |
| declared | keys ⊆ exits and every value is a mapping | nested; exits without a key get `{}`; order follows `exits` |
| declared, exactly `[done]` | no key is an exit name | flat: `done: <outputs>` |
| declared | anything else | `E-OUTPUTS`. If the mapping mixes exit names with field names: "outputs mixes exit names (done) with field names (total)". Otherwise: "outputs keys a, b are not declared exits; nest outputs by exit (done: {...}) — a flat mapping means a done-only step" |

In all cases:

- `exits` must be non-empty and unique.
- `error` in `exits` or as an `outputs` key is `E-OUTPUTS`: "`error` is implicit on every step/process and cannot be declared".
- After normalisation the model always holds `exits: list[ExitName]` and `outputs: dict[ExitName, dict[FieldName, TypeNode]]` with exactly one entry per exit.

### 5.4 Schema inference from example values (M3, `infer_fields`)

```python
def infer_fields(samples: Sequence[Mapping[str, Any]]) -> dict[str, TypeNode]
```

This is a deterministic baseline that the compiler shows to the user in plain language. Per field, it unifies the values across all samples:

- `bool` becomes boolean; `int` becomes integer; `float`, or a mix of `int` and `float`, becomes number.
- A `datetime.date` or a string matching `^\d{4}-\d{2}-\d{2}$` becomes date.
- A `datetime.datetime` or a string matching `^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$` becomes datetime.
- Other strings become string. `path` is never inferred.
- A `dict` becomes a nested mapping, with fields unified recursively.
- A `list` becomes `[unify(items)]`, or `list[string]` when every sample list is empty.
- `None`, or a field missing from some samples, marks the field optional.
- Conflicting scalar kinds fall back to string. A scalar mixed with a container falls back to `object`.

`describe_fields(fields) -> list[str]` renders lines such as `"due_date — a date (YYYY-MM-DD)"`, `"tags — a list of text (optional)"`.

---

## 6. Document models

Every model below is a `SpecModel`: unknown keys are errors, and YAML keys that are Python keywords use aliases (`from`, `with`, `finally`). Loaders:

```python
def load_workspace_config(path: Path) -> WorkspaceConfig
def load_proto_step(path: Path) -> ProtoStep
def load_process(path: Path) -> ProcessDoc          # also runs expression-syntax checks (fails load)
def load_step_lock(path: Path) -> StepLock
def load_edges_lock(path: Path) -> EdgesLock
def load_process_lock(path: Path) -> ProcessLock
def load_env_manifest(path: Path) -> EnvManifest
```

### 6.1 `wynd.yaml` (`workspace.py`)

```python
class StepRoot(SpecModel):
    path: str | None = None
    url: str | None = None              # reserved (5.1). v1: any value -> E-ROOTS "url: roots are reserved; v1 resolves local paths only"

class WorkspaceConfig(SpecModel):
    process_roots: list[str] = ["processes"]
    step_roots: dict[Alias, StepRoot] = {}       # "alias: path" shorthand is accepted (before-validator)
    cassette_warn_mb: float = 5.0                # compile job warns above this per step (6.5)
    def to_authoring(self) -> dict               # step roots with only `path` are written as shorthand
```

Rules (`E-ROOTS`):

- Every root path is relative POSIX with no `..`, no leading `/` and no trailing `/`. It is not `.wynd` and not inside `.wynd`.
- No two roots are equal or nested inside one another, across process roots and step roots alike.
- The alias `process` is reserved, because `process:<id>` is a `use:` form.

```yaml
# wynd.yaml
process_roots:
  - processes
step_roots:
  shared: shared/steps
  finance: teams/finance/steps
  vendor: { path: vendor/steps }
cassette_warn_mb: 5
```

Layout constants, used by process, compiler, controller and cli:

```python
WORKSPACE_FILE = "wynd.yaml";  PROCESS_FILE = "process.yaml";  PROTO_DIR = "proto";  STEPS_DIR = "steps"
STEP_PROTO_FILE = "proto.yaml"           # proto-step inside a step-root package dir (5.1 "or a proto-step YAML")
STEP_LOCK_FILE = "step.lock.yaml";  EDGES_LOCK_FILE = "edges.lock.yaml"
PROCESS_LOCK_FILE = "process.lock.yaml";  ENV_MANIFEST_FILE = "process.env.yaml"
STATE_DIR = ".wynd";  CASSETTES_DIR = "cassettes"

def find_workspace_root(start: Path) -> Path | None     # nearest ancestor (inclusive) containing wynd.yaml
```

Process-local proto-steps live at `processes/<id>/proto/<name>.yaml`, and their compiled packages at `processes/<id>/steps/<name>/`.

**`use:` forms** (5.1: exactly three):

```python
@dataclass(frozen=True)
class UseRef:
    form: Literal["local", "root", "process"]
    target: str                  # local: step dir name; root: path inside the root; process: process id
    alias: str | None = None     # root form only
    def __str__(self) -> str     # canonical text

def parse_use(text: str) -> UseRef   # ValueError (E-USE)
```

- local: `^\./steps/([a-z][a-z0-9_]*)$`
- process: `^process:(<ProcessId>)$`
- root: `^(<Alias>):([A-Za-z0-9_][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_][A-Za-z0-9_.-]*)*)$`. Segments cannot be `.` or `..` because they must start with an alphanumeric character or `_`.

Anything else, including `../`, is rejected with: `use: must be one of ./steps/<name>, <alias>:<path>, process:<id> (got '../x')`. Whether the alias exists and whether the target resolves are checked by `process`.

### 6.2 Env fragments and env vars (`fragments.py`)

```python
class EnvVar(SpecModel):
    name: EnvName
    description: str = ""
    secret: bool = False
    required: bool = True
    default: str | None = None
    one_of: str | None = None       # group label; the group is satisfied when any member is set
    used_by: list[str] = []         # "step:<key>" | "tool:<step>.<tool>" | "mcp:<server>" | "provider:<name>"
                                    # | "edge:<from>" | "storage" | "runtime"

class EnvFragment(SpecModel):
    deps: list[str] = []            # PEP 508 requirement strings (minimums)
    system: list[str] = []          # OS package names for the base's package manager (used verbatim)
    requires: Literal["glibc"] | None = None
    vars: list[EnvVar] = []         # env vars this step/provider needs

def merge_env_vars(vars: Iterable[EnvVar]) -> list[EnvVar]
    # merge by name, sorted by name: first non-empty description; secret = any; required = any;
    # default must agree when both are non-None (else SpecError E-ENV-CONFLICT); one_of = first non-None;
    # used_by = sorted union
def merge_fragments(frags: Iterable[EnvFragment]) -> EnvFragment
    # deps: order-preserving unique; system: sorted unique; requires: "glibc" if any; vars: merge_env_vars
```

Example `claude-code` provider fragment, which the runtime owns and ships:

```yaml
deps: ["claude-agent-sdk>=0.2,<0.3"]
system: []
vars:
  - name: CLAUDE_CODE_OAUTH_TOKEN
    description: Claude Code subscription token ("dev key", from `claude setup-token`). Not needed where the claude CLI is logged in.
    secret: true
    required: false
    one_of: claude-auth
  - name: ANTHROPIC_API_KEY
    description: Anthropic API key, accepted by claude-code instead of the subscription token.
    secret: true
    required: false
    one_of: claude-auth
```

### 6.3 Proto-step (`proto_step.py`, clause 6.1)

```python
class Example(SpecModel):
    inputs: dict[str, Any] = {}
    outputs: dict[str, Any] = {}       # fields of `exit`'s model, without the exit key
    exit: str = "done"                 # a declared exit, or "error"
    description: str | None = None     # e.g. the compiler's edge-case question the user confirmed

class ProtoStep(SpecModel):
    kind: Literal["proto_step"]
    name: Name
    instruction: str                                       # non-empty after strip
    inputs: dict[FieldName, TypeSpec] = {}
    outputs: dict[ExitName, dict[FieldName, TypeSpec]]     # normalised (§5.3)
    exits: list[ExitName]                                  # normalised
    exit_codes: dict[int | Literal["*"], str] | None = None   # shell only; keys 0..255 or "*"; digit strings coerced
    examples: list[Example] = []
    env: EnvFragment = EnvFragment()

    def models(self) -> ModelSet               # build_models(self.name, self.inputs, self.outputs)
    def interface(self) -> Interface           # interface_from_fields(...)
    def to_authoring(self) -> dict             # flat outputs + no `exits` when exits == ["done"];
                                               # otherwise nested outputs (every exit, {} included) + `exits`
```

There are no other fields. Section 6.1 of the spec says "that is the whole proto-step", so extra keys are rejected.

`exit_codes` values must be in `exits ∪ {error}`; `check_proto_step` reports violations as `E-EXIT-CODES`. When a shell step has no `exit_codes`, the default is `{0: done, "*": error}`.

Relative values in examples for `path`-typed fields resolve against the process directory, for `process.yaml` and for process-local protos. For step-root protos they resolve against the step package directory. The test generator applies this rule; spec only documents it.

```python
def check_proto_step(p: ProtoStep) -> list[Diagnostic]
```

`check_proto_step` checks within the document only:

- `E-EXAMPLE`: an example's exit is not declared.
- `E-EXAMPLE`: an example's inputs do not validate against `models().input`. Missing inputs are reported; the model is `extra="forbid"`.
- `E-EXAMPLE`: an example's outputs do not validate against that exit's model. This is skipped for `exit: error`.
- `E-EXIT-CODES`.
- `W-PROTO-NO-EXAMPLES`.
- `W-PROTO-EXIT-UNTESTED`: an exit that no example covers.
- `W-OUTPUTS-AMBIGUOUS`.

Each diagnostic carries a loc such as `("examples", 1, "outputs", "due_date")`.

```yaml
# processes/process_supplier_invoice/proto/extract_invoice_fields.yaml
kind: proto_step
name: extract_invoice_fields
instruction: |
  Given the text of a supplier invoice, extract the invoice number, total, currency and due date.
inputs:
  invoice_text: string
outputs:
  done:
    invoice_number: string
    total: number
    currency: string
    due_date: date
  not_an_invoice: {}
exits: [done, not_an_invoice]
examples:
  - inputs: { invoice_text: "INVOICE INV-1042 ... Total due: £1,200.50 by 1 Oct 2026" }
    outputs: { invoice_number: "INV-1042", total: 1200.50, currency: GBP, due_date: 2026-10-01 }
    exit: done
  - inputs: { invoice_text: "Dear customer, your order has shipped" }
    exit: not_an_invoice
env:
  deps: []
```

### 6.4 Process (`process_doc.py`, clauses 6.2, 3.3–3.5)

```python
WithValue = str | int | float | bool | None | list["WithValue"] | dict[str, "WithValue"]   # strings are expressions

class ProcessEnv(SpecModel):
    base: Literal["debian-slim-python", "alpine-python"] = "debian-slim-python"
    vars: dict[EnvName, str] = {}        # env vars used by expressions: name -> description (feeds the manifest)

class StepRef(SpecModel):
    use: str                             # validated with parse_use
    @property
    def use_ref(self) -> UseRef: ...

class RetryOverride(SpecModel):          # branch-level override of the target step's lockfile policy
    run: int | None = Field(None, ge=0)
    validation: int | None = Field(None, ge=0)
    tool: int | None = Field(None, ge=0)

class Limits(SpecModel):
    max_traversals: int | str | None = None   # int >= 1, or an expression; the validator fills 10 on cycle branches
    timeout: float | str | None = None        # seconds (> 0) for the target step's run, or an expression
    retries: RetryOverride | None = None

class Branch(SpecModel):
    step: str                                  # step key, or "$exit.<exit>"
    when: str | bool | None = None             # deterministic edge: expression; agentic edge: prose condition
    with_: dict[FieldName, WithValue] = Field(default_factory=dict, alias="with")
    limits: Limits | None = None
    name: BranchName | None = None
    @property
    def exit_target(self) -> str | None: ...   # "done" for "$exit.done", else None

class Edge(SpecModel):
    from_: str = Field(alias="from")           # "<step>.<exit>", pattern ^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$
    to: list[Branch]                           # normalised: always a non-empty list
    kind: Literal["deterministic", "agentic"] = "deterministic"
    instruction: str | None = None             # agentic only (M5): preamble for the verifier
    context: list[str] = []                    # agentic only (M5): same vocabulary as AgenticStep.context
    @property
    def source_step(self) -> str: ...
    @property
    def source_exit(self) -> str: ...
    @property
    def key(self) -> str: ...                  # == from_, e.g. "validate.done"; used by edges["..."]

class FinallyStep(SpecModel):
    step: StepKey
    with_: dict[FieldName, WithValue] = Field(default_factory=dict, alias="with")

class ProcessDoc(SpecModel):
    kind: Literal["process"]
    name: Name
    goal: str | None = None
    latency: Literal["fast", "normal"] | None = None
    provider: str | None = None                 # process default; None -> DEFAULT_PROVIDER
    env: ProcessEnv = ProcessEnv()
    entry: StepKey
    inputs: dict[FieldName, TypeSpec] = {}
    outputs: dict[ExitName, dict[FieldName, TypeSpec]]   # normalised (§5.3)
    exits: list[ExitName]                                # normalised
    examples: list[Example] = []
    steps: dict[StepKey, StepRef]                        # non-empty
    edges: list[Edge] = []
    on_error: StepKey | None = None
    finally_: list[FinallyStep] = Field(default_factory=list, alias="finally")   # "name" strings accepted
    ui: dict[str, Any] | None = None            # opaque, owned by the web editor (layout); excluded from hashes

    @property
    def effective_provider(self) -> str: ...
    def models(self) -> ModelSet                # process inputs / outputs-by-exit models
    def interface(self) -> Interface            # the ProcessStep contract
    def edge(self, key: str) -> Edge | None
    def to_authoring(self) -> dict              # shorthand `to: x` for one plain branch; flat outputs when
                                                # exits == [done]; `exits:` only when "done" not in exits;
                                                # `finally` items without `with` as plain strings
```

**`to:` normalisation** (a before-validator on `Edge`; error code `E-TO`):

- `to: <str>` becomes `[{"step": <str>, "with": edge.with, "limits": edge.limits}]`, and the edge-level `with` and `limits` are consumed.
- `to: [<str> | <mapping>, ...]`: string items become `{"step": s}`.
- Edge-level `with:` or `limits:` alongside a list `to:` is an error: "with:/limits: go on each branch when to: is a list".
- A missing `to`, an empty list, or a bare mapping is an error.

**Expression sites.** Every place an expression can appear is enumerated once, and the loader, `check_process_doc`, the validator and the builder all use that enumeration:

```python
@dataclass(frozen=True)
class ExprSite:
    loc: Loc                                    # normalised-model loc, e.g. ("edges", 3, "to", 0, "with", "dest")
    text: str
    role: Literal["when", "with", "limit", "finally_with"]
    edge: str | None                            # edge key ("validate.done"), None for finally
    branch: int | None

def expression_sites(doc: ProcessDoc) -> list[ExprSite]
    # when: string `when` of deterministic edges only (bool literals are not sites; agentic `when` is prose)
    # with: every string leaf of a branch's `with` tree (loc includes the nested path)
    # limit: string `max_traversals` / `timeout`
    # finally_with: string leaves under finally[i].with
def site_position(doc: ProcessDoc, loc: Loc) -> Mark | None
    # SourceMap lookup; for shorthand edges it retries with ("to", 0) removed so the line points at the
    # edge-level `with:` the author wrote
```

`load_process` parses every site after pydantic validation. Any `ExprSyntaxError` becomes an `E-EXPR-SYNTAX` diagnostic located with `SourceMap.expr_position`, and the load fails.

```python
def check_process_doc(doc: ProcessDoc) -> list[Diagnostic]
```

`check_process_doc` checks within the document only. Graph and cross-document checks belong to `process`.

| Code | Check |
|---|---|
| `E-ENTRY` | `entry` is not a key of `steps` |
| `E-STEP-UNKNOWN` | `on_error`, a `finally` step, an edge `from` step, or a branch `step` is not in `steps` |
| `E-EDGE-DUP` | two edges share a `from` ("one edge per exit") |
| `E-EXIT-TARGET` | `$exit.<x>` where `x` is not in `exits` and is not `error` |
| `E-BRANCH-NAME` | branch names are not unique within an edge |
| `W-BRANCH-UNREACHABLE` | branches after the first branch without `when:` (3.4) |
| `W-LIMITS-EXIT` | `limits` on a `$exit` branch |
| `E-AGENTIC-WHEN` | a non-else branch of an agentic edge has an empty prose `when` |
| `E-EXPR-SYNTAX` | an expression fails to parse (for docs built in code rather than loaded) |
| `E-EXPR-FUNC` / `E-EXPR-ARITY` / `E-REF-NAME` / `E-REF-SHAPE` | per-site `check_expression` (§7.9) |
| `E-EXAMPLE` | a process example fails exactly as for proto-steps |
| `W-OUTPUTS-AMBIGUOUS` | see §5.3 |

The dogfood `process.yaml` is Appendix A.2.

### 6.5 `step.lock.yaml` (`lockfiles.py`, clause 6.3)

```python
StepKind = Literal["deterministic", "agentic", "shell"]   # ProcessStep is not a package; it comes from use: process:
Tier = Literal["cheap", "standard", "strong"]
Thinking = Literal["none", "low", "medium", "high"]
Effect = Literal["network", "filesystem", "shell"]

class RetryPolicy(SpecModel):
    run: int = Field(0, ge=0)          # re-run pre/run/post after an exception. Agentic: full restart only for a
                                       # transport failure before the first tool call (3.5)
    validation: int = Field(0, ge=0)   # agentic: re-request structured output in the same conversation
    tool: int = Field(0, ge=0)         # per tool call, transient network errors, idempotent tools only

DEFAULT_RETRIES: dict[StepKind, RetryPolicy] = {
    "deterministic": RetryPolicy(),
    "shell": RetryPolicy(),
    "agentic": RetryPolicy(run=1, validation=2, tool=1),
}
def effective_retries(policy: RetryPolicy, override: RetryOverride | None) -> RetryPolicy  # field-wise override

class ToolSnapshot(SpecModel):         # @tool methods and runtime.tools library functions
    name: str
    source: Literal["method", "library"]
    effects: list[Effect] = []
    idempotent: bool = False
    env: list[EnvName] = []

class McpToolSnapshot(SpecModel):
    name: str
    description: str = ""
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    idempotent: bool = False           # from the server's idempotentHint annotation when present

class McpSnapshot(SpecModel):
    server: str                        # name in the user MCP registry
    allow: list[str] = Field(min_length=1)   # required (3.8)
    tools: list[McpToolSnapshot]       # exactly the allowed tools, sorted by name
    hash: HashStr                      # hash_obj([t.model_dump(mode="json") for t in tools]); runtime compares live

class ShellLock(SpecModel):
    exit_codes: dict[int | Literal["*"], str] = {0: "done", "*": "error"}

class StepLock(SpecModel):
    wynd: Literal[1] = 1               # lockfile schema version
    name: Name                         # == package directory name (== proto name when compiled)
    kind: StepKind
    entrypoint: str                    # "<module>:<Class>", pattern ^[A-Za-z_][\w.]*:[A-Za-z_]\w*$
    proto_hash: HashStr | None = None  # None for hand-written steps with no proto
    provider: str | None = None        # agentic only; None = process default (hand-edited override, 3.9)
    tier: Tier | None = None           # agentic only; filled "cheap" if absent
    thinking: Thinking | None = None   # agentic only; filled "low" if absent
    retries: RetryPolicy | None = None # filled DEFAULT_RETRIES[kind] if absent
    effects: list[Effect] = []
    tools: list[ToolSnapshot] = []     # agentic only
    mcp: list[McpSnapshot] = []        # agentic only
    context: list[str] = []            # agentic only; snapshot of the class attribute (validated: §7.9)
    shell: ShellLock | None = None     # shell only; filled ShellLock() if absent
    fragment: EnvFragment = EnvFragment()   # declared deps (minimums), system, requires, env vars needed
    locked_deps: list[str] = []        # pinned closure "name==version", sorted, excluding wynd-spec/wynd-runtime
    interface: Interface               # snapshot of Input / per-exit Output JSON Schemas

    def wheel_metadata(self) -> dict   # model_dump(json) minus provider/tier/thinking (6.3: tier not in wheel)
```

Post-validation rules (`E-LOCK`):

- A non-agentic step must not set `provider`, `tier`, `thinking`, `tools`, `mcp` or `context`.
- A non-shell step must not set `shell`.
- `effects` must include every effect of every tool, plus `network` if `mcp` is non-empty. This is checked, not auto-filled, because the lockfile is a review surface: "effects must declare network (used by tool web_search)".
- `set(t.name for t in snapshot.tools) == set(snapshot.allow)`.
- `shell.exit_codes` values must be in `interface.exits ∪ {error}`.
- Every `context` entry must parse (§7.9).
- Every tool `env` name must appear in `fragment.vars`.

The interface and context snapshots exist so that `wynd validate` and the builder never import step code. A step's dependencies, such as `pypdf`, live in other venvs. The runtime worker checks `interface_hash(interface_from_models(cls.Input, cls.Output)) == interface_hash(lock.interface)` and `cls.context == lock.context` at import time and fails loudly on a mismatch. See §13 for who refreshes snapshots after hand edits.

A full example is Appendix A.4.

### 6.6 `edges.lock.yaml` (M5, agentic edges; committed next to `process.yaml`)

```python
class EdgeLock(SpecModel):
    provider: str | None = None                 # None = process default
    tier: Tier = "cheap"
    thinking: Thinking = "low"
    retries: RetryPolicy = RetryPolicy(validation=2)

class EdgesLock(SpecModel):
    wynd: Literal[1] = 1
    edges: dict[str, EdgeLock] = {}             # key = Edge.key ("extract.done"); absent key -> EdgeLock()
```

The file exists only when a process has `kind: agentic` edges. The compiler writes it, and humans may edit it, like `step.lock.yaml`.

### 6.7 `process.lock.yaml` (`lockfiles.py`, clause 6.4)

This file is the **run plan**. `wynd build` writes it under `.wynd/build/<process>/<commit>/`, and the in-image supervisor reads it. Local mode can build the same object in memory, with `wheel=None` and local venv paths, so that one executor consumes one plan format. The builder designer may add fields; everything listed here is what the runtime needs.

```python
class WyndBase(SpecModel):
    version: str                         # == wynd-runtime == wynd-spec version
    variant: Literal["slim", "alpine"]
    image: str                           # e.g. "wynd-base:0.1.0-slim" (registry-qualified when published)

class BaseChoice(SpecModel):
    requested: Literal["debian-slim-python", "alpine-python"]
    chosen: Literal["debian-slim-python", "alpine-python"]
    reason: str                          # "default" | "requested" | "fallback: <package> requires glibc"

class LockedUnit(SpecModel):             # what a step key resolves to; exactly one field set
    package: str | None = None           # workspace-relative package dir, e.g. "processes/psi/steps/read_pdf"
    process: ProcessId | None = None     # ProcessStep child

class LockedProcess(SpecModel):
    path: str                            # workspace-relative process dir
    hash: HashStr                        # process_hash
    definition: ProcessDoc               # resolved graph (max_traversals filled on cycle branches)
    units: dict[StepKey, LockedUnit]

class LockedPackage(SpecModel):
    hash: HashStr                        # step_hash of the source package dir at `commit`
    distribution: str                    # dist name from the package pyproject
    wheel: str | None = None             # relative to the build dir (image mode)
    venv: str                            # venv id, e.g. "venv-0"
    lock: StepLock                       # the committed step.lock.yaml, verbatim

class VenvPlan(SpecModel):
    deps_hash: HashStr                   # dependency_set_hash(requirements)
    requirements: list[str]              # pinned lines: members' locked_deps + provider deps
    packages: list[str]                  # package keys installed into this venv

class ProcessLock(SpecModel):
    wynd: Literal[1] = 1
    process: ProcessId                   # root process
    commit: str | None                   # compile-tree commit (40 hex); None only for an in-memory local plan
    process_hash: HashStr
    wynd_base: WyndBase | None = None    # None in local mode
    base: BaseChoice
    provider: str                        # effective default provider of the root process
    system: list[str] = []               # merged system packages
    providers: dict[str, EnvFragment] = {}   # provider name -> fragment used
    processes: dict[ProcessId, LockedProcess]   # root + every child in the reference closure
    packages: dict[str, LockedPackage]
    venvs: dict[str, VenvPlan]
    edges: dict[str, EdgeLock] = {}      # M5: "<process id>:<edge key>" -> settings from edges.lock.yaml
```

Package keys are workspace-relative directory paths. They are unique, simple and stable. The `alias:path` form is for `use:` and for display only. Appendix A.5 is an example.

### 6.8 `process.env.yaml` (`env_manifest.py`, clauses 4 and 4.1)

```python
class EnvManifest(SpecModel):
    wynd: Literal[1] = 1
    process: ProcessId
    commit: str | None = None
    vars: list[EnvVar]                   # merge_env_vars output (sorted by name), used_by filled

@dataclass(frozen=True)
class EnvCheck:
    ok: bool                             # no error diagnostics
    diagnostics: list[Diagnostic]

def check_env(manifest: EnvManifest, environ: Mapping[str, str]) -> EnvCheck
```

A variable counts as **set** when it is present and non-empty.

- `E-ENV-MISSING`: a variable is required, has no default, and is unset.
- A `one_of` group is satisfied when any member is set. An unsatisfied group is `E-ENV-ONE-OF` if any member is required, and `W-ENV-ONE-OF` otherwise. The `claude-code` group raises the warning, because a logged-in CLI also works.
- Messages name the variable and its `used_by`. They never contain a value.

`check_env` lives in spec, not `process`, so that it can run in-image as the startup probe (4.1).

The manifest example is Appendix A.6.

### 6.9 Runtime records (`records.py`)

These are here because other formats embed their schemas: `on_error` handler Input, `$exit.error` output, the error-exit schema used in exit-aware reference checks, and `previous.summary`.

```python
class Summary(SpecModel):                      # 3.6 step 5 — derived deterministically by the runtime
    step: str
    exit: str
    key_outputs: dict[str, Any] = {}
    note: str = ""

StepErrorCause = Literal["exception", "input", "output_validation", "tool", "hook",
                         "transport", "timeout", "mcp_mismatch", "process"]
class StepError(SpecModel):                    # payload of every step's implicit `error` exit
    exit: Literal["error"] = "error"
    cause: StepErrorCause
    message: str
    error_type: str | None = None              # exception class name
    attempts: int = 1
    process_error: "ProcessError | None" = None   # cause == "process": the child's ProcessError (3.5)

ProcessErrorCause = Literal["step_error", "no_branch", "max_traversals", "timeout", "expression",
                            "handler_error", "explicit", "process_input"]
class ProcessError(SpecModel):                 # 3.5: step, cause, inputs, partial outputs, trace pointer
    run_id: str
    step: str | None                           # step path ("validate", "sub.fix"); None if not step-specific
    cause: ProcessErrorCause
    message: str
    inputs: dict[str, Any] | None = None       # the failing step's bound inputs (process inputs if step is None)
    partial_outputs: dict[str, Any] = {}       # step key -> latest outputs (JSON, without exit)
    step_error: StepError | None = None
    trace: str                                 # opaque pointer produced by the runtime TraceSink

class ProcessErrorOutput(SpecModel):           # the process output for $exit.error
    exit: Literal["error"] = "error"
    error: ProcessError

STEP_ERROR_SCHEMA: dict = StepError.model_json_schema()
```

The runtime owns the cause vocabularies and may extend the `Literal`s. It should add to the list above rather than rename entries.

### 6.10 Interface snapshots (`interface.py`)

```python
class Interface(SpecModel):
    input: dict[str, Any]                      # JSON Schema of Input (pydantic model_json_schema(mode="validation")
                                               # for code; fields_schema(...) for YAML)
    outputs: dict[ExitName, dict[str, Any]]    # exit -> JSON Schema of that exit's model, incl. "exit" const
    @property
    def exits(self) -> list[str]: ...
    def with_error(self) -> dict[str, dict]: ... # outputs + {"error": STEP_ERROR_SCHEMA}

def split_output(output: Any) -> dict[str, type[BaseModel]]
def output_adapter(output: Any) -> TypeAdapter
def interface_from_models(input_model: type[BaseModel], output: Any) -> Interface
def interface_from_fields(inputs: Mapping[str, TypeNode],
                          outputs: Mapping[str, Mapping[str, TypeNode]]) -> Interface
def interfaces_equivalent(a: Interface, b: Interface) -> list[str]   # M3: human-readable differences; [] if same
```

**`split_output` rules.** Violations raise `TypeError` naming the class, because these are code errors rather than YAML errors.

- A `BaseModel` subclass becomes `{exit: model}`. It must declare `exit: Literal["done"] = "done"` (the 7 plain-model shorthand).
- A `X | Y` (`types.UnionType`), a `typing.Union[...]`, or an `Annotated[Union[...], ...]` (unwrapped) yields one entry per member.
- Each member must be a `BaseModel` subclass with field `exit` whose annotation is `Literal[<one str>]` and whose default equals that string. Exits must be unique, and `error` is reserved.
- The result is ordered by union order.

`output_adapter` returns `TypeAdapter(Annotated[Union[models], Field(discriminator="exit")])`, or `TypeAdapter(model)` for a single exit. The spike verified tagged-union validation and the `union_tag_invalid` error at loc `()`.

---

## 7. Expression language (`wynd.spec.expr`, clause 3.4.1)

### 7.1 Grammar (verbatim; verified LALR(1) with Lark 1.3.1, no conflicts)

```lark
// Wynd expression language (wynd.spec.expr) -- LALR(1)
?start: expr

?expr: conditional
     | or_expr

conditional: "if" expr "then" expr ("elif" expr "then" expr)* "else" expr

?or_expr: and_expr
        | or_expr "or" and_expr          -> or_

?and_expr: not_expr
         | and_expr "and" not_expr       -> and_

?not_expr: comparison
         | "not" not_expr                -> not_

?comparison: sum
           | sum "==" sum                -> eq
           | sum "!=" sum                -> ne
           | sum "<" sum                 -> lt
           | sum "<=" sum                -> le
           | sum ">" sum                 -> gt
           | sum ">=" sum                -> ge
           | sum "in" sum                -> in_
           | sum "not" "in" sum          -> not_in

?sum: product
    | sum "+" product                    -> add
    | sum "-" product                    -> sub

?product: unary
        | product "*" unary              -> mul
        | product "/" unary              -> div
        | product "%" unary              -> mod

?unary: postfix
      | "-" unary                        -> neg

?postfix: atom
        | postfix "." NAME               -> member
        | postfix "[" expr "]"           -> index

?atom: STRING                            -> string
     | NUMBER                            -> number
     | "true"                            -> true
     | "false"                           -> false
     | "null"                            -> null
     | NAME                              -> name
     | NAME "(" [expr ("," expr)* ","?] ")"   -> call
     | "[" [expr ("," expr)* ","?] "]"        -> list_
     | "{" [pair ("," pair)* ","?] "}"        -> object_
     | "(" expr ")"

pair: (NAME | STRING) ":" expr

NAME: /[A-Za-z_][A-Za-z0-9_]*/
STRING: /"(?:[^"\\\n]|\\.)*"/ | /'(?:[^'\\\n]|\\.)*'/
NUMBER: /(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/

%ignore /[ \t\r\n]+/
```

Parser: `Lark(GRAMMAR, parser="lalr", propagate_positions=True, maybe_placeholders=True)`. It is built once at import. Each parse call keeps its own state, and a threaded test covers that.

Lark's contextual lexer lets a keyword act as a member name after `.`. `steps.x.outputs.in` and `steps.x.outputs.if` parse and evaluate, as verified in the spike. In other positions keywords are reserved.

Tokens:

- **Strings** use double or single quotes. Escapes are `\\ \" \' \n \t \r \/ \uXXXX`. Any other escape is `E-EXPR-SYNTAX` "invalid escape \q", located at the string token.
- **Numbers**: a literal with `.` or an exponent is a float, otherwise an int. Negative numbers are unary minus.

### 7.2 Precedence (lowest to highest)

| Level | Construct | Associativity / notes |
|---|---|---|
| 1 | `if c then a (elif c then b)* else d` | prefix form; `else` mandatory; allowed wherever a full `expr` is (top level, parentheses, call args, list/object items, subscripts). `x + if …` is a syntax error; write `x + (if … )` |
| 2 | `or` | left |
| 3 | `and` | left |
| 4 | `not` | prefix, so `not a == b` is `not (a == b)` and `not a in b` is `not (a in b)` |
| 5 | `== != < <= > >= in not in` | **non-associative**: `a < b < c` is a syntax error |
| 6 | `+ -` | left |
| 7 | `* / %` | left |
| 8 | unary `-` | prefix |
| 9 | `.name`, `[expr]` | postfix; calls only on bare names (`f(a)(b)` is a syntax error) |
| 10 | literals, names, `f(args)`, `( )`, `[ ]`, `{ }` | trailing commas allowed |

### 7.3 AST (`nodes.py`)

These are frozen dataclasses. Every node carries `line` and `column`, which are 1-based and relative to the expression text.

```
Lit(value) | Name(id) | Member(obj, name) | Index(obj, index) | Call(func, args: tuple)
Unary(op: "-"|"not", operand) | Binary(op, left, right)      # op in + - * / % == != < <= > >= in "not in" and or
Cond(arms: tuple[(cond, value), ...], orelse) | ListLit(items) | ObjectLit(pairs: tuple[(str, node), ...])
```

```python
@functools.lru_cache(maxsize=4096)
def parse(text: str) -> Node         # raises ExprSyntaxError(text, line, column, message)
```

Measured cost: about 90 µs per uncached parse and about 3 µs to evaluate a cached `a.b.c and d.e < 3`. Expressions are never pre-compiled to Python.

### 7.4 Values, truthiness, equality, operators

**Value domain** is JSON: `None`, `bool`, `int`, `float`, `str`, `list`, `dict[str, …]`.

- Scope values must already be JSON-mode. The runtime stores `model_dump(mode="json")` without `exit`.
- YAML non-string scalars in `with:` pass through as literals, except that `date`/`datetime` become ISO strings.
- `bool` is never treated as a number.

**Truthiness**, used by `when:`, `if`, `not`, `and` and `or`: `null`, `false`, `0`, `0.0`, `""`, `[]` and `{}` are false. Everything else is true.

| Operator | Semantics | Errors (`EvalError`) |
|---|---|---|
| `and` / `or` | short-circuit; **always returns a boolean** (not an operand) | none |
| `not x` | `not truthy(x)` | none |
| `==` / `!=` | strict structural equality: int/float compare numerically (`1 == 1.0`), bool ≠ number (`true == 1` is false), different types unequal, lists and objects compared recursively with the same rule, `null == null` | none |
| `< <= > >=` | number vs number, or string vs string (lexicographic by code point; ISO dates order correctly) | any other pair: `cannot compare number > null` |
| `in` / `not in` | `x in str` is a substring test (x must be a string); `x in list` uses strict equality; `x in object` tests keys; `x in null` is false | right side of any other type: `'in' needs a string, list or object on the right, got number`; `1 in "abc"`: `'in' a string needs a string, got number` |
| `+` | number+number, string+string (concatenation), list+list (concatenation) | anything else: `cannot add number and string` |
| `- * / %` | numbers only; `/` is true division (always float); `%` follows Python (sign of divisor) | non-numbers: `operator '*' needs numbers, got string and number`; `/ 0` and `% 0`: `division by zero` |
| unary `-` | numbers only | `cannot negate string` |

### 7.5 References and the scope contract (`scope.py`)

The executor builds a `Scope` for each process instance, so child processes get their own scope. It updates the scope as steps complete and branches are taken, and passes it to every evaluation.

```python
@dataclass
class StepState:
    runs: int = 0                          # completed executions so far (any exit, including error)
    exit: str | None = None                # exit of the latest completed run
    outputs: dict[str, Any] | None = None  # latest completed run's fields, JSON-mode, WITHOUT "exit"
    summary: dict[str, Any] | None = None  # latest Summary.model_dump(mode="json")

@dataclass
class EdgeState:
    taken: list[int]                       # times each branch (by index in `to:`) has been taken
    names: dict[str, int] = field(default_factory=dict)   # branch name -> index

@dataclass
class Scope:
    steps: dict[str, StepState]            # EVERY step key of this process (never-run -> StepState())
    edges: dict[str, EdgeState]            # EVERY edge key of this process
    process_inputs: dict[str, Any]
    env: Mapping[str, str]                 # usually os.environ
    run_id: str
    previous: str | None = None            # key of the most recently completed step
    clock: Callable[[], datetime] = utc_now
    volatile: list[str] = field(default_factory=list)     # appended by the evaluator (run.id, now() results)

    def complete(self, step: str, exit: str, outputs: Mapping[str, Any],
                 summary: Mapping[str, Any] | None) -> None   # runs += 1; set exit/outputs/summary; previous = step
    def take(self, edge: str, branch: int) -> int              # taken[branch] += 1; returns the new count
    def replacements(self) -> dict[str, str]                   # {run_id: "<run.id>", each now() value: "<now>"}

def new_scope(doc: ProcessDoc, *, inputs: Mapping[str, Any], env: Mapping[str, str],
              run_id: str, clock: Callable[[], datetime] = utc_now) -> Scope
```

Counter timing is defined here so that every executor behaves the same way:

- `when:` conditions are evaluated with counts from *before* the current resolution.
- The executor evaluates the chosen branch's `with:`, then calls `take()`, then runs the target.
- `max_traversals: N` allows the branch to be taken N times. Taking it an (N+1)th time routes to the process error handler with cause `max_traversals`.

**Reference forms and results**

| Reference | Value | Notes |
|---|---|---|
| `steps.<k>` | `{"runs", "exit", "outputs"}` for step key k | unknown k is `EvalError: unknown step 'k'` (the validator catches it first) |
| `steps.<k>.runs` | int; **0 if the step never ran** | per process instance |
| `steps.<k>.exit` | str, or **null if never ran** | |
| `steps.<k>.outputs` | latest run's fields as an object **without `exit`**, or **null if never ran** | `with: { fields: steps.extract.outputs }` passes this object |
| `steps.<k>.outputs.<f>…` | member access (see below) | |
| `edges["<step>.<exit>"][<i>].taken` | int | the key must be a string; unknown key, out-of-range index or unknown name is an `EvalError` |
| `edges["<step>.<exit>"].<name>.taken` | int | `.taken` is the only member of a branch |
| `process.inputs.<f>` | value | `process.inputs` is the whole object |
| `env.<NAME>` / `env["NAME"]` | string, or **null if unset** | `default(env.X, "y")` works |
| `run.id` | string | recorded in `scope.volatile` |
| `previous.outputs[.<f>]` | outputs of `scope.previous`, or null | during edge resolution `previous` is the edge's source step |
| `previous.summary[.<k>]` | Summary of `scope.previous` (`{step, exit, key_outputs, note}`), or null | |
| any other bare name | `EvalError: unknown name 'x'` | |

**Member and index access**, applied uniformly:

- Access on `null` returns `null`, so `steps.save.outputs.record` is null when `save` never ran.
- A key missing from an object returns `null`.
- A list index takes an integer, and negative indexes count from the end. An out-of-range index returns `null`. A non-integer index is `EvalError: list index must be an integer, got string`.
- Member access on a string, number or boolean is `EvalError: cannot access 'foo' on a string`.

A comparison, arithmetic operation or function that then receives the `null` raises its own error, which routes to the error handler. Only the static checks in §7.9 prevent this at validate time.

### 7.6 Builtins (fixed set; unknown names are an error at validate time and at run time)

| Function | Arity | Semantics | Null handling | Errors |
|---|---|---|---|---|
| `len(x)` | 1 | length of string, list or object | `len(null)` is 0 | other types: `len() of number` |
| `lower(s)` / `upper(s)` | 1 | case conversion | null gives null | non-string |
| `contains(c, x)` | 2 | same as `x in c` | `c` null gives false | as for `in` |
| `startswith(s, p)` | 2 | prefix test | `s` null gives false | non-strings |
| `join(xs, sep=",")` | 1–2 | join a list of **strings** | `xs` null gives `""` | non-string items or separator |
| `split(s, sep?)` | 1–2 | without sep, split on whitespace (Python `str.split()`); with sep, split on that string | `s` null gives `[]` | empty separator; non-string |
| `default(x, y)` | 2 | `x` unless `x` is **null** (0, "" and false are kept) | | |
| `coalesce(a, …)` | ≥1 | first non-null argument, else null | | zero args |
| `now()` | 0 | current UTC time from `scope.clock` as ISO-8601 with seconds and `Z` (`2026-09-22T21:50:03Z`); the value is appended to `scope.volatile` | | |

Arity error message: `len() takes 1 argument(s), got 2` or `coalesce() takes 1..99 argument(s), got 0`.

### 7.7 Where expressions appear and how YAML values are treated (`evaluator.py`)

```python
def evaluate(expr: str | Node, scope: Scope) -> Any                        # raises EvalError
def evaluate_condition(when: str | bool | None, scope: Scope) -> bool       # None -> True (no `when`); bool as-is;
                                                                             # str -> truthy(evaluate(...))
def evaluate_value(value: WithValue, scope: Scope) -> Any                   # str -> expression; dict/list recurse;
                                                                             # date/datetime -> ISO str; other scalars literal
def evaluate_with(with_: Mapping[str, WithValue], scope: Scope) -> dict[str, Any]
def evaluate_limit(value: int | float | str | None, scope: Scope) -> int | float | None   # str -> must yield a number
def truthy(value: Any) -> bool
def strict_eq(a: Any, b: Any) -> bool
```

The rules:

- **Every YAML string** under `when:`, `with:`, `limits:` and `finally[].with` is an expression. YAML quoting does not change this: in `label: "unlabelled"` the YAML parser consumes the quotes, and the result is the bare name `unlabelled`, which is an error.
- A literal string needs expression quotes inside the YAML value: `label: '"unlabelled"'`, or `label: default(x, "unlabelled")`. The `E-REF-NAME` message shows this.
- Non-string YAML scalars (`3`, `true`, `null`, `1.5`) are literals.
- Mappings and lists recurse.

`EvalError(message, node, text)` carries the expression text and node position. The executor turns it into a `ProcessError` with `cause="expression"` and a message such as `edges[validate.done].to[0].with.dest: 1:4: cannot compare number > null`.

### 7.8 Syntax errors

`ExprSyntaxError(text, line, column, message)`, where line and column are relative to the expression.

- **End of input** is reported at the position after the last character, not at Lark's last-token position: "unexpected end of expression", plus the expected-clause below. For example, `1 +` gives `…; expected a value`.
- **Unexpected token**: `unexpected '<tok>'`, plus an expected-clause built from the **exact** accepted set `e.interactive_parser.accepts()`. Lark's `e.expected` is LALR-over-approximate and is never used. The clause lists, in this order and only where present:
  - "a name" if the set is exactly `{NAME}`, otherwise "a value" if any of NAME, NUMBER or STRING is accepted;
  - "an operator" if any binary operator is accepted;
  - `'elif'`, `'else'`, `'then'`, `')'`, `']'`, `'}'`, `','`, `':'`;
  - "the end of the expression" for `$END`.

  Items are joined as `a, b or c`. The following outputs were verified in the spike:
  - `if a then b elze c` gives `unexpected 'elze'; expected an operator, 'elif' or 'else'`.
  - `unlabelled here` gives `unexpected 'here'; expected an operator or the end of the expression`.
  - `if a then b` gives `unexpected end of expression; expected 'elif' or 'else'`.
  - `{a 1}` gives `unexpected '1'; expected ':'`.
  - `[1 2]` gives `unexpected '2'; expected an operator, ']' or ','`.
  - `if a b` gives `unexpected 'b'; expected an operator or 'then'`.
  - `steps.x.` gives `unexpected end of expression; expected a name`.
- **Unexpected character**: `unexpected character '&'`.
- **Literal-text hint.** When the text has no quote, bracket, parenthesis or operator character and contains a space, so it looks like prose, append: `; if you meant literal text, quote it inside the YAML value: '"..."'`.

These positions are pinned by tests, taken from the spike:

- `a < b < c` gives 1:7.
- `if a then b` gives 1:12.
- `1 +` gives 1:4.
- `"unterminated` gives 1:1.
- `x + if a then 1 else 2` gives 1:8. The contextual lexer reads `if` as a name, so the error lands on `a`.

### 7.9 Static analysis (`analysis.py`)

```python
@dataclass(frozen=True)
class Ref:
    root: str                                # "steps" | "edges" | "process" | "env" | "run" | "previous" | other name
    path: tuple[str | int | None, ...]       # member names / literal subscripts; None = dynamic subscript
    line: int
    column: int
    guarded: bool = False                    # inside default()'s 1st arg or any coalesce() arg

def references(expr: str | Node) -> list[Ref]          # maximal reference chains, source order
def value_references(value: WithValue) -> list[tuple[Loc, Ref]]   # walks a with-tree; Loc relative to it
def env_names(exprs: Iterable[str]) -> set[str]        # names referenced via env.X / env["X"]
def check_expression(expr: str) -> list[Diagnostic]    # process-independent checks (below)
```

Extraction rules, verified in the spike:

- A chain of `Name`, `Member` and `Index` nodes with literal subscripts becomes one `Ref`.
- A dynamic subscript contributes `None` to the path, and its inner expression is analysed separately.

For example, `default(steps.fix.outputs.x, run.id) + steps.extract.outputs.items[steps.fix.runs].sku` yields:

- `steps.fix.outputs.x` (guarded)
- `run.id`
- `steps.extract.outputs.items[*].sku`
- `steps.fix.runs`

`check_expression` performs the checks that need no process context:

| Code | Check |
|---|---|
| `E-EXPR-SYNTAX` | the expression does not parse |
| `E-EXPR-FUNC` | unknown function |
| `E-EXPR-ARITY` | wrong number of arguments |
| `E-REF-NAME` | a root that is not one of the six. The message includes the hint *"to pass the literal text, quote it inside the YAML value: '\"unlabelled\"'"* |
| `E-REF-SHAPE` | `steps.<k>.<m>` with m ∉ {outputs, exit, runs}; `steps.<k>.exit.x`; `steps.<k>.runs.x`; `edges` without a literal string key; `edges[...]` not followed by `[int]` or `.name`, then `.taken`, then nothing; `process` without `.inputs`; bare `env`, or `env` deeper than one name; `run` other than `run.id`; `previous` without `.outputs` or `.summary` |

**Reference validity against a process (M1).** The process validator computes, for each expression site, which exits each step may have taken on paths reaching that site. This is a graph data-flow problem and belongs to `process`. Spec checks each expression against that result:

```python
@dataclass(frozen=True)
class StepView:
    exits: Mapping[str, dict]          # exits step k may have taken on some path reaching the site
                                       # -> that exit's output JSON Schema (use Interface.with_error() for "error")
    may_be_unrun: bool = False         # some path reaches the site without k having run

@dataclass(frozen=True)
class TypeEnv:
    steps: Mapping[str, StepView]              # every step key in the process
    edges: Mapping[str, Sequence[str | None]]  # edge key -> branch names by index
    process_inputs: dict                       # JSON Schema (ProcessDoc.interface().input)
    previous: StepView | None = None           # the step that completed immediately before the site

def check_references(expr: str, env: TypeEnv) -> list[Diagnostic]    # includes check_expression's findings
```

Algorithm (`schema_at(schema, path) -> Schema | MISSING | ANY` in `schemas.py`):

- **Walking a schema.** First resolve `$ref` against the root's `$defs`; recursive refs return ANY. Then unwrap nullable `anyOf`. Then handle the path segment:
  - a string segment on an object with `properties` takes the property, or returns MISSING;
  - an open object (no properties), or `{}`, returns ANY for the rest of the path;
  - an `int` or `None` segment on an array takes `items`;
  - any other type returns MISSING.
- **`steps.<k>…`**:
  - k is not in `env.steps`: `E-REF-STEP` "unknown step 'fx' (did you mean 'fix'?)".
  - `outputs` with path p, when the view has `may_be_unrun` and the ref is not guarded: `E-REF-UNRUN` "step 'fix' may not have run on every path to this edge; guard with default(...)/coalesce(...) or restructure".
  - `outputs` with path p, where p is missing on every exit in `view.exits`: `E-REF-FIELD` "'totl' is not an output of step 'extract' (exits: done, not_an_invoice)". This applies even when guarded, because it is a typo.
  - `outputs` with path p, where p is missing on some exits but not all, and the ref is not guarded: `E-REF-EXIT` "field 'record' is not declared on exit 'invalid' of step 'validate', which can reach this edge" (3.4 and 8: reference checks are exit-aware).
  - `exit` and `runs` are always valid.
- **`previous.outputs…`**: the same checks against `env.previous`.
- **`previous.summary.<k>`**: k must be in {step, exit, key_outputs, note}; anything below `key_outputs` is ANY.
- **`process.inputs.<p>`**: walk `env.process_inputs`. MISSING is `E-REF-INPUT`.
- **`edges[K]`**: K must be in `env.edges` (`E-REF-EDGE` "no edge 'validate.dne'"). The index must be less than the branch count, or the name must be a declared branch name.
- **`env.X`** and **`run.id`** are always valid. `env_names` feeds the manifest.

**Context entries (3.7, `context.py`)**:

```python
@dataclass(frozen=True)
class ContextRef:
    kind: Literal["full_trace", "process.goal", "process.inputs", "previous.summary",
                  "previous.outputs", "step.outputs", "step.summary"]
    step: str | None = None
    path: tuple[str, ...] = ()

def parse_context_entry(text: str) -> ContextRef    # ValueError (E-CONTEXT)
```

The accepted forms are `full_trace`, `process.goal`, `process.inputs`, `previous.summary`, `previous.outputs`, `steps.<k>.outputs[.<f>…]` and `steps.<k>.summary`. Entries are parsed with the expression parser and must be a single reference chain. Checking that the step key exists belongs to `process`.

### 7.10 Type inference (M3, `infer.py`, plus `schemas.check_assignable`)

```python
def infer_type(expr: str, env: TypeEnv) -> tuple[dict, list[Diagnostic]]   # result is a JSON Schema; {} = unknown
def check_assignable(src: dict, dst: dict) -> tuple[Literal["ok", "warning", "error"], str]
```

**Inference rules.** All result schemas are normalised. `{}` means ANY.

- **Literals and references**:
  - Literals get their JSON type.
  - `steps.k.runs` and `edges…taken` are integer.
  - `steps.k.exit` is `{"enum": [exits in view]}`, made nullable if the step may be unrun.
  - `steps.k.outputs[.p]` is the `anyOf` of `schema_at` over the view's exits, without the `exit` property; identical members are merged.
  - `env.X` is `anyOf[string, null]`.
  - `run.id` is string.
  - `previous.summary` is the `Summary` schema.
- **Operators**:
  - `and`, `or`, `not`, comparisons, `in` and `not in` give boolean.
  - `+` on numbers gives number (integer if both sides are integer); on strings, string; on arrays, an array of the union of item types.
  - `- * %` give number (integer if both sides are integer). `/` gives number.
  - Unary `-` keeps its operand's type.
- **Conditionals**: `if` gives the `anyOf` of its arms.
- **Builtins**:
  - `len` gives integer.
  - `lower` and `upper` give string, or null if the argument is nullable.
  - `contains` and `startswith` give boolean.
  - `join` gives string.
  - `split` gives an array of string.
  - `default(x, y)` gives `anyOf(strip_null(x), y)`.
  - `coalesce` gives the `anyOf` of its non-null arguments, plus null only if the last argument is nullable.
  - `now()` gives a string with `format: date-time`.
- **Definite errors** are reported only when both operand types are known and non-ANY (`E-TYPE-OP`). Examples are `number + string`, ordering a boolean, or `/` on strings. A nullable operand of an ordering or arithmetic operator gives `W-TYPE-NULL`.

**`check_assignable(src, dst)`**. The validator uses it to check each `with:` value against the target's Input field schema, and against the process output schema for `$exit` targets.

- If `dst` or `src` is ANY, the result is ok.
- If `src` is nullable and `dst` is not, the result is a warning ("may be null").
- Otherwise compare after `strip_null`:
  - Same type: ok.
  - integer to number: ok.
  - number to integer: warning.
  - A string with any format (path, date, date-time) to plain string: ok.
  - path and string, either way: ok.
  - Plain string to date or date-time: warning ("parsed at run time").
  - Open object to any object: ok.
  - Closed object to closed object: every required property of `dst` must exist in `src` (otherwise error), and each property is checked recursively.
  - array to array: items are checked recursively.
  - `anyOf` src: ok if every member is ok; warning if some members are ok; error if none are.
  - Anything else (boolean, number, string, object or array mismatched against each other) is an error, "expected <dst> got <src>".

`E-TYPE-ASSIGN`, `W-TYPE-ASSIGN` and `W-TYPE-NULL` are emitted by the validator, which calls these functions.

### 7.11 Volatile values (cassette keys, clause 7)

- The evaluator appends every value it produces for `run.id` and `now()` to `scope.volatile`.
- `Scope.replacements()` returns `{run_id: "<run.id>", **{v: "<now>" for each now() value}}`. The runtime adds its own entries, for example `{workspace_root: "<workspace>"}`.
- `normalise_volatile(request, replacements)` in `hashing.py`, described in §8, rewrites a provider request before it is hashed for a cassette key.

Replacement runs longest-key first, so a workspace path that contains the run id collapses to `<workspace>`. Tests freeze time by injecting `clock`.

---

## 8. Hashing (`hashing.py`)

```python
def jsonable(obj: Any) -> Any
    # dict (keys -> str; int keys become "0"...; collisions after str() -> TypeError), list/tuple -> list,
    # str/int/bool/None as-is, float (non-finite -> ValueError), date/datetime -> isoformat(), Path -> as_posix(),
    # Enum -> .value, BaseModel -> model_dump(mode="json", by_alias=True, exclude_defaults=True), TypeNode -> render_type
def canonical_json(obj: Any) -> bytes
    # json.dumps(jsonable(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
def hash_bytes(data: bytes) -> str            # "sha256:" + hexdigest
def hash_obj(obj: Any) -> str                 # hash_bytes(canonical_json(obj))

def proto_hash(proto: ProtoStep) -> str
    # hash_obj({"kind": "proto_step", "doc": proto.model_dump(mode="json", by_alias=True, exclude_defaults=True)})
    # computed on the NORMALISED model: formatting, comments, key order and flat-vs-nested equivalents do not change it;
    # any change to instruction, types, exits, exit_codes, examples or env does.

def step_hash(package_dir: Path) -> str
def process_hash(doc: ProcessDoc, units: Mapping[str, str]) -> str
def interface_hash(iface: Interface) -> str
    # hash_obj({"input": normalize_schema(iface.input), "outputs": {e: normalize_schema(s) for e, s in ...}})
def normalise_requirement(req: str) -> str
def dependency_set_hash(requirements: Iterable[str], python: str | None = None) -> str
def normalise_volatile(value: Any, replacements: Mapping[str, str]) -> Any
```

**`step_hash(package_dir)`** hashes the compiled step source package. It is used for RunRegistry test-result keys (commit plus step hash) and as an input to `process_hash`.

1. Walk `package_dir` recursively and collect regular files and symlinks. Exclude:
   - `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache` and `*.egg-info` directories at any depth;
   - `.venv`, `build` and `dist` at the top level only;
   - `*.pyc`, `*.pyo` and `.DS_Store` files.
2. For each file, in sorted order of its relative POSIX path, compute a digest:
   - For a **git-lfs pointer file** (content starts with `version https://git-lfs.github.com/spec/v1\n`, has an `oid sha256:<hex>` line, and is under 1 KiB), use that oid. LFS oids are the sha256 of the real content, so the hash is the same whether or not LFS content was fetched. This matters because cassettes are LFS-tracked (6.5).
   - For a symlink, use sha256 of `"symlink:" + target`.
   - Otherwise use sha256 of the bytes.
3. `h = sha256()`. For each file, `h.update(f"{rel}\0{digest}\n".encode())`. Return `"sha256:" + h.hexdigest()`.

The lockfile (and therefore the proto hash), the source, the generated tests and the cassettes all change the hash.

**`process_hash(doc, units)`** is the compiled hash of a process. `units` maps every key of `doc.steps` to either the `step_hash` of the resolved package, or, for `process:` units, the child's `process_hash`. A missing key raises `ValueError`.

- The result is `hash_obj({"kind": "process", "doc": <doc dumped by_alias, exclude_defaults, without "ui">, "units": {k: units[k] for k in sorted(doc.steps)}})`.
- A parent therefore changes whenever any child's compiled steps change (5.1).
- The caller (`process`) resolves children and detects reference cycles before calling.
- Examples are included, because they are the integration tests.

**`interface_hash`** uses `normalize_schema(schema)` from `schemas.py`:

- Inline `$ref` from `$defs`, but leave recursive refs as `$ref`.
- Drop `title`, `description`, `examples` and `$defs`.
- Drop `default` and `type` on the `exit` property, keeping `const`.
- Sort `required`.

Titles and docstrings therefore never cause false mismatches. `interfaces_equivalent` (M3) additionally ignores `additionalProperties` and `default`, and reports differences per exit and field.

**`dependency_set_hash(reqs, python=None)`** is the identity of a venv; the builder groups steps by it. Each requirement is normalised as follows:

1. Strip it.
2. Split off the name with `^([A-Za-z0-9][A-Za-z0-9._-]*)(.*)$`.
3. Normalise the name per PEP 503: `re.sub(r"[-_.]+", "-", name).lower()`.
4. Collapse runs of whitespace in the rest to single spaces and strip it.
5. Rejoin as `name + rest`.

Then deduplicate, sort, and return `hash_obj({"requirements": [...], "python": python})`.

**`normalise_volatile(value, replacements)`** returns a deep copy of a JSON value. In every string, dict key and value alike, each occurrence of each non-empty replacement key is replaced by its placeholder, longest key first.

---

## 9. Public API (`wynd/spec/__init__.py` and `wynd/spec/expr/__init__.py`)

```python
# wynd.spec
from wynd.spec.errors import Diagnostic, SpecError, format_loc
from wynd.spec.yamlio import SourceMap, Mark, parse_yaml, read_yaml, parse_model, load_model, dump_yaml
from wynd.spec.base import (SpecModel, DEFAULT_PROVIDER, DEFAULT_TIER, DEFAULT_THINKING,
                            DEFAULT_MAX_TRAVERSALS, TIERS)
from wynd.spec.typelang import (TypeNode, TScalar, TList, TObject, parse_type, render_type, type_schema,
                                fields_schema, ModelSet, build_models, normalise_outputs, describe_type,
                                describe_fields, infer_fields)
from wynd.spec.schemas import normalize_schema, schema_at, is_nullable, strip_null, check_assignable, ANY, MISSING
from wynd.spec.interface import (Interface, split_output, output_adapter, interface_from_models,
                                 interface_from_fields, interfaces_equivalent)
from wynd.spec.workspace import (WorkspaceConfig, StepRoot, UseRef, parse_use, find_workspace_root,
                                 load_workspace_config, WORKSPACE_FILE, PROCESS_FILE, PROTO_DIR, STEPS_DIR,
                                 STEP_PROTO_FILE, STEP_LOCK_FILE, EDGES_LOCK_FILE, PROCESS_LOCK_FILE,
                                 ENV_MANIFEST_FILE, STATE_DIR, CASSETTES_DIR)
from wynd.spec.fragments import EnvVar, EnvFragment, merge_env_vars, merge_fragments
from wynd.spec.proto_step import Example, ProtoStep, load_proto_step, check_proto_step
from wynd.spec.process_doc import (ProcessDoc, ProcessEnv, StepRef, Edge, Branch, Limits, RetryOverride,
                                   FinallyStep, ExprSite, expression_sites, site_position, load_process,
                                   check_process_doc)
from wynd.spec.lockfiles import (StepKind, Tier, Thinking, Effect, RetryPolicy, DEFAULT_RETRIES,
                                 effective_retries, ToolSnapshot, McpToolSnapshot, McpSnapshot, ShellLock,
                                 StepLock, EdgeLock, EdgesLock, WyndBase, BaseChoice, LockedUnit, LockedProcess,
                                 LockedPackage, VenvPlan, ProcessLock, load_step_lock, load_edges_lock,
                                 load_process_lock)
from wynd.spec.env_manifest import EnvManifest, EnvCheck, check_env, load_env_manifest
from wynd.spec.records import Summary, StepError, ProcessError, ProcessErrorOutput, STEP_ERROR_SCHEMA
from wynd.spec.context import ContextRef, parse_context_entry
from wynd.spec.hashing import (jsonable, canonical_json, hash_bytes, hash_obj, proto_hash, step_hash,
                               process_hash, interface_hash, normalise_requirement, dependency_set_hash,
                               normalise_volatile)

# wynd.spec.expr
from wynd.spec.expr.errors import ExprError, ExprSyntaxError, EvalError
from wynd.spec.expr.scope import StepState, EdgeState, Scope, new_scope, utc_now
from wynd.spec.expr.evaluator import (parse, evaluate, evaluate_condition, evaluate_value, evaluate_with,
                                      evaluate_limit, truthy, strict_eq, BUILTINS)
from wynd.spec.expr.analysis import (Ref, references, value_references, env_names, check_expression,
                                     StepView, TypeEnv, check_references)
from wynd.spec.expr.infer import infer_type
```

---

## 10. Interfaces

### 10.1 Provided (every other package consumes these)

| Consumer | Uses |
|---|---|
| **runtime** | `ProcessLock` (run plan), `ProcessDoc` (definition), `StepLock`, `RetryPolicy`/`effective_retries`, `Scope`/`new_scope`/`evaluate_*` (executor edge resolution), `split_output`/`output_adapter`/`interface_from_models`/`interface_hash` (worker import check, middleware validation), `build_models` (process input/output validation), `Summary`/`StepError`/`ProcessError`/`ProcessErrorOutput`, `EnvFragment`/`EnvVar` (provider fragments), `normalise_volatile`/`hash_obj`/`Scope.replacements` (cassette keys), `check_env` (in-image `env check` probe), `parse_context_entry` (context assembly), `McpSnapshot.hash` (live MCP verification) |
| **process** (loader, validator, builder, JobRunner interface) | `load_*`, `check_proto_step`, `check_process_doc`, `expression_sites`/`site_position`, `references`/`check_references`/`TypeEnv`/`StepView`, `infer_type`/`check_assignable` (M3), `env_names`, `parse_use`, `WorkspaceConfig`, layout constants, `DEFAULT_MAX_TRAVERSALS`, `Limits` (filled via `model_copy`), `merge_fragments`/`merge_env_vars`, `EnvManifest`, `ProcessLock` and sub-models, `step_hash`/`process_hash`/`dependency_set_hash`, `Interface.with_error`, `STEP_ERROR_SCHEMA`, `Diagnostic`/`SpecError` |
| **compiler** | `ProtoStep` (+ `to_authoring`, `dump_yaml` to append confirmed examples), `build_models` (test generation), `infer_fields`/`describe_fields`, `interfaces_equivalent`, `proto_hash`, `StepLock`, `McpSnapshot`, `EdgesLock` (M5), `DEFAULT_RETRIES`, `DEFAULT_TIER`/`DEFAULT_THINKING` |
| **controller** | `proto_hash` vs `StepLock.proto_hash` (derived *design*/*compiled* status), `step_hash`/`process_hash` (RunRegistry keys), `ProcessDoc.to_authoring` + `dump_yaml` (web edits), `check_process_doc` (fast edit-time feedback), `Interface`/`fields_schema` (run-panel input forms), `describe_fields` |
| **cli** | `WorkspaceConfig().to_authoring()` for `wynd init`; `ProcessDoc` for `wynd new`; `Diagnostic.format` for all output; `check_env` |
| **web** (via controller JSON) | JSON Schemas from `Interface` and `fields_schema`; the opaque `ui` block on `ProcessDoc` for node positions |
| **kube** | `EnvManifest` (maps to Secrets/ConfigMaps) |

### 10.2 Consumed (defined elsewhere; spec only embeds or refers to them)

| From | What | How spec uses it |
|---|---|---|
| runtime | `TraceSink` pointer string format | stored opaquely in `ProcessError.trace` |
| runtime | cause vocabularies for step and process errors | `Literal`s in `records.py`, which runtime may extend |
| runtime | `Summary.key_outputs` projection algorithm | shape only |
| runtime | storage selector env var names (7.1) and `WYND_HOME` | the builder adds them to `EnvManifest.vars` with `used_by: ["storage"]` / `["runtime"]` |
| runtime | provider names `claude-code` and `anthropic`, and their `EnvFragment`s | `DEFAULT_PROVIDER`; `ProcessLock.providers` |
| runtime / registry owner | MCP server URL env var naming (proposed `WYND_MCP_<SERVER>_URL`), auth env var from the registry | appear in `StepLock.fragment.vars` with `used_by: ["mcp:<server>"]` |
| process (validator) | per-site reachability (which exits each step may have taken, may-be-unrun) | builds `TypeEnv` / `StepView` |
| process (builder) | venv ids, wheel names, distribution names, module-name uniqueness within a closure | stored in `ProcessLock` |
| compiler | how the two-step fallback (9, rule 3) edits `process.yaml` | spec only needs `ProcessDoc.to_authoring` to round-trip |

---

## 11. Interpretations (where the spec is ambiguous; each is the simplest faithful reading)

1. **`use:` has three forms.** Section 5.1 says exactly three and lists them; section 13 says "four". The three listed forms are implemented.
2. **Flat vs nested `outputs`** follows the table in §5.3. Without `exits:`, the mapping is nested only if it has a `done` key and every value is a mapping. A done-only step whose only field is a nested object named `done` is therefore misread. This is documented.
3. **Every YAML string under `when`, `with`, `limits` and `finally[].with` is an expression.** Literal text needs inner quotes (`'"high"'`). YAML quoting is not enough. The error message teaches this.
4. **YAML 1.2 booleans.** `yes`, `no`, `on` and `off` are strings, so exit names such as `no` work. Dates stay `date` objects on load and become ISO strings when they enter the expression value domain or a hash.
5. **`and`/`or` return booleans**, not operands. `default`/`coalesce` cover the "fallback value" use, and `if/then/else` covers value selection (3.4.1 rejects the `a && b || c` idiom).
6. **`not in` is supported.** It is composed from the listed `not` and `in`.
7. **Unary minus exists.** It is needed for negative literals.
8. **Null propagation.** Member or index access on null, or on a missing key, gives null. Ordering, arithmetic and string builtins on null raise `EvalError`, which routes to the process error handler. This is what makes `default(steps.x.outputs.label, "unlabelled")` (3.4.1) work.
9. **`when:` uses truthiness**, not strict booleans. The M3 inference may later warn on non-boolean conditions.
10. **`previous`** is the most recently completed step. During edge resolution this is the edge's source step.
11. **Exit-aware checks allow a guard.** A reference inside `default()`'s first argument, or inside any `coalesce()` argument, may point at a possibly-unrun step or at a field present on only some exits. Without this, the strict rule in 8 would reject safe expressions such as those in `finally` bindings. A field that exists on no exit is always an error.
12. **`finally:` items** may be a step key (Input bound from `process.inputs` by name, like `entry`) or `{step, with}`. The object form is needed so that `close_session` can receive the session id. `on_error` is a step key only. Its Input is the `ProcessError`, and handler exit `x` maps to `$exit.x` implicitly, with outputs passed by field name (3.5: "its exits map to $exit.<name>").
13. **Branch `limits`.**
    - `timeout` is seconds for the target step's run.
    - `retries` overrides the target's lockfile `RetryPolicy` field by field, for runs entered through that branch.
    - `max_traversals: N` allows N takes of that branch.
    - Values may be expressions, which must yield numbers.
14. **`RetryPolicy` has three counters** (`run`, `validation`, `tool`). The defaults follow 3.5. The agentic `run: 1` (restarts for transport failures before the first tool call) is an assumption; the spec gives no number.
15. **Agentic edges (M5).**
    - `kind: agentic` edges keep the same `to:` list, but each `when:` is a prose condition that an LLM judges. The first branch without `when` is still the else.
    - `with:` stays deterministic.
    - Optional edge `instruction` and `context` fields are added.
    - Provider, tier and thinking live in a committed `edges.lock.yaml` (knobs live in lockfiles).
16. **Deterministic edges are interpreted, not code-generated.** 3.4 says "edges are compiled like steps"; in v1 that is satisfied by parsing and caching the AST and checking it at validate time. No generated edge code exists.
17. **Interface and context snapshots live in `step.lock.yaml`** so that the validator never imports step code. The runtime verifies the snapshots at import.
18. **`error` is reserved.** It cannot be declared as an exit. `$exit.error` is always a valid target (cause `explicit`). Examples may expect `exit: error`.
19. **Names match paths.** A process `name` must equal the last segment of its id, and a proto-step `name` must equal its file stem or package directory name ("folder name equals process id"). `process` checks this, because it needs paths.
20. **`latency`** takes `fast` or `normal`, and absent means normal. Only `fast` triggers warnings.
21. **The default provider is `claude-code`** (lead decision). `ProcessDoc.provider` is optional, and `effective_provider` falls back to `DEFAULT_PROVIDER`. Tier-to-model defaults for `claude-code` (cheap→haiku, standard→sonnet, strong→opus) belong in the user registry and never in spec models.
22. **Step-root protos** live at `<root>/<path>/proto.yaml` inside the package directory. Process-local protos live at `processes/<id>/proto/<name>.yaml`.
23. **`system:` package names are used verbatim on either base.** Musl fallback is triggered only by `requires: glibc`.
24. **`exit_codes`** is accepted on any proto, because the compiler picks the kind. When a shell step omits it, the default is `{0: done, "*": error}`.
25. **Env var `one_of` groups.** An unsatisfied group is a warning unless a member is required. This lets a logged-in local `claude` CLI satisfy `claude-code` without a token, while a deployer can still see the token requirement.
26. **Nested mappings and `[T]` lists cannot be optional.** Use `object?` or `list[...]?`. This keeps the type grammar tiny.
27. **`ProcessDoc.ui`** is an opaque block passed through for the web editor's layout. It is excluded from hashes, so moving a node never invalidates a compile.
28. **Example paths** in `path`-typed fields resolve against the process directory (or the package directory for step-root protos).
29. **Lockfiles and manifests carry `wynd: 1`.** Authored documents (`wynd.yaml`, proto, process) do not, because `kind:` identifies them.

---

## 12. Test plan (`packages/spec/tests/`, pytest, fully offline and deterministic)

```
tests/
  test_packaging.py      no src/wynd/__init__.py; wheel-namespace sanity; leaf-import AST lint (§2.4)
  test_yamlio.py         loader, marks, bools, dup keys, flow-style hint, dump round-trip
  test_typelang.py       type strings, schemas, models, outputs normalisation, describe, infer_fields
  test_interface.py      split_output / adapter / interface_from_* / equivalence / hash stability
  test_workspace.py      wynd.yaml and parse_use
  test_proto_step.py     fixtures under fixtures/proto/
  test_process_doc.py    fixtures under fixtures/process/; to: normalisation; sites; check_process_doc
  test_lockfiles.py      StepLock rules; ProcessLock round-trip; EdgesLock
  test_env_manifest.py   merge rules; check_env matrix
  test_records.py        StepError <-> ProcessError nesting; schema constant
  test_hashing.py        canonical_json, proto/step/process/interface/dependency hashes, volatile
  expr/test_parse.py     syntax table + positions + messages
  expr/test_eval.py      evaluation tables (ok and error)
  expr/test_refs.py      references / env_names / check_expression / check_references
  expr/test_infer.py     M3 inference + assignability tables
  expr/test_threads.py   8 threads x 1000 parse+evaluate of distinct expressions; identical results
  test_examples_workspace.py   loads every YAML in examples/invoices with the public loaders (integration)
  fixtures/{proto,process,locks,workspace}/*.yaml  (+ *.expected.txt holding expected diagnostic lines)
```

### 12.1 Expression evaluation: success cases (from the spike's `test_expr.py`; all pass)

The scope fixture:

- steps:
  - `read` done, `{text: "INVOICE 42"}`
  - `extract` done, `{invoice_number: "INV-1042", total: 1200.5, currency: "GBP", label: null, items: [{sku: "A", qty: 2}]}`
  - `validate`, 2 runs, `{valid: false, fixable: true, fields: {total: 12000}, errors: ["bad currency"]}`
  - `fix`, 1 run, `{total: 1}`
  - `save`, never run
- edges: `validate.done` taken `[0, 1, 0]`, name `retry` at index 1
- process inputs: `pdf_path: "/w/in.pdf"`
- env: `REVIEW_DIR`, `RECORDS_DIR`
- `run_id`: `run-123`
- `previous` = `validate`
- frozen clock: `2026-09-22T21:50:03.123456Z`

| Expression | Result |
|---|---|
| `steps.validate.outputs.valid` | `False` |
| `steps.validate.outputs.fixable and steps.fix.runs < 3` | `True` |
| `steps.save.runs` / `steps.save.outputs` / `steps.save.outputs.record` / `steps.save.exit` | `0` / `None` / `None` / `None` |
| `if steps.validate.outputs.fields.total > 10000 then env.REVIEW_DIR else env.RECORDS_DIR` | `"/review"` |
| `if 1 > 2 then "a" elif 2 > 1 then "b" else "c"` | `"b"` |
| `default(steps.extract.outputs.label, "unlabelled")` / `default(0, 5)` | `"unlabelled"` / `0` |
| `coalesce(null, steps.extract.outputs.label, "x", "y")` / `coalesce(null)` | `"x"` / `None` |
| `edges["validate.done"][1].taken` / `edges["validate.done"].retry.taken` | `1` / `1` |
| `process.inputs.pdf_path` / `env.MISSING` / `env["REVIEW_DIR"]` / `run.id` | `"/w/in.pdf"` / `None` / `"/review"` / `"run-123"` |
| `previous.outputs.valid` / `previous.summary.step` | `False` / `"validate"` |
| `1 + 2 * 3` / `(1 + 2) * 3` / `7 / 2` / `7 % 3` / `-7 % 3` / `- 2 - -3` | `7` / `9` / `3.5` / `1` / `2` / `1` |
| `"a" + "b"` / `'it\'s'` / `"é"` / `[1] + [2]` | `"ab"` / `"it's"` / `"é"` / `[1, 2]` |
| `1 == 1.0` / `true == 1` / `null == null` / `"1" == 1` / `[1, true] == [1, true]` / `[1] == [true]` | T / F / T / F / T / F |
| `"GBP" in ["GBP","EUR"]` / `"x" not in ["GBP"]` / `"VOICE" in steps.read.outputs.text` / `"total" in steps.validate.outputs.fields` / `"a" in null` | T / T / T / T / F |
| `not steps.validate.outputs.valid` / `not 1 == 2` / `not []` | T / T / T |
| `1 and "x"` / `0 or ""` / `false and (1 / 0)` / `true or (1 / 0)` | `True` / `False` / `False` / `True` (short circuit) |
| `len(steps.validate.outputs.errors)` / `len("abc")` / `len({a: 1})` / `len(null)` | 1 / 3 / 1 / 0 |
| `lower("AbC")` / `upper(null)` | `"abc"` / `None` |
| `contains(steps.validate.outputs.errors, "bad currency")` / `contains("abc","b")` | T / T |
| `startswith(steps.extract.outputs.invoice_number, "INV-")` / `startswith(null, "x")` | T / F |
| `join(["a","b"])` / `join(["a","b"], " ")` / `join(null, "-")` | `"a,b"` / `"a b"` / `""` |
| `split("a b  c")` / `split("a,b", ",")` / `split(null, ",")` | `["a","b","c"]` / `["a","b"]` / `[]` |
| `now()` | `"2026-09-22T21:50:03Z"` |
| `steps.extract.outputs.items[0].sku` / `…items[5]` / `…items[-1].qty` | `"A"` / `None` / `2` |
| `{a: 1, "b c": [true, null]}` / `[1, 2, 3,]` / `1.5e2` | object / list / `150.0` |
| `steps.x.outputs.in + steps.x.outputs.if` (keyword member names) | `3` |

Results are compared on both value and type (`1` ≠ `1.0` ≠ `True`).

### 12.2 Evaluation errors (`EvalError`; test asserts the substring and the position)

| Expression | Message contains |
|---|---|
| `steps.validate.outputs.fields.total > null` | `cannot compare number > null` |
| `steps.save.outputs.total > 3` | `cannot compare null > number` |
| `"a" < 1` / `true < false` | `cannot compare string < number` / `cannot compare boolean < boolean` |
| `1 + "a"` / `1 + null` | `cannot add number and string` / `cannot add number and null` |
| `1 / 0` / `1 % 0` | `division by zero` |
| `"a" * 2` / `-"a"` | `needs numbers` / `cannot negate string` |
| `unlabelled` | `unknown name 'unlabelled'` |
| `frobnicate(1)` / `len(1, 2)` / `now(1)` / `coalesce()` | `unknown function` / `takes 1 argument(s), got 2` / `takes 0` / `takes 1..99` |
| `len(5)` / `lower(1)` / `join([1, 2])` / `split("a", "")` | `len() of number` / `lower() needs a string` / `join() needs a list of strings` / `non-empty string` |
| `edges["nope.done"][0].taken` / `…[7].taken` / `….bogus.taken` / `…[0].count` | `unknown edge` / `has no branch 7` / `no branch named 'bogus'` / `only have .taken` |
| `steps.read.outputs.text.foo` / `steps.extract.outputs.items["x"]` | `cannot access 'foo' on a string` / `list index must be an integer` |
| `1 in 2` / `1 in "abc"` | `'in' needs a string, list or object` / `'in' a string needs a string` |

### 12.3 Syntax errors (`ExprSyntaxError`: position and message)

| Expression | Position | Message |
|---|---|---|
| `a < b < c` | 1:7 | `unexpected '<'; expected an operator or the end of the expression` |
| `if a then b` | 1:12 | `unexpected end of expression; expected 'elif' or 'else'` |
| `1 +` | 1:4 | `unexpected end of expression; expected a value` |
| `a ==` | 1:5 | `unexpected end of expression; expected a value` |
| `unlabelled here` | 1:12 | `unexpected 'here'; expected an operator or the end of the expression` |
| `x + if a then 1 else 2` | 1:8 | `unexpected 'a'; expected an operator or the end of the expression` |
| `f(a)(b)` | 1:5 | `unexpected '('; expected an operator or the end of the expression` |
| `"unterminated` | 1:1 | `unexpected character '"'` |
| `a &&& b` | 1:3 | `unexpected character '&'` |
| `"\q"` | 1:1 | `invalid escape \q` |
| `` (empty) | 1:1 | `unexpected end of expression; expected a value` |
| `(a` | 1:3 | `unexpected end of expression; expected ')'` |
| `steps.x.` | 1:9 | `unexpected end of expression; expected a name` |
| `if a then b elze c` | 1:13 | `unexpected 'elze'; expected an operator, 'elif' or 'else'` |

### 12.4 References and static checks

- **`references`.** The combined expression from §7.9 yields exactly `[steps.validate.outputs.fields.total, edges.validate.done.retry.taken, env.REVIEW_DIR, steps.fix.outputs.x (guarded), run.id, steps.extract.outputs.items[*].sku, steps.fix.runs]`. This was verified in the spike.
- **`env_names`** over the dogfood process gives `{REVIEW_DIR, RECORDS_DIR}`.
- **`check_expression`** table:

| Expression | Code |
|---|---|
| `steps.x.foo` | `E-REF-SHAPE` |
| `steps.x.exit.y` | `E-REF-SHAPE` |
| `edges[k][0].taken` | `E-REF-SHAPE` (non-literal key) |
| `edges["a.b"]` | `E-REF-SHAPE` (missing `.taken`) |
| `env` | `E-REF-SHAPE` |
| `env.A.b` | `E-REF-SHAPE` |
| `run.idx` | `E-REF-SHAPE` |
| `process.goal` | `E-REF-SHAPE` |
| `previous.exit` | `E-REF-SHAPE` |
| `unlabelled` | `E-REF-NAME`, message contains `'"unlabelled"'` |
| `lenn(x)` | `E-EXPR-FUNC` |
| `default(1)` | `E-EXPR-ARITY` |

- **`check_references`** uses a `TypeEnv` fixture built by hand from the dogfood process interfaces, as the validator would produce it at the site `validate.done[0].with.dest`:
  - `validate`: {done}
  - `fix`: {done}, may_be_unrun
  - `extract`: {done}
  - `save`: unrun only

| Expression | Code |
|---|---|
| `steps.validate.outputs.record` | none |
| `steps.validate.outputs.fields.total` | none (`fields` is open `object`, so the rest is ANY) |
| `steps.vaildate.outputs.x` | `E-REF-STEP` with "did you mean 'validate'" |
| `steps.extract.outputs.totl` | `E-REF-FIELD` |
| `steps.fix.outputs.total` | `E-REF-UNRUN` |
| `default(steps.fix.outputs.total, 0)` | none |
| `steps.save.outputs.record` | `E-REF-UNRUN` |
| `edges["validate.done"].retry.taken` | none |
| `edges["validate.done"].nope.taken` | `E-REF-EDGE` |
| `edges["validate.done"][9].taken` | `E-REF-EDGE` |
| `process.inputs.pdf_path` | none |
| `process.inputs.pdf` | `E-REF-INPUT` |
| `previous.outputs.valid` | none |
| `previous.summary.key_outputs.x` | none |

  An exit-aware case uses a second fixture where `validate` can arrive via `done` or `invalid`, and `invalid` has no `record`:

| Expression | Code |
|---|---|
| `steps.validate.outputs.record` | `E-REF-EXIT`, naming exit `invalid` |
| `coalesce(steps.validate.outputs.record, {})` | none |

  An error-exit case: at an edge `from: read.error`, `steps.read.outputs.message` is valid via `STEP_ERROR_SCHEMA`.

- **`parse_context_entry`**:
  - Accepted: `full_trace`, `process.goal`, `previous.summary`, `steps.classify.outputs`, `steps.classify.outputs.kind`.
  - Rejected with `E-CONTEXT`: `steps.classify`, `steps.x.runs`, `foo`, `steps.x.outputs +1`.

### 12.5 Type inference and assignability (M3)

- **`infer_type`**:

| Expression | Result |
|---|---|
| `steps.fix.runs < 3` | boolean |
| `if c then env.A else "x"` | `anyOf[string, null]` |
| `steps.extract.outputs.total * 2` | number |
| `steps.extract.outputs.invoice_number + 1` | `E-TYPE-OP` |
| `len(steps.validate.outputs.errors)` | integer |
| `default(env.X, "d")` | string |
| `steps.validate.outputs.fields` | `{"type":"object"}` |
| `steps.x.outputs` with exits done{a: string}, other{a: integer} | `anyOf` of the two object schemas |

- **`check_assignable`** cases:

| Source → destination | Result |
|---|---|
| integer → number | ok |
| number → integer | warning |
| `anyOf[string, null]` → string | warning (`W-TYPE-NULL`) |
| string → date | warning |
| date → string | ok |
| path ↔ string | ok |
| boolean → string | error |
| `{}` → anything | ok |
| open object → closed object | ok |
| closed{a} → closed{a, b required} | error ("missing b") |
| `array[integer]` → `array[number]` | ok |
| `array[string]` → `array[integer]` | error |
| `anyOf[integer, string]` → integer | warning |

### 12.6 Type language and outputs normalisation

- **Parse, render and schema round-trip** for the table in §5.2, plus these cases:
  - `list[list[date?]]`
  - `list[ integer ]?`, which renders as `list[integer]?`
  - the YAML sequence form `[{sku: string, qty: integer?}]`
  - nested mappings
- **Parse errors** with exact messages: `strng`, `list[string`, `list[string]]`, `string??`, `[a, b]`, `5`, `{a: strng}` (the message names field `a`).
- **Models**:
  - `build_models` models validate the spike's payloads, including coercions such as `"12.5"`→12.5 and `"2026-10-01"`→date.
  - `extra` fields are rejected.
  - The discriminated union routes on `exit`, and an unknown exit gives `union_tag_invalid`.
  - `model_dump(mode="json")` gives ISO dates.
- **`normalise_outputs`**: one test per row of the §5.3 table, plus:
  - `error` declared gives `E-OUTPUTS`;
  - duplicate exits;
  - a non-mapping value in nested form;
  - the "mixes exit names with field names" message.
- **`infer_fields`**:
  - ints mixed with floats give number;
  - a date string gives date;
  - a key missing in some samples gives optional;
  - lists of dicts give `[mapping]`;
  - all-empty lists give `list[string]`;
  - a string/int conflict gives string.

### 12.7 YAML loader and diagnostics (fixtures with expected formatted output)

Each fixture `fixtures/<kind>/<case>.yaml` has a sibling `<case>.expected.txt` holding the exact `Diagnostic.format()` lines, compared verbatim with `file` relativised. Cases:

| Fixture | Expected |
|---|---|
| `process/bad_expr.yaml` (`elze`) | `…:47:17: error[E-EXPR-SYNTAX] edges[3].to[0].with.dest: unexpected 'elze'; expected an operator, 'elif' or 'else' (expression 1:62)`. The column is exact for single-line plain scalars. |
| `process/folded_expr.yaml` (multi-line plain scalar, as in the spec's `dest:`) | Scalar-start position; the message carries `(expression 1:NN)` and a caret snippet. |
| `process/shorthand_with_error.yaml` (`to: save` + edge-level `with:` containing a bad expression) | The line of the edge-level `with` value, via the `site_position` fallback. |
| `process/unknown_field.yaml` (`whn:` on a branch) | `E-SCHEMA … unknown field 'whn' (did you mean 'when'?)` |
| `process/to_list_with_edge_with.yaml` | `E-TO` |
| `process/bad_use.yaml` (`use: ../x`) | `E-USE` with the three-forms message |
| `process/dup_key.yaml` | `E-YAML-DUP` at the second key |
| `process/flow_optional.yaml` (`{due: date?}`) | `E-YAML` plus the quoting hint |
| `process/kind_wrong.yaml` | `E-KIND` |
| `process/not_mapping.yaml` | `E-YAML-ROOT` |
| `proto/flat_with_exits.yaml` | `E-OUTPUTS` |
| `proto/bad_type.yaml` | `E-TYPE` at the field's line |
| `proto/bad_example.yaml` | via `check_proto_step`: `E-EXAMPLE examples[1].outputs.total: Input should be a valid number…` |
| `proto/exit_codes_unknown.yaml` | `E-EXIT-CODES` |
| `locks/tier_on_deterministic.yaml` | `E-LOCK` |
| `locks/effects_missing.yaml` | `E-LOCK` "effects must declare network (used by tool web_search)" |
| `locks/mcp_allow_mismatch.yaml` | `E-LOCK` |
| `locks/bad_context.yaml` | `E-CONTEXT` |
| `workspace/url_root.yaml` | `E-ROOTS` "url: roots are reserved…" |
| `workspace/nested_roots.yaml` | `E-ROOTS` |
| `workspace/alias_process.yaml` | `E-ROOTS` |

Other loader tests:

- `yes`, `no`, `on` and `off` load as strings; `True` and `false` load as booleans.
- `exit_codes: {0: done, "*": error}` keys load as int and str.
- Dates load as `date`.
- **Round-trip:** for every valid fixture and every file in `examples/invoices`, `parse_model(dump_yaml(doc.to_authoring()))` gives an equal model with the same `proto_hash`/`process_hash`. `to_authoring` emits shorthand `to: x` for single plain branches and flat outputs for done-only docs.
- **`check_process_doc` table**: each code in §6.4 has a minimal fixture, including `W-BRANCH-UNREACHABLE` (a branch after the else) and `E-EXIT-TARGET` (`$exit.nope`).

### 12.8 Hashing

- `canonical_json`:
  - key order independence;
  - `{"é": 1}` is encoded as UTF-8, not escaped;
  - `1.0` ≠ `1`;
  - `date(2026, 10, 1)` becomes `"2026-10-01"`;
  - `Path("a/b")` becomes `"a/b"`;
  - int keys become strings;
  - NaN raises.
- `proto_hash`:
  - unchanged by reformatting, comments, key order, and flat vs `exits: [done]` + nested equivalents;
  - changed by editing the instruction, a type, an example value, or adding an example.
- `step_hash`:
  - ignores `__pycache__`, `*.pyc`, `.pytest_cache` and top-level `dist`;
  - does not ignore `src/pkg/build/x.py`;
  - changes on any source, test, lock or cassette byte;
  - an LFS pointer file and its real content hash identically;
  - is independent of directory walk order.
- `process_hash`:
  - changes when any unit hash changes, including a child process hash;
  - does not change when `ui` changes;
  - raises `ValueError` on a missing unit.
- `interface_hash`:
  - identical for a hand-written pydantic step and `interface_from_fields` of the equivalent proto once titles are normalised. Verify this equivalence with `interfaces_equivalent` returning `[]`; the hash is only required to be stable for the same class.
  - Changes when a field is added.
- `dependency_set_hash`: `["PyPDF_2>=3", "requests  ==2.0"]` hashes the same as `["requests==2.0", "pypdf-2>=3"]`.
- `normalise_volatile`: the run id inside a workspace path becomes `<workspace>`, because the longest key wins. `now()` values become `<now>`. Dict keys are rewritten.

### 12.9 Interface and records

- `split_output` accepts a plain `done` model, `A | B`, `Union[A, B]` and `Annotated[Union[A, B], Field(discriminator="exit")]`.
- `split_output` raises `TypeError` for:
  - a plain model with exit `spam`;
  - a missing `exit` field;
  - a non-Literal exit;
  - a Literal with two values;
  - a default that does not match;
  - duplicate exits;
  - an `error` exit;
  - a non-model union member.
- `output_adapter` round-trips each exit.
- `StepError` with a nested `ProcessError` serialises and validates.
- `STEP_ERROR_SCHEMA` contains `cause` and `message`.

### 12.10 Env manifest

| Case | Expected |
|---|---|
| required and unset | `E-ENV-MISSING`, `ok` is False |
| default present | ok |
| `one_of` group with neither member set, all optional | `W-ENV-ONE-OF`, `ok` is True |
| `one_of` group with neither member set, one member required | `E-ENV-ONE-OF` |
| empty string | counts as unset |

Secret values never appear in any message. The test sets `SECRET=hunter2` and asserts that `hunter2` appears nowhere. `merge_env_vars` raises `E-ENV-CONFLICT` on differing defaults and unions `used_by`.

### 12.11 Performance smoke (not flaky)

10,000 evaluations of a cached condition must take under 0.5 s; the measured time is about 30 ms. One uncached parse must take under 5 ms; measured, about 0.09 ms.

---

## 13. Notes and risks for the synthesizer

1. **Comment loss on rewrite.** PyYAML cannot round-trip comments. The web editor's auto-commit and the compiler's example appending will strip comments from `process.yaml` and proto files. The options are:
   - (a) accept and document it;
   - (b) edit surgically: re-dump only the changed top-level key and splice it into the original text using `SourceMap` marks;
   - (c) approve `ruamel.yaml` for `process`/`controller` (not for spec).

   I recommend (a) for v1 and (b) as a follow-up. This decision is needed from the lead.
2. **Refreshing step-lock snapshots after hand edits.** The 10 CLI list has no command for this. Suggestion:
   - a runtime helper `python -m wynd.runtime.snapshot <package_dir>`, which imports the step in its venv, computes `interface_from_models`, reads `context`, and rewrites `interface` and `context` in `step.lock.yaml`;
   - exposed as `wynd lock <id>`, or folded into `wynd test`, which fails with the exact command to run;
   - the worker's import check, which prevents silent drift.
3. **Module-name collisions.** Two step packages in one closure must not share a top-level import module when they land in the same venv. The builder should namespace them, for example as `wynd_steps.<package_key_slug>`. The process validator should enforce uniqueness.
4. **`process.lock.yaml` as the single run plan** for both local and image mode (§6.7) removes a second executor input format. The runtime and process designers should confirm.
5. **Diagnostic codes are a shared namespace.** Spec owns the `E-YAML*`, `E-SCHEMA`, `E-KIND`, `E-TYPE*`, `E-OUTPUTS`, `E-USE`, `E-TO`, `E-EXPR*`, `E-REF*`, `E-EXAMPLE`, `E-EXIT*`, `E-ENTRY`, `E-STEP-UNKNOWN`, `E-EDGE-DUP`, `E-BRANCH-NAME`, `E-AGENTIC-WHEN`, `E-LOCK`, `E-CONTEXT`, `E-ROOTS`, `E-ENV*` families and the matching `W-` codes. `process` should use `E-GRAPH-*`, `E-RESOLVE-*` and `W-GRAPH-*` for its own checks.
6. **Literal strings in `with:`** will trip authors, as in `label: "unlabelled"`. The message is explicit. The web graph editor's branch/transform editor should offer a "text" vs "expression" toggle that writes `'"text"'` for text.

---

## Appendix A: formats as they appear for the dogfood process

### A.1 `examples/invoices/wynd.yaml`

```yaml
process_roots:
  - processes
step_roots: {}
```

### A.2 `examples/invoices/processes/process_supplier_invoice/process.yaml`

```yaml
kind: process
name: process_supplier_invoice
goal: Turn a supplier invoice PDF into a validated record in the records store.
provider: claude-code
env:
  base: debian-slim-python
  vars:
    RECORDS_DIR: Directory where validated invoice records are written as JSON.
    REVIEW_DIR: Directory for invoices over 10,000 that need a second look.
entry: read
inputs:
  pdf_path: path
outputs:
  done:
    record: object
  not_an_invoice: {}
  needs_review: {}
examples:
  - inputs: { pdf_path: examples/inv1.pdf }
    outputs: { record: { invoice_number: INV-1042, total: 1200.5, currency: GBP, due_date: "2026-10-01" } }
    exit: done
steps:
  read:     { use: ./steps/read_pdf }
  extract:  { use: ./steps/extract_invoice_fields }
  validate: { use: ./steps/validate_fields }
  fix:      { use: ./steps/fix_fields }
  save:     { use: ./steps/save_record }
  escalate: { use: ./steps/escalate_to_human }
edges:
  - from: read.done
    to: extract
    with: { invoice_text: steps.read.outputs.text }
  - from: extract.done
    to: validate
    with: { fields: steps.extract.outputs }
  - from: extract.not_an_invoice
    to: $exit.not_an_invoice
  - from: validate.done
    to:
      - step: save
        when: steps.validate.outputs.valid
        with:
          record: steps.validate.outputs.record
          dest: if steps.validate.outputs.fields.total > 10000 then env.REVIEW_DIR
                else env.RECORDS_DIR
      - step: fix
        name: retry
        when: steps.validate.outputs.fixable and steps.fix.runs < 3
        with:
          fields: steps.validate.outputs.fields
          errors: steps.validate.outputs.errors
      - step: escalate
        with: { fields: steps.validate.outputs.fields, errors: steps.validate.outputs.errors }
  - from: fix.done
    to: validate
    with: { fields: steps.fix.outputs }
  - from: save.done
    to: $exit.done
    with: { record: steps.save.outputs.record }
  - from: escalate.done
    to: $exit.needs_review
```

After validation (done by `process`), the branches `validate.done[1]` (fix) and `fix.done[0]` (validate) sit on the fix↔validate cycle. Both get `limits: {max_traversals: 10}` in the resolved definition inside `process.lock.yaml`. `steps.fix.runs < 3` remains the intended loop exit.

### A.3 `proto/validate_fields.yaml` (flat outputs mean a done-only step) and `proto/read_pdf.yaml`

```yaml
kind: proto_step
name: validate_fields
instruction: |
  Check extracted invoice fields: invoice_number non-empty, total > 0, currency is a 3-letter ISO code,
  due_date is a real date. If valid, produce the record. If invalid but every problem is a formatting issue
  (e.g. "gbp", "1,200.50"), mark it fixable.
inputs:
  fields: object
outputs:
  valid: boolean
  fixable: boolean
  fields: object
  errors: list[string]
  record: object?
examples:
  - inputs: { fields: { invoice_number: INV-1042, total: 1200.5, currency: GBP, due_date: "2026-10-01" } }
    outputs:
      valid: true
      fixable: false
      fields: { invoice_number: INV-1042, total: 1200.5, currency: GBP, due_date: "2026-10-01" }
      errors: []
      record: { invoice_number: INV-1042, total: 1200.5, currency: GBP, due_date: "2026-10-01" }
  - inputs: { fields: { invoice_number: INV-7, total: 10, currency: gbp, due_date: "2026-10-01" } }
    outputs:
      valid: false
      fixable: true
      fields: { invoice_number: INV-7, total: 10, currency: gbp, due_date: "2026-10-01" }
      errors: ["currency must be a 3-letter upper-case ISO code"]
```

```yaml
kind: proto_step
name: read_pdf
instruction: Extract the plain text of every page of the PDF at pdf_path, pages separated by a blank line.
inputs:
  pdf_path: path
outputs:
  text: string
  pages: integer
examples:
  - inputs: { pdf_path: examples/inv1.pdf }
    outputs: { text: "INVOICE INV-1042 ...", pages: 1 }
env:
  deps: ["pypdf>=5"]
```

### A.4 `steps/extract_invoice_fields/step.lock.yaml` (agentic) and `steps/read_pdf/step.lock.yaml` (deterministic)

```yaml
wynd: 1
name: extract_invoice_fields
kind: agentic
entrypoint: extract_invoice_fields.step:ExtractInvoiceFields
proto_hash: sha256:3b1f0c9e5d2a4f6b8c7d9e0f1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d
tier: cheap
thinking: low
retries: { run: 1, validation: 2, tool: 1 }
effects: []
tools: []
mcp: []
context: [process.goal]
fragment:
  deps: []
  system: []
  vars: []
locked_deps: []
interface:
  input:
    title: Input
    type: object
    properties:
      invoice_text: { title: Invoice Text, type: string }
    required: [invoice_text]
  outputs:
    done:
      title: Done
      type: object
      properties:
        exit: { const: done, default: done, title: Exit, type: string }
        invoice_number: { title: Invoice Number, type: string }
        total: { title: Total, type: number }
        currency: { title: Currency, type: string }
        due_date: { format: date, title: Due Date, type: string }
      required: [invoice_number, total, currency, due_date]
    not_an_invoice:
      title: NotAnInvoice
      type: object
      properties:
        exit: { const: not_an_invoice, default: not_an_invoice, title: Exit, type: string }
```

(`provider` is omitted, so the process default `claude-code` applies. The provider's own env fragment, with the `claude-auth` `one_of` group, is added by the builder, not stored here.)

```yaml
wynd: 1
name: read_pdf
kind: deterministic
entrypoint: read_pdf.step:ReadPdf
proto_hash: sha256:9a0c...e1
retries: { run: 0, validation: 0, tool: 0 }
effects: []
fragment:
  deps: ["pypdf>=5"]
locked_deps: ["pypdf==5.4.0"]
interface:
  input:
    title: Input
    type: object
    properties:
      pdf_path: { format: path, title: Pdf Path, type: string }
    required: [pdf_path]
  outputs:
    done:
      title: Done
      type: object
      properties:
        exit: { const: done, default: done, title: Exit, type: string }
        text: { title: Text, type: string }
        pages: { title: Pages, type: integer }
      required: [text, pages]
```

### A.5 `.wynd/build/process_supplier_invoice/<commit>/process.lock.yaml` (abridged)

```yaml
wynd: 1
process: process_supplier_invoice
commit: 9f1c2e7a4b3d5e6f708192a3b4c5d6e7f8091a2b
process_hash: sha256:5d1e...
wynd_base: { version: 0.1.0, variant: slim, image: "wynd-base:0.1.0-slim" }
base: { requested: debian-slim-python, chosen: debian-slim-python, reason: default }
provider: claude-code
system: []
providers:
  claude-code:
    deps: ["claude-agent-sdk>=0.2,<0.3"]
    vars:
      - { name: CLAUDE_CODE_OAUTH_TOKEN, secret: true, required: false, one_of: claude-auth, description: "..." }
      - { name: ANTHROPIC_API_KEY, secret: true, required: false, one_of: claude-auth, description: "..." }
processes:
  process_supplier_invoice:
    path: processes/process_supplier_invoice
    hash: sha256:5d1e...
    definition: { kind: process, name: process_supplier_invoice, ...resolved graph with max_traversals filled... }
    units:
      read: { package: processes/process_supplier_invoice/steps/read_pdf }
      extract: { package: processes/process_supplier_invoice/steps/extract_invoice_fields }
      # validate, fix, save, escalate ...
packages:
  processes/process_supplier_invoice/steps/read_pdf:
    hash: sha256:77aa...
    distribution: wynd-step-read-pdf
    wheel: dist/wynd_step_read_pdf-0.1.0-py3-none-any.whl
    venv: venv-0
    lock: { ...StepLock A.4... }
  processes/process_supplier_invoice/steps/extract_invoice_fields:
    hash: sha256:12bc...
    distribution: wynd-step-extract-invoice-fields
    wheel: dist/wynd_step_extract_invoice_fields-0.1.0-py3-none-any.whl
    venv: venv-1
    lock: { ... }
venvs:
  venv-0:
    deps_hash: sha256:0e4f...
    requirements: ["pypdf==5.4.0"]
    packages: [processes/process_supplier_invoice/steps/read_pdf]
  venv-1:
    deps_hash: sha256:c3d9...
    requirements: ["claude-agent-sdk==0.2.3", "..."]
    packages: [processes/process_supplier_invoice/steps/extract_invoice_fields,
               processes/process_supplier_invoice/steps/fix_fields]
```

### A.6 `.wynd/build/process_supplier_invoice/<commit>/process.env.yaml`

```yaml
wynd: 1
process: process_supplier_invoice
commit: 9f1c2e7a4b3d5e6f708192a3b4c5d6e7f8091a2b
vars:
  - name: ANTHROPIC_API_KEY
    description: Anthropic API key, accepted by claude-code instead of the subscription token.
    secret: true
    required: false
    one_of: claude-auth
    used_by: ["provider:claude-code"]
  - name: CLAUDE_CODE_OAUTH_TOKEN
    description: Claude Code subscription token ("dev key", from `claude setup-token`). Not needed where the claude CLI is logged in.
    secret: true
    required: false
    one_of: claude-auth
    used_by: ["provider:claude-code"]
  - name: RECORDS_DIR
    description: Directory where validated invoice records are written as JSON.
    used_by: ["edge:validate.done"]
  - name: REVIEW_DIR
    description: Directory for invoices over 10,000 that need a second look.
    used_by: ["edge:validate.done"]
  - name: WYND_HOME
    description: User-level registry location (MCP servers, providers). Falls back to ~/.wynd when unset.
    required: false
    used_by: ["runtime"]
  # + storage backend selectors from runtime (7.1), used_by: ["storage"], each with its default
```

### A.7 `edges.lock.yaml` (M5; only if the process had `kind: agentic` edges)

```yaml
wynd: 1
edges:
  extract.done:
    tier: cheap
    thinking: low
    retries: { run: 0, validation: 2, tool: 0 }
```
