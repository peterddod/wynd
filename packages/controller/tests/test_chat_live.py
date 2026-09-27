"""A real chat turn (`$DRAFTS/06 §11.4`; PLAN §8.2 "@live: chat turn edits only process.yaml in one commit").

On a temporary git copy of the dogfood workspace, a claude-code turn (tier standard) acting on
process_supplier_invoice is asked to raise the fix loop limit from 3 to 5. It must end in exactly one
`design(process_supplier_invoice): …` commit that touches only `process.yaml` and carries the chat trailers, with the
new limit in place and the process still valid. Uses the real `DesignService`.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest
import yaml

from wynd.controller.api.models_web import SendMessageRequest

pytestmark = pytest.mark.live

DOGFOOD = Path(__file__).resolve().parents[3] / "examples" / "invoices"
PID = "process_supplier_invoice"
PROCESS_FILE = f"processes/{PID}/process.yaml"
PROMPT = ("Raise the fix loop limit of this process from 3 to 5 fix attempts: the `steps.fix.runs < 3` condition on "
          "the validate.done branch into fix. Change nothing else.")


def wait_turn(ctl, chat_id: str, timeout: float):
    deadline = time.monotonic() + timeout
    while ctl.chats.snapshot(chat_id).chat.running_turn is not None:
        assert time.monotonic() < deadline, "the turn did not finish"
        time.sleep(0.5)
    return ctl.chats.snapshot(chat_id)


def test_a_chat_turn_raises_the_fix_loop_limit_in_one_commit(tmp_path, make_controller, git):
    ws = tmp_path / "invoices"
    shutil.copytree(DOGFOOD, ws, ignore=shutil.ignore_patterns(".wynd", ".env", "__pycache__", ".pytest_cache"))
    git(ws, "init", "-q", "-b", "main")
    git(ws, "add", "-A")
    git(ws, "commit", "-q", "-m", "dogfood copy")
    base = git(ws, "rev-parse", "HEAD").strip()
    ctl = make_controller(ws, env={"WYND_CHAT_PROVIDER": "claude-code", "WYND_CHAT_TIER": "standard"})
    try:
        chat = ctl.chats.create()
        ctl.chats.send(chat.id, SendMessageRequest(text=PROMPT, acting_on=PID, client_id="live-1"))
        snapshot = wait_turn(ctl, chat.id, timeout=900)
    finally:
        ctl.chats.close()

    assistant = next(item for item in snapshot.items if item.type == "assistant")
    assert assistant.status == "done", assistant.error
    commits = git(ws, "rev-list", f"{base}..HEAD").split()
    assert len(commits) == 1
    message = git(ws, "log", "-1", "--format=%B", commits[0])
    assert message.startswith(f"design({PID}): ")
    assert {"Wynd-Origin: chat", "Wynd-Reason: chat_turn", f"Wynd-Chat: {chat.id}"} <= set(message.splitlines())
    assert git(ws, "show", "--name-only", "--format=", commits[0]).split() == [PROCESS_FILE]
    assert [(i.type, i.sha) for i in snapshot.items if i.type == "commit"] == [("commit", commits[0])]

    doc = yaml.safe_load((ws / PROCESS_FILE).read_text())
    fix = next(branch for edge in doc["edges"] if edge["from"] == "validate.done" for branch in edge["to"]
               if branch["step"] == "fix")
    assert "steps.fix.runs < 5" in fix["when"]
    assert ctl.processes.validate(PID).ok
