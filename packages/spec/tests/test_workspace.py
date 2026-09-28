"""`wynd.yaml`, `use:` forms and step identifiers (PLAN §3.1, §3.3; $DRAFTS/01 §6.1)."""

import hashlib

import pytest
from pydantic import ValidationError

from wynd.spec import (
    SpecError,
    StepRoot,
    UseRef,
    WorkspaceConfig,
    dump_yaml,
    find_workspace_root,
    load_workspace_config,
    local_step_id,
    parse_model,
    parse_use,
    root_step_id,
    slug,
    step_module_name,
)


def test_defaults_and_shorthand_roots():
    assert WorkspaceConfig().model_dump() == {"process_roots": ["processes"], "step_roots": {}, "cassette_warn_mb": 5.0}
    config = parse_model(
        "process_roots: [processes, teams/processes]\n"
        "step_roots:\n  shared: shared/steps\n  vendor: {path: vendor/steps}\n"
        "cassette_warn_mb: 2\n",
        WorkspaceConfig,
    )
    assert config.step_roots == {"shared": StepRoot(path="shared/steps"), "vendor": StepRoot(path="vendor/steps")}
    authoring = config.to_authoring()
    assert authoring == {"process_roots": ["processes", "teams/processes"],
                         "step_roots": {"shared": "shared/steps", "vendor": "vendor/steps"}, "cassette_warn_mb": 2.0}
    assert parse_model(dump_yaml(authoring), WorkspaceConfig).model_dump() == config.model_dump()
    assert WorkspaceConfig().to_authoring() == {"process_roots": ["processes"], "step_roots": {}}


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"step_roots": {"vendor": {"url": "https://x"}}}, "url: roots are reserved; v1 resolves local paths only"),
        ({"step_roots": {"process": "shared"}}, "step-root alias 'process' is reserved"),
        ({"step_roots": {"shared": "processes/shared"}}, "roots 'processes' and 'processes/shared' overlap"),
        ({"process_roots": ["a", "a"]}, "roots 'a' and 'a' overlap"),
        ({"process_roots": ["../up"]}, "root '../up' must not contain empty, '.' or '..' segments"),
        ({"process_roots": ["/abs"]}, "root '/abs' must be a relative POSIX path"),
        ({"process_roots": ["a/"]}, "root 'a/' must be a relative POSIX path"),
        ({"process_roots": [".wynd/processes"]}, "root '.wynd/processes' must not be .wynd or inside it"),
        ({"step_roots": {"empty": {}}}, "step root 'empty' needs a path"),
    ],
)
def test_root_rules(data, message):
    with pytest.raises(ValidationError) as err:
        WorkspaceConfig.model_validate(data)
    [error] = err.value.errors()
    assert error["type"] == "E-ROOTS" and message in error["msg"]


def test_sibling_roots_with_a_common_prefix_are_fine():
    WorkspaceConfig(process_roots=["processes", "processes-old"], step_roots={"a": StepRoot(path="steps")})


def test_load_workspace_config(tmp_path):
    (tmp_path / "wynd.yaml").write_text("process_roots:\n  - processes\nstep_roots: {}\n")
    assert load_workspace_config(tmp_path / "wynd.yaml") == WorkspaceConfig()
    (tmp_path / "bad.yaml").write_text("process_roots: [processes]\nstep_rots: {}\n")
    with pytest.raises(SpecError) as err:
        load_workspace_config(tmp_path / "bad.yaml")
    assert err.value.diagnostics[0].message == "unknown field 'step_rots' (did you mean 'step_roots'?)"


@pytest.mark.parametrize(
    ("text", "ref"),
    [
        ("./steps/read_pdf", UseRef("local", "read_pdf")),
        ("shared:extract", UseRef("root", "extract", "shared")),
        ("finance:extract/invoice", UseRef("root", "extract/invoice", "finance")),
        ("my-root:a_b/c-d", UseRef("root", "a_b/c-d", "my-root")),
        ("process:finance/invoices", UseRef("process", "finance/invoices")),
        ("process:process_supplier_invoice", UseRef("process", "process_supplier_invoice")),
    ],
)
def test_parse_use_forms(text, ref):
    assert parse_use(text) == ref
    assert str(ref) == text


FORMS = "use: must be one of ./steps/<name>, <alias>:<path>, process:<id>"


@pytest.mark.parametrize(
    ("text", "hint"),
    [
        ("../x", "relative traversal ('..') is not permitted"),
        ("shared:../x", "relative traversal ('..') is not permitted"),
        ("/abs/step", "absolute paths are not permitted"),
        ("~/step", "absolute paths are not permitted"),
        ("C:\\steps\\x", "absolute paths are not permitted"),
        ("shared:a\\b", "use forward slashes"),
        ("./steps/a/b", "process-local steps are ./steps/<name> (a single segment)"),
        ("./read_pdf", "process-local steps are ./steps/<name> (a single segment)"),
        ("process:a b", "invalid process id"),
        ("read_pdf", None),
        ("Shared:x", None),
    ],
)
def test_parse_use_rejects_with_one_hint(text, hint):
    with pytest.raises(ValueError) as err:
        parse_use(text)
    expected = f"{FORMS} (got {text!r})" + (f"; {hint}" if hint else "")
    assert str(err.value) == expected


def test_find_workspace_root(tmp_path):
    (tmp_path / "ws").mkdir()
    (tmp_path / "ws" / "wynd.yaml").write_text("{}\n")
    deep = tmp_path / "ws" / "processes" / "p"
    deep.mkdir(parents=True)
    (deep / "process.yaml").write_text("")
    assert find_workspace_root(deep) == tmp_path / "ws"
    assert find_workspace_root(deep / "process.yaml") == tmp_path / "ws"
    assert find_workspace_root(tmp_path / "ws") == tmp_path / "ws"
    assert find_workspace_root(tmp_path) is None


def test_step_ids():
    assert local_step_id("finance/invoices", "read") == "finance/invoices#read"
    assert root_step_id("shared", "extract/invoice") == "shared:extract/invoice"


def test_step_module_name_is_stable_and_unique():
    digest = hashlib.sha256(b"p#read").hexdigest()[:10]
    assert step_module_name("p#read") == f"read_{digest}"
    assert step_module_name("p#read") != step_module_name("q#read")
    assert step_module_name("finance:extract/invoice").startswith("invoice_")
    assert step_module_name("p#2fast").startswith("s_2fast_")
    assert step_module_name("shared:x/My-Step").startswith("my_step_")
    assert step_module_name("p#").startswith("step_")
    assert all(step_module_name(s).isidentifier() for s in ("p#read", "a:b/c-d", "p#9", "p#"))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("process_supplier_invoice", "process_supplier_invoice"),
        ("finance/invoices", "finance-invoices"),
        ("Finance/Invoices", "finance-invoices"),
        ("a  b", "a-b"),
        ("a//b", "a-b"),
        ("-._x._-", "x"),
        ("v1.2", "v1.2"),
        ("héllo wörld", "h-llo-w-rld"),
        ("", "x"),
        ("///", "x"),
    ],
)
def test_slug(text, expected):
    assert slug(text) == expected
