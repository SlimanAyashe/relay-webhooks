"""`docs/failure-modes.md` promises, row by row, that the claimed behavior has a proof you
can run. This is what makes that promise checkable.

Two rules, both cheap and both easy to violate by accident:
every row's Proof cell names a test that exists, and the handful of rows whose proof is an
executed operational drill rather than a pytest are enumerated here explicitly, so "no
automated proof" stays a deliberate, visible exception instead of a quiet default.
"""

import re

import pytest

from tests import testrefs

FAILURE_MODES_DOC = testrefs.REPO_ROOT / "docs" / "failure-modes.md"

# Rows whose honest proof is an executed drill or a one-off inspection, not a test. Each
# entry is a substring of that row's Proof cell; the count is asserted below so this list
# can't grow without someone noticing.
MANUAL_PROOF_MARKERS = (
    "docs/PROJECT_STATUS.md",
    "docker inspect",
    "docs/runbook.md",
)
MAX_MANUAL_PROOF_ROWS = 4


def _table_rows() -> list[tuple[str, str, str]]:
    rows = []
    for line in FAILURE_MODES_DOC.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 3 or set(cells[0]) <= {"-", " "} or cells[0] == "Failure point":
            continue
        rows.append((cells[0], cells[1], cells[2]))
    return rows


ROWS = _table_rows()


def test_the_table_was_parsed_at_all() -> None:
    assert len(ROWS) >= 20, f"only parsed {len(ROWS)} rows out of {FAILURE_MODES_DOC.name}"


@pytest.mark.parametrize("row", ROWS, ids=[re.sub(r"\W+", "-", r[0])[:60] for r in ROWS])
def test_every_row_points_at_a_proof_that_exists(row: tuple[str, str, str]) -> None:
    failure_point, _expected, proof = row
    node_ids = testrefs.node_ids_in_text(proof)

    if not node_ids:
        assert any(marker in proof for marker in MANUAL_PROOF_MARKERS), (
            f"row {failure_point!r} claims a behavior but names no runnable proof: {proof!r}"
        )
        return

    missing = testrefs.missing(node_ids)
    assert not missing, f"row {failure_point!r} names tests that no longer exist: {missing}"


def test_rows_proven_only_by_a_manual_drill_stay_the_rare_exception() -> None:
    manual = [
        failure_point
        for failure_point, _expected, proof in ROWS
        if not testrefs.node_ids_in_text(proof)
    ]

    assert len(manual) <= MAX_MANUAL_PROOF_ROWS, (
        f"too many failure modes rest on a manual drill rather than a runnable test: {manual}"
    )
