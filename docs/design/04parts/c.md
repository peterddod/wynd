## 4. Graph validator (§8, §3.3–3.5, §3.4.1, §6.2)

### 4.1 Diagnostics

```python
Severity = Literal["error", "warning", "info"]

class Diagnostic(BaseModel):
    code: str                       # "E303"
    severity: Severity
    message: str
    process: str | None = None      # process id the diagnostic belongs to (children keep their own id)
    file: str | None = None         # workspace-relative file (process.yaml, wynd.yaml, step.lock.yaml, proto yaml)
    path: str | None = None         # YAML location, e.g. "edges[4].to[1].with.fields" (06 calls this `path`)
    line: int | None = None         # 1-based, from yamlloc
    col: int | None = None          # 1-based; for expressions, column of the scalar start (+ expression column if single-line)

class ValidationReport(BaseModel):
    process: str
    diagnostics: list[Diagnostic]                       # sorted by (process, file, line, code)
    normalized: dict[str, ProcessSpec]                  # per process id in the closure: shorthand expanded,
                                                        # max_traversals filled; this is what goes into ExecutionPlan
    env_refs: dict[str, list[str]]                      # env var name -> ["edge:<from>[i].<field>", ...] (for the manifest)
    @property
    def ok(self) -> bool: ...                           # no severity == "error"

def validate(lp: LoadedProcess, *, providers: ProviderCatalog | None = None) -> ValidationReport: ...
def validate_process(ws: Workspace, pid: str, *, providers: ProviderCatalog | None = None) -> ValidationReport: ...  # (06)
def format_diagnostic(d: Diagnostic) -> str: ...
    # "processes/p/process.yaml:37:9: error E303: …"  or  "[p] warning W206: …" when file is None
```

`ProviderCatalog = Callable[[str], ProviderInfo]`, defaulting to `wynd.runtime.providers.provider_info` (consumed, §15). An unknown provider name raises `KeyError`, which produces W207.

JSON form (`wynd validate --json`, owned by 06):

```json
{"process": "process_supplier_invoice", "ok": false, "diagnostics": [
  {"code": "E303", "severity": "error", "process": "process_supplier_invoice",
   "file": "processes/process_supplier_invoice/process.yaml", "path": "edges[6].to[2].with.note",
   "line": 44, "col": 17,
   "message": "'steps.fix.outputs.note' is not available on every path here: 'fix' may not have run yet. Guard with a when: on steps.fix.runs > 0 or use default()."}]}
```

`yamlloc.build_locmap(text)` uses `yaml.compose()` and walks Mapping and Sequence nodes, building dotted/indexed paths. Each path maps to the value node's `start_mark` as (line + 1, column + 1).

### 4.2 Pass order

`validate(lp)` validates every process in the closure, each with its own interfaces and diagnostics, memoised by id. It then applies the closure-wide rules to the root. Per process:

1. **Normalise edges.** `to: <target>` becomes a single branch carrying the edge-level `with:`/`limits:`, with loc prefix `edges[i]`. The list form keeps loc prefix `edges[i].to[j]`. Every expression string is parsed with `wynd.spec.expr.parse`; a syntax error gives E216, and that expression is then skipped by later passes.
2. **Structure** (§4.3): E201–E207, E213, E214, E217, E218, E220–E222, W201.
3. **Exit coverage** (§4.4): E208.
4. **Reachability** (§4.5): E209.
5. **Cycles and `max_traversals`** (§4.6): I201, and fills `normalized`.
6. **Bindings** (§4.7): E210–E212, E215, E219, plus the M3 type parts.
7. **Dataflow** (§4.8), then an **expression walk** over every `when`/`with`/`limits` under its state (§4.9): E301–E307. The type checker (§4.10) runs inside the same walk: E401–E406, W209.
8. **Child and provider rules** (§4.11): W203–W207.

Closure-wide, on the root: E126 (module collision), and W206 across the closure using the root's `latency` and provider.

Branches after the else (W201) are dropped from `normalized` and from passes 3–7. The runtime ignores them too.

### 4.3 Structural rules

