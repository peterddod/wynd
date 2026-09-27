# Wynd runtime API (for step authors)

from wynd.runtime import DeterministicStep, AgenticStep, ShellStep, ShellResult, McpServer, tool
from wynd.runtime.tools import http_get, web_search, workspace_read, workspace_write, shell, now

A step class nests its models: `class Input(BaseModel)`, one model per exit, each with
`exit: Literal["<name>"] = "<name>"`, and `Output = A | B` (or `Output = Done` for a done-only step).
run(self, input: Input) -> Output returns exactly one exit model. Raising any exception sends the run to the
step's implicit `error` exit. The step never decides where the process goes next.

self.runtime (available in pre, run, post and tool methods):
  self.runtime.env(name: str, default: str | None = None) -> str   # raises if unset and no default
  self.runtime.workspace: Path                # per-run directory and the cwd; files here are cleaned up after the run
  self.runtime.cache                          # .get(key) / .set(key, value) / .get_or_set(key, factory): the only allowed cross-run state
  self.runtime.log(message: str, **fields)    # structured log line into the trace
  self.runtime.http.get(url, **kw) / .post(url, **kw)   # declared "network" effect required

Hooks: pre(self) -> None and post(self) -> None run once per step run. Use them only for state the runtime
cannot see.

DeterministicStep: no model, no network unless "network" is declared.
ShellStep: exit_codes = {{0: "done", "*": "error"}}; command(self, input) -> list[str];
           outputs(self, exit: str, result: ShellResult) -> Output   # ShellResult(returncode, stdout, stderr)
AgenticStep: the class docstring is the instruction. run(self, input: Input) -> Output: ...  (body is literally ...)
           context = [...]; tools = [web_search, ...]; mcp = [McpServer("github", allow=["list_issues"])]
@tool(effects=["network"], idempotent=True, env=["API_KEY"]) marks a method as callable by the model. Its type
hints and docstring are the tool schema.

Built-in tools (can also be called directly from deterministic code):
{builtin_catalog}
