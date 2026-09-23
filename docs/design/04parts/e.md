## 8. Builder (§4, §6.4, §6.5, §8)

### 8.1 Build job flow

`wynd build <id>` is `submit("build", "HEAD", {"process": id, "push": <registry name | null>})` plus a wait (§6.6). Before submitting, the CLI/controller (06) refuses if `git.dirty_paths(ws.root, reference_closure(ws, id))` is non-empty. The job itself only ever sees a commit.

```python
def run_build_job(ctx: JobContext) -> JobOutcome                         # (06: wynd.process.build:run_build_job)
def prepare_build(ctx: JobContext) -> Prepared                            # shared by build and bake: steps 1–6
def build_process(p: Prepared, *, image_builder: ImageBuilder, resolver: Resolver, artefacts: ArtefactStore,
                  user_registry: UserRegistry, push: str | None, platform: str | None, log) -> BuildInfo
```

1. `ws = load_workspace(ctx.workspace)`, `lp = ws.load_process(pid)`, `report = validate(lp)`. Errors → `failed` (report attached). If `push` is set, `user_registry.get("image_registries", push)` must exist, else `failed: "unknown image registry '<name>'"` before anything is built.
2. `env = merge_fragments(lp)`. Design phase → `failed: "process is in design phase: steps … are not compiled (run wynd compile …)"`.
3. `closure = reference_closure(ws, pid)`, `C = git.closure_head(ctx.workspace, closure, ref=ctx.job.base_sha)`. This is the **commit being built** and keys everything below. `source_sha = ctx.job.base_sha` (a later commit with identical closure content).
4. `phash = process_hash(ws.tree, lp)`.
5. **Tests gate.** `rec = ctx.registry.get_test_result(C, f"process:{pid}:{phash}")`. If it is absent or not passed, run `run_replay_tests(ws, pid, commit=C, registry=ctx.registry, venv_root=ctx.workspace_root/".wynd/venvs")`. Not passed → `failed: "tests fail on <C>"` (report attached).
6. `Prepared(ws, lp, report, env, groups=venv_groups(env), commit=C, source_sha, process_hash=phash, runtime=detect_runtime_source())`.
7. `stage = <ws_root>/.wynd/tmp/build-<job-id>/` (removed at the end).
8. `platform = platform or image_builder.native_platform()`, for example `linux/arm64`.
9. `base = choose_base(lp.spec.env.base, env, groups, …)` (§8.3).
10. `find_links = runtime_find_links(runtime, stage/"runtime-wheels")` (§8.2).
11. For each group `g`: `pins = resolver.compile([*g.requirements, f"wynd-spec=={V}", f"wynd-runtime=={V}"], universal=True, python_version="3.12", find_links=find_links)`. Write them to `stage/venvs/<g.key>/requirements.txt`. A `ResolutionError` → `failed: "cannot resolve venv <key> (steps …): <uv stderr tail>"`.
12. For each compiled step package in the closure: `build_step_wheel(pkg, out=stage/"dist")` (§8.4).
13. `manifest = assemble_env_manifest(ws, pid, user_registry, commit=C)` → `stage/process.env.yaml` (§10).
14. `lock = make_process_lock(p, base, platform, groups, pins, wheels, plan=execution_plan(…, mode="image", venv_root="/opt/wynd/venvs", venv_ids={k: k}, ws_root=None))` → `stage/process.lock.yaml` (§8.7).
15. `render_dockerfile(lock)` → `stage/Dockerfile` (§8.5).
16. `ensure_base(base.image, V, base.variant, image_builder=…, log=…)` (§9.3).
17. `tag = f"wynd/{step_slug(pid)}:{C[:12]}"`. Build `ImageBuildRequest(context=stage, dockerfile=stage/"Dockerfile", tags=(tag,), labels=labels(lock, manifest), platforms=(platform,))`.
18. Push if requested (§8.8).
19. `info = artefacts.put_build(stage, BuildInfo(...))`. This moves the build files into `.wynd/build/<pid>/<C>/`, replacing any previous build of the same commit.
20. Outcome: `succeeded`. Artefacts: `build_dir`, `image` (and `image` for the pushed ref). Report: `{commit, source_sha, image, image_id, digest, base: {...}, venvs: n, wheels: n, fallback_reason}`.

Build dir contents. The Docker context is exactly this dir; nothing else is ever in it, in particular no `.env`:

```text
.wynd/build/<process-id>/<commit>/
  Dockerfile
  process.lock.yaml
  process.env.yaml
  dist/<wheel>.whl …                       # one wheel per step (§6.4)
  venvs/<key>/requirements.txt             # resolved pins per venv (part of the venv plan)
  build.json                               # BuildInfo (artefact-store metadata, §11)
```

