"""`render_tree` golden text (PLAN §8.1 runs/tree row; `$DRAFTS/06 §5.10`, `$DRAFTS/08 §4.7`): a dogfood-shaped
trace with a loop, a named branch, an agentic step, an `edge.check` and a nested child process, plus error, timeout
and in-progress runs."""

from __future__ import annotations

from wynd.controller.runs.tree import build_tree, render_events, render_tree

RUN = "run_20260922T215001100_a1b2c3"
PID = "process_supplier_invoice"
HAIKU = {"provider": "claude-code", "model_id": "haiku", "tier": "cheap", "thinking": "low"}
USAGE = {"input_tokens": 812, "output_tokens": 64, "cost_usd": 0.0011, "latency_ms": 1100, "calls": 1}


def trace(*events: tuple[str, dict]) -> list[dict]:
    return [{"v": 1, "seq": seq, "ts": f"2026-09-22T21:50:{seq:02d}.000000Z", "run_id": RUN, "type": type_, **fields}
            for seq, (type_, fields) in enumerate(events, start=1)]


def run_start(mode: str = "local") -> tuple[str, dict]:
    return ("run.start", {"process": PID, "mode": mode, "inputs": {"pdf_path": "/x.pdf"}, "commit": None,
                          "runtime_version": "0.1.0", "workspace": "/w", "cassette": "replay", "metadata": {}})


def start(name: str, span: int, *, run: int = 1, parent: int | None = None, kind: str = "deterministic",
          inputs: dict | None = None, step: str | None = None) -> tuple[str, dict]:
    return ("step.start", {"step": step or name, "name": name, "process": PID, "span": span, "parent": parent,
                           "id": f"{PID}#{name}", "kind": kind, "run": run, "role": "node", "via": None,
                           "inputs": inputs or {}, "venv": None if kind == "process" else "v1"})


def end(name: str, span: int, ms: float, *, run: int = 1, parent: int | None = None, exit: str = "done",
        kind: str = "deterministic", outputs: dict | None = None, key: dict | None = None, usage: dict | None = None,
        model: dict | None = None, step: str | None = None) -> tuple[str, dict]:
    return ("step.end", {"step": step or name, "span": span, "parent": parent, "run": run, "kind": kind,
                         "exit": exit, "timed_out": False, "outputs": outputs or {},
                         "summary": {"step": step or name, "exit": exit, "key_outputs": key or {}, "note": ""},
                         "attempts": 1, "validation_failures": 0, "timings": {"duration_ms": ms},
                         "usage": usage, "model": model, "replayed": False})


def taken(frm: str, branch: int, to: str, *, name: str | None = None, parent: int | None = None) -> tuple[str, dict]:
    return ("edge.taken", {"process": PID, "parent": parent, "from": frm, "branch": branch, "name": name, "to": to,
                           "kind": "deterministic", "with": {}, "taken": 1})


DOGFOOD = trace(
    run_start(),
    start("read", 2, inputs={"pdf_path": "/x.pdf"}),
    end("read", 2, 14, key={"pages": 1}, outputs={"text": "ACME", "pages": 1}),
    taken("read.done", 0, "extract"),
    start("extract", 5, kind="agentic"),
    ("model.call", {"step": "extract", "span": 5, "parent": None, "provider": "claude-code"}),
    end("extract", 5, 1210, kind="agentic", usage=USAGE, model=HAIKU),
    taken("extract.done", 0, "validate"),
    start("validate", 9),
    end("validate", 9, 3, key={"valid": False, "fixable": True}),
    taken("validate.done", 1, "fix"),
    start("fix", 12),
    end("fix", 12, 402),
    taken("fix.done", 0, "validate"),
    start("validate", 15, run=2),
    end("validate", 15, 2, run=2, key={"valid": True}),
    ("edge.check", {"process": PID, "parent": None, "edge": "validate.done", "branch": 0,
                    "branch_key": "validate.done[save]", "target": "save", "take": True,
                    "reason": "the totals agree", "provider": "claude-code", "tier": "cheap", "model_id": "haiku",
                    "usage": USAGE, "duration_ms": 2900, "replayed": True}),
    taken("validate.done", 0, "archive", name="save"),
    start("archive", 19, kind="process"),
    start("write", 20, parent=19, step="archive.write"),
    end("write", 20, 5, parent=19, step="archive.write", key={"path": "/records/acme.json"}),
    taken("write.done", 0, "$exit.done", parent=19),
    end("archive", 19, 9, kind="process"),
    taken("archive.done", 0, "$exit.done"),
    ("run.end", {"exit": "done", "outputs": {}, "error": None, "status": "succeeded", "duration_ms": 1840,
                 "workspace_kept": False, "workspace": None, "usage": {**USAGE, "cost_usd": 0.0022, "calls": 2},
                 "finally_errors": []}),
)

