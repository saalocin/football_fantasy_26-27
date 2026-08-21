"""Tests for the bronze integrity sweep.

⚠ Every test points the sweep at a tmp_path. A guard whose own suite writes into
the repo it guards is not a guard anyone should trust.

The sweep tests the *data*, so these tests are mostly about seeding a specific
defect and confirming the sweep goes red **naming it** — a check that fails
without saying what failed sends someone hunting through a directory by hand.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fpl.lib.bronze import INGEST, write_bronze
from fpl.qa import ERROR, WARN, Report, main, sweep

HEADER = (
    "source_id,kind,title,url,license,verdict,approved_in,update_frequency,joins_on,summary,notes"
)
ROW = (
    'fpl-api,official,FPL,https://fantasy.premierleague.com/api/,"read at host",'
    "INGEST,FOO-21,per-minute,fpl_element_id,primary,ok"
)


class Registry:
    def verdict(self, source_id: str) -> str | None:
        return INGEST if source_id == "fpl-api" else None


def register_at(tmp_path: Path, *rows: str) -> Path:
    path = tmp_path / "sources.csv"
    path.write_text("\n".join([HEADER, *rows]) + "\n", encoding="utf-8")
    return path


def land(root: Path, artifact_id: str = "bootstrap-static", data: bytes = b'{"a":1}') -> Path:
    return write_bronze(
        source_id="fpl-api",
        artifact_id=artifact_id,
        data=data,
        url="https://fantasy.premierleague.com/api/",
        title="FPL bootstrap-static",
        license="INGEST, confirmed at host 2026-08-20",
        registry=Registry(),
        root=root,
    ).path


def failures(report: Report, severity: str = ERROR) -> str:
    return "\n".join(
        f"{c.name} {c.detail}" for c in report.checks if not c.passed and c.severity == severity
    )


# ------------------------------------------------------------- the empty case


def test_an_empty_bronze_is_green(tmp_path: Path) -> None:
    """⚠ The state the repo is in today. It must not be red.

    Nothing has been fetched and FOO-20 has not created the register. That is a
    legitimate stage of the project, not a defect.
    """
    report = sweep(tmp_path / "bronze", tmp_path / "sources.csv")
    assert report.ok is True
    assert report.errors == []


def test_a_missing_register_warns_when_nothing_needs_it(tmp_path: Path) -> None:
    root = tmp_path / "bronze"
    root.mkdir()
    report = sweep(root, tmp_path / "sources.csv")

    assert report.ok is True
    assert "the source register loads and validates" in failures(report, WARN)


def test_a_missing_register_is_an_error_once_artifacts_exist(tmp_path: Path) -> None:
    """⚠ The moment data exists, an unreadable register means it is unprovenanced."""
    root = tmp_path / "bronze"
    land(root)
    report = sweep(root, tmp_path / "sources.csv")

    assert report.ok is False
    assert "the source register loads and validates" in failures(report)


def test_manual_needs_no_manifest(tmp_path: Path) -> None:
    """Curated CSVs are bronze, but hand-authored: no sha256, no retrieval date."""
    root = tmp_path / "bronze"
    (root / "manual").mkdir(parents=True)
    (root / "manual" / "sources.csv").write_text(HEADER + "\n", encoding="utf-8")

    report = sweep(root, root / "manual" / "sources.csv")
    assert report.ok is True


# ----------------------------------------------------------------- happy path


def test_a_clean_bronze_passes_every_check(tmp_path: Path) -> None:
    root = tmp_path / "bronze"
    land(root, "bootstrap-static")
    land(root, "fixtures", b"[]")

    report = sweep(root, register_at(tmp_path, ROW))

    assert report.ok is True, failures(report)
    assert report.warnings == []
    assert len(report.checks) > 5


# ------------------------------------------------- seeded defects (rule 10)


def test_an_unmanifested_file_is_named(tmp_path: Path) -> None:
    """⚠ Unprovenanced data is not data at all."""
    root = tmp_path / "bronze"
    land(root)
    (root / "fpl-api" / "smuggled.json").write_bytes(b"{}")

    report = sweep(root, register_at(tmp_path, ROW))

    assert report.ok is False
    assert "smuggled.json" in failures(report)


def test_a_manifest_entry_with_no_file_is_named(tmp_path: Path) -> None:
    """⚠ And an entry with no file is a lie."""
    root = tmp_path / "bronze"
    land(root, "fixtures", b"[]")
    land(root, "bootstrap-static")
    (root / "fpl-api" / "fixtures.json").unlink()

    report = sweep(root, register_at(tmp_path, ROW))

    assert report.ok is False
    assert "fixtures" in failures(report)


def test_edited_bytes_are_caught_by_the_sha256(tmp_path: Path) -> None:
    """The check that catches a hand-edit of bronze, which is forbidden.

    Also the reason data/bronze/** is marked -text in .gitattributes: a line
    ending rewritten on checkout would trip exactly this.
    """
    root = tmp_path / "bronze"
    path = land(root)
    path.write_bytes(b'{"a":2}')

    report = sweep(root, register_at(tmp_path, ROW))

    assert report.ok is False
    assert "sha256" in failures(report)
    assert "bootstrap-static.json" in failures(report)


def test_a_wrong_byte_count_is_caught(tmp_path: Path) -> None:
    root = tmp_path / "bronze"
    land(root)
    manifest = root / "fpl-api" / "manifest.json"
    records = json.loads(manifest.read_text(encoding="utf-8"))
    records[0]["bytes"] = 9999
    manifest.write_text(json.dumps(records), encoding="utf-8")

    report = sweep(root, register_at(tmp_path, ROW))
    assert "byte count" in failures(report)


@pytest.mark.parametrize(
    "field", ["source_id", "title", "url", "license", "retrieved_at", "sha256"]
)
def test_a_missing_provenance_field_is_named(tmp_path: Path, field: str) -> None:
    root = tmp_path / "bronze"
    land(root)
    manifest = root / "fpl-api" / "manifest.json"
    records = json.loads(manifest.read_text(encoding="utf-8"))
    records[0][field] = ""
    manifest.write_text(json.dumps(records), encoding="utf-8")

    report = sweep(root, register_at(tmp_path, ROW))

    assert report.ok is False
    assert field in failures(report)


def test_a_source_id_that_disagrees_with_its_directory_is_caught(tmp_path: Path) -> None:
    root = tmp_path / "bronze"
    land(root)
    manifest = root / "fpl-api" / "manifest.json"
    records = json.loads(manifest.read_text(encoding="utf-8"))
    records[0]["source_id"] = "somewhere-else"
    manifest.write_text(json.dumps(records), encoding="utf-8")

    report = sweep(root, register_at(tmp_path, ROW))
    assert "matches its directory" in failures(report)


def test_a_source_id_missing_from_the_register_is_caught(tmp_path: Path) -> None:
    root = tmp_path / "bronze"
    land(root)
    other = tmp_path / "empty.csv"
    other.write_text(HEADER + "\n", encoding="utf-8")

    report = sweep(root, other)

    assert report.ok is False
    assert "resolves in the register" in failures(report)


def test_duplicate_manifest_ids_are_caught(tmp_path: Path) -> None:
    root = tmp_path / "bronze"
    land(root)
    manifest = root / "fpl-api" / "manifest.json"
    records = json.loads(manifest.read_text(encoding="utf-8"))
    manifest.write_text(json.dumps(records * 2), encoding="utf-8")

    report = sweep(root, register_at(tmp_path, ROW))
    assert "unique" in failures(report)


def test_a_missing_manifest_is_caught(tmp_path: Path) -> None:
    root = tmp_path / "bronze"
    land(root)
    (root / "fpl-api" / "manifest.json").unlink()

    report = sweep(root, register_at(tmp_path, ROW))

    assert report.ok is False
    assert "has a manifest" in failures(report)


def test_an_unparseable_manifest_is_caught_not_crashed_on(tmp_path: Path) -> None:
    """A corrupt manifest must be a red check, never a traceback."""
    root = tmp_path / "bronze"
    land(root)
    (root / "fpl-api" / "manifest.json").write_text("{not json", encoding="utf-8")

    report = sweep(root, register_at(tmp_path, ROW))

    assert report.ok is False
    assert "parses" in failures(report)


def test_a_stray_temp_file_is_caught(tmp_path: Path) -> None:
    """Evidence of an interrupted write, which write_bronze should never leave."""
    root = tmp_path / "bronze"
    land(root)
    (root / "fpl-api" / ".bootstrap-static.json.tmp").write_bytes(b"half")

    report = sweep(root, register_at(tmp_path, ROW))

    assert report.ok is False
    assert "temp file" in failures(report)


# -------------------------------------------------------- severity behaviour


def test_an_unsorted_manifest_warns_but_does_not_fail(tmp_path: Path) -> None:
    """Determinism, not correctness: the data is still described truthfully."""
    root = tmp_path / "bronze"
    land(root, "bootstrap-static")
    land(root, "fixtures", b"[]")
    manifest = root / "fpl-api" / "manifest.json"
    records = json.loads(manifest.read_text(encoding="utf-8"))
    manifest.write_text(json.dumps(list(reversed(records))), encoding="utf-8")

    report = sweep(root, register_at(tmp_path, ROW))

    assert report.ok is True, "an unsorted manifest must not fail a build"
    assert "sorted by id" in failures(report, WARN)


# ------------------------------------------------------------- the report


def test_the_report_carries_no_timestamp(tmp_path: Path) -> None:
    """⚠ report/qa.md is committed. A generated-at line makes every run a diff."""
    root = tmp_path / "bronze"
    land(root)
    first = sweep(root, register_at(tmp_path, ROW)).to_markdown()
    second = sweep(root, register_at(tmp_path, ROW)).to_markdown()
    assert first == second, "two runs over unchanged data must be byte-identical"


def test_the_report_names_every_check(tmp_path: Path) -> None:
    root = tmp_path / "bronze"
    land(root)
    markdown = sweep(root, register_at(tmp_path, ROW)).to_markdown()
    assert "# Bronze integrity report" in markdown
    assert "PASS" in markdown
    assert "Do not edit" in markdown


# ---------------------------------------------------------------- the command


def test_main_exits_zero_on_clean_data(tmp_path: Path, capsys) -> None:
    root = tmp_path / "bronze"
    land(root)
    report_path = tmp_path / "report" / "qa.md"

    code = main([str(root)], register_path=register_at(tmp_path, ROW), report_path=report_path)

    assert code == 0
    assert report_path.exists()


def test_main_exits_non_zero_and_names_the_error(tmp_path: Path, capsys) -> None:
    root = tmp_path / "bronze"
    land(root)
    (root / "fpl-api" / "smuggled.json").write_bytes(b"{}")

    code = main(
        [str(root)],
        register_path=register_at(tmp_path, ROW),
        report_path=tmp_path / "report" / "qa.md",
    )

    assert code == 1, "a build must be gateable on this"
    assert "smuggled.json" in capsys.readouterr().out


def test_terminal_output_is_ascii(tmp_path: Path, capsys) -> None:
    """⚠ The Windows console is cp1252 and raises on a tick or an em-dash.

    A guard that crashes on the machine it exists to protect is worse than no
    guard. Found the hard way while building FOO-26.
    """
    root = tmp_path / "bronze"
    land(root)
    (root / "fpl-api" / "smuggled.json").write_bytes(b"{}")
    main(
        [str(root)],
        register_path=tmp_path / "absent.csv",
        report_path=tmp_path / "report" / "qa.md",
    )

    out = capsys.readouterr().out
    out.encode("ascii")  # raises UnicodeEncodeError if anything non-ASCII slipped in
    assert out.strip()


def test_the_report_is_written_with_lf_on_every_platform(tmp_path: Path) -> None:
    """Python writes CRLF on Windows by default; git stores this file as LF.

    The mismatch is invisible because git normalises on read, which is exactly
    what makes it worth pinning: the committed blob and the file on disk should
    not differ by which machine ran the command.
    """
    root = tmp_path / "bronze"
    land(root)
    report_path = tmp_path / "report" / "qa.md"

    main([str(root)], register_path=register_at(tmp_path, ROW), report_path=report_path)

    assert b"\r" not in report_path.read_bytes()


# ------------------------------------------------------- gameweek coverage


def land_gw(root: Path, gameweek: int, season: str = "2026-27") -> None:
    write_bronze(
        source_id="fpl-api",
        artifact_id=f"bootstrap-static-gw{gameweek:02d}",
        data=f'{{"gw":{gameweek}}}'.encode(),
        url="https://fantasy.premierleague.com/api/",
        title=f"bootstrap GW{gameweek}",
        license="INGEST",
        registry=Registry(),
        season=season,
        gameweek=gameweek,
        root=root,
    )


def test_an_interior_gap_is_caught_with_no_configuration(tmp_path: Path) -> None:
    """GW1, 2 and 4 present proves GW3 was missed. Nothing needs to be told."""
    root = tmp_path / "bronze"
    for gw in (1, 2, 4):
        land_gw(root, gw)

    report = sweep(root, register_at(tmp_path, ROW))

    assert report.ok is False
    assert "GW3" in failures(report)
    assert "cannot be re-fetched" in failures(report)


def test_several_interior_gaps_are_all_named(tmp_path: Path) -> None:
    root = tmp_path / "bronze"
    for gw in (1, 4, 5, 9):
        land_gw(root, gw)
    detail = failures(sweep(root, register_at(tmp_path, ROW)))
    for gw in ("GW2", "GW3", "GW6", "GW7", "GW8"):
        assert gw in detail


def test_a_contiguous_run_passes(tmp_path: Path) -> None:
    root = tmp_path / "bronze"
    for gw in (1, 2, 3, 4):
        land_gw(root, gw)
    report = sweep(root, register_at(tmp_path, ROW))
    assert report.ok is True, failures(report)


def test_a_trailing_gap_needs_through_gameweek(tmp_path: Path) -> None:
    """⚠ 'we have up to GW7' and 'the season is at GW7' look identical."""
    root = tmp_path / "bronze"
    for gw in (1, 2, 3):
        land_gw(root, gw)

    assert sweep(root, register_at(tmp_path, ROW)).ok is True

    late = sweep(root, register_at(tmp_path, ROW), through_gameweek=6)
    assert late.ok is False
    for gw in ("GW4", "GW5", "GW6"):
        assert gw in failures(late)


def test_nothing_captured_at_all_is_caught_when_the_season_has_started(tmp_path: Path) -> None:
    root = tmp_path / "bronze"
    root.mkdir()
    report = sweep(root, register_at(tmp_path, ROW), through_gameweek=3)
    assert report.ok is False
    assert "nothing captured at all" in failures(report)


def test_seasons_are_tracked_separately(tmp_path: Path) -> None:
    root = tmp_path / "bronze"
    land_gw(root, 1, "2025-26")
    land_gw(root, 3, "2025-26")
    land_gw(root, 1, "2026-27")
    detail = failures(sweep(root, register_at(tmp_path, ROW)))
    assert "2025-26" in detail and "GW2" in detail
    assert "2026-27: every expected gameweek" not in detail, "a one-week season has no gaps"


def test_artifacts_without_a_gameweek_do_not_affect_coverage(tmp_path: Path) -> None:
    """element-summary is cumulative history, not a snapshot of a week."""
    root = tmp_path / "bronze"
    land_gw(root, 1)
    write_bronze(
        source_id="fpl-api",
        artifact_id="element-summary-7-2026-27",
        data=b"{}",
        url="https://x.test/",
        title="history",
        license="INGEST",
        registry=Registry(),
        season="2026-27",
        root=root,
    )
    assert sweep(root, register_at(tmp_path, ROW)).ok is True


def test_missing_gameweeks_is_pure_and_handles_the_empty_case() -> None:
    from fpl.qa import missing_gameweeks

    assert missing_gameweeks({1, 2, 4}) == [3]
    assert missing_gameweeks({1, 2, 3}) == []
    assert missing_gameweeks({1, 2}, through=5) == [3, 4, 5]
    assert missing_gameweeks(set()) == []
    assert missing_gameweeks(set(), through=3) == [1, 2, 3]
    assert missing_gameweeks({5, 6}) == [], "a season that started late is not a gap"
