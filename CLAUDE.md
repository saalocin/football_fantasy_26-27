# Fantasy Football (Premier League FPL) Optimizer

## Vision

Build a tool that helps make better decisions in Fantasy Premier League (FPL):
who to pick, who to captain, when to transfer, and when to play a chip
(Wildcard, Free Hit, Bench Boost, Triple Captain). The end goal is a
data-driven optimizer that turns player statistics and projections into
concrete, explainable squad and transfer recommendations — not just raw
numbers, but a "here's what to do and why."

## Problem shape

FPL decision-making is a constrained optimization problem played out over a
38-gameweek season, under the rules below. Uncertainty is the hard part: player
form, fixture difficulty, injuries, rotation risk, and price changes all need to
feed into projections.

## Game rules (2026/27 season)

Verified against https://fantasy.premierleague.com/en/help/rules on 2026-08-20.
These are the constraints the optimizer must respect — re-check this section at
the start of each season, since FPL changes rules year to year.

### Squad and team

- 15 players: **2 GK, 5 DEF, 5 MID, 3 FWD**.
- Initial squad value must not exceed **£100.0m**.
- Max **3 players from any one Premier League club**.
- Starting XI picked from the 15. Valid formation: exactly **1 GK, at least 3
  DEF, at least 1 FWD** (so MID ranges 2–5, FWD 1–3).
- Captain scores double. If the captain plays 0 minutes, the armband passes to
  the vice-captain; if both play 0 minutes, nothing is doubled.
- **Autosubs** run at the end of the gameweek, in bench order: a non-playing GK
  is replaced only by the backup GK; non-playing outfield players are replaced
  by the highest-priority bench player who played and does not break the
  formation rules. "Played" means appearing on the pitch or receiving a card.

### Scoring

| Action | Points |
| --- | --- |
| Playing up to 60 minutes | 1 |
| Playing 60+ minutes (excluding stoppage time) | 2 |
| Goal — GK | 10 |
| Goal — DEF | 6 |
| Goal — MID | 5 |
| Goal — FWD | 4 |
| Assist | 3 |
| Clean sheet — GK or DEF | 4 |
| Clean sheet — MID | 1 |
| Every 3 shot saves (GK) | 1 |
| Penalty save | 5 |
| **Defensive contribution** — DEF with 10+ CBIT | 2 |
| **Defensive contribution** — MID/FWD with 12+ CBIRT | 2 |
| Bonus (top 3 in match) | 1–3 |
| Every 2 goals conceded — GK or DEF | -1 |
| Penalty miss | -2 |
| Yellow card | -1 |
| Red card | -3 |
| Own goal | -2 |

- **Defensive contribution (DEFCON)** is a major modelling target: defenders
  need 10+ combined clearances, blocks, interceptions and tackles; midfielders
  and forwards need 12+ of those plus recoveries. It does **not** stack — 20
  actions still scores 2, not 4.
- **Clean sheet** requires 60+ minutes played (excluding stoppage time) without
  conceding while on the pitch. Goals conceded after a player is substituted off
  do not cost them the clean sheet.
- **Red cards**: the player keeps being penalised for goals their team concedes,
  and the red card deduction includes any yellow already given.
- **Bonus** goes to the top 3 BPS scorers in each match (3/2/1), with documented
  tie-break rules. The BPS formula was reworked for 2026/27 to reduce overlap
  with DEFCON and improve GK, full-back, and attacker prospects — see the rules
  page for the full BPS table rather than relying on older community write-ups.

### Transfers and prices

- Unlimited free transfers before the first deadline.
- **1 free transfer per gameweek**, unused ones roll over, **capped at 5 saved**.
- Each extra transfer costs **-4 points**.
- Hard limit of **20 transfers in a single gameweek** outside of Wildcard/Free Hit.
- **Selling price**: you keep half of any price rise, rounded down to the nearest
  £0.1m. Bought at £7.5m, now £7.8m → sells for £7.6m. Purchase prices must be
  tracked per player; current price is not selling price.

### Chips

Two of each chip per season — **8 chips total**. Only one chip per gameweek.

| Chip | Effect |
| --- | --- |
| Wildcard | All transfers that gameweek are free |
| Free Hit | Unlimited transfers for one gameweek, squad reverts afterwards |
| Bench Boost | Bench points count toward the total |
| Triple Captain | Captain scores 3x instead of 2x |

- First set expires at the **Gameweek 19 deadline (Sat 2 Jan 15:30)**; the second
  set becomes available after that and runs to the end of the season.
- **Free Hit cannot be played in consecutive gameweeks** (play it in GW19 and the
  next is GW21 at the earliest).
- Saved free transfers are **retained** through a Wildcard or Free Hit.
- Bench Boost and Triple Captain are cancellable before the deadline; Wildcard
  and Free Hit are **not** cancellable once confirmed.

