# The architecture — what runs, in what order, and where a change goes

**Read this before changing the pipeline.** It is the map: the stages, the
commands that drive them, which of them reach the network, and — the part that
saves the most time — §5, a table saying where a given kind of change belongs.

[`CLAUDE.md`](../CLAUDE.md) is read every session and has to stay short enough
that it actually gets read. It owns the layer definitions, the manifest shape,
the source register and the hard rules. This file is the longer form, read when
you are about to change something.

| about to change | read |
| --- | --- |
| whether a source may be used at all | [`CLAUDE.md`](../CLAUDE.md) "How a source is approved" — then a Linear ticket for Chester |
| an FPL scoring, chip or deadline rule | [`CLAUDE.md`](../CLAUDE.md) "Game rules (2026/27 season)" |
| how a source becomes legal to fetch | `source-onboarding.md` — not written yet (FOO-20) |

---

## §1 · The stages

```
   the open internet                        hand-authored
   fantasy.premierleague.com/api/           data/bronze/manual/*.csv
   …and whatever else Chester approves      (the source register)
            │                                        │
            │   ⚠ only a fetch-* command             │   no network,
            │     may cross this line                │   no fetch
            ▼                                        ▼
   ┌──────────────────────────────────────────────────────────────┐
   │  BRONZE    data/bronze/<source_id>/                          │
   │            the payload, byte-for-byte as served              │  never edited
   │            + manifest.json carrying, per artifact:           │  + curated CSVs,
   │              sha256 · licence · url · retrieved_at           │    which ARE a
   │              · season · gameweek · bytes                     │    first-class source
   └──────────────────────────────────────────────────────────────┘
            │
            │   ⚠ NOTHING CROSSES THIS LINE YET.
            │     Bronze is the whole project today.
            ▼
   ┌──────────────────────────────────────────────────────────────┐
   │  SILVER    data/silver/                    NOT STARTED       │  what is true,
   │            normalized tables, rebuilt from bronze            │  and who says so
   └──────────────────────────────────────────────────────────────┘
            │
            ▼
   ┌──────────────────────────────────────────────────────────────┐
   │  GOLD      data/gold/                      NOT STARTED       │  what a reader
   │            projections, optimizer inputs, the GW report      │  sees
   └──────────────────────────────────────────────────────────────┘
```

Silver and gold are named here, and given a committed directory with a README,
precisely so that nobody builds in one by accident. **Naming the empty rooms is
the point.** The storage question silver has to answer first (Parquet vs SQLite
vs DuckDB) is parked deliberately — deciding it now would bind a layer nobody
has designed.

---

## §2 · The command surface, and what reaches the network

⚠ **Only a `fetch-*` command may open a socket.** Everything downstream of the
fetch is offline and deterministic, which is what makes a rebuild diffable: if a
build step could reach the internet, "nothing changed" would stop being a
statement anyone could check.

Today the pipeline commands do not exist yet. This is the honest surface:

| command | network | writes | status |
| --- | --- | --- | --- |
| `uv sync` | yes (PyPI) | `.venv/`, `uv.lock` | shipped |
| `uv run pytest` | **no** | nothing | shipped |
| `uv run ruff check .` | **no** | nothing | shipped |
| `uv run ruff format .` | **no** | source files | shipped |
| `fpl-fetch` | **yes** | `data/bronze/fpl-api/` | shipped, **gated** (see below) |
| `fpl-qa` | **no** | `report/qa.md` + exit code | shipped |
| `fpl-check-sources` | **yes** | nothing | not ticketed |

Two conventions inherited from diadoche and worth keeping when those land:

- **A source-reachability check stays off the build path.** A host that
  rate-limits must never turn a build red. That is why `check-sources` is listed
  separately from `qa` rather than folded into it.
- **`qa` exits non-zero on any error**, so it can gate a build, and writes its
  report to `report/`. Generated output lives in `report/`; hand-written
  reference lives in `docs/`. Keeping them apart is what lets you trust that a
  file under `docs/` was written by a person on purpose.

---

## §3 · What each layer owns

Stated so the boundary decides arguments rather than taste. When someone asks
"where does this belong?", these three sentences are the answer.

### Bronze owns what a source said

Bytes exactly as served, plus a manifest giving sha256, licence, url and the
day. **Nothing is normalised here** — not JSON reformatting, not line endings,
not type coercion, not even gzip. Every one of those sits between the bytes the
host sent and the hash we recorded over them.

The curated CSVs under `data/bronze/manual/` are bronze too. They are a *human*
source, `source_id = manual`, carrying the same provenance obligation as
anything fetched. They are not a patch layer and not configuration.

