"""CMP-D: the prompt files, their placeholders, the runtime API reference and user-message rendering."""

import importlib
import inspect
import re
import string
from datetime import date
from importlib import resources
from pathlib import Path

import pytest
import yaml

from wynd.compiler import prompts
from wynd.compiler.calls import CALLS
from wynd.compiler.prompts import TIER_WORDS, Code, load, render, runtime_api, split_note, system_prompt
from wynd.runtime.tools import BUILTINS

PLACEHOLDERS = {
    "preamble": set(),
    "infer_schema": set(),
    "propose_examples": {"max_proposals"},
    "revise_example": set(),
    "decide": set(),
    "write_deterministic": {"class_name"},
    "write_shell": {"class_name", "program", "exit_codes"},
    "write_agentic": {"tier_words", "split_note"},
    "write_agentic_split_note": {"deferred_summary"},
    "revise_code": set(),
    "revise_agentic": set(),
    "runtime_api": {"builtin_catalog"},
}
# Literal braces the prompts are meant to contain after formatting (doubled in the files).
LITERAL_BRACES = ['"{\\"total\\": 0}"', '{0: "done", "*": "error"}']


def placeholders(text: str) -> set[str]:
    return {field for _, field, _, _ in string.Formatter().parse(text) if field is not None}


def values_for(name: str) -> dict[str, str]:
    return {field: f"<{field.upper()}>" for field in PLACEHOLDERS[name]}


def strip_expected(text: str) -> str:
    for literal in LITERAL_BRACES:
        text = text.replace(literal, "")
    return text


def test_every_prompt_file_is_packaged_and_listed():
    files = {p.name.removesuffix(".md") for p in resources.files(prompts).iterdir() if p.name.endswith(".md")}
    assert files == set(PLACEHOLDERS)
    assert set(CALLS) <= files


@pytest.mark.parametrize("name", sorted(PLACEHOLDERS))
def test_placeholders_are_exactly_the_documented_ones(name):
    assert placeholders(load(name)) == PLACEHOLDERS[name]


@pytest.mark.parametrize("name", sorted(PLACEHOLDERS))
def test_prompts_render_without_stray_braces(name):
    values = values_for(name)
    text = load(name).format_map(values)
    for value in values.values():
        assert value in text
    rest = strip_expected(text)
    assert "{" not in rest and "}" not in rest, name
    assert text.endswith("\n") and not text.endswith("\n\n")


@pytest.mark.parametrize("kind", sorted(CALLS))
def test_system_prompt_is_preamble_then_call_prompt(kind):
    text = system_prompt(kind, **values_for(kind))
    preamble = load("preamble").rstrip("\n")
    assert text.startswith(preamble + "\n\n" + load(kind).format_map(values_for(kind))[:40])
    assert text.startswith("You are the Wynd compiler.")
    assert "Answer only with the JSON object required by the output schema." in text
    assert "{" not in strip_expected(text) and "}" not in strip_expected(text)
    assert not text.endswith("\n")


def test_missing_placeholder_value_is_an_error():
    with pytest.raises(KeyError, match="class_name"):
        system_prompt("write_deterministic")


def test_write_agentic_split_note_and_tier_words():
    assert set(TIER_WORDS) == {"cheap", "standard", "strong"}
    plain = system_prompt("write_agentic", tier_words=TIER_WORDS["cheap"], split_note="")
    assert "At run time\na small, fast language model receives your docstring" in plain
    assert "hyphen lists.\n\nCapabilities." in plain
    split = system_prompt("write_agentic", tier_words=TIER_WORDS["cheap"],
                          split_note=split_note("amounts written in words"))
    assert "hyphen lists.\n- This step is a fallback." in split
    assert "(for example: amounts written in words). Do not assume the input is typical.\n\nCapabilities." in split


def test_shell_prompt_shows_the_exit_codes_value():
    text = system_prompt("write_shell", class_name="Shout", program="tr", exit_codes='{0: "done", "*": "error"}')
    assert 'Set the class attribute exit_codes to exactly {0: "done", "*": "error"}.' in text
    assert 'runs the program "tr"' in text and "Shout(ShellStep)" in text


def test_propose_examples_keeps_its_json_example():
    text = system_prompt("propose_examples", max_proposals=3)
    assert "Propose at most 3, most valuable first." in text
    assert 'for example "{\\"total\\": 0}".' in text


# --- runtime_api.md --------------------------------------------------------------------------------------------------

def runtime_symbols(text: str) -> set[tuple[str, str]]:
    """(module, name) for every `from wynd.runtime… import a, b` line and every dotted `wynd.runtime.x.Y`."""
    found = set()
    for module, names in re.findall(r"^from (wynd\.runtime[\w.]*) import (.+)$", text, re.M):
        found |= {(module, n.strip()) for n in names.split(",")}
    for dotted in re.findall(r"\bwynd\.runtime(?:\.\w+)+", text):
        module, _, name = dotted.rpartition(".")
        found.add((module, name))
    return found


