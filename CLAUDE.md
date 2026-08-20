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
38-gameweek season:

- **Squad selection**: 15 players (2 GK, 5 DEF, 5 MID, 3 FWD) under a £100m
  budget, max 3 players per real-life club, only 11 start each gameweek.
- **Scoring**: points come from goals, assists, clean sheets, bonus points,
  minutes played, cards, etc. — position-dependent scoring rules.
- **Transfers**: 1 free transfer per gameweek (rolls over up to a cap), extra
  transfers cost -4 points each.
- **Chips**: Wildcard (free transfers for a gameweek), Free Hit (temporary
  squad for one gameweek), Bench Boost (bench points count), Triple Captain
  (captain scores 3x instead of 2x) — each usable a limited number of times
  per season, timing matters a lot.
- **Uncertainty**: player form, fixture difficulty, injuries, rotation risk,
  and price changes all need to feed into projections.

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

## Status

Project scaffolding not yet started. This file will be kept up to date as
architecture, data sources, and modeling decisions are made.