⚠ **Bronze is this project's memory, not its cache.** The FPL API serves only
current state; once a gameweek passes, its prices, ownership, form and injury
flags cannot be re-fetched at any price. This single fact is the reason for the
`data/` commit rule, the `-text` rule, and the whole snapshot design.

### Silver owns what is true, and who says so — NOT STARTED

One row per fact, `source_id` and `retrieved_at` on every one. Silver never
decides how a thing is *shown*, and never asserts anything a source did not.

Silver holds what was **observed**: player-gameweek results, fixtures and their
outcomes, prices and ownership as they stood, injury flags as published. Every
row traces to something the FPL API actually said on a given day.

### Gold owns what a reader sees — NOT STARTED

Shape, ordering, ranking, thresholds, presentation — and **everything we
compute**. ⚠ **A judgement a reader could disagree with belongs in gold, not
silver.**

Gold holds what was **derived**: the minutes model, expected points, our own
fixture-difficulty ratings, the DEFCON threshold probabilities, the chosen
squad, the captain, the gameweek report.

⚠ **A PROJECTION IS A JUDGEMENT, SO PROJECTIONS ARE GOLD.** This is worth
stating flatly because it is the boundary every M2–M5 ticket sits on, and an
earlier draft of this section got it backwards. "Salah scored 2 goals in GW1" is
silver — the API said so. "Salah is projected 6.4 next week" is gold — *we* say
so, and a reasonable person with the same silver could say 5.1.

The test that settles it: **could two competent people build this from identical
silver and disagree?** If yes, it is gold. Every model in M2 fails that test, so
every model in M2 writes gold.

### The concrete mapping

| thing | layer | why |
| --- | --- | --- |
| `bootstrap-static` payload as served | bronze | what the source said |
| the source register (`sources.csv`) | bronze | a human source, `source_id = manual` |
| player-gameweek results, normalised | silver | observed, traceable to a fetch |
| fixtures, prices, ownership as they stood | silver | observed |
| minutes model · xPts · our FDR · DEFCON probabilities | **gold** | derived, disputable |
| chosen squad · captain · transfer plan · chip call | **gold** | derived, disputable |
| the gameweek report | gold | shaped for one reader |

⚠ **Gold may read gold.** The optimizer consumes projections, which are
themselves gold. That is not a layer violation — it is one derived product
feeding another, the same way diadoche's search index is built from its graph.

---

## §4 · The module map

`src/fpl/lib/` is a shared kernel, not a framework. **There are no base classes
and no fetcher registry.** A source is a script that *calls* these modules, not
a subclass that inherits from them — which means adding a source means reading
two module APIs, not learning an abstraction.

| module | owns | ticket |
| --- | --- | --- |
| `lib/http.py` | the only socket in the project: `USER_AGENT`, `Throttle`, `fetch()` | FOO-24 ✅ |
| `lib/bronze.py` | the only way bytes land: `write_bronze()`, `is_present()`, `read_manifest()` | FOO-25 ✅ |
| `lib/sources.py` | the register: `SourceRegister.load()`, validation, the verdict lookup | FOO-26 ✅ |

**The registry seam.** `write_bronze` takes a `SourceRegistry` protocol — one
method, `verdict(source_id) -> str | None` — as a required argument with no
default. There is deliberately no unchecked way to store bytes.
`SourceRegister` satisfies that protocol; `bronze.py` neither knows nor cares
how the answer is obtained, and tests pass a dict-backed fake.

⚠ **The register file itself does not exist yet** — `SourceRegister.load()`
raises naming FOO-20, which creates it by transcribing Chester's verdicts. The
loader is ready; the data is not.

A fetcher is therefore: check `is_present()` → if absent, `fetch()` → then
`write_bronze()`. ⚠ **Check presence *before* reaching the network**, or a clean
re-run costs a polite round-trip per artifact instead of costing nothing.

`src/fpl/fetch_fpl.py` is the worked example — copy its shape for a new source.

⚠ **`fpl-fetch` is built but gated.** It exits 2 without touching the network
until the register carries an `INGEST` verdict for `fpl-api`, which is Chester's
call in FOO-21. `--dry-run` works regardless and needs no register, so the plan
is inspectable while the gate is shut. This is the rule working, not a bug:

```
$ uv run fpl-fetch --gameweek 1
fetch: refusing to run -- data/bronze/manual/sources.csv is not a usable source register:
  - the file does not exist. It is created by FOO-20, transcribing Chester's verdicts from Linear.
```