### Deadlines and finalisation

- Deadlines are **90 minutes before the first kickoff** of the gameweek, and will
  not change within 24 hours of the scheduled time.
- **Points are provisional until 09:00 UK time the day after the gameweek's last
  match** — Opta revisions can change goals, assists, DEFCON, and bonus until
  then. The data pipeline must not treat pre-lockdown scores as final.

### 2026/27 changes worth knowing

- BPS reworked (see above).
- Later lockdown at 09:00 UK the following morning (was one hour after the final
  whistle).
- Live in-play points and mini-league updates; projected bonus appears after 20
  minutes of each match.
- Official price-change prediction tool now exists in-game, published daily at
  00:00 UK time.
- No extra December transfers this season (unlike last season's AFCON provision).

## Planned approach

1. **Data ingestion** — pull player data, fixtures, and historical
   gameweek results from the official FPL API (and optionally supplementary
   sources for underlying stats like xG/xA).
2. **Projections** — estimate expected points per player per upcoming
   gameweek(s), accounting for fixture difficulty, form, and minutes risk.
3. **Optimization** — use integer linear programming (ILP) to select the
   optimal 15-man squad / starting XI / captain given the budget and squad
   constraints, and to recommend transfers across a planning horizon.
4. **Chip strategy** — reason about *when* to deploy chips based on fixture
   swings (double gameweeks, blank gameweeks) rather than just single-week
   optimization.
5. **Decision support output** — present recommendations in a form a human
   can sanity-check and act on manually in the official FPL app (this tool
   advises; it does not need to submit transfers automatically).

## Architecture (medallion)

Same shape as `diadoche` (a sibling project of Nicolaas's, not vendored here) —
three data layers under `data/`, each
built from the one above it, never edited by hand downstream of bronze. That
project is TypeScript and this one is Python, so the *layout, the manifest
contract and the hard rules* carry across; none of the code does.

⚠ **Before changing the pipeline, read [`docs/architecture.md`](docs/architecture.md)** —
the stage map, the command surface, which commands may reach the network, the
standing rules, and a table saying where a given kind of change belongs.

- `data/bronze/<source_id>/` — **raw source data, committed exactly as fetched,
  never edited.** One directory per source, each carrying a `manifest.json` with
  `sha256`, `license`, `url` and `retrieved_at` for every artifact. Hand-curated
  CSVs in `data/bronze/manual/` are **a first-class bronze source**
  (`source_id = manual`), not a patch layer.
- `data/silver/` — normalized tables, rebuilt from bronze.
  ⚠ **NOT STARTED. Do not build it yet.**
- `data/gold/` — derived products: projections, optimizer inputs, the gameweek
  recommendation payload. ⚠ **NOT STARTED.**

⚠ **Bronze is this project's memory, not its cache.** The FPL API serves only
*current* state. Once a gameweek passes, its prices, ownership, form and injury
flags cannot be re-fetched at any price — not from the API, not from anywhere. A
gameweek we did not commit is a gameweek that is gone. This is why the rule below
about `data/` is absolute rather than a preference.

### The bronze manifest

Every bronze directory carries a `manifest.json` — a JSON array, one object per
artifact. `season` and `gameweek` are this project's additions to the diadoche
shape: diadoche's bronze has no time axis, and ours is worth nothing without one.

```json
{
  "id": "bootstrap-static",
  "source_id": "fpl-api",
  "title": "FPL bootstrap-static — players, teams, prices, current gameweek",
  "url": "https://fantasy.premierleague.com/api/bootstrap-static/",
  "license": "…the verdict, and the day it was confirmed at the host…",
  "retrieved_at": "2026-08-20T15:04:54.097Z",
  "season": "2026-27",
  "gameweek": 1,
  "bytes": 1636166,
  "sha256": "e874b27a51d1…"
}
```

### The source register

`data/bronze/manual/sources.csv` declares every source that may be fetched:

```
source_id,kind,title,url,license,verdict,approved_in,update_frequency,joins_on,summary,notes
```

`source_id` is a permanent lowercase slug — it goes into every manifest, so it is
frozen once used. `verdict` is one of:

| verdict | means |
|---|---|
| `INGEST` | the bytes may be stored in bronze |
| `FACTS-ONLY` | the figures may be used; the payload may not be stored |
| `LINK-ONLY` | cite it and link to it; take nothing |
| `REJECTED` | evaluated and refused — may not be used at all |

⚠ **A refused source still gets a row.** `REJECTED` exists so that "we looked at
this and said no" is recorded rather than absent. A source with no row at all
means nobody has evaluated it yet, and those two states must never look alike —
otherwise the same terms of use get researched twice a year apart.

`notes` records the licence confirmation **and the date it was read at the host**.

### How a source is approved

⚠ **The approval happens in Linear; the row is a transcription of it.**

**One source, one ticket, one verdict.** Linear has no approval feature, so the
verdict is carried by the **`Verdict` label group** on team `FOO` — `INGEST`,
`FACTS-ONLY`, `LINK-ONLY`, `REJECTED`. Label groups are mutually exclusive, so a
ticket can hold exactly one, which is the constraint a verdict needs. This is
also why a ticket may not cover two sources: it could not carry both answers.

1. **Chester decides.** He applies one `Verdict` label, comments the evidence —
   the licence text, the URL, the date he read it at the host — and moves the
   ticket to `Done`. Those three together are the approval; `Done` on its own is
   not, because it says the work finished without saying what was decided.
2. **Nicolaas transcribes.** The `sources.csv` row is written from that ticket,
   in a commit of its own, before any data uses the source. ⚠ **Never invent a
   verdict here.** If a row needs one no ticket states, that is a question for
   Chester, not a judgement call at the keyboard.
3. **`approved_in` names the ticket.** Every row carries the id of the ticket
   that approved it, so the repo can always be walked back to the decision, and
   `qa` can compare the row's `verdict` against that ticket's label. A row whose
   `approved_in` names no real ticket is a row nobody approved.

## Hard rules

- **Never gitignore anything under `data/`.** Bronze lives in the repo. Clone it
  and query immediately.
- **Bronze is never edited.** A wrong byte in bronze is re-fetched, not corrected.
  A correction is a curated row that overrides it, with a reason.
- ⚠ **No source is fetched before it has a row in `data/bronze/manual/sources.csv`
  carrying a licence verdict and the ticket that approved it.** The register is
  the gate. What goes in it is **Chester's call, decided in Linear** — not the
  fetcher author's, and not a judgement made while writing the fetcher.
- **Everything below the fetch layer is offline and deterministic**, so a rebuild
  can be diffed. Only a `fetch-*` command may reach the network.
- **Provenance on every artifact**: `source_id` + `retrieved_at`, and every
  `source_id` resolves in `sources.csv`.
- **A fetch is idempotent.** A clean re-run writes nothing and leaves
  `git status` clean.
- **One commit per task, and ⚠ the subject line BEGINS with the Linear ticket
  id** — `FOO-25 · bronze: manifest carries sha256 and the day`. That is what
  makes `git log --oneline | grep FOO-25` answer *"what shipped for this ticket"*
  without opening anything. Where a commit genuinely serves no ticket, say
  `(no ticket)` rather than leaving the slot empty.
- **Linear is the tracker** — see "Project tracking" below for the team, the
  project and the milestones. ⚠ **Nothing that is open lives in a markdown file
  at the repo root.** A todo in a file is a todo the rest of the team cannot see.

## Tech stack

- **Language**: Python 3.13
- **Dependencies**: `uv` + `pyproject.toml` (one tool for venv, deps, lockfile)
- **Data handling**: pandas / numpy
- **Optimization**: PuLP or OR-Tools for the ILP solver — not needed for bronze.
  ⚠ OR-Tools' CPython 3.13 Windows wheel is **confirmed working**
  (`ortools-9.15.6755-cp313-cp313-win_amd64.whl`, verified 2026-08-20 by solving
  the actual squad-selection problem). It pulls numpy, pandas and protobuf with
  it — about 23 MB — so on a bronze-only checkout that is real weight for
  nothing. Add it when M3 starts, not before.
- Further libraries (projection modeling, CLI/UI, etc.) to be decided as the
  project takes shape.

## Project tracking (Linear)

Planning lives in Linear, team **FOO** ("Football Fantasy Team optimizer"),
project **FPL Optimizer**:
https://linear.app/nicolaas/project/fpl-optimizer-41ad13d97816

Linear is the source of truth for ideas, data sources, architecture decisions,
features, bugs, vision, and the project plan. Issues are labelled `Feature`,
`Improvement`, or `Bug`, and grouped under milestones:

| Milestone | Scope |
| --- | --- |
| M1 — Data foundation | FPL API ingestion, local store, data source evaluation |
| M2 — Projections | Minutes model, expected points, fixture difficulty, backtesting |
| M3 — Optimizer core | ILP squad selection, transfer planning, captaincy |
| M4 — Chip strategy and season planning | Blank/double gameweek detection, chip timing |
| M5 — Decision support output | Gameweek report with explanations, own-squad analysis |

Check Linear before starting work, and file new ideas or bugs there rather than
in scratch notes.

## Status

Repo scaffolding not yet started; the Linear backlog is seeded. **Bronze layer
only** — silver and gold are deliberately not started, see the warnings above.
Live status is the Linear board, not this file.