DOGFOOD_TEXT = """\
run run_20260922T215001100_a1b2c3  process_supplier_invoice  local  exit=done  1.84s  $0.0022
├─ read            done   14ms    pages=1
├─ extract         done   1.21s   haiku 812→64 tok $0.0011
├─ validate        done   3ms     valid=false fixable=true
│  → validate.done[1] → fix
├─ fix             done   402ms
├─ validate #2     done   2ms     valid=true
│  validate.done → save  check: TAKEN — "the totals agree" (claude-code/cheap, 2.90s, $0.0011)
│  → validate.done[save] → archive
└─ archive         done   9ms
   └─ write        done   5ms     path=/records/acme.json"""


def test_dogfood_trace_golden():
    assert render_tree(build_tree(DOGFOOD)) == DOGFOOD_TEXT
    assert render_events(reversed(DOGFOOD)) == DOGFOOD_TEXT                 # nesting is by seq and span


def test_full_adds_inputs_and_outputs():
    lines = render_events(DOGFOOD, full=True).splitlines()
    assert lines[1:4] == [
        "├─ read            done   14ms    pages=1",
        '│  in: {"pdf_path":"/x.pdf"}',
        '│  out: {"text":"ACME","pages":1}',
    ]
    assert lines[-3:] == ["   └─ write        done   5ms     path=/records/acme.json",
                          "      in: {}",
                          "      out: {}"]


def test_long_values_are_clipped():
    events = trace(run_start(), start("read", 2, inputs={"text": "x" * 1000}),
                   end("read", 2, 1, key={"text": "y" * 200}, outputs={"text": "z" * 1000}))
    lines = render_events(events, full=True).splitlines()
    assert lines[1] == "└─ read            done   1ms     text=" + "y" * 74 + "…"
    assert len(lines[2]) == len("   in: ") + 400 and lines[2].endswith("…")
    assert len(lines[3]) == len("   out: ") + 400


def test_errors_timeouts_and_failed_checks():
    error = {"run_id": RUN, "process": PID, "step": "read", "cause": "step_error",
             "message": "step read took its error exit", "inputs": {}}
    events = trace(
        run_start("image"),
        start("read", 2),
        end("read", 2, 20, exit="error", outputs={"cause": "exception", "message": "FileNotFoundError: /x.pdf\nmore",
                                                   "type": "FileNotFoundError"}),
        ("process.error", {"process": PID, "parent": None, "error": error, "handler": "default"}),
        start("notify", 5),
        ("step.end", {"step": "notify", "span": 5, "parent": None, "run": 1, "kind": "deterministic", "exit": None,
                      "timed_out": True, "timings": {"duration_ms": 30000}}),
        ("edge.check", {"process": PID, "parent": None, "edge": "a.done", "branch": 0, "branch_key": "a.done[0]",
                        "target": "b", "take": None, "error_cause": "cassette_miss"}),
        ("run.end", {"exit": "error", "outputs": {}, "error": error, "status": "failed", "duration_ms": 30050}),
    )
    assert render_events(events) == """\
run run_20260922T215001100_a1b2c3  process_supplier_invoice  image  exit=error  30.05s
├─ read            error  20ms
│  cause: exception: FileNotFoundError: /x.pdf
│  ✗ step_error at read: step read took its error exit (handler: default)
└─ notify          timeout 30.00s
   a.done → b  check: FAILED (cassette_miss)"""


def test_a_running_trace_and_a_negative_verdict():
    events = trace(
        run_start(),
        start("validate", 2),
        end("validate", 2, 3),
        ("edge.check", {"process": PID, "parent": None, "edge": "validate.done", "branch": 0,
                        "branch_key": "validate.done[0]", "target": "save", "take": False, "reason": "no due date",
                        "provider": "claude-code", "tier": "cheap", "duration_ms": 950}),
        taken("validate.done", 1, "escalate"),
        start("escalate", 6),
    )
    assert render_events(events) == """\
run run_20260922T215001100_a1b2c3  process_supplier_invoice  local  running
├─ validate        done   3ms
│  validate.done → save  check: NOT TAKEN — "no due date" (claude-code/cheap, 950ms)
│  → validate.done[1] → escalate
└─ escalate        …"""
    assert render_events([]) == "run ?  ?  ?  running"
