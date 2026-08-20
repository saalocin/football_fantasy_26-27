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

## Tech stack

- **Language**: Python
- **Data handling**: pandas / numpy
- **Optimization**: PuLP or OR-Tools for the ILP solver
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

Repo scaffolding not yet started; the Linear backlog is seeded. This file will
be kept up to date as architecture, data sources, and modeling decisions are
made.
