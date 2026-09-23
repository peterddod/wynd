"""Static lint of step modules, L001–L005 (PLAN §5.1 lint row, §5.7 RT-STEP cases)."""

import textwrap

import pytest

from support.rt_step_doubles import FIXTURES
from wynd.runtime.lint import LintIssue, check_step_module


def codes(source: str) -> list[str]:
    return [issue.code for issue in check_step_module(textwrap.dedent(source), "step.py")]


# --- L001 -----------------------------------------------------------------------------------------------------------

def test_global_anywhere_is_l001():
    assert codes("""
        COUNT = 0
        def bump():
            global COUNT
            COUNT += 1
    """) == ["L001"]


def test_nonlocal_only_counts_at_module_level():
    assert codes("nonlocal x\n") == ["L001"]
    assert codes("""
        def outer():
            n = 0
            def inner():
                nonlocal n
                n += 1
            return inner
    """) == []


# --- L002 -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("source", [
    "CACHE = {}",
    "SEEN: list[str] = []",
    "IDS = {1, 2}",
    "PAIRS = ('a', ['b'])",
    "MERGED = {**BASE}",
    "SQUARES = {n: n * n for n in BASE}",
    "GEN = (n for n in BASE)",
    "A = B = []",
    "X = tuple([1]) + [2]",
    "if TYPE_CHECKING:\n    HINTS = []",
    "try:\n    import ujson\nexcept ImportError:\n    FALLBACK = []",
])
def test_module_level_mutable_values_are_l002(source):
    assert codes(source) == ["L002"]


@pytest.mark.parametrize("source", [
    "CURRENCIES = frozenset({'GBP', 'EUR'})",
    "ORDER = tuple(['a', 'b'])",
    "LETTERS = frozenset(c for c in 'abc')",
    "__all__ = ['Step']",
    "__all__ += ['Other']",
    "NAMES = ('a', 'b')",
    "LIMIT = 1_000_000",
    "Handler = Callable[[int], str]",
    "make = lambda: []",
    "def f():\n    local = []\n    return local",
])
def test_immutable_and_local_values_are_fine(source):
    assert codes(source) == []


# --- L003 -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("source", [
    "import requests\nSESSION = requests.Session()",
    "SEEN = dict()",
    "import collections\nCACHE = collections.OrderedDict()",
    "import os\nTOKEN = os.environ.get('TOKEN')",
    "from dotenv import load_dotenv\nload_dotenv()",
    "import sys\nif sys.flags.debug or check():\n    pass",
    "with open('data.txt') as f:\n    pass",
])
def test_module_level_calls_outside_the_allowlist_are_l003(source):
    assert codes(source) == ["L003"]


def test_calls_inside_a_mutable_literal_report_both():
    assert codes("X = [f(), 1]") == ["L002", "L003"]


@pytest.mark.parametrize("source", [
    "import re\nNUMBER = re.compile(r'[0-9]+')",
    "from re import compile\nNUMBER = compile('x')",
    "from typing import TypeVar, NewType\nT = TypeVar('T')\nUserId = NewType('UserId', int)",
    "import typing\nT = typing.TypeVar('T')",
    "import logging\nlog = logging.getLogger(__name__)",
    "from datetime import date, datetime, timedelta\nA = date(2000, 1, 1)\nB = datetime(2000, 1, 1)\n"
    "C = timedelta(days=1)",
    "import datetime\nA = datetime.date(2000, 1, 1)",
    "import datetime as dt\nA = dt.timedelta(hours=2)",
    "from decimal import Decimal\nLIMIT = Decimal('1.5')",
    "from pathlib import Path\nROOT = Path('data')",
    "from typing import Annotated\nfrom pydantic import Field\nMoney = Annotated[float, Field(ge=0)]",
    "from dataclasses import dataclass\n@dataclass(frozen=True)\nclass Point:\n    x: int",
    "def f(x=make()):\n    return x",
    "\"\"\"Docstring.\"\"\"",
])
def test_allowlisted_calls_and_definitions_are_fine(source):
    assert codes(source) == []


