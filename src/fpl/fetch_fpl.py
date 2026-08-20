"""Fetch the official FPL API into bronze.

The primary source, and the only one the project cannot do without. This module
does exactly three things per artifact: ask whether we already have it, fetch the
bytes if we do not, and hand them to :func:`fpl.lib.bronze.write_bronze`.

⚠ **No parsing. No typed models. No normalising.** Bronze owns *what a source
said*; turning JSON into objects is silver's job and silver is deliberately not
started. The temptation to "just parse it while we are here" is precisely what
the layer boundary exists to refuse — and any re-encoding on the way in would
break the sha256 taken over the bytes as served.

⚠ **This will refuse to run until `fpl-api` has a row in the register carrying
an INGEST verdict.** That is not an oversight to work around; it is the hard rule
in CLAUDE.md, and the FPL API is not exempt from it just because it is obvious.
The row arrives when Chester's FOO-21 lands and FOO-20 transcribes it.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import httpx

from fpl.lib.bronze import BRONZE_ROOT, WriteResult, is_present, write_bronze
from fpl.lib.http import DEFAULT_MIN_INTERVAL, HttpError, Throttle, fetch
from fpl.lib.sources import REGISTER_PATH, RegisterError, SourceRegister, SourceRow

__all__ = ["BASE_URL", "SOURCE_ID", "Artifact", "core_artifacts", "fetch_all", "main"]

BASE_URL = "https://fantasy.premierleague.com/api"

#: Must match the slug Chester registers. Frozen once it reaches a manifest.
SOURCE_ID = "fpl-api"


@dataclass(frozen=True, slots=True)
class Artifact:
    """One thing to fetch, and what to call it once landed."""

    id: str
    path: str
    title: str

    @property
    def url(self) -> str:
        return f"{BASE_URL}/{self.path}"


def core_artifacts(season: str, gameweek: int) -> list[Artifact]:
    """The two endpoints worth capturing every gameweek.

    ⚠ **The gameweek is in the artifact id, not only in the manifest.** These
    endpoints serve *current* state, so gameweek 2's bytes differ from gameweek
    1's. Reusing one id would make the second capture collide with the first and
    raise `BronzeImmutable` — correctly, because overwriting would destroy the
    only copy of gameweek 1 that will ever exist.

    FOO-4 formalises the snapshot cadence; this is the shape it needs.
    """
    suffix = f"gw{gameweek:02d}"
    return [
        Artifact(
            id=f"bootstrap-static-{suffix}",
            path="bootstrap-static/",
            title=f"FPL bootstrap-static — players, teams, prices ({season} GW{gameweek})",
        ),
        Artifact(
            id=f"fixtures-{suffix}",
            path="fixtures/",
            title=f"FPL fixtures — difficulty and kickoff times ({season} GW{gameweek})",
        ),
    ]


def entry_artifacts(entry_id: int, season: str, gameweek: int) -> list[Artifact]:
    """Our own squad state. Same API surface, so the same rules apply."""
    suffix = f"gw{gameweek:02d}"
    return [
        Artifact(
            id=f"entry-{entry_id}-{suffix}",
            path=f"entry/{entry_id}/",
            title=f"FPL entry {entry_id} — summary ({season} GW{gameweek})",
        ),
        Artifact(
            id=f"entry-{entry_id}-picks-{suffix}",
            path=f"entry/{entry_id}/event/{gameweek}/picks/",
            title=f"FPL entry {entry_id} — picks ({season} GW{gameweek})",
        ),
    ]


def element_summary_artifacts(player_ids: Iterable[int], season: str) -> list[Artifact]:
    """Per-player history.

    ⚠ No gameweek suffix, and that is deliberate: this endpoint is cumulative
    history rather than a snapshot, so re-fetching mid-season legitimately
    returns *more* rows for the same player. Landing it under a stable id would
    collide on the second run. Keyed by season instead, so each season's final
    history is one artifact — re-fetch by deleting the file, per the rules.
    """
    return [
        Artifact(
            id=f"element-summary-{player_id}-{season.replace('/', '-')}",
            path=f"element-summary/{player_id}/",
            title=f"FPL element-summary {player_id} — gameweek history ({season})",
        )
        for player_id in player_ids
    ]


def fetch_all(
    artifacts: list[Artifact],
    *,
    row: SourceRow,
    registry: SourceRegister,
    season: str,
    gameweek: int | None,
    client: httpx.Client,
    throttle: Throttle | None = None,
    root: Path = BRONZE_ROOT,
    log: bool = True,
) -> list[WriteResult]:
    """Land each artifact, skipping whatever is already present.

    Returns one :class:`WriteResult` per artifact actually written. Artifacts
    already on disk produce nothing and cost nothing.
    """
    written: list[WriteResult] = []

    for artifact in artifacts:
        # ⚠ BEFORE the network, not after. write_bronze would also no-op on a
        # repeat, but only once the fetch has already happened — which is a
        # polite round-trip per artifact for no reason on every clean re-run.
        if is_present(SOURCE_ID, artifact.id, root=root):
            if log:
                print(f"  have  {artifact.id}")
            continue

        data = fetch(artifact.url, client=client, throttle=throttle)
        result = write_bronze(
            source_id=SOURCE_ID,
            artifact_id=artifact.id,
            data=data,
            url=artifact.url,
            title=artifact.title,
            # The licence text comes from the approved row, never from a literal
            # in this file. If it were hard-coded here it could drift from the
            # verdict that permitted the fetch in the first place.
            license=row.license,
            registry=registry,
            season=season,
            gameweek=gameweek,
            root=root,
        )
        written.append(result)
        if log:
            print(f"  wrote {artifact.id}  ({result.entry.bytes} bytes)")

    return written


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fpl-fetch", description="Fetch the official FPL API into data/bronze/fpl-api/."
    )
    parser.add_argument("--season", default="2026-27", help="season label for the manifest")
    parser.add_argument("--gameweek", type=int, required=True, help="gameweek being captured")
    parser.add_argument("--entry", type=int, help="also capture this FPL entry (team) id")
    parser.add_argument(
        "--players",
        type=int,
        nargs="*",
        help="also capture element-summary for these player ids",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list what would be fetched and touch no network",
    )
    parser.add_argument("--root", type=Path, default=BRONZE_ROOT)
    parser.add_argument("--register", type=Path, default=REGISTER_PATH)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)

    artifacts = core_artifacts(args.season, args.gameweek)
    if args.entry:
        artifacts += entry_artifacts(args.entry, args.season, args.gameweek)
    if args.players:
        artifacts += element_summary_artifacts(args.players, args.season)

    if args.dry_run:
        print(f"fetch: {len(artifacts)} artifact(s) for {args.season} GW{args.gameweek}")
        for artifact in artifacts:
            state = "have" if is_present(SOURCE_ID, artifact.id, root=args.root) else "want"
            print(f"  {state}  {artifact.id}  <- {artifact.url}")
        return 0

    # ⚠ The gate. Everything above this line is offline bookkeeping; nothing
    # below it runs until the register says this source may be stored.
    try:
        registry = SourceRegister.load(args.register)
    except RegisterError as exc:
        print(f"fetch: refusing to run -- {exc}", file=sys.stderr)
        return 2

    row = registry.row(SOURCE_ID)
    if row is None or not row.may_ingest:
        verdict = row.verdict if row else "no row"
        print(
            f"fetch: refusing to run -- {SOURCE_ID} has verdict '{verdict}'. "
            "Only INGEST permits storing bytes. The verdict is Chester's call, "
            "made in Linear; see FOO-21.",
            file=sys.stderr,
        )
        return 2

    print(f"fetch: {len(artifacts)} artifact(s) for {args.season} GW{args.gameweek}")
    throttle = Throttle(DEFAULT_MIN_INTERVAL)
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            written = fetch_all(
                artifacts,
                row=row,
                registry=registry,
                season=args.season,
                gameweek=args.gameweek,
                client=client,
                throttle=throttle,
                root=args.root,
            )
    except HttpError as exc:
        print(f"fetch: {exc}", file=sys.stderr)
        return 1

    print(f"fetch: {len(written)} written, {len(artifacts) - len(written)} already present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