Reproducibility: every input comes from commit `C` plus the pinned base version. The only network-dependent step is resolution, and its full pins are recorded in `process.lock.yaml`. Rebuilding from the lock produces the same venvs. The compiler is never invoked.

### 8.2 Resolver

```python
class ResolutionError(WyndProcessError):
    stderr: str

class Resolver(Protocol):
    def compile(self, requirements: Sequence[str], *, universal: bool, python_version: str = "3.12",
                python_platform: str | None = None, only_binary: bool = False,
                find_links: Sequence[Path] = ()) -> list[str]: ...        # full pinned set, one requirement per line
    def build_wheel(self, src: Path, out_dir: Path) -> Path: ...

class UvResolver:            # subprocess `uv` (WYND_UV overrides the executable)
    # compile: uv pip compile - --no-header --no-annotate [--universal] --python-version 3.12
    #          [--python-platform P] [--only-binary :all:] [--find-links D]…  (stdin = requirements)
    # build_wheel: uv build --wheel --out-dir <out_dir> <src>   → returns the single new .whl
```

`runtime_find_links(runtime, dest) -> list[Path]`:
- **Editable runtime:** `uv build --wheel` of `packages/spec` and `packages/runtime` into `dest`, returning `[dest]`. This makes `wynd-runtime==V` resolvable before it is published. The wheels are used for resolution only; the image installs the base's vendored copies.
- **Pinned runtime:** `[]` (PyPI has `wynd-runtime==V`).

### 8.3 Base variant choice and musl fallback (§4)

```python
@dataclass(frozen=True)
class BaseChoice:
    requested: Literal["debian-slim-python", "alpine-python"]
    variant: Literal["slim", "alpine"]
    version: str                     # == wynd-runtime version
    image: str                       # base_image_ref(version, variant) e.g. "wynd-base:0.1.0-slim"
    reason: str | None               # why alpine was not used (None when requested == used)

def choose_base(requested, env: MergedEnv, groups, *, resolver, platform, version, find_links) -> BaseChoice:
    if requested == "debian-slim-python": return slim(reason=None)
    if env.glibc_required_by:
        return slim(reason=f"alpine-python requested; fell back to debian-slim-python: requires: glibc declared by {', '.join(env.glibc_required_by)}")
    arch = {"linux/amd64": "x86_64", "linux/arm64": "aarch64"}[platform]
    for g in groups:
        try: resolver.compile([*g.requirements, runtime pins], universal=False,
                              python_platform=f"{arch}-unknown-linux-musl", only_binary=True, find_links=find_links)
        except ResolutionError as e:
            return slim(reason=f"alpine-python requested; fell back to debian-slim-python: venv {g.key} "
                               f"(steps {', '.join(g.steps)}) has no musl-compatible wheels: {first_line(e.stderr)}")
    return alpine(reason=None)
```

"Cannot be satisfied on musl" therefore means one of two things: a fragment declares `requires: glibc`, or the binary-only musllinux resolution fails for the target architecture. One Debian slim beats a partial Alpine. `system:` package names are passed verbatim to the variant's package manager (`apt-get` or `apk`); see I-16.

### 8.4 Step wheels (§6.3)

`build_step_wheel(tree, pkg, lock, *, out, resolver, stage_root) -> Path`:

1. Stage: copy the package's tracked files (from `tree.files`) under `pkg.dir` into `stage_root/<slug>/`, excluding `cassettes/` and `test_*.py`. The wheel only includes `wynd_steps/` anyway, but excluding them keeps the build context small.
2. The `entrypoint` must be `wynd_steps.<mod>:<Class>` and `wynd_steps/<mod>/__init__.py` must exist. If not, fail with `"step <id>: entrypoint must be wynd_steps.<module>:<Class> with a package directory"`.
3. Write the wheel metadata to `stage_root/<slug>/wynd_steps/<mod>/wynd-step.json`:

   ```json
   {"schema": 1, "step": "process_supplier_invoice#read_pdf", "entrypoint": "wynd_steps.process_supplier_invoice_read_pdf:ReadPdf",
    "kind": "deterministic", "proto_hash": "sha256:3f0c…", "step_hash": "sha256:a41d…",
    "requirements": ["pypdf==5.4.0"], "effects": ["filesystem"], "tools": [], "mcp": []}
   ```

   It carries **no `tier`, `thinking` or `provider`**, so wheels are reusable across processes at different tiers (§6.3). `requirements` is the step's pinned set (`lock.env.deps`).
