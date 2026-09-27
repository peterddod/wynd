"""Compiling the dogfood from its proto-steps alone reproduces the M1 packages, offline (PLAN §7.2).

A copy of `examples/invoices` in a temporary repository, with `provider: fake` and no `steps/` or process cassettes,
is compiled with `fixtures/scripts/dogfood.yaml` (a ScriptedLLM whose write_deterministic responses are the M1
modules) and the sample's fake-provider script for the agentic steps.
"""

import dataclasses
from pathlib import Path

from wynd.compiler import jobs
from wynd.compiler.llm import MemoLLM
from wynd.compiler.testing import FakeJobContext, ScriptedLLM
from wynd.spec.interface import interfaces_equivalent
from wynd.spec.lockfiles import StepLock
from wynd.spec.yamlio import parse_model

SCRIPT = Path(__file__).parent / "fixtures" / "scripts" / "dogfood.yaml"
PID = "process_supplier_invoice"
PROCESS = f"processes/{PID}"
M1_KINDS = {"read_pdf": "deterministic", "extract_invoice_fields": "agentic", "validate_fields": "deterministic",
            "fix_fields": "agentic", "save_record": "deterministic", "escalate_to_human": "deterministic"}
UNCOMPARED = {"compiled", "locked_deps", "interface"}


def _lock(path: Path) -> StepLock:
    return parse_model(path.read_text(), StepLock)


def test_compiling_the_dogfood_reproduces_m1(repo_root, make_repo, monkeypatch, git):
    m1 = repo_root / "examples" / "invoices" / PROCESS
    process_yaml = (m1 / "process.yaml").read_text()
    assert "provider: claude-code" in process_yaml
    ws = make_repo(repo_root / "examples" / "invoices", files={
        f"{PROCESS}/process.yaml": process_yaml.replace("provider: claude-code", "provider: fake"),
        f"{PROCESS}/steps": None,
        f"{PROCESS}/cassettes": None,
    })
    monkeypatch.setenv("WYND_FAKE_PROVIDER_SCRIPT", str(ws / "tests" / "fixtures" / "fake_provider.json"))
    llm = ScriptedLLM(SCRIPT)
    real = jobs.default_deps

    def deps(ctx, session):
        base = real(ctx, session)
        return dataclasses.replace(base, llm=MemoLLM(jobs.MeteredLLM(llm, session), session.data.memo,
                                                     on_usage=lambda *a: None))

    monkeypatch.setattr(jobs, "default_deps", deps)
    ctx = FakeJobContext(ws, PID, inputs={"accept_proposals": True})
    outcome = jobs.run_compile_job(ctx)
    ctx.finish(outcome)

    assert outcome.status == "succeeded", outcome.error
    assert outcome.report["integration_tests"]["passed"] == 5 and outcome.report["integration_tests"]["failed"] == 0
    git(ws, "merge", "-q", "--ff-only", outcome.commit)
    steps = ws / PROCESS / "steps"
    assert sorted(p.name for p in steps.iterdir()) == sorted(M1_KINDS)
    for package, kind in M1_KINDS.items():
        new, old = _lock(steps / package / "step.lock.yaml"), _lock(m1 / "steps" / package / "step.lock.yaml")
        assert new.kind == kind
        assert new.model_dump(exclude=UNCOMPARED) == old.model_dump(exclude=UNCOMPARED), package
        assert interfaces_equivalent(new.interface, old.interface) == [], package
        assert new.compiled["decision"]["rule"] == (1 if kind == "deterministic" else 2)
        if kind == "deterministic":
            assert (steps / package / f"{package}.py").read_bytes() == \
                (m1 / "steps" / package / f"{package}.py").read_bytes(), package
            assert new.interface == old.interface, package
        else:
            assert list((steps / package / "cassettes").glob("*.json")), package
    assert _lock(steps / "read_pdf" / "step.lock.yaml").locked_deps == ["pypdf==6.19.0"]
    assert len(llm.calls) == 18