- `entry` must be a key of `steps` (E201).
- `from` must match `^<alias>\.<exit>$` (E202). Aliases may not contain `.`, which the spec model enforces.
- The alias must be declared (E203). The exit must be one of `exit_names(iface)`, which includes `error` (E204); this is skipped when the interface source is `none`.
- At most one edge per `(alias, exit)` (E205).
- A branch target is exactly one of these:
  - a declared alias;
  - `$exit.<name>` where `<name>` is declared in `process.outputs` (E207);
  - `$ignore` (§4.4).
  Anything else gives E206.
- `$exit.error` is reserved (E207, second message). The process `error` exit is produced only by the error handler, never routed to directly.
- `limits.max_traversals`, if given, must be an int ≥ 1 (E217). Other `limits:` fields are type-checked by the spec model.
- Branch `name:` must be unique within its edge, must match `SEG`, and must not be all digits (E222). All-digit names would be ambiguous with indexes in `edges["a.b"].<name>`.
- `on_error` and each `finally` entry must be declared aliases (E213, E214). A handler or finally step must not also be `entry`, a branch target, or the `from` of any edge (E220, E221): its exits map to `$exit` implicitly (§3.5).
- `kind: agentic` edges (M5) go through the same rules. Their agentic-only fields are validated by the spec model (M5 design).

### 4.4 Exits routed or explicitly ignored

For every alias that is not the handler or a finally step, every declared exit except `error` must have an edge (E208). An exit is **explicitly ignored** with:

```yaml
edges:
  - from: extract.not_an_invoice
    to: $ignore          # acknowledged, unhandled: if taken at run time → process error handler (cause "ignored_exit")
```

`$ignore` is allowed only in the shorthand form with no `with:`/`limits:`, or as a single-element list with no `when:` (E218). The implicit `error` exit is exempt: no edge means the error handler. An author may still route `x.error` explicitly. For `on_error` steps, every exit other than `error` must be declared in `process.outputs` (E219), because it becomes `$exit.<name>`.

Runtime contract (consumed): the executor treats a branch target of `$ignore` exactly like an unrouted `error` exit. The `ProcessError.cause` is `"ignored_exit"`.

### 4.5 Reachability

BFS over step aliases. The roots are `entry`, `on_error` and all `finally` steps. The successors of `s` are the step targets of all effective branches of all edges from `s`, regardless of `when:`. Unvisited aliases get E209: `step '<a>' is unreachable from entry '<entry>'`. If the reason is a branch dropped after the else, the message adds `(only targeted by branches ignored after an else; see W201)`.

### 4.6 Cycles and `max_traversals` (SCC-based)

```text
G = directed graph over aliases; edge s→t for every effective branch (s.e → t) whose target is an alias
comp = tarjan_scc(G)              # iterative Tarjan; nodes visited in sorted alias order (membership is order-independent anyway)
for every effective branch b of every edge from s with alias target t:
    if comp[s] == comp[t]:        # s→t is a real edge, so same SCC ⇔ t reaches s ⇔ the branch lies on a cycle (s==t: self-loop)
        if b.limits is None or b.limits.max_traversals is None:
            normalized: b.limits.max_traversals = DEFAULT_MAX_TRAVERSALS  (10, from wynd.spec)
            emit I201 "branch '<s>.<e>' → '<t>' lies on a cycle; max_traversals defaulted to 10"
```

The fill is applied to the in-memory normalised spec, which goes into the `ExecutionPlan` and `process.lock.yaml`. It is never written back to `process.yaml`, so the tree is never dirtied. It is reported as *info*, shown by `wynd validate -v`, to answer §14's over-warning concern. Branches to `$exit`/`$ignore` are never on a cycle. A cycle through a `ProcessStep` is a single node here; cycles inside the child are handled when the child is validated.

Dogfood: SCC `{validate, fix}`. The filled branches are `validate.done` → `fix` (to[1]) and `fix.done` → `validate`. Not filled: `validate.done` → `save` / `escalate` and `extract.done` → `validate`.

### 4.7 Bindings