4. `resolver.build_wheel(stage, out)`. Two steps in the closure that produce the same wheel filename give E126 at validation, so this cannot happen at build time.

Wheels are installed with `--no-deps`. The venv's pinned `requirements.txt` supplies everything, which guarantees "nothing undeclared is ever installed".

### 8.5 Process Dockerfile (one per process)

`render_dockerfile(lock: ProcessLock) -> str` is a pure function of the lock, which makes golden tests possible. Rules:
- Venvs sorted by id. Wheels sorted by filename. `system` sorted.
- The `RUN apt-get`/`apk` line is emitted only if `system_packages` is non-empty.
- Build inputs are bind-mounted, not copied, so they leave no layer.
- A final `check-plan` import smoke test runs before switching to the non-root user.

Dogfood, slim:

```dockerfile
# syntax=docker/dockerfile:1.7
# Generated by wynd-process 0.1.0 for process process_supplier_invoice at commit 9f1c0a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f.
# Do not edit: rebuild with `wynd build process_supplier_invoice`.
FROM wynd-base:0.1.0-slim
USER root
RUN --mount=type=cache,target=/var/cache/uv,sharing=locked \
    --mount=type=bind,source=venvs,target=/opt/wynd/build/venvs \
    --mount=type=bind,source=dist,target=/opt/wynd/build/dist \
    uv venv --python /usr/local/bin/python3.12 /opt/wynd/venvs/0b4c7e21d9a86f35 \
 && uv pip install --python /opt/wynd/venvs/0b4c7e21d9a86f35/bin/python --no-deps --find-links /opt/wynd/wheels \
      -r /opt/wynd/build/venvs/0b4c7e21d9a86f35/requirements.txt \
 && uv pip install --python /opt/wynd/venvs/0b4c7e21d9a86f35/bin/python --no-deps \
      /opt/wynd/build/dist/wynd_step_process_supplier_invoice_escalate_to_human-0.1.0-py3-none-any.whl \
      /opt/wynd/build/dist/wynd_step_process_supplier_invoice_save_record-0.1.0-py3-none-any.whl \
      /opt/wynd/build/dist/wynd_step_process_supplier_invoice_validate_fields-0.1.0-py3-none-any.whl \
 && uv venv --python /usr/local/bin/python3.12 /opt/wynd/venvs/5e1a90c3b7f24d18 \
 && uv pip install --python /opt/wynd/venvs/5e1a90c3b7f24d18/bin/python --no-deps --find-links /opt/wynd/wheels \
      -r /opt/wynd/build/venvs/5e1a90c3b7f24d18/requirements.txt \
 && uv pip install --python /opt/wynd/venvs/5e1a90c3b7f24d18/bin/python --no-deps \
      /opt/wynd/build/dist/wynd_step_process_supplier_invoice_extract_invoice_fields-0.1.0-py3-none-any.whl \
      /opt/wynd/build/dist/wynd_step_process_supplier_invoice_fix_fields-0.1.0-py3-none-any.whl \
 && uv venv --python /usr/local/bin/python3.12 /opt/wynd/venvs/9d7f2a6b0c1e4f83 \
 && uv pip install --python /opt/wynd/venvs/9d7f2a6b0c1e4f83/bin/python --no-deps --find-links /opt/wynd/wheels \
      -r /opt/wynd/build/venvs/9d7f2a6b0c1e4f83/requirements.txt \
 && uv pip install --python /opt/wynd/venvs/9d7f2a6b0c1e4f83/bin/python --no-deps \
      /opt/wynd/build/dist/wynd_step_process_supplier_invoice_read_pdf-0.1.0-py3-none-any.whl
COPY process.lock.yaml process.env.yaml /opt/wynd/process/
RUN /opt/wynd/supervisor/bin/wynd-supervisor check-plan /opt/wynd/process/process.lock.yaml
USER wynd
LABEL dev.wynd.process="process_supplier_invoice" \
      dev.wynd.commit="9f1c0a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f" \
      dev.wynd.base-version="0.1.0" \
      dev.wynd.base-variant="slim" \
      dev.wynd.run-api-port="8080" \
      org.opencontainers.image.revision="9f1c0a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f"
```

