"""The one way bytes get into ``data/bronze/``.

Everything else in the pipeline is a client of this module. It owns four
guarantees, and each exists because the alternative fails quietly:

1. **Nothing lands without a permitting verdict.** Only ``INGEST`` allows bytes
   to be stored. The distinction is legally load-bearing, so it is enforced here
   rather than trusted to whoever writes the next fetcher.
2. **Writes are atomic.** An interrupted run must never leave a half-file that
   later hashes as though it were whole.
3. **Every artifact is provenanced.** A file with no manifest entry is
   unprovenanced data, which under our own rules is not data at all; a manifest
   entry with no file is a lie.
4. **Bronze is never edited.** Writing different bytes over an existing artifact
   raises. A deliberate re-fetch is done by deleting the file, not by a
   ``--force`` flag — a flag would make "never edited" a preference.

See "Architecture (medallion)" in CLAUDE.md for why bronze is this project's
memory rather than its cache.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

__all__ = [
    "BRONZE_ROOT",
    "INGEST",
    "MANIFEST_NAME",
    "BronzeError",
    "BronzeImmutable",
    "ManifestEntry",
    "SourceRegistry",
    "VerdictRefused",
    "WriteResult",
    "is_present",
    "iso_timestamp",
    "manifest_path",
    "read_manifest",
    "sha256_of",
    "write_bronze",
]

BRONZE_ROOT = Path("data/bronze")
MANIFEST_NAME = "manifest.json"

#: The only verdict that permits storing a payload. See the ladder in CLAUDE.md.
INGEST = "INGEST"

#: Ids become path segments, so this pattern is a traversal guard as much as a
#: naming convention: no dots, no separators, nothing that can climb out of the
#: bronze root.
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class BronzeError(Exception):
    """Base for every refusal this module makes."""


class VerdictRefused(BronzeError):
    """The source may not be stored — unregistered, or not ``INGEST``."""

    def __init__(self, source_id: str, verdict: str | None) -> None:
        if verdict is None:
            detail = (
                f"source_id {source_id!r} has no row in the register. "
                "No source is fetched before it has one carrying a licence verdict "
                "and the ticket that approved it — see CLAUDE.md."
            )
        else:
            detail = (
                f"source_id {source_id!r} has verdict {verdict!r}; only {INGEST!r} "
                "permits storing bytes in bronze."
            )
        super().__init__(detail)
        self.source_id = source_id
        self.verdict = verdict


class BronzeImmutable(BronzeError):
    """Different bytes already live at this path, and bronze is never edited."""

    def __init__(self, path: Path, existing_sha: str, incoming_sha: str) -> None:
        super().__init__(
            f"{path} already holds different bytes "
            f"(has {existing_sha[:12]}…, offered {incoming_sha[:12]}…). "
            "Bronze is never edited: delete the file to re-fetch deliberately."
        )
        self.path = path
        self.existing_sha = existing_sha
        self.incoming_sha = incoming_sha


class SourceRegistry(Protocol):
    """Whatever can answer "may this source be stored?".

    Deliberately one method. The real implementation reads
    ``data/bronze/manual/sources.csv`` and validates it (FOO-26); tests pass a
    dict. This module does not care how the answer is obtained, only that
    someone authoritative gave it.
    """

    def verdict(self, source_id: str) -> str | None:
        """The verdict for ``source_id``, or ``None`` if it has no row."""
        ...


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    """One artifact's provenance.

    ``season`` and ``gameweek`` are this project's addition to the shape diadoche
    uses. Its bronze has no time axis; ours is worth nothing without one, because
    a gameweek's state is only ever knowable if we captured it while current.
    """

    id: str
    source_id: str
    title: str
    url: str
    license: str
    retrieved_at: str
    bytes: int
    sha256: str
    season: str | None = None
    gameweek: int | None = None

    def to_json(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "id": self.id,
            "source_id": self.source_id,
            "title": self.title,
            "url": self.url,
            "license": self.license,
            "retrieved_at": self.retrieved_at,
        }
        # Omitted rather than null when absent: a key that is sometimes missing
        # reads as "not applicable", where an explicit null reads as "we looked
        # and there wasn't one".
        if self.season is not None:
            record["season"] = self.season
        if self.gameweek is not None:
            record["gameweek"] = self.gameweek
        record["bytes"] = self.bytes
        record["sha256"] = self.sha256
        return record

    @classmethod
    def from_json(cls, record: dict[str, Any]) -> ManifestEntry:
        return cls(
            id=record["id"],
            source_id=record["source_id"],
            title=record.get("title", ""),
            url=record.get("url", ""),
            license=record.get("license", ""),
            retrieved_at=record.get("retrieved_at", ""),
            bytes=record.get("bytes", 0),
            sha256=record.get("sha256", ""),
            season=record.get("season"),
            gameweek=record.get("gameweek"),
        )


@dataclass(frozen=True, slots=True)
class WriteResult:
    """What :func:`write_bronze` did.

    ``written is False`` means the artifact was already present, byte-identical,
    and correctly manifested — so nothing changed on disk. A clean re-run of a
    fetcher should produce nothing but these.
    """

    entry: ManifestEntry
    path: Path
    written: bool


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def iso_timestamp(when: datetime | None = None) -> str:
    """UTC ISO-8601 with milliseconds and a ``Z``, matching the diadoche shape."""
    moment = (when or datetime.now(UTC)).astimezone(UTC)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _require_slug(value: str, label: str) -> str:
    if not _SLUG.match(value):
        raise ValueError(
            f"{label} {value!r} is not a slug. Lowercase letters, digits, hyphen "
            "and underscore only — these become path segments."
        )
    return value


def source_dir(source_id: str, root: Path = BRONZE_ROOT) -> Path:
    return root / _require_slug(source_id, "source_id")


def manifest_path(source_id: str, root: Path = BRONZE_ROOT) -> Path:
    return source_dir(source_id, root) / MANIFEST_NAME


def artifact_path(
    source_id: str, artifact_id: str, *, suffix: str = ".json", root: Path = BRONZE_ROOT
) -> Path:
    return source_dir(source_id, root) / f"{_require_slug(artifact_id, 'artifact_id')}{suffix}"


def read_manifest(source_id: str, root: Path = BRONZE_ROOT) -> list[ManifestEntry]:
    """Every entry in a source's manifest, or an empty list if there is none."""
    path = manifest_path(source_id, root)
    if not path.exists():
        return []
    records = json.loads(path.read_text(encoding="utf-8"))
    return [ManifestEntry.from_json(record) for record in records]


