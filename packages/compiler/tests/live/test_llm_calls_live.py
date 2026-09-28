"""CMP-D live check: one real call per compiler call kind through the default provider (claude-code), with tiny
payloads. Catches structured-output incompatibilities of the response models early (`$DRAFTS/05 §16.3`)."""

import pytest

from wynd.compiler.calls import CALLS
from wynd.compiler.llm import ProviderLLM, Tier, default_llm
from wynd.compiler.prompts import TIER_WORDS, Code, render, runtime_api, system_prompt
from wynd.runtime.storage.local import FileRegistry

pytestmark = pytest.mark.live

STEP = "shout: return the input text in upper case. Exits: done (fields: text)."
MODULE = '''from typing import Literal

from pydantic import BaseModel

from wynd.runtime import DeterministicStep


class Shout(DeterministicStep):
    class Input(BaseModel):
        text: str

    class Done(BaseModel):
        exit: Literal["done"] = "done"
        text: str

    Output = Done

    def run(self, input: Input) -> Output:
        return self.Done(text=input.text.lower())
'''
EXAMPLES = [{"inputs": {"text": "hi"}, "exit": "done", "outputs": {"text": "HI"}},
            {"inputs": {"text": "Ok 2"}, "exit": "done", "outputs": {"text": "OK 2"}}]
FAILURE = "test_example_1: expected exit done outputs {text: HI}; actual exit done outputs {text: hi}"

PLACEHOLDERS = {
    "propose_examples": {"max_proposals": 1},
    "write_deterministic": {"class_name": "Shout"},
    "write_shell": {"class_name": "Shout", "program": "tr", "exit_codes": '{0: "done", "*": "error"}'},
    "write_agentic": {"tier_words": TIER_WORDS["cheap"], "split_note": ""},
}
PROMPTS = {
    "infer_schema": [("Step", STEP), ("Exits", ["done"]), ("Examples", EXAMPLES)],
    "propose_examples": [("Step", STEP), ("Schema", {"inputs": {"text": "string"}, "outputs": {"done": {"text": "string"}}}),
                         ("Examples", EXAMPLES), ("Maximum proposals", "1")],
    "revise_example": [("Schema", {"inputs": {"text": "string"}, "outputs": {"done": {"text": "string"}}}),
                       ("Proposed example", {"inputs": {"text": ""}, "exit": "done", "outputs": {"text": ""}}),
                       ("The user's answer", "An empty text should give an empty text; that is right.")],
    "decide": [("Step", STEP), ("Examples", EXAMPLES), ("Available tools", "none")],
    "write_deterministic": [("Runtime API reference", runtime_api()), ("Step contract", STEP),
                            ("Class name and base", "Shout(DeterministicStep)"), ("Examples", EXAMPLES)],
    "write_shell": [("Runtime API reference", runtime_api()), ("Step contract", STEP),
                    ("Class name and base", "Shout(ShellStep)"), ("Program", "tr")],
    "write_agentic": [("Step contract", STEP), ("Examples", EXAMPLES), ("Model tier", TIER_WORDS["cheap"])],
    "revise_code": [("Current module", Code(MODULE)), ("Failures", FAILURE), ("Attempt", "2 of 4")],
    "revise_agentic": [("Current docstring and capabilities", "Return the text in lower case."),
                       ("Failures", FAILURE), ("Attempt", "2 of 4"), ("Model tier", TIER_WORDS["cheap"])],
}


@pytest.mark.parametrize("kind", sorted(CALLS))
def test_one_real_call_per_kind(kind, tmp_path):
    llm = default_llm(FileRegistry(tmp_path / "home"), tmp_path / "llm")
    assert isinstance(llm, ProviderLLM)
    spec = CALLS[kind]
    result = llm.call(kind, node="shout", system=system_prompt(kind, **PLACEHOLDERS.get(kind, {})),
                      prompt=render(PROMPTS[kind]), response_model=spec.response_model, tier=Tier(spec.tier),
                      thinking=spec.thinking)
    assert isinstance(result.value, spec.response_model)       # the provider accepted the schema and it validated
    assert result.usage.input_tokens > 0 and result.usage.output_tokens > 0
    assert result.usage.latency_ms > 0