| Rule | Code | Check |
|---|---|---|
| entry `Input` ↔ `process.inputs` | E210 | Every `process.inputs` name is a property of the entry Input, else `process input(s) {x} are not fields of entry step '{entry}' Input`. Every *required* Input property is in `process.inputs`, else `entry step '{entry}' requires input(s) {x} that process.inputs does not provide`. M3: `type_to_schema(process type)` must be assignable to the property type (E401 at `inputs.<f>`). |
| branch → step `with:` | E212 | Keys must be Input properties (`binds unknown input(s)`). Every required Input property must be bound (`does not bind required input(s)`). For a `ProcessStep` target, the child's inputs. |
| branch → `$exit.<x>` `with:` | E211 | Keys must equal the declared field set of `outputs.<x>` exactly (all declared fields are required): `'$exit.{x}' with: must bind exactly {declared}; missing {m}, unexpected {u}`. |
| `finally` steps | E215 | They take no `with:`. Their required Input properties must be a subset of `process.inputs` (bound by name, like `entry`). |
| `on_error` step | E219, E404, E406 | Exits ⊆ process output exits ∪ {error} (E219). M3: its Input must accept `ProcessError` (`assignable(ProcessError schema → Input)`, E404). Each exit's output type must be assignable to the declared process output fields of that exit (E406). |

When the relevant interface has `precise=False`, E210–E212 are emitted as warnings with the suffix ` [provisional: interface inferred from examples]`. When the source is `none` they are skipped (W129 is already emitted).

### 4.8 Exit-aware dataflow

Goal (§8): `steps.x.outputs.f` is valid at an evaluation point only if, on **every** path from `entry` to that point, `x` has run and its latest exit declares `f`.

**Lattice.** `State = dict[alias, frozenset[str]]`. Each value is the set of possible latest exits of that step, plus the sentinel `NOT_RUN = ""` (`""` is never an exit name). The join is pointwise union. The order is pointwise ⊆. The lattice is finite (subsets of `exit_names ∪ {NOT_RUN}` per alias) and every transfer is monotone, so the worklist terminates.

**Guard atoms.** Parsed from expression ASTs; anything else is "no information":

```text
ExitIs(x, S)     steps.x.exit == "a" | "a" == steps.x.exit | steps.x.exit in ["a", "b"]       (list of string literals)
ExitIsNot(x, S)  steps.x.exit != "a" | not (steps.x.exit == "a") | not (steps.x.exit in [...])
HasRun(x)        steps.x.runs > 0 | steps.x.runs >= 1 | steps.x.runs != 0 | steps.x.exit != null
NotRun(x)        steps.x.runs == 0 | steps.x.exit == null
negate: ExitIs ↔ ExitIsNot (same S), HasRun ↔ NotRun
atoms(e) = conjuncts of the top-level `and` chain of e that parse as atoms (others ignored)
single(e) = the atom if e *is* one atom (no and/or), else None

refine_pos(st, atoms):  ExitIs(x,S): st[x] ∩= S      ExitIsNot(x,S): st[x] −= S
                        HasRun(x):  st[x] −= {NOT_RUN}  NotRun(x):   st[x] ∩= {NOT_RUN}
refine_neg(st, e):      a = single(e); a ? refine_pos(st, [negate(a)]) : st
infeasible(st):         any value is empty
```

Soundness: `ExitIs` removes `NOT_RUN` because `null == "a"` is false. `ExitIsNot` keeps `NOT_RUN` because `null != "a"` is true. Negative refinement is applied only when a whole condition is a single atom, because `not (A and B)` gives no per-atom information. Unknown guards give no refinement, so the analysis only ever over-approximates the reachable exit sets. Errors can therefore be false positives, which the author clears with a guard or `default()`, but never false negatives.

**Transfer and fixpoint.**

```text
INIT = {x: {NOT_RUN} for x in aliases}
IN   = {entry: INIT}                         # absent key = not (yet) reached
work = deque([entry])
while work:
    s = work.popleft()
    for edge in edges_from(s) in declaration order:           # edge.from = s.e ; e may be "error"
        st_e = IN[s] | {s: {e}}                               # s just completed with exit e
        neg  = st_e
        for j, b in enumerate(effective_branches(edge)):
            WHEN_STATE[edge, j] = neg                         # `when` of b is evaluated only if earlier whens were false
            st_b = refine_pos(neg, atoms(b.when))
            WITH_STATE[edge, j] = st_b                        # `with`/`limits` of b are evaluated only if b is taken
            if b targets alias t and not infeasible(st_b):
                new = st_b if t not in IN else join(IN[t], st_b)
                if new != IN.get(t): IN[t] = new; work.append(t)
            neg = refine_neg(neg, b.when)
```

