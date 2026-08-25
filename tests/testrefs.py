"""Resolving `tests/path.py::test_name` references to real test functions.

Used by the audits that keep `docs/failure-scenarios.md` and `docs/failure-modes.md`
honest: a Proof cell naming a test that was renamed, moved or deleted is worse than an
empty one, because it reads as evidence. Parsing the file's AST (rather than importing it
or shelling out to `pytest --collect-only`) keeps the check instant and dependency-free.
"""

import ast
import re
from functools import cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#  `tests/integration/test_foo.py::test_bar` -- parametrized ids (`::test_bar[case]`) are
#  matched up to the function name, which is what actually has to exist.
NODE_ID_PATTERN = re.compile(r"tests/[\w./-]+\.py::[\w]+")


@cache
def test_names_in(relative_path: str) -> frozenset[str]:
    path = REPO_ROOT / relative_path
    if not path.is_file():
        return frozenset()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return frozenset(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test")
    )


def exists(node_id: str) -> bool:
    relative_path, _, function_name = node_id.partition("::")
    return function_name in test_names_in(relative_path)


def missing(node_ids: object) -> list[str]:
    """The subset of `node_ids` that does not resolve to a test function in the repo."""
    assert isinstance(node_ids, list | tuple | set | frozenset)
    return sorted(node_id for node_id in node_ids if not exists(str(node_id)))


def node_ids_in_text(text: str) -> list[str]:
    return NODE_ID_PATTERN.findall(text)
