"""Tests for the source register loader.

The register is the gate the whole bronze layer hangs off, so most of these are
about what it *refuses* rather than what it accepts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fpl.lib.bronze import VerdictRefused, write_bronze
from fpl.lib.sources import (
    COLUMNS,
    VERDICTS,
    RegisterError,
    SourceRegister,
    UndeclaredColumn,
)

HEADER = ",".join(COLUMNS)

GOOD = (
    "fpl-api,official,FPL bootstrap-static,https://fantasy.premierleague.com/api/,"
    '"Terms read at the host 2026-08-20",INGEST,FOO-21,per-minute,fpl_element_id,'
    "The primary source,Confirmed at the host 2026-08-20"
)
FACTS_ONLY = (
    "understat,stats,Understat xG,https://understat.test/,"
    '"Personal use only, no scraping",FACTS-ONLY,FOO-29,daily,name+club,'
    "Underlying numbers,Read at host 2026-08-20"
)
REJECTED = (
    "somefeed,news,Some Feed,https://somefeed.test/,"
    '"Terms forbid automated access",REJECTED,FOO-30,,,'
    "Ruled out,Terms read at host 2026-08-20"
)


def write_register(tmp_path: Path, *rows: str, header: str = HEADER) -> Path:
    path = tmp_path / "sources.csv"
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return path


# ----------------------------------------------------------------- happy path


def test_loads_a_valid_register(tmp_path: Path) -> None:
    register = SourceRegister.load(write_register(tmp_path, GOOD, FACTS_ONLY, REJECTED))

    assert len(register) == 3
    assert register.verdict("fpl-api") == "INGEST"
    assert register.verdict("understat") == "FACTS-ONLY"
    assert register.verdict("somefeed") == "REJECTED"
    assert register.verdict("never-heard-of-it") is None


def test_may_ingest_is_true_only_for_ingest(tmp_path: Path) -> None:
    register = SourceRegister.load(write_register(tmp_path, GOOD, FACTS_ONLY, REJECTED))
    assert register.may_ingest("fpl-api") is True
    assert register.may_ingest("understat") is False
    assert register.may_ingest("somefeed") is False
    assert register.may_ingest("never-heard-of-it") is False


def test_a_rejected_row_is_a_valid_entry(tmp_path: Path) -> None:
    """ "We looked and said no" must be recordable, not just absent."""
    register = SourceRegister.load(write_register(tmp_path, REJECTED))
    row = register.row("somefeed")
    assert row is not None
    assert row.verdict == "REJECTED"
    assert row.may_ingest is False
    assert "somefeed" in register, "a refusal is still a row, so still registered"


def test_a_bom_from_excel_does_not_break_the_header(tmp_path: Path) -> None:
    """The file is hand-edited and may pass through Excel."""
    path = tmp_path / "sources.csv"
    path.write_text("﻿" + HEADER + "\n" + GOOD + "\n", encoding="utf-8")
    assert SourceRegister.load(path).verdict("fpl-api") == "INGEST"


def test_values_are_stripped(tmp_path: Path) -> None:
    padded = GOOD.replace("fpl-api,", "  fpl-api  ,", 1)
    register = SourceRegister.load(write_register(tmp_path, padded))
    assert register.verdict("fpl-api") == "INGEST"


# ------------------------------------------------------- the header assertion


def test_a_renamed_column_is_an_error_not_an_empty_value(tmp_path: Path) -> None:
    """⚠ THE DEFECT THIS MODULE EXISTS TO CLOSE.

    Rename `license` to `licence` and every row still parses — the value just
    arrives blank, which looks like a row nobody filled in rather than a file
    the code can no longer read.
    """
    path = write_register(tmp_path, GOOD, header=HEADER.replace("license", "licence"))

    with pytest.raises(RegisterError) as caught:
        SourceRegister.load(path)

    problems = "\n".join(caught.value.problems)
    assert "missing column(s): license" in problems
    assert "undeclared column(s): licence" in problems


def test_an_extra_column_is_refused(tmp_path: Path) -> None:
    path = write_register(tmp_path, GOOD + ",surprise", header=HEADER + ",surprise")
    with pytest.raises(RegisterError, match="undeclared column"):
        SourceRegister.load(path)


def test_column_order_does_not_matter(tmp_path: Path) -> None:
    """Order is cosmetic; the set is what the code depends on."""
    columns = list(COLUMNS)
    reordered = [columns[-1], *columns[:-1]]
    values = dict(zip(columns, _split(GOOD), strict=True))
    path = write_register(
        tmp_path,
        ",".join(f'"{values[name]}"' for name in reordered),
        header=",".join(reordered),
    )
    assert SourceRegister.load(path).verdict("fpl-api") == "INGEST"


def _split(row: str) -> list[str]:
    import csv
    import io

    return next(csv.reader(io.StringIO(row)))


# ------------------------------------------------------------ row validation


@pytest.mark.parametrize(
    "column", ["source_id", "kind", "title", "url", "license", "verdict", "approved_in"]
)
def test_required_columns_may_not_be_empty(tmp_path: Path, column: str) -> None:
    values = dict(zip(list(COLUMNS), _split(GOOD), strict=True))
    values[column] = ""
    path = write_register(tmp_path, ",".join(f'"{v}"' for v in values.values()))

    with pytest.raises(RegisterError) as caught:
        SourceRegister.load(path)
    assert f"{column} is required and empty" in "\n".join(caught.value.problems)


@pytest.mark.parametrize("verdict", ["ingest", "MAYBE", "OK", "FACTS ONLY", "INGEST-ISH"])
def test_a_verdict_outside_the_ladder_is_refused(tmp_path: Path, verdict: str) -> None:
    """The ladder is hard-coded because it is the gate, not a vocabulary.

    Lowercase is refused too: a gate that accepts near-misses is a gate that
    eventually accepts the wrong thing.
    """
    assert verdict not in VERDICTS, "this case is only meaningful off the ladder"
    row = GOOD.replace(",INGEST,", f",{verdict},", 1)
    with pytest.raises(RegisterError, match="is not one of"):
        SourceRegister.load(write_register(tmp_path, row))


@pytest.mark.parametrize("bad", ["Understat", "under stat", "../escape", "_leading"])
def test_source_id_must_be_a_permanent_slug(tmp_path: Path, bad: str) -> None:
    row = GOOD.replace("fpl-api,", f"{bad},", 1)
    with pytest.raises(RegisterError, match="is not a slug"):
        SourceRegister.load(write_register(tmp_path, row))


@pytest.mark.parametrize("bad", ["foo-21", "21", "FOO21", "https://linear.app/x", "TBD"])
def test_approved_in_must_look_like_a_ticket(tmp_path: Path, bad: str) -> None:
    """⚠ The approval lives in Linear; this column is the only way back to it."""
    row = GOOD.replace(",FOO-21,", f",{bad},", 1)
    with pytest.raises(RegisterError) as caught:
        SourceRegister.load(write_register(tmp_path, row))
    assert "does not look like a Linear ticket id" in "\n".join(caught.value.problems)


def test_duplicate_source_ids_are_refused(tmp_path: Path) -> None:
    """Slugs are permanent and go into every manifest entry."""
    with pytest.raises(RegisterError) as caught:
        SourceRegister.load(write_register(tmp_path, GOOD, GOOD))
    assert "already used on line 2" in "\n".join(caught.value.problems)


def test_every_problem_is_reported_at_once(tmp_path: Path) -> None:
    """Fixing a curated file must not be a guess-and-rerun loop."""
    broken = "BadId,,Title,https://x.test/,lic,MAYBE,nope,,,,"
    with pytest.raises(RegisterError) as caught:
        SourceRegister.load(write_register(tmp_path, broken))

    problems = "\n".join(caught.value.problems)
    assert "is not a slug" in problems
    assert "kind is required and empty" in problems
    assert "is not one of" in problems
    assert "does not look like a Linear ticket id" in problems
    assert len(caught.value.problems) >= 4


def test_line_numbers_point_at_the_file(tmp_path: Path) -> None:
    """Line 1 is the header, so the first data row is line 2."""
    broken = REJECTED.replace(",REJECTED,", ",NONSENSE,", 1)
    with pytest.raises(RegisterError) as caught:
        SourceRegister.load(write_register(tmp_path, GOOD, broken))
    assert "line 3:" in "\n".join(caught.value.problems)


# --------------------------------------------------------------- fail closed


def test_a_missing_register_raises_rather_than_being_empty(tmp_path: Path) -> None:
    """⚠ "Empty register" and "wrong working directory" must not look alike."""
    with pytest.raises(RegisterError) as caught:
        SourceRegister.load(tmp_path / "nope.csv")
    assert "does not exist" in "\n".join(caught.value.problems)
    assert "FOO-20" in "\n".join(caught.value.problems), "say who creates it"


def test_an_empty_register_loads_and_refuses_everything(tmp_path: Path) -> None:
    register = SourceRegister.load(write_register(tmp_path))
    assert len(register) == 0
    assert register.verdict("fpl-api") is None


# ------------------------------------------------- the code-side declaration


def test_reading_an_undeclared_column_raises(tmp_path: Path) -> None:
    """The mirror of the header assertion: this guards the CODE.

    Without it a typo like row["licence"] reads as empty and quietly produces a
    manifest with no licence in it.
    """
    from fpl.lib.sources import _DeclaredRow

    row = _DeclaredRow({"source_id": "fpl-api"})
    assert row["source_id"] == "fpl-api"
    assert row["notes"] == "", "a declared but absent column is legitimately empty"

    with pytest.raises(UndeclaredColumn):
        row["licence"]


# --------------------------------------------------------------- integration


def test_the_register_drives_write_bronze(tmp_path: Path) -> None:
    """The whole point: one gate, and the write path cannot skip it."""
    register = SourceRegister.load(write_register(tmp_path, GOOD, FACTS_ONLY, REJECTED))
    root = tmp_path / "bronze"
    common = {
        "artifact_id": "payload",
        "data": b"{}",
        "url": "https://example.test/",
        "title": "t",
        "license": "l",
        "registry": register,
        "root": root,
    }

    result = write_bronze(source_id="fpl-api", **common)
    assert result.written is True

    for refused in ("understat", "somefeed"):
        with pytest.raises(VerdictRefused):
            write_bronze(source_id=refused, **common)

    with pytest.raises(VerdictRefused):
        write_bronze(source_id="never-registered", **common)

    assert [path.name for path in root.iterdir()] == ["fpl-api"], (
        "a refused source must not leave a directory behind"
    )