Edges from aliases that never enter `IN` (unreachable, E209) are not checked for references. Handler and finally steps take no expressions and are not part of the flow.

`previous` on an edge `s.e` refers to `(s, e)`, so `previous.outputs.f` is checked against exit `e` of `s` exactly.

### 4.9 Expression walk (reference validity, exit-aware checks, soft positions)

Consumed AST (`wynd.spec.expr`, §15). The walk is `check(node, st, ctx) -> Type`. `ctx` carries: `source=(s, e)`, the loc prefix, `soft: bool`, the process, and the interfaces.

| Node | Rule |
|---|---|
| `Ref steps.x.outputs(.f(.g…)?)?` | `x` unknown → E301. `poss = st[x]`. If `NOT_RUN ∈ poss` and not soft → E303 (`'x' may not have run yet`). With a field `f`: `declaring = {e ∈ exit_names(x) : f ∈ exit_fields(x, e)}`; if empty → E302 (**even when soft**: typo protection); `missing = (poss − {NOT_RUN}) − declaring`; if missing and not soft → E303 listing each exit (`'x' may have last exited via 'e', which has no field 'f'`). Deeper segments are navigated with `navigate`; a missing sub-field → E302 (`'steps.x.outputs.f' has no field 'g'`). With no field (whole `outputs`): only the `NOT_RUN` check. |
| `Ref steps.x.exit` | `x` known (E301). Type `Str`, plus `Null` if `NOT_RUN ∈ poss`. Runtime contract: `steps.x.exit` is `null` before `x` has run. |
| `Ref steps.x.runs` | `x` known. Type `Int`. |
| `Ref steps.x.<other>` | E305 (`unknown member '<other>' of steps.x; expected outputs, exit or runs`). |
| `Ref previous.outputs(.f…)` | Against `(s, e)`: missing field → E303 (`previous ('s') exited via 'e', which has no field 'f'`). `previous.exit` → `Str`. `previous.summary` → `Obj{step, exit, key_outputs, note}`. Other → E305. |
| `Ref process.inputs.f` | `f ∈ process.inputs` else E304. Type from `type_to_schema`. `process.<other>` → E305. |
| `Ref env.NAME` | Always valid. Type `Str`. Records `NAME` in `report.env_refs` with use `edge:<from>[j].<with-key>|when|limits.<k>`. |
| `Ref run.id` | `Str`. `run.<other>` → E305. |
| `Ref edges["k"][i].taken` / `edges["k"].<name>.taken` | `k` must be the `from` of an edge (E306). `i` must be < that edge's effective branch count (E306). `<name>` must be a branch `name:` (E306). Member must be `taken` (E305). Type `Int`. |
| Unknown root | E305. |
| `a and b` | Check `a` under `st`, then `b` under `refine_pos(st, atoms(a))`. Type `Bool`. |
| `a or b` | Check `a` under `st`, then `b` under `refine_neg(st, a)`. Type `Bool`. |
| `not a` | Type `Bool`. |
| `if c1 then v1 elif c2 then v2 else v3` | `c_i` under `neg_{<i}`. `v_i` under `refine_pos(neg_{<i}, atoms(c_i))`. `else` under `neg_all`. Type is the union of the arms. |
| `default(a, b)` | `a` is checked with `soft=True` **iff `a` is directly a `Ref`**; `b` normally. Type `nonnull(a) ∪ b`. |
| `coalesce(a1, …, an)` | `a1…a(n-1)` are soft iff they are directly `Ref`s; `an` is normal. Type `∪ nonnull(ai) ∪ an`. |
| Other `Call` | Name must be in `wynd.spec.expr.BUILTINS` with its arity (E307). Argument types are checked in M3. |
| Operators, literals | See §4.10. |

