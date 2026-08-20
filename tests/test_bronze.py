"""Tests for the bronze write and manifest contract.

Everything runs against a tmp_path root, never the real ``data/bronze/``. A test
suite that wrote into the committed data directory would be indistinguishable
from a fetcher doing its job, which is the one thing bronze must never be
ambiguous about.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from fpl.lib.bronze import (
    INGEST,
    BronzeImmutable,
    ManifestEntry,
    VerdictRefused,
    is_present,
    iso_timestamp,
    manifest_path,
    read_manifest,
    sha256_of,
    write_bronze,
)

BODY = b'{"events": [], "elements": []}\r\n'
WHEN = datetime(2026, 8, 20, 15, 4, 54, 97000, tzinfo=UTC)


class FakeRegistry:
    """Stands in for FOO-26. One method, because that is the whole contract."""

    def __init__(self, **verdicts: str) -> None:
        self._verdicts = verdicts

    def verdict(self, source_id: str) -> str | None:
        return self._verdicts.get(source_id)


def ingesting() -> FakeRegistry:
    return FakeRegistry(fpl_api=INGEST)


def land(root: Path, *, artifact_id: str = "bootstrap-static", data: bytes = BODY, **kwargs):
    defaults = {
        "source_id": "fpl_api",
        "artifact_id": artifact_id,
        "data": data,
        "url": "https://fantasy.premierleague.com/api/bootstrap-static/",
        "title": "FPL bootstrap-static",
        "license": "verdict INGEST, confirmed at the host 2026-08-20",
        "registry": ingesting(),
        "retrieved_at": WHEN,
        "root": root,
    }
    return write_bronze(**{**defaults, **kwargs})


# ------------------------------------------------------------ the verdict gate


def test_ingest_verdict_lands_the_bytes(tmp_path: Path) -> None:
    result = land(tmp_path)
    assert result.written is True
    assert result.path.read_bytes() == BODY, "bytes must survive exactly as served"


@pytest.mark.parametrize("verdict", ["FACTS-ONLY", "LINK-ONLY", "REJECTED"])
def test_only_ingest_permits_storage(tmp_path: Path, verdict: str) -> None:
    """The distinction is legally load-bearing, so it is enforced, not trusted."""
    with pytest.raises(VerdictRefused) as caught:
        land(tmp_path, registry=FakeRegistry(fpl_api=verdict))
    assert caught.value.verdict == verdict


def test_an_unregistered_source_is_refused(tmp_path: Path) -> None:
    with pytest.raises(VerdictRefused) as caught:
        land(tmp_path, registry=FakeRegistry())
    assert caught.value.verdict is None
    assert "no row in the register" in str(caught.value)


def test_a_refusal_leaves_nothing_behind(tmp_path: Path) -> None:
    """Not even an empty directory as evidence we nearly stored something."""
    with pytest.raises(VerdictRefused):
        land(tmp_path, registry=FakeRegistry(fpl_api="REJECTED"))
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------- the manifest


def test_manifest_records_the_full_provenance(tmp_path: Path) -> None:
    land(tmp_path, season="2026-27", gameweek=1)
    records = json.loads(manifest_path("fpl_api", tmp_path).read_text(encoding="utf-8"))

    assert len(records) == 1
    assert records[0] == {
        "id": "bootstrap-static",
        "source_id": "fpl_api",
        "title": "FPL bootstrap-static",
        "url": "https://fantasy.premierleague.com/api/bootstrap-static/",
        "license": "verdict INGEST, confirmed at the host 2026-08-20",
        "retrieved_at": "2026-08-20T15:04:54.097Z",
        "season": "2026-27",
        "gameweek": 1,
        "bytes": len(BODY),
        "sha256": sha256_of(BODY),
    }


def test_season_and_gameweek_are_omitted_when_absent(tmp_path: Path) -> None:
    """A missing key reads as "not applicable"; a null reads as "we looked"."""
    land(tmp_path)
    records = json.loads(manifest_path("fpl_api", tmp_path).read_text(encoding="utf-8"))
    assert "season" not in records[0]
    assert "gameweek" not in records[0]


def test_several_artifacts_share_one_manifest(tmp_path: Path) -> None:
    land(tmp_path, artifact_id="fixtures", data=b"[]")
    land(tmp_path, artifact_id="bootstrap-static")
    land(tmp_path, artifact_id="element-summary-1", data=b"{}")

    entries = read_manifest("fpl_api", tmp_path)
    assert [entry.id for entry in entries] == [
        "bootstrap-static",
        "element-summary-1",
        "fixtures",
    ], "entries are sorted by id so the file is deterministic"


def test_manifest_is_byte_identical_regardless_of_write_order(tmp_path: Path) -> None:
    """Two runs that landed the same artifacts must produce the same file."""
    one, two = tmp_path / "one", tmp_path / "two"
    for artifact in ("alpha", "beta", "gamma"):
        land(one, artifact_id=artifact, data=artifact.encode())
    for artifact in ("gamma", "alpha", "beta"):
        land(two, artifact_id=artifact, data=artifact.encode())

    assert manifest_path("fpl_api", one).read_bytes() == manifest_path("fpl_api", two).read_bytes()


# --------------------------------------------------------------- idempotency


def test_relanding_identical_bytes_changes_nothing(tmp_path: Path) -> None:
    """A clean re-run must write nothing and leave git status clean."""
    land(tmp_path)
    before = manifest_path("fpl_api", tmp_path).read_bytes()

    result = land(tmp_path, retrieved_at=datetime(2027, 1, 1, tzinfo=UTC))

    assert result.written is False
    assert manifest_path("fpl_api", tmp_path).read_bytes() == before, (
        "re-running churned the manifest — retrieved_at must not be rewritten"
    )
    assert result.entry.retrieved_at == "2026-08-20T15:04:54.097Z"


def test_a_file_with_no_manifest_entry_is_healed(tmp_path: Path) -> None:
    """Unprovenanced data is not data. Finding some means writing the entry."""
    land(tmp_path)
    manifest_path("fpl_api", tmp_path).unlink()

    result = land(tmp_path)

    assert result.written is True
    assert [entry.id for entry in read_manifest("fpl_api", tmp_path)] == ["bootstrap-static"]


def test_an_entry_with_no_file_is_repaired(tmp_path: Path) -> None:
    """The mirror case: a manifest entry with nothing behind it is a lie."""
    result = land(tmp_path)
    result.path.unlink()

    again = land(tmp_path)

    assert again.written is True
    assert again.path.read_bytes() == BODY


# --------------------------------------------------------------- immutability


def test_different_bytes_over_an_existing_artifact_raises(tmp_path: Path) -> None:
    """Bronze is never edited. There is deliberately no --force flag."""
    land(tmp_path)

    with pytest.raises(BronzeImmutable) as caught:
        land(tmp_path, data=b'{"events": [1]}')

    assert "delete the file to re-fetch" in str(caught.value)
    assert caught.value.existing_sha == sha256_of(BODY)


def test_deleting_the_file_is_how_you_re_fetch(tmp_path: Path) -> None:
    result = land(tmp_path)
    result.path.unlink()

    fresh = land(tmp_path, data=b'{"events": [1]}')

    assert fresh.written is True
    assert fresh.path.read_bytes() == b'{"events": [1]}'


# ----------------------------------------------------------------- is_present


def test_is_present_requires_both_halves(tmp_path: Path) -> None:
    assert is_present("fpl_api", "bootstrap-static", root=tmp_path) is False

    result = land(tmp_path)
    assert is_present("fpl_api", "bootstrap-static", root=tmp_path) is True

    manifest_path("fpl_api", tmp_path).unlink()
    assert is_present("fpl_api", "bootstrap-static", root=tmp_path) is False, (
        "a file with no manifest entry is unprovenanced, so not present"
    )

    land(tmp_path)
    result.path.unlink()
    assert is_present("fpl_api", "bootstrap-static", root=tmp_path) is False, (
        "an entry with no file is a lie, so not present"
    )


# ------------------------------------------------------------------ atomicity


def test_no_temp_files_survive_a_successful_write(tmp_path: Path) -> None:
    land(tmp_path, season="2026-27")
    leftovers = [path.name for path in (tmp_path / "fpl_api").iterdir() if ".tmp" in path.name]
    assert leftovers == []


def test_a_failed_write_leaves_no_half_file(tmp_path: Path, monkeypatch) -> None:
    """An interrupted run must not leave bytes that later hash as though whole."""
    import fpl.lib.bronze as bronze

    def exploding_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(bronze.os, "replace", exploding_replace)

    with pytest.raises(OSError, match="disk full"):
        land(tmp_path)

    assert list((tmp_path / "fpl_api").iterdir()) == [], "a temp file survived the failure"


# ------------------------------------------------------------- traversal guard


@pytest.mark.parametrize("bad", ["../escape", "has/slash", "UPPER", "", ".hidden", "trailing "])
def test_ids_that_are_not_slugs_are_refused(tmp_path: Path, bad: str) -> None:
    """Ids become path segments, so the slug rule is a traversal guard."""
    with pytest.raises(ValueError, match="not a slug"):
        write_bronze(
            source_id="fpl_api",
            artifact_id=bad,
            data=BODY,
            url="https://example.test/",
            title="t",
            license="l",
            registry=ingesting(),
            root=tmp_path,
        )


def test_a_source_id_cannot_climb_out_of_the_bronze_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a slug"):
        land(tmp_path, source_id="../../etc", registry=FakeRegistry(**{"../../etc": INGEST}))


# -------------------------------------------------------------------- helpers


def test_iso_timestamp_matches_the_manifest_shape() -> None:
    assert iso_timestamp(WHEN) == "2026-08-20T15:04:54.097Z"


def test_iso_timestamp_normalises_to_utc() -> None:
    from datetime import timedelta, timezone

    sast = timezone(timedelta(hours=2))
    assert iso_timestamp(datetime(2026, 8, 20, 17, 4, 54, 97000, tzinfo=sast)) == (
        "2026-08-20T15:04:54.097Z"
    )


def test_manifest_entry_round_trips_through_json() -> None:
    entry = ManifestEntry(
        id="fixtures",
        source_id="fpl_api",
        title="Fixtures — difficulty and kickoff times",
        url="https://fantasy.premierleague.com/api/fixtures/",
        license="INGEST",
        retrieved_at="2026-08-20T15:04:54.097Z",
        bytes=42,
        sha256="abc",
        season="2026-27",
        gameweek=3,
    )
    assert ManifestEntry.from_json(entry.to_json()) == entry