def test_runtime_api_names_only_symbols_that_import():
    text = runtime_api()
    symbols = runtime_symbols(text)
    assert ("wynd.runtime", "AgenticStep") in symbols and ("wynd.runtime.tools", "shell") in symbols
    for module, name in sorted(symbols):
        imported = importlib.import_module(module)
        assert hasattr(imported, name), f"{module}.{name} named in runtime_api.md does not exist"


def test_runtime_api_documents_the_real_step_api():
    from wynd.runtime.handle import RuntimeHandle, StepCache
    from wynd.runtime.step import ShellStep

    text = runtime_api()
    assert "self.runtime.env(name: str, default: str | None = None) -> str" in text
    assert list(inspect.signature(RuntimeHandle.env).parameters) == ["self", "name", "default"]
    for method in ("get", "set", "get_or_set"):
        assert f".{method}(" in text and hasattr(StepCache, method)
    assert ShellStep.exit_codes == {0: "done", "*": "error"}
    assert 'exit_codes = {0: "done", "*": "error"}' in text


def test_runtime_api_lists_every_builtin_tool():
    text = runtime_api()
    catalog = text.split("Built-in tools (can also be called directly from deterministic code):\n", 1)[1]
    lines = catalog.splitlines()
    assert [line.split("(", 1)[0].strip() for line in lines] == [spec.name for spec in BUILTINS]
    web = next(line for line in lines if line.strip().startswith("web_search("))
    assert "query: string" in web and "count: integer?" in web and "env: BRAVE_API_KEY" in web
    assert "effects: network" in web and "idempotent" in web
    shell = next(line for line in lines if line.strip().startswith("shell("))
    assert 'shell.allow("<program>", ...)' in shell
    assert "{" not in text.replace('{0: "done", "*": "error"}', "")


# --- render ----------------------------------------------------------------------------------------------------------

def fenced(text: str, lang: str) -> list[str]:
    return re.findall(rf"```{lang}\n(.*?)\n```", text, re.S)


def test_render_sections_plain_yaml_and_code():
    examples = [{"inputs": {"text": "£12.50", "when": date(2026, 10, 1)}, "exit": "done", "outputs": {"amount": 12.5}},
                {"inputs": {"text": "line one\nline two"}, "exit": "not_an_amount", "outputs": {}}]
    text = render([
        ("Step", "parse_amount: read the amount.\n"),
        ("Examples", examples),
        ("Previous implementation", None),
        ("Current module", Code("def run():\n    return 1\n")),
        ("Exits", ["done", "not_an_amount"]),
    ])
    assert text.startswith("## Step\n\nparse_amount: read the amount.\n\n## Examples\n\n```yaml\n")
    assert "Previous implementation" not in text
    assert [line for line in text.splitlines() if line.startswith("## ")] == [
        "## Step", "## Examples", "## Current module", "## Exits"]
    assert fenced(text, "python") == ["def run():\n    return 1"]
    first, second = fenced(text, "yaml")
    assert yaml.safe_load(first) == examples
    assert yaml.safe_load(second) == ["done", "not_an_amount"]
    assert "text: |-\n" in first                     # multi-line strings stay readable
    assert text.endswith("```\n") and not text.endswith("\n\n")


def test_render_is_deterministic_and_keeps_key_order():
    sections = [("Schema", {"outputs": {"z": "number", "a": "string"}, "inputs": {"b": "date?"}}), ("Note", "n")]
    assert render(sections) == render([(t, v) for t, v in sections])
    body = fenced(render(sections), "yaml")[0]
    assert body.index("outputs") < body.index("inputs") and body.index("z:") < body.index("a:")


def test_render_rejects_other_values():
    with pytest.raises(TypeError, match="section 'Max': expected str, dict or list, got int"):
        render([("Max", 3)])


@pytest.mark.parametrize("name", sorted(set(PLACEHOLDERS) - {"runtime_api"}))
def test_prompts_are_the_draft_text_verbatim(name):
    """PLAN §7 adopts `$DRAFTS/05 §14` verbatim; runtime_api.md is the sketch kept in sync with the runtime instead."""
    draft = (Path(__file__).resolve().parents[3] / "docs" / "design" / "05-compiler.md").read_text()
    anchor = "`{split_note}` for the agentic half" if name == "write_agentic_split_note" else f"`{name}.md`"
    start = draft.index("```text\n", draft.index(anchor)) + len("```text\n")
    assert load(name) == draft[start:draft.index("\n```", start)] + "\n"