**Soft-position contract** (consumed; runtime evaluator must agree): in a soft position, an unavailable reference evaluates to `null` rather than raising. Unavailable means: the step has not run, the latest exit lacks the field, or a sub-field is missing. This is what makes `default(steps.extract.outputs.label, "unlabelled")` (§3.4.1) meaningful.

Where to check. For every edge `s.e` and effective branch `j`:
- `when` under `WHEN_STATE[edge, j]`.
- each `with:` value and each expression-valued `limits:` field under `WITH_STATE[edge, j]`.

`with:` values that are YAML mappings or lists are walked recursively. YAML scalars that are not strings are literals. YAML strings are expressions. That convention is owned by spec and runtime; the validator only follows it.

References against a `precise=False` interface downgrade E302 and E303 to warnings with the provisional suffix. References to a step whose interface source is `none` skip field checks.

### 4.10 Type checking (M3)

The type checker runs from M1 as part of the same walk. It is naturally lenient where schemas are `any` (provisional or unknown interfaces), which is how the §3.4.1 staging ("types land in M3") is met without a flag. M3 adds precise schemas from compiled lock snapshots and inferred proto schemas.

```python
Type = Any | Null | Bool | Int | Num | Str(fmt: str | None) | List(item: Type) | Obj(fields: dict[str, Type], required: frozenset[str], open: bool) | Union(members: frozenset[Type])

def from_schema(schema: dict, defs: dict) -> Type
    # string(+format) → Str(fmt); integer → Int; number → Num; boolean → Bool; null → Null
    # array → List(from_schema(items)); object with properties → Obj(..., open = additionalProperties is not False)
    # object without properties / additionalProperties schema → Obj({}, ∅, open=True); anyOf/oneOf → Union
    # enum/const of strings → Str; {} or missing → Any; $ref resolved via defs
def assignable(src: Type, dst: Type) -> bool
    # Any on either side → True; Union src → all members assignable; Union dst → any member accepts
    # Int → Num ok; Str(fmt) → Str(any fmt) ok (pydantic parses "2026-10-01" into date/path)
    # Null → only if dst admits Null; List → items assignable
    # Obj → Obj: every dst.required field present in src (required in src or src open) and assignable;
    #          extra src fields allowed (pydantic ignores extras by default)
```

Operators:

| Operator | Operand types | Result |
|---|---|---|
| `== !=` | any | `Bool` |
| `< <= > >=` | both numeric, or both `Str`, else E402 | `Bool` |
| `in` | right operand `List`, `Str` or `Obj`, else E402 | `Bool` |
| `+` | `Num+Num` → `Num` (`Int` if both `Int`); `Str+Str` → `Str`; `List+List` → `List` | as listed; else E402 |
| `- * %` | numeric | `Num`/`Int` |
| `/` | numeric | `Num` |
| `and or not` | any (truthiness) | `Bool` |

Runtime contract: `and`/`or` return booleans.

Builtins (E403 on argument mismatch):

| Builtin | Signature |
|---|---|
| `len` | `(Str\|List\|Obj) → Int` |
| `lower`, `upper` | `(Str) → Str` |
| `contains` | `(Str\|List, Any) → Bool` |
| `startswith` | `(Str, Str) → Bool` |
| `join` | `(List[Str], Str) → Str` |
| `split` | `(Str, Str) → List[Str]` |
| `default` | `(T, U) → nonnull(T) ∪ U` |
| `coalesce` | `(T…) → ∪` |
| `now` | `() → Str(date-time)` |

Checks:

| Check | Code |
|---|---|
| `with:` value vs target Input property | E401 |
| `$exit` binding value vs declared output type | E405 |
| `when:` whose type is not `Bool` or `Any` | W209 (warning, because truthiness is allowed) |
| on_error input and output | E404, E406 |

### 4.11 Child-process and provider rules