When `system_packages` is non-empty, this is inserted after `USER root`:

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils \
 && rm -rf /var/lib/apt/lists/*                          # slim
RUN apk add --no-cache poppler-utils                     # alpine
```

Notes:
- `ENTRYPOINT`/`CMD` are inherited from the base (supervisor). `WYND_PLAN`, `WYND_ENV_MANIFEST` and `WYND_VENV_ROOT` are also set by the base (§9).
- `spec` + `runtime` are installed into every venv as normal packages from the pinned `requirements.txt`, which contains `wynd-spec==V` and `wynd-runtime==V` resolved from `/opt/wynd/wheels`. There is no `--system-site-packages`, so every venv is hermetic (§4).
- The `dev.wynd.env-manifest` label (06 asks for it: compact JSON manifest) is passed as a build **label** from `ImageBuildRequest.labels`, not written into the Dockerfile text. The manifest holds no secret values.

### 8.6 `ImageBuilder` (BuildKit behind an interface)

```python
@dataclass(frozen=True)
class ImageBuildRequest:
    context: Path
    dockerfile: Path
    tags: tuple[str, ...]
    labels: Mapping[str, str] = field(default_factory=dict)
    platforms: tuple[str, ...] = ()          # () = builder native
    build_args: Mapping[str, str] = field(default_factory=dict)
    push: bool = False                       # push all tags as part of the build (multi-platform publish)

@dataclass(frozen=True)
class ImageBuildResult:
    tags: tuple[str, ...]; image_id: str | None; digest: str | None

class ImageBuilder(Protocol):
    name: str
    def build(self, req: ImageBuildRequest, log: Callable[[str], None]) -> ImageBuildResult: ...
    def exists(self, ref: str) -> bool: ...
    def push(self, ref: str, log: Callable[[str], None]) -> str: ...        # returns repo digest "sha256:…"
    def tag(self, src: str, dst: str) -> None: ...
    def login(self, registry_host: str, username: str, password: str) -> None: ...
    def native_platform(self) -> str: ...                                    # e.g. "linux/arm64"

def open_image_builder(environ: Mapping[str, str] = os.environ) -> ImageBuilder
    # name = environ.get("WYND_IMAGE_BUILDER", "buildx"); load entry point group "wynd.image_builders"
    # (kube package registers e.g. "buildctl" for rootless BuildKit in-cluster — backend by env var, §15)
```

`BuildxImageBuilder` (subprocess `docker`, output streamed to `log`):

| Method | Command |
|---|---|
| build | `docker buildx build --progress=plain --file F [--tag T]… [--label K=V]… [--build-arg K=V]… [--platform P1,P2] (--push \| --load) --metadata-file M CONTEXT`. Digest from `M["containerimage.digest"]`, image id from `M["containerimage.config.digest"]`. |
| exists | `docker image inspect REF` (rc == 0) |
| push | `docker push REF`, digest parsed from `digest: sha256:…` |
| tag | `docker tag SRC DST` |
| login | `docker login HOST -u USER --password-stdin` |
| native_platform | `docker version --format '{{.Server.Os}}/{{.Server.Arch}}'` |

It uses the default buildx builder, the **docker driver** (verified on this machine: `default`/`desktop-linux`, BuildKit v0.27, containerd image store). This is what lets `FROM wynd-base:<ver>-<variant>` resolve a **local** tag with no registry, and it supports multi-platform `--push` for `wynd base publish`.

### 8.7 `process.lock.yaml`

Model: `wynd.spec.plan.ProcessLock`, authored here. It is read by the supervisor, since it contains the `ExecutionPlan`. It carries no timestamps (build time is in the job record and `build.json`), so the same commit produces a byte-identical lock.

```yaml
schema_version: 1
process: process_supplier_invoice
commit: 9f1c0a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f        # closure HEAD = "git commit hash of the compile tree"
source_sha: 1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e    # commit checked out by the job (same closure content)
process_hash: sha256:77aa…
wynd_version: 0.1.0                                     # wynd-process that produced this build
platform: linux/arm64
base:
  requested: debian-slim-python
  variant: slim
  version: 0.1.0                                        # wynd-base version == wynd-runtime version
  image: wynd-base:0.1.0-slim
  reason: null                                          # e.g. "alpine-python requested; fell back to debian-slim-python: requires: glibc declared by step:shared:ocr"
system_packages: []
fragments:
  - {source: "provider:claude-code", deps: ["claude-agent-sdk>=0.2,<0.3"], system: [], requires: null}
  - {source: "step:process_supplier_invoice#read_pdf", deps: ["pypdf==5.4.0"], system: [], requires: null}
  # … one per step and per provider in use
steps:
  "process_supplier_invoice#read_pdf":
    dir: processes/process_supplier_invoice/steps/read_pdf
    hash: sha256:a41d…
    wheel: dist/wynd_step_process_supplier_invoice_read_pdf-0.1.0-py3-none-any.whl
    lock: {…verbatim step.lock.yaml, including tier NAMES; never a tier→model mapping…}
venvs:
  - id: 9d7f2a6b0c1e4f83
    inputs: ["pypdf==5.4.0"]
    requirements: [annotated-types==0.7.0, lark==1.2.2, pydantic==2.11.9, pydantic-core==2.33.2,
                   pypdf==5.4.0, pyyaml==6.0.2, typing-extensions==4.14.1, typing-inspection==0.4.1,
                   wynd-runtime==0.1.0, wynd-spec==0.1.0]
    steps: ["process_supplier_invoice#read_pdf"]
  # …
plan: {…ExecutionPlan, mode: image, venv_root: /opt/wynd/venvs…}
```

### 8.8 Registry push

`push` names an entry from the user registry section `image_registries` (06 `UserRegistry.get("image_registries", name)`):

```yaml
{name: ghcr, url: ghcr.io/peterddod, username: peterddod, password_env: GHCR_TOKEN}
```

Push steps:
1. `remote = f"{url}/{step_slug(pid)}:{C[:12]}"`.
2. If `password_env` is set and present in the environment: `image_builder.login(host(url), username, os.environ[password_env])`. Otherwise rely on the existing docker credential store.
3. `image_builder.tag(local, remote)`, then `digest = image_builder.push(remote)`.

`BuildInfo.pushed = [f"{remote}@{digest}"]`. An unknown registry name fails the job before anything is built (checked in step 1 of §8.1).

### 8.9 `bake` (single executable; §8, §13)

A job kind of its own (06: `wynd.process.bake:run_bake_job`; CLI `wynd bake <id>`). It shares `prepare_build` (validation, design check, tests gate), so a bake is also commit-pinned and test-gated.

Bakeable iff:
1. `env.system` is empty, and no step has `kind: shell`. "Pure-Python" means the process needs nothing but a Python 3.12 interpreter.
2. The union of all groups' requirements plus the runtime pins resolves as **one** environment for the host platform (`resolver.compile(universal=False)`). If not: `"not bakeable: steps' dependency sets conflict (<uv error>); use wynd build"`.

Steps:

```text
stage/site/         ← uv pip install --target stage/site --python 3.12 --no-deps [--find-links runtime wheels] -r reqs.txt
                    ← uv pip install --target stage/site --python 3.12 --no-deps dist/*.whl
stage/_wynd_bake/plan.json        ExecutionPlan(mode="image", venv_root="", venvs=[PlanVenv(id="bake", steps=[all])], …)
stage/_wynd_bake/process.env.yaml the env manifest
stage/_wynd_bake/meta.json        {"id": sha256(of everything above)[:16], "process", "commit", "platform", "wynd_version"}
stage/__main__.py                 templates/bake_main.py (stdlib only)
zipapp.create_archive(stage, <build dir>/<slug>.pyz, interpreter="/usr/bin/env python3.12", compressed=True)
```

Bootstrap (`bake_main.py`):
1. Open its own archive (`sys.argv[0]`) and read `meta.json`.
2. Extract once to `$WYND_HOME/bake/<id>/`. `WYND_HOME` falls back to `~/.wynd` only when unset (§7.1). Extraction goes to a temp dir first, restores exec bits from `ZipInfo.external_attr >> 16` (needed for the `claude` binary bundled in `claude-agent-sdk`), writes a `.complete` marker, then does an atomic rename.
3. Put `site` first on `sys.path` and call `wynd.runtime.bake.main(plan_path, site, argv)` (consumed). That function sets `venvs[0].python = sys.executable` and `sys_path = [site]`, runs the process with inputs from argv/stdin, prints the outputs JSON, and returns the exit code. Every worker is the same interpreter, so there is still one worker per (single) venv.

**Why zipapp and not pex/shiv:** pydantic-core is a compiled extension, which `zipimport` cannot load. Any single-file Python app must therefore extract to disk. shiv's core is exactly this extract-once bootstrap (~60 lines), and adding it or pex as a dependency buys nothing over stdlib `zipapp` plus our bootstrap. The output is platform-specific (host platform); cross-platform bake is out of scope for v1.

Result: `BuildInfo.bake = "<build dir>/<slug>.pyz"`, and the artefact kind is `bake`.

---

## 9. `wynd-base` image family (§4, §10)

### 9.1 Template

A single template, `templates/base.Dockerfile`, with `@TOKEN@` substitution (Dockerfile `$` syntax stays untouched). Two variant blocks are selected in Python. Constants live in `base.py`:

```python
UV_VERSION = "0.10.7"
PYTHON_IMAGES = {"slim": "python:3.12-slim-trixie", "alpine": "python:3.12-alpine3.22"}
USER_SETUP = {
    "slim":   "groupadd --system --gid 10001 wynd && useradd --system --uid 10001 --gid 10001 --home-dir /home/wynd --create-home wynd",
    "alpine": "addgroup -S -g 10001 wynd && adduser -S -u 10001 -G wynd -h /home/wynd wynd",
}
```

Rendered `wynd-base:0.1.0-slim`:

```dockerfile
# syntax=docker/dockerfile:1.7
# wynd-base 0.1.0-slim. Generated by wynd-process 0.1.0 (`wynd base build 0.1.0`). Apache-2.0 contents (spec, runtime).
FROM ghcr.io/astral-sh/uv:0.10.7 AS uv
FROM python:3.12-slim-trixie
COPY --from=uv /uv /uvx /usr/local/bin/
ENV PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_CACHE_DIR=/var/cache/uv \
    WYND_BASE_VERSION=0.1.0 \
    WYND_BASE_VARIANT=slim \
    WYND_HOME=/var/lib/wynd \
    WYND_VENV_ROOT=/opt/wynd/venvs \
    WYND_PLAN=/opt/wynd/process/process.lock.yaml \
    WYND_ENV_MANIFEST=/opt/wynd/process/process.env.yaml \
    WYND_PORT=8080
COPY wheels/ /opt/wynd/wheels/
COPY runtime-requirements.txt /opt/wynd/runtime-requirements.txt
RUN --mount=type=cache,target=/var/cache/uv,sharing=locked \
    uv venv --python /usr/local/bin/python3.12 /opt/wynd/supervisor \
 && uv pip install --python /opt/wynd/supervisor/bin/python --no-deps --find-links /opt/wynd/wheels \
      -r /opt/wynd/runtime-requirements.txt \
 && groupadd --system --gid 10001 wynd && useradd --system --uid 10001 --gid 10001 --home-dir /home/wynd --create-home wynd \
 && mkdir -p /opt/wynd/venvs /opt/wynd/process /var/lib/wynd /work \
 && chown wynd:wynd /var/lib/wynd /work
WORKDIR /work
USER wynd
EXPOSE 8080
LABEL dev.wynd.base-version="0.1.0" dev.wynd.base-variant="slim" org.opencontainers.image.licenses="Apache-2.0"
ENTRYPOINT ["/opt/wynd/supervisor/bin/wynd-supervisor"]
CMD ["serve"]
```

The alpine variant is identical except for two lines: `FROM python:3.12-alpine3.22` and the `adduser` line. uv's distributed binary is statically linked, so it runs on musl.

What the base contains (§4):
- Python 3.12.
- `uv` (pinned).
- The vendored `wynd-spec` + `wynd-runtime` wheels at version V in `/opt/wynd/wheels`. Process venvs install from here.
- A supervisor venv containing only spec + runtime and their pins.
- The supervisor entrypoint `wynd-supervisor` (not named `wynd`).
- The worker entry `python -m wynd.runtime.worker`, available in every venv because runtime is installed in each.
- Nothing provider-specific: providers contribute fragments (§3.9); there is no agent variant.

### 9.2 Build and publish

```python
def base_image_ref(version: str, variant: str, repo: str | None = None) -> str
    # repo = repo or os.environ.get("WYND_BASE_REPO", "wynd-base") → f"{repo}:{version}-{variant}"
def render_base_dockerfile(version: str, variant: Literal["slim", "alpine"]) -> str
def build_base(version: str, variants: Sequence[str] = ("slim", "alpine"), *, log, image_builder: ImageBuilder | None = None,
               runtime: RuntimeSource | None = None, resolver: Resolver | None = None, repo: str | None = None,
               platforms: Sequence[str] = (), push: bool = False) -> list[str]            # (06) returns refs
def publish_base(version: str, variants: Sequence[str], registry: Mapping[str, str], *, log,
                 platforms: Sequence[str] = ("linux/amd64", "linux/arm64"), image_builder=None) -> list[str]   # (06)
```

`build_base`:
1. `runtime = detect_runtime_source()`. If `runtime.editable` and `runtime.version != version` → error `"source tree is wynd-runtime {runtime.version}; cannot build base {version}"`. The runtime version and the base version are the same number (§4).
2. Create a context tempdir under `.wynd/tmp/` (or the system tempdir outside a workspace):
   - `wheels/`: `uv build --wheel packages/spec packages/runtime` if editable, else empty (the index is used).
   - `runtime-requirements.txt`: `uv pip compile --universal --python-version 3.12 --find-links wheels` of `wynd-runtime==V`.
   - `Dockerfile` per variant.
3. `image_builder.build(ImageBuildRequest(tags=(base_image_ref(version, variant, repo),), platforms=platforms, push=push))` per variant. Default: native platform, `--load` into the local image store.

`publish_base` is `build_base` with `repo=f"{registry['url']}/wynd-base"`, `platforms=("linux/amd64", "linux/arm64")` and `push=True` (BuildKit multi-platform push), after `login` if `password_env` is set.

### 9.3 Local builds without a registry

`ensure_base(ref, version, variant, *, image_builder, log)`:
1. If `image_builder.exists(ref)`, return.
2. If `ref`'s repository has no registry host (the default `WYND_BASE_REPO=wynd-base`), run `build_base(version, [variant])` now. This works whenever the runtime source is available, which is the monorepo dev loop. If it is not available, fail with `"base image {ref} not found locally; run wynd base build {version} or set WYND_BASE_REPO to a registry"`.
3. If `ref` names a registry (for example `WYND_BASE_REPO=ghcr.io/peterddod/wynd-base`), do nothing: BuildKit pulls on `FROM`.

Local builds therefore resolve `FROM wynd-base:0.1.0-slim` from the local image store, which works because buildx uses the docker driver.

Dev-loop caveat: runtime source edits reach images only after `wynd base build <ver>`. Local mode, being editable, sees them immediately.

---

## 10. Env manifest and `wynd env check` (§4, §4.1, §3.8)

### 10.1 Format

Model: `wynd.spec.envmanifest`, authored here and usable in-image. `used_by` is a list of strings, as in (06).

```python
Mode = Literal["local", "image"]

class EnvVar(BaseModel):
    name: str
    description: str = ""
    secret: bool = False
    required: bool = True                 # ignored for group members (the group decides)
    default: str | None = None            # documented default (e.g. storage selectors); satisfies `required`
    group: str | None = None              # member of an alternatives group
    modes: list[Mode] = ["local", "image"]  # modes in which `required` is enforced
    used_by: list[str] = []               # "step:<id>", "tool:<step id>/<tool>", "mcp:<server>", "provider:<name>",
                                          # "edge:<process>:<from>[<j>].<field>", "storage", "runtime"

class EnvGroup(BaseModel):
    description: str = ""
    min: int = 1
    modes: list[Mode] = ["local", "image"]

class EnvManifest(BaseModel):
    schema_version: Literal[1] = 1
    process: str
    commit: str | None                    # None when assembled from a working tree
    vars: list[EnvVar]                    # sorted by name
    groups: dict[str, EnvGroup] = {}

class EnvProblem(BaseModel):
    code: Literal["missing", "group_unsatisfied"]
    name: str                             # var or group name
    message: str

def check_env(manifest: EnvManifest, environ: Mapping[str, str], *, mode: Mode) -> list[EnvProblem]:
    # var (no group), required, no default, mode ∈ var.modes, environ.get(name, "") == "" → missing
    # group g with mode ∈ g.modes: count(v in members if environ.get(v.name)) < g.min → group_unsatisfied
```

Dogfood `process.env.yaml`:

```yaml
schema_version: 1
process: process_supplier_invoice
commit: 9f1c0a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f
vars:
  - name: ANTHROPIC_API_KEY
    description: Anthropic API key accepted by the claude-code provider (alternative to CLAUDE_CODE_OAUTH_TOKEN).
    secret: true
    group: claude-code-auth
    used_by: ["provider:claude-code", "step:process_supplier_invoice#extract_invoice_fields", "step:process_supplier_invoice#fix_fields"]
  - name: CLAUDE_CODE_OAUTH_TOKEN
    description: Claude Code subscription token (create with `claude setup-token`); the dev key for containers and CI.
    secret: true
    group: claude-code-auth
    used_by: ["provider:claude-code", "step:process_supplier_invoice#extract_invoice_fields", "step:process_supplier_invoice#fix_fields"]
  - name: RECORDS_DIR
    description: Referenced by edge expression.
    used_by: ["edge:process_supplier_invoice:validate.done[0].dest"]
  - name: REVIEW_DIR
    description: Referenced by edge expression.
    used_by: ["edge:process_supplier_invoice:validate.done[0].dest"]
  - name: WYND_HOME
    description: User-level registries location (MCP servers, provider tiers).
    required: false
    default: ~/.wynd
    used_by: ["runtime"]
  - name: WYND_RUN_REGISTRY
    description: RunRegistry backend selector.
    required: false
    default: local
    used_by: ["storage"]
  # … WYND_TRACE_SINK, WYND_WORKSPACE_STORE, WYND_REGISTRY (from runtime.storage_env_vars())
groups:
  claude-code-auth:
    description: One credential for the claude-code provider. Not required locally, where the logged-in `claude` CLI subscription is used.
    min: 1
    modes: [image]
```

### 10.2 Assembly

```python
def assemble_env_manifest(ws: Workspace, pid: str, user_registry: UserRegistry | None = None, *,
                          commit: str | None = None, providers: ProviderCatalog | None = None,
                          storage_vars: Sequence[EnvVar] | None = None) -> EnvManifest     # (06)
```

Sources, merged by name:
- `used_by` is the union.
- `secret` is true if any source says so.
- `required` is true if any source says so.
- `description` is the first non-empty one, with sources ordered deterministically (steps by id, then providers, then MCP, edges, storage, runtime).
- Groups are merged by name; `min` takes the maximum and `modes` the union.

| Source | Contributes |
|---|---|
| each compiled step in the closure: `lock.env_vars` | `EnvVar(name, description, secret, used_by=["step:<id>", "tool:<id>/<tool>"…])` |
| each step's `lock.mcp[*]` | `auth_env` from the snapshot. If absent, from `user_registry.get("mcp", server)["auth_env"]`. The var is `secret`, `used_by: "mcp:<server>"`. A `url_env` in the registry entry becomes a non-secret var. |
| each provider in `MergedEnv.providers` | `provider_info(p).fragment.env` + `.env_groups`, with `used_by` including the agentic steps using it |
| `ValidationReport.env_refs` for every process in the closure | `EnvVar(name, description="Referenced by edge expression.", used_by=[edge refs])` |
| `wynd.runtime.storage.storage_env_vars()` (consumed) | storage backend selectors (§7.1), `required: false` with defaults |
| constant | `WYND_HOME` (`required: false`, default `~/.wynd`) |

Only names, descriptions and defaults are recorded, never values (§4, §3.8).

### 10.3 `wynd env check`

06 owns the command (`envcheck.py`: `.env` loading and the resolution order). This workstream supplies:
- the manifest: `assemble_env_manifest` for a working tree (no build needed, M1), or `BuildInfo.manifest` / the image label for a built image;
- `check_env`.

Mode is `local` for `wynd run --local`/`wynd test --live`, and `image` for `wynd run --image`/`serve`, releases and the in-image startup gate. The supervisor runs `check_env(read(WYND_ENV_MANIFEST), os.environ, mode="image")` at start (runtime, consumed). That makes it a Kubernetes startup probe or init container (§4.1).

---

## 11. Artefact store (§5.1, §6.4)

"Build outputs … live in an artefact store: `.wynd/build/<process>/<commit>/` locally, object storage in a cluster" (§5.1). This is a backend selected by env var:

```python
class BuildInfo(BaseModel):                         # (06 fields + extras)
    process: str; commit: str; source_sha: str; job_id: str | None
    process_hash: str
    image: str | None                               # local tag, e.g. wynd/process_supplier_invoice:9f1c0a2b3c4d
    image_id: str | None; image_digest: str | None
    pushed: list[str] = []                          # ["ghcr.io/peterddod/process_supplier_invoice:9f1c0a2b3c4d@sha256:…"]
    base: dict                                      # BaseChoice as dict
    manifest: EnvManifest
    bake: str | None = None                         # path to .pyz when target was bake
    created_at: datetime
    dir: str                                        # local path (or URI for remote stores)

class ArtefactStore(Protocol):
    def put_build(self, staged_dir: Path, info: BuildInfo) -> BuildInfo: ...   # moves/uploads; writes build.json; returns info with dir
    def get_build(self, pid: str, commit: str) -> BuildInfo | None: ...
    def list_builds(self, pid: str) -> list[BuildInfo]: ...                    # newest first
    def local_dir(self, pid: str, commit: str) -> Path: ...                    # materialise locally (download for remote stores)

class LocalArtefactStore:           # root = <state_dir>/build ; <root>/<pid>/<commit>/ ; put = rmtree old + os.rename(staged)
    def __init__(self, state_dir: Path) -> None: ...

def open_artefact_store(state_dir: Path, environ: Mapping[str, str] = os.environ) -> ArtefactStore   # (06)
    # WYND_ARTEFACT_STORE (default "local"); entry point group "wynd.artefact_stores"
```

A build of the same commit replaces the previous one. Status "built" (§11) = `get_build(pid, closure_head)` is not `None` (06 derives it).

---
