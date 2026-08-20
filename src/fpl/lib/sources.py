"""The source register: ``data/bronze/manual/sources.csv``.

Turns the hard rule in CLAUDE.md — *no source is fetched before it has a row
carrying a licence verdict and the ticket that approved it* — from a sentence
someone has to remember into something the code enforces.

The register is itself a first-class bronze source (``source_id = manual``): a
human wrote it, so it carries the same provenance obligation as anything
fetched. It is not configuration.

⚠ **This module validates the file; it does not decide what goes in it.** The
verdicts are Chester's, made in Linear, and each row's ``approved_in`` names the
ticket that carries the evidence. See "How a source is approved" in CLAUDE.md.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, fields
from pathlib import Path

__all__ = [
    "COLUMNS",
    "REGISTER_PATH",
    "VERDICTS",
    "RegisterError",
    "SourceRegister",
    "SourceRow",
    "UndeclaredColumn",
]

REGISTER_PATH = Path("data/bronze/manual/sources.csv")

#: The verdict ladder. ⚠ Hard-coded on purpose, unlike every other vocabulary
#: here: ``verdict`` is the gate that decides whether bytes may be stored, and a
#: gate whose permitted values can be widened by editing the file it guards is
#: not a gate. Adding a rung is a change to FOO-19 and to this constant, in the
#: same commit. ``kind`` by contrast is descriptive and deliberately unvalidated
#: beyond being non-empty — it will grow, and it gates nothing.
VERDICTS = frozenset({"INGEST", "FACTS-ONLY", "LINK-ONLY", "REJECTED"})

#: The only verdict that permits storing a payload in bronze.
INGEST = "INGEST"

_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

#: Team-agnostic rather than ``FOO-\d+``: the team key is not this module's
#: business, and hard-coding it would break the day a second team files a row.
_TICKET = re.compile(r"^[A-Z][A-Z0-9]{1,5}-\d+$")


@dataclass(frozen=True)
class Column:
    """What a column must contain for a row to be usable."""

    required: bool = False
    slug: bool = False
    one_of: frozenset[str] | None = None
    ticket: bool = False


#: ⚠ The declaration the file is held to, in both directions. The header must
#: match this exactly — see :meth:`SourceRegister.load` for why a renamed column
#: is the worst of the available failures.
COLUMNS: dict[str, Column] = {
    "source_id": Column(required=True, slug=True),
    "kind": Column(required=True),
    "title": Column(required=True),
    "url": Column(required=True),
    "license": Column(required=True),
    "verdict": Column(required=True, one_of=VERDICTS),
    "approved_in": Column(required=True, ticket=True),
    "update_frequency": Column(),
    "joins_on": Column(),
    "summary": Column(),
    "notes": Column(),
}


class RegisterError(Exception):
    """The register is unusable. Carries every problem found, not just the first.

    Reporting one error at a time turns fixing a curated file into a
    guess-and-rerun loop. Everything wrong is listed at once so it can be fixed
    in one pass.
    """

    def __init__(self, path: Path, problems: list[str]) -> None:
        listing = "\n".join(f"  - {problem}" for problem in problems)
        super().__init__(f"{path} is not a usable source register:\n{listing}")
        self.path = path
        self.problems = problems


class UndeclaredColumn(KeyError):
    """Code asked a row for a column the declaration does not carry."""


@dataclass(frozen=True)
class SourceRow:
    """One approved (or refused) source.

    A ``REJECTED`` row is a perfectly valid register entry. It records that we
    looked and said no, which is the whole reason the rung exists — a source
    with no row at all means nobody has evaluated it yet, and those two states
    must never look alike.
    """

    source_id: str
    kind: str
    title: str
    url: str
    license: str
    verdict: str
    approved_in: str
    update_frequency: str = ""
    joins_on: str = ""
    summary: str = ""
    notes: str = ""

    @property
    def may_ingest(self) -> bool:
        return self.verdict == INGEST


class _DeclaredRow(Mapping[str, str]):
    """A row that raises when read for a column the declaration lacks.

    This is the mirror of asserting the header: that guards the *file* against
    the declaration, this guards the *code* against it. Without it, a typo in
    ``row["licence"]`` would read as empty and quietly produce a manifest with
    no licence in it.
    """

    __slots__ = ("_row",)

    def __init__(self, row: dict[str, str]) -> None:
        self._row = row

    def __getitem__(self, key: str) -> str:
        if key not in COLUMNS:
            raise UndeclaredColumn(
                f"{key!r} is not a column of the source register. Declared: {', '.join(COLUMNS)}."
            )
        return self._row.get(key, "")

    def __iter__(self) -> Iterator[str]:
        return iter(COLUMNS)

    def __len__(self) -> int:
        return len(COLUMNS)


class SourceRegister:
    """Every source the project is allowed to touch.

    Satisfies the ``SourceRegistry`` protocol that :func:`fpl.lib.bronze.write_bronze`
    requires, which is the whole point: the rule lives in one place and the
    write path cannot be called without it.
    """

    def __init__(self, rows: list[SourceRow]) -> None:
        self._rows = {row.source_id: row for row in rows}

    # -- the protocol bronze depends on ------------------------------------

    def verdict(self, source_id: str) -> str | None:
        """The verdict for ``source_id``, or ``None`` if it has no row."""
        row = self._rows.get(source_id)
        return row.verdict if row else None

    # -- everything else ---------------------------------------------------

    def row(self, source_id: str) -> SourceRow | None:
        return self._rows.get(source_id)

    def may_ingest(self, source_id: str) -> bool:
        return self.verdict(source_id) == INGEST

    def __len__(self) -> int:
        return len(self._rows)

    def __iter__(self) -> Iterator[SourceRow]:
        return iter(self._rows.values())

    def __contains__(self, source_id: object) -> bool:
        return source_id in self._rows

    @classmethod
    def load(cls, path: Path = REGISTER_PATH) -> SourceRegister:
        """Read and validate the register, or raise listing every problem.

        ⚠ **A missing file raises rather than yielding an empty register.**
        An empty register refuses everything, which is safe — but "the register
        is empty" and "you are running from the wrong directory" would then look
        identical, and the second is far more likely. Fail loudly and name the
        path.

        Raises:
            RegisterError: the file is missing, the header does not match the
                declaration, or any row is invalid.
        """
        if not path.exists():
            raise RegisterError(
                path,
                [
                    "the file does not exist. It is created by FOO-20, "
                    "transcribing Chester's verdicts from Linear."
                ],
            )

        # utf-8-sig: this file is hand-edited and may pass through Excel, which
        # writes a BOM. Without this the first column reads as '﻿source_id'
        # and the header assertion fails for a reason nobody would guess.
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            header = reader.fieldnames or []
            problems = _header_problems(header)
            if problems:
                raise RegisterError(path, problems)
            records = list(reader)

        rows: list[SourceRow] = []
        seen: dict[str, int] = {}
        for number, record in enumerate(records, start=2):  # line 1 is the header
            declared = _DeclaredRow({key: (value or "").strip() for key, value in record.items()})
            problems.extend(_row_problems(declared, number))
            source_id = declared["source_id"]
            if source_id in seen:
                first = seen[source_id]
                problems.append(
                    f"line {number}: source_id {source_id!r} already used on line {first}. "
                    "Slugs are permanent and unique — they go into every manifest."
                )
            elif source_id:
                seen[source_id] = number
            rows.append(SourceRow(**{name: declared[name] for name in _FIELD_NAMES}))

        if problems:
            raise RegisterError(path, problems)
        return cls(rows)


_FIELD_NAMES = tuple(field.name for field in fields(SourceRow))


def _header_problems(header: list[str]) -> list[str]:
    """⚠ THE DEFECT THIS EXISTS TO CLOSE: A RENAMED COLUMN READS AS EMPTY.

    Rename ``license`` to ``licence`` and every row still parses; the value
    simply arrives blank, and a blank licence looks like a row somebody had not
    filled in yet rather than a file the code can no longer read. Holding the
    header to the declaration turns that into an error at the only moment it is
    cheap to notice.
    """
    found, declared = set(header), set(COLUMNS)
    problems = []
    if missing := sorted(declared - found):
        problems.append(f"header is missing column(s): {', '.join(missing)}")
    if extra := sorted(found - declared):
        problems.append(
            f"header has undeclared column(s): {', '.join(extra)}. "
            "Add them to COLUMNS in this module, in the same commit."
        )
    return problems


def _row_problems(row: Mapping[str, str], number: int) -> list[str]:
    problems = []
    for name, rule in COLUMNS.items():
        value = row[name]
        if not value:
            if rule.required:
                problems.append(f"line {number}: {name} is required and empty")
            continue
        if rule.slug and not _SLUG.match(value):
            problems.append(
                f"line {number}: {name} {value!r} is not a slug "
                "(lowercase, digits, hyphen, underscore)"
            )
        if rule.one_of is not None and value not in rule.one_of:
            problems.append(
                f"line {number}: {name} {value!r} is not one of {', '.join(sorted(rule.one_of))}"
            )
        if rule.ticket and not _TICKET.match(value):
            problems.append(
                f"line {number}: {name} {value!r} does not look like a Linear "
                "ticket id (e.g. FOO-21). The approval lives in Linear; this "
                "column is the only way back to it."
            )
    return problems