---

## §5 · Where does this change go

| you are about to change | it goes in |
| --- | --- |
| how a request is retried, throttled, or identified | `src/fpl/lib/http.py` — read FOO-24's comment first; several alternatives are already ruled out there |
| how bytes land, or the manifest's shape | `src/fpl/lib/bronze.py`, and update the example in `CLAUDE.md` in the same commit |
| **whether a source may be used at all** | **not code.** A Linear ticket for Chester — see "How a source is approved" in `CLAUDE.md` |
| the register's columns, or a row in it | `data/bronze/manual/sources.csv` (FOO-20). One row, one commit, before any data uses it |
| adding a fetcher for a new source | a new `src/fpl/fetch_<source>.py` calling http + bronze. No base class to inherit |
| an FPL scoring, chip or deadline rule | "Game rules" in `CLAUDE.md`, re-verified at the host at the start of each season |
| a rule everyone must follow | `CLAUDE.md` "Hard rules" if it is short and constant; §6 below if it needs its reasoning |
| a layer-boundary argument | §3 above. If §3 does not settle it, §3 is what needs changing |

---

## §6 · Standing rules

Numbered so they can be cited. When a change earns a new rule it goes here **in
the same commit**, and the reasoning, measurements and blast radius go in the
commit message where `git show` will always find them.

1. **Bronze is never edited.** A wrong byte is re-fetched, not corrected.
   Deletion is the re-fetch; there is deliberately no `--force`, because a flag
   turns this rule into a preference and the flag ends up in a script.
2. **Only a `fetch-*` command reaches the network.** Everything below it is
   offline and deterministic, so a rebuild can be diffed.
3. **No source is fetched or stored before it has a register row** carrying a
   verdict and the ticket that approved it. Only `INGEST` permits storing bytes;
   `FACTS-ONLY`, `LINK-ONLY` and `REJECTED` all raise. Enforced in
   `bronze.write_bronze`, not trusted to the caller.
4. **Nothing under `data/` is ever gitignored.** If a data path looks big enough
   to want ignoring, the answer is a different storage format, never an ignore
   rule.
5. **Fetched bronze is never line-ending normalised** (`data/bronze/** -text`).
   A sha256 taken over bytes as served does not survive normalisation, and the
   damage is silent and machine-local.
6. **Provenance is both halves.** A file with no manifest entry is unprovenanced
   data; a manifest entry with no file is a lie. Either alone means "not
   present", and both broken states are healed rather than ignored.
7. **A fetch is idempotent**, and idempotency does not churn `retrieved_at`.
   A clean re-run writes nothing and leaves `git status` clean; rewriting the
   timestamp would make a no-op show up as a diff.
8. **Ids are slugs because they are path segments.** `^[a-z0-9][a-z0-9_-]*$` is
   a traversal guard as much as a naming convention.
9. **Generated output lives in `report/`, hand-written reference in `docs/`.**
10. ⚠ **A new guard is run inverted before it is trusted.** Flip the predicate —
    the mutant must return the whole population, not a handful — then seed one
    deliberately bad row and confirm the guard goes red *naming it*. A check that
    passes because it is looking at the wrong thing passes just as green as one
    that works.
11. **Approval lives in Linear; the register row is a transcription of it.**
    Every row carries `approved_in`, so the repo can always be walked back to the
    decision that permitted the source.

---

## §7 · The guard layers

Two exist, and they have different blind spots. Neither substitutes for the
other, and a third is still to come.

| layer | reads | blind to |
| --- | --- | --- |
| `uv run pytest` | the **code** — pure functions in `lib/` | anything about the data actually on disk |
| `uv run ruff check .` | style and a set of common bugs | behaviour |
| `uv run fpl-qa` | the **data** in `data/bronze/` | the network; whether a value is *correct*, only whether it is consistent |

`fpl-qa` separates **error** from **warn**: only errors fail the build. An
unsorted manifest is a warning because the data is still described truthfully —
it just makes the next rewrite a noisy diff. A sha256 that does not match its
bytes is an error, because something is wrong with the data itself.

⚠ **An empty `data/bronze/` is green, not red.** Nothing fetched and no register
yet is a legitimate stage of the project. The missing register warns; it only
becomes an error once an artifact exists that depends on it.

⚠ **Two of the tests in `tests/test_layout.py` are structural guards, not unit
tests.** They assert that nothing under `data/` is gitignored and that fetched
bronze is never normalised. Both cover rules that fail *silently*. If either
starts failing, the fix is the `.gitignore` or `.gitattributes` rule — never the
test.