- **W203/W204/W205.** For each `process:` binding whose child **explicitly declares** `env.base`, `provider` or `latency` with a value different from the parent's effective value, warn. The message is: `process step '{alias}' (process:{child}) declares {field} '{child}' but the parent's '{parent}' applies`. A child that omits the field inherits silently.
- **W207.** An effective provider name that `ProviderCatalog` does not know: `provider '{p}' is not installed (entry point group wynd.providers)`.
- **W206** (closure-wide, on the root). If the root has `latency: fast`, then for every step package in the closure with `lock.kind == "agentic"`, compute the effective provider: `lock.provider`, else the **root's** `provider`, else `DEFAULT_PROVIDER` (`"claude-code"`). The root's provider applies to children (§3.2). If `provider_info(p).kind == "agent"`, warn: `process is latency: fast but step '{id}' uses AgentProvider '{p}' (per-call harness startup is recorded in the trace)`.
- **E126** (closure-wide). Two distinct step ids in the closure whose `entrypoint` modules are equal, or whose wheel distribution names are equal: `steps '{a}' and '{b}' both provide module '{m}'; step modules must be unique within a process closure`.

### 4.12 Code table

Severity: E = error, W = warning, I = info. `{…}` are format fields.

| Code | Message template |
|---|---|
| E100 | `no wynd.yaml found in {start} or any parent directory` |
| E101 | `workspace {root} is not inside a git repository` |
| E102 | `wynd.yaml: {loc}: {msg}` |
| E103 | `{kind} root '{path}': {reason}` (must be relative / must not contain '.' or '..' segments / invalid segment '{seg}' / does not exist) |
| E104 | `roots '{a}' and '{b}' overlap; roots must be disjoint` |
| E105 | `step root alias '{alias}' is invalid: {reason}` (must match [a-z][a-z0-9_-]* / 'process' is reserved) |
| E106 | `step root '{alias}': url roots are reserved and not supported in v1` |
| E110 | `process '{inner}' is nested inside process '{outer}'; processes are leaves and must not nest` |
| E111 | `step package '{inner}' is nested inside step package '{outer}'` |
| E112 | `process id '{id}' is defined under both '{a}' and '{b}'` |
| E113 | `process.yaml name '{name}' must equal its folder name '{folder}'` |
| E114 | `'{path}' is a process.yaml under step root '{alias}'; processes live only under process roots` |
| E115 | `step package '{path}' is outside any step root; process-local steps must be at <process>/steps/<name>` |
| E116 | `'{path}': segment '{seg}' is not a valid id segment ([A-Za-z0-9_][A-Za-z0-9_-]*)` |
| E117 | `{file}:{line}:{col}: YAML error: {problem}` |
| E118 | `{file}: {loc}: {msg}` (pydantic validation of process / proto / lock; pyproject.toml unreadable) |
| E119 | `proto-step name '{name}' must equal '{expected}'` |
| E120 | `step '{alias}': use '{use}' is not valid; expected ./steps/<name>, <alias>:<path> or process:<id>{; hint}` |
| E121 | `step '{alias}': unknown step root alias '{root}' (configured: {aliases})` |
| E122 | `step '{alias}': '{use}' does not resolve to a step package or proto-step (looked for {pkg} and {proto})` |
| E123 | `step package '{dir}' has {present} but not {missing}` |
| E124 | `step '{alias}': process '{id}' not found under any process root` |
| E125 | `process reference cycle: {cycle}` |
| E126 | `steps '{a}' and '{b}' both provide module '{module}'; step modules must be unique within a process closure` |
| E127 | `unknown process '{id}'` |
| W128 | `step '{id}': pyproject dependency '{name}' is missing from step.lock.yaml env.deps` |
| W129 | `step '{id}': step.lock.yaml has no interface snapshot; field checks skipped (run wynd validate {pid} --sync-interfaces)` |
| I130 | `step '{alias}': proto-step changed since compile ({old} → {new}); process is in design phase` |
| E201 | `entry '{entry}' is not a declared step` |
| E202 | `edge from '{from}' must be '<step>.<exit>'` |
| E203 | `edge from '{from}': unknown step '{step}'` |
| E204 | `edge from '{from}': step '{step}' has no exit '{exit}' (exits: {exits})` |
| E205 | `exit '{from}' has more than one edge (edges[{i}] and edges[{j}]); one edge per exit` |
| E206 | `branch target '{target}' is not a declared step, $exit.<name> or $ignore` |
| E207 | `'$exit.{name}' is not declared under process outputs (declared: {exits})` / `'$exit.error' is reserved; the error exit is produced by the error handler` |
| E208 | `exit '{step}.{exit}' is not routed; add an edge or route it to $ignore` |
| E209 | `step '{step}' is unreachable from entry '{entry}'{extra}` |
| E210 | see §4.7 |
| E211 | `'$exit.{exit}' with: must bind exactly {declared}; missing {missing}, unexpected {extra}` |
| E212 | `branch to '{target}': with: binds unknown input(s) {extra}` / `branch to '{target}': with: does not bind required input(s) {missing}` |
| E213 | `on_error '{name}' is not a declared step` |
| E214 | `finally step '{name}' is not a declared step` |
| E215 | `finally step '{name}' requires input(s) {missing} that process.inputs does not provide` |
| E216 | `invalid expression: {msg} (column {col})` |
| E217 | `limits.max_traversals must be a positive integer` |
| E218 | `$ignore must be the only, unconditioned target of an edge (to: $ignore)` |
| E219 | `on_error step '{name}' exit '{exit}' is not a declared process output exit` |
| E220 | `step '{name}' is the {role} step and must not also be entry or a branch target` |
| E221 | `edges from {role} step '{name}' are not allowed; its exits map to $exit implicitly` |
| E222 | `branch name '{name}' in edge '{from}' is duplicated or invalid` |
| W201 | `edge '{from}': branch {i} (to '{target}') follows the else branch {j} and is ignored` |
| W202 | `step '{alias}': interface inferred from examples (no declared schemas); field checks are advisory until compile` |
| I201 | `branch '{from}' → '{target}' lies on a cycle; max_traversals defaulted to {n}` |
| W203/W204/W205 | `process step '{alias}' (process:{child}) declares {field} '{child_value}' but the parent's '{parent_value}' applies` |
| W206 | `process is latency: fast but step '{id}' uses AgentProvider '{provider}' (per-call harness startup)` |
| W207 | `provider '{provider}' is not installed (entry point group wynd.providers)` |
| W209 | `when: expression has type {type}; expected a boolean` |
| E301 | `'steps.{step}' is not a declared step` |
| E302 | `step '{step}' declares no output field '{field}' on any exit (exits: {exits})` / `'{prefix}' has no field '{field}'` |
| E303 | `'{ref}' is not available on every path here: {reasons}. Guard with when: (e.g. steps.{step}.exit == "{exit}") or use default()` |
| E304 | `process input '{field}' is not declared (inputs: {inputs})` |
| E305 | `unknown reference '{text}'` |
| E306 | `edges["{key}"]: {reason}` (no such edge / branch index {i} out of range (edge has {n}) / no branch named '{name}') |
| E307 | `unknown function '{name}'` / `{name}() takes {arity} argument(s), got {n}` |
| E401 | `cannot bind {src} to input '{field}' of type {dst}` |
| E402 | `operator '{op}' is not defined for {left} and {right}` |
| E403 | `{name}() argument {i} must be {expected}, got {actual}` |
| E404 | `on_error step '{name}' Input must accept ProcessError ({detail})` |
| E405 | `output '{field}' of $exit.{exit} expects {dst}, got {src}` |
| E406 | `on_error step '{name}' exit '{exit}' output does not match process output '{exit}': {detail}` |

The table lives in `errors.py` as `CODES: dict[str, tuple[Severity, str]]`. A test asserts that every code emitted anywhere is in the table and that every entry is covered by a golden fixture.

### 4.13 Worked example: dogfood

`examples/invoices/processes/process_supplier_invoice/process.yaml` is the §6.2 process with `provider: claude-code`. Expected report: `ok = true`, with two I201 diagnostics (`validate.done[1]` → `fix`, `fix.done[0]` → `validate`) and nothing else. The dataflow gives, among others:

```text
IN[validate] = {read:{done}, extract:{done}, validate:{∅,done}, fix:{∅,done}, save:{∅}, escalate:{∅}}
WITH_STATE[validate.done, 2 (escalate)] has fix:{∅,done}
```

So a hypothetical `with: {note: steps.fix.outputs.note}` on the escalate branch is E303 (`'fix' may not have run yet`). `with: {note: default(steps.fix.outputs.note, "")}` passes, provided `note` exists on some exit of `fix`.

---