def _write_manifest(source_id: str, entries: list[ManifestEntry], root: Path) -> None:
    # Sorted by id so the file is deterministic: two runs that landed the same
    # artifacts produce byte-identical manifests, and a diff shows only what
    # actually changed.
    ordered = sorted(entries, key=lambda entry: entry.id)
    payload = json.dumps([entry.to_json() for entry in ordered], indent=2, ensure_ascii=False)
    _atomic_write(manifest_path(source_id, root), (payload + "\n").encode("utf-8"))


def _atomic_write(path: Path, data: bytes) -> None:
    """Write via a temp file in the same directory, then rename.

    Same directory matters: ``os.replace`` is only atomic within one filesystem,
    and a temp file in the system temp dir may well be on another.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def is_present(
    source_id: str, artifact_id: str, *, suffix: str = ".json", root: Path = BRONZE_ROOT
) -> bool:
    """Whether this artifact is already landed AND provenanced.

    ⚠ Both halves are required. A file with no manifest entry is unprovenanced,
    and an entry with no file is a lie — so either alone means "not present",
    and the fetcher should go and get it.

    Fetchers call this *before* reaching the network. That is what makes a clean
    re-run cost nothing rather than costing a polite round-trip per artifact.
    """
    path = artifact_path(source_id, artifact_id, suffix=suffix, root=root)
    if not path.exists():
        return False
    return any(entry.id == artifact_id for entry in read_manifest(source_id, root))


def write_bronze(
    *,
    source_id: str,
    artifact_id: str,
    data: bytes,
    url: str,
    title: str,
    license: str,
    registry: SourceRegistry,
    season: str | None = None,
    gameweek: int | None = None,
    suffix: str = ".json",
    retrieved_at: datetime | None = None,
    root: Path = BRONZE_ROOT,
) -> WriteResult:
    """Land ``data`` in bronze, with provenance, or refuse.

    Args:
        registry: authority on whether this source may be stored. Required and
            not defaulted — there is no "unchecked" way to call this.
        data: the bytes exactly as served. Not text, and not re-encoded: the
            sha256 is taken over these, and anything that touches them on the
            way in breaks every later integrity check.

    Returns:
        A :class:`WriteResult` whose ``written`` is ``False`` when the artifact
        was already there, identical and manifested.

    Raises:
        VerdictRefused: the source is unregistered or its verdict is not INGEST.
        BronzeImmutable: different bytes already occupy this path.
        ValueError: an id that is not a slug.
    """
    # Verdict first, before anything touches the disk. A refusal must not leave
    # a directory behind as evidence that we nearly stored something.
    verdict = registry.verdict(source_id)
    if verdict != INGEST:
        raise VerdictRefused(source_id, verdict)

    path = artifact_path(source_id, artifact_id, suffix=suffix, root=root)
    incoming_sha = sha256_of(data)

    if path.exists():
        existing_sha = sha256_of(path.read_bytes())
        if existing_sha != incoming_sha:
            raise BronzeImmutable(path, existing_sha, incoming_sha)

    entry = ManifestEntry(
        id=artifact_id,
        source_id=source_id,
        title=title,
        url=url,
        license=license,
        retrieved_at=iso_timestamp(retrieved_at),
        bytes=len(data),
        sha256=incoming_sha,
        season=season,
        gameweek=gameweek,
    )

    entries = read_manifest(source_id, root)
    existing_entry = next((item for item in entries if item.id == artifact_id), None)

    if path.exists() and existing_entry is not None:
        # Identical bytes, already provenanced. Do nothing at all — rewriting
        # the entry would churn retrieved_at and make a clean re-run show up as
        # a diff, which is exactly what idempotency is supposed to prevent.
        return WriteResult(entry=existing_entry, path=path, written=False)

    if not path.exists():
        _atomic_write(path, data)

    # Reached when the file was missing, or when it existed with no entry —
    # a real state, and one worth healing rather than ignoring.
    entries = [item for item in entries if item.id != artifact_id]
    entries.append(entry)
    _write_manifest(source_id, entries, root)
    return WriteResult(entry=entry, path=path, written=True)
