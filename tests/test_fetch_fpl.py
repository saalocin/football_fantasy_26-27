"""Tests for the FPL bronze fetcher.

⚠ Nothing here touches the network. Every test drives `httpx.MockTransport`, so
the suite exercises the real fetch path without a single request leaving the
machine — which also means it stays honest while FOO-21 is still open and we are
not permitted to call the real API at all.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from fpl.fetch_fpl import (
    SOURCE_ID,
    core_artifacts,
    element_summary_artifacts,
    entry_artifacts,
    fetch_all,
    main,
)
from fpl.lib.bronze import BronzeImmutable, read_manifest
from fpl.lib.sources import SourceRegister

HEADER = (
    "source_id,kind,title,url,license,verdict,approved_in,update_frequency,joins_on,summary,notes"
)


def register_at(tmp_path: Path, verdict: str = "INGEST") -> Path:
    row = (
        f"{SOURCE_ID},official,FPL API,https://fantasy.premierleague.com/api/,"
        f'"Terms read at the host 2026-08-20",{verdict},FOO-21,per-minute,'
        "fpl_element_id,the primary source,confirmed at host"
    )
    path = tmp_path / "sources.csv"
    path.write_text(HEADER + "\n" + row + "\n", encoding="utf-8")
    return path


def api(bodies: dict[str, bytes] | None = None) -> tuple[httpx.Client, list[str]]:
    """A stand-in FPL API that records what was asked of it."""
    calls: list[str] = []
    bodies = bodies or {}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, content=bodies.get(request.url.path, b'{"ok":true}'))

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def land(tmp_path: Path, gameweek: int = 1, **kwargs):
    registry = SourceRegister.load(register_at(tmp_path))
    client, calls = kwargs.pop("client_and_calls", api())
    written = fetch_all(
        core_artifacts("2026-27", gameweek),
        row=registry.row(SOURCE_ID),
        registry=registry,
        season="2026-27",
        gameweek=gameweek,
        client=client,
        root=tmp_path / "bronze",
        log=False,
        **kwargs,
    )
    return written, calls


# ------------------------------------------------------------------ artifacts


def test_core_artifacts_cover_bootstrap_and_fixtures() -> None:
    artifacts = core_artifacts("2026-27", 1)
    paths = {a.path for a in artifacts}
    assert paths == {"bootstrap-static/", "fixtures/"}
    assert all(a.url.startswith("https://fantasy.premierleague.com/api/") for a in artifacts)


def test_snapshot_ids_carry_the_gameweek() -> None:
    """⚠ Not just the manifest — the id itself.

    These endpoints serve current state, so GW2's bytes differ from GW1's.
    A shared id would collide, and overwriting would destroy the only copy of
    GW1 that will ever exist.
    """
    gw1 = {a.id for a in core_artifacts("2026-27", 1)}
    gw2 = {a.id for a in core_artifacts("2026-27", 2)}
    assert gw1.isdisjoint(gw2)
    assert "bootstrap-static-gw01" in gw1
    assert "bootstrap-static-gw02" in gw2


def test_element_summary_is_keyed_by_season_not_gameweek() -> None:
    """Cumulative history, not a snapshot: re-fetching returns more of the same."""
    ids = {a.id for a in element_summary_artifacts([1, 2], "2026-27")}
    assert ids == {"element-summary-1-2026-27", "element-summary-2-2026-27"}


def test_entry_artifacts_cover_summary_and_picks() -> None:
    artifacts = entry_artifacts(12345, "2026-27", 7)
    assert [a.path for a in artifacts] == ["entry/12345/", "entry/12345/event/7/picks/"]
    assert all("gw07" in a.id for a in artifacts)


# -------------------------------------------------------------------- landing


def test_fetches_and_lands_with_provenance(tmp_path: Path) -> None:
    written, calls = land(tmp_path)

    assert len(written) == 2
    assert sorted(calls) == ["/api/bootstrap-static/", "/api/fixtures/"]

    entries = {e.id: e for e in read_manifest(SOURCE_ID, tmp_path / "bronze")}
    entry = entries["bootstrap-static-gw01"]
    assert entry.season == "2026-27"
    assert entry.gameweek == 1
    assert entry.url == "https://fantasy.premierleague.com/api/bootstrap-static/"
    assert entry.sha256


def test_the_licence_comes_from_the_register_not_a_literal(tmp_path: Path) -> None:
    """⚠ Hard-coding it here would let it drift from the verdict that allowed the fetch."""
    land(tmp_path)
    entry = read_manifest(SOURCE_ID, tmp_path / "bronze")[0]
    assert entry.license == "Terms read at the host 2026-08-20"


def test_bytes_land_exactly_as_served(tmp_path: Path) -> None:
    body = b'{"elements": []}\r\n\xe2\x9a\xbd'
    client, _ = api({"/api/bootstrap-static/": body})
    land(tmp_path, client_and_calls=(client, []))

    path = tmp_path / "bronze" / SOURCE_ID / "bootstrap-static-gw01.json"
    assert path.read_bytes() == body


# ---------------------------------------------------------------- idempotency


def test_a_clean_rerun_fetches_nothing(tmp_path: Path) -> None:
    """⚠ Presence is checked BEFORE the network, so a re-run costs zero requests."""
    land(tmp_path)

    client, calls = api()
    written, _ = land(tmp_path, client_and_calls=(client, calls))

    assert written == []
    assert calls == [], "a re-run reached the network for data it already had"


def test_a_later_gameweek_lands_alongside_the_earlier_one(tmp_path: Path) -> None:
    land(tmp_path, gameweek=1)
    land(tmp_path, gameweek=2)

    ids = sorted(e.id for e in read_manifest(SOURCE_ID, tmp_path / "bronze"))
    assert ids == [
        "bootstrap-static-gw01",
        "bootstrap-static-gw02",
        "fixtures-gw01",
        "fixtures-gw02",
    ]


def test_changed_bytes_under_the_same_id_still_refuse(tmp_path: Path) -> None:
    """The immutability rule survives going through the fetcher."""
    land(tmp_path, gameweek=1)
    (tmp_path / "bronze" / SOURCE_ID / "manifest.json").unlink()

    client, _ = api({"/api/bootstrap-static/": b'{"different":1}'})
    with pytest.raises(BronzeImmutable):
        land(tmp_path, gameweek=1, client_and_calls=(client, []))


# ---------------------------------------------------------------- the gate


def test_it_refuses_to_run_without_a_register(tmp_path: Path, capsys) -> None:
    code = main(
        [
            "--gameweek",
            "1",
            "--root",
            str(tmp_path / "bronze"),
            "--register",
            str(tmp_path / "absent.csv"),
        ]
    )
    assert code == 2
    assert "refusing to run" in capsys.readouterr().err


@pytest.mark.parametrize("verdict", ["FACTS-ONLY", "LINK-ONLY", "REJECTED"])
def test_it_refuses_to_run_on_a_non_ingest_verdict(tmp_path: Path, capsys, verdict: str) -> None:
    """⚠ The FPL API is not exempt just because it is the obvious source."""
    code = main(
        [
            "--gameweek",
            "1",
            "--root",
            str(tmp_path / "bronze"),
            "--register",
            str(register_at(tmp_path, verdict)),
        ]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert verdict in err
    assert "FOO-21" in err, "say whose call it is and where"
    assert not (tmp_path / "bronze").exists(), "a refusal must not create the directory"


# ---------------------------------------------------------------- the dry run


def test_dry_run_touches_no_network_and_needs_no_register(tmp_path: Path, capsys) -> None:
    """Useful precisely while the gate is closed: shows the plan without acting."""
    code = main(
        [
            "--gameweek",
            "3",
            "--entry",
            "12345",
            "--dry-run",
            "--root",
            str(tmp_path / "bronze"),
            "--register",
            str(tmp_path / "absent.csv"),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "4 artifact(s)" in out
    assert "bootstrap-static-gw03" in out
    assert "entry-12345-picks-gw03" in out
    assert "want" in out


def test_dry_run_reports_what_is_already_present(tmp_path: Path, capsys) -> None:
    land(tmp_path, gameweek=1)
    main(
        [
            "--gameweek",
            "1",
            "--dry-run",
            "--root",
            str(tmp_path / "bronze"),
            "--register",
            str(register_at(tmp_path)),
        ]
    )
    assert "have  bootstrap-static-gw01" in capsys.readouterr().out