# --- L004 -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("source", [
    "from functools import lru_cache\n@lru_cache\ndef f(x):\n    return x",
    "import functools\n@functools.cache\ndef f(x):\n    return x",
    "from functools import lru_cache\ndef f(x):\n    return x\ncached = lru_cache(maxsize=1)(f)",
    "import functools as ft\nclass S:\n    @ft.lru_cache(maxsize=2)\n    def f(self):\n        return 1",
    "from functools import cache as memo\n@memo\ndef f():\n    return 1",
    "import functools\ndef f(g):\n    return functools.cache(g)",
])
def test_functools_caches_are_l004(source):
    assert "L004" in codes(source)
    assert codes(source).count("L004") == 1


def test_lru_cache_call_at_module_level_is_also_a_module_level_call():
    source = "from functools import lru_cache\ndef f(x):\n    return x\ncached = lru_cache(maxsize=1)(f)"
    assert codes(source) == ["L003", "L003", "L004"]      # the wrapping call, the lru_cache(...) call, the cache


@pytest.mark.parametrize("source", [
    "import functools\nclass S:\n    @functools.cached_property\n    def f(self):\n        return 1",
    "def cache(fn):\n    return fn\n@cache\ndef f():\n    return 1",
])
def test_other_decorators_are_fine(source):
    assert codes(source) == []


# --- L005 -----------------------------------------------------------------------------------------------------------

def test_mutable_class_attributes_are_l005():
    source = """
        import requests
        from wynd.runtime import DeterministicStep

        class Lookup(DeterministicStep):
            seen = []
            session = requests.Session()

        class Helper:
            table = {"a": 1}
            count: dict = dict()
    """
    issues = check_step_module(textwrap.dedent(source), "lookup.py")
    assert [(i.code, i.line) for i in issues] == [("L005", 6), ("L005", 7), ("L005", 10), ("L005", 11)]
    assert "seen of Lookup" in issues[0].message


def test_step_attributes_and_model_bodies_are_exempt():
    source = """
        from typing import Literal
        from pydantic import BaseModel, ConfigDict, Field, RootModel
        from wynd.runtime import AgenticStep, McpServer, ShellStep
        from wynd.runtime.tools import web_search

        class Base(BaseModel):
            model_config = ConfigDict(extra="forbid")
            tags: list[str] = []

        class Derived(Base):
            extra: dict = {}
            items: list[int] = Field(default_factory=list)

        class Numbers(RootModel[list[int]]):
            root: list[int] = []

        class Search(AgenticStep):
            \"\"\"Search.\"\"\"
            tools = [web_search]
            context = ["process.goal"]
            mcp = [McpServer("github", allow=["get_issue"])]

            class Input(BaseModel):
                query: str

            class Done(BaseModel):
                exit: Literal["done"] = "done"
                hits: list[str] = []

            Output = Done
            LIMITS = frozenset({1, 2})

            def run(self, input): ...

        class Count(ShellStep):
            exit_codes = {0: "done", "*": "error"}
    """
    assert codes(source) == []


def test_issue_fields_and_order():
    issues = check_step_module("X = {}\nimport requests\nS = requests.Session()\n", "pkg/mod.py")
    assert issues == [
        LintIssue("pkg/mod.py", 1, 5, "L002", "error", issues[0].message),
        LintIssue("pkg/mod.py", 3, 5, "L003", "error", issues[1].message),
    ]
    assert "frozenset" in issues[0].message and "self.runtime.cache" in issues[1].message


def test_syntax_errors_propagate():
    with pytest.raises(SyntaxError):
        check_step_module("def broken(:\n", "bad.py")


def test_dogfood_style_modules_are_clean():
    source = '''
        """Wynd step validate_fields (deterministic)."""
        import re
        from datetime import date
        from typing import Literal

        from pydantic import BaseModel

        from wynd.runtime import DeterministicStep

        SUPPORTED_CURRENCIES = frozenset({"AUD", "EUR", "GBP", "USD"})
        INVOICE_NUMBER = re.compile(r"[A-Z0-9][A-Z0-9/-]{1,31}")
        EARLIEST_DUE = date(2000, 1, 1)
        APPROVAL_LIMIT = 1_000_000


        class FieldError(BaseModel):
            field: str
            fixable: bool


        class ValidateFields(DeterministicStep):
            """Validate."""

            class Input(BaseModel):
                errors: list[FieldError]

            class Output(BaseModel):
                exit: Literal["done"] = "done"
                valid: bool

            def run(self, input):
                errors: list[FieldError] = []
                return self.Output(valid=not errors)
    '''
    assert codes(source) == []
    for module in sorted(FIXTURES.glob("*/*.py")):
        assert check_step_module(module.read_text(), str(module)) == [], module
