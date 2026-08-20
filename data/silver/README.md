# Silver — NOT STARTED

⚠ **Do not build in this directory yet.**

Silver owns *what is true, and who says so*: normalized tables rebuilt
deterministically from `data/bronze/`, one row per fact, every row carrying
`source_id` and `retrieved_at`.

None of that exists. The project is deliberately on bronze only — see
"Architecture (medallion)" in [`CLAUDE.md`](../../CLAUDE.md). The storage
question this layer has to answer first (Parquet vs SQLite vs DuckDB) is parked
on purpose: deciding it now would bind a layer nobody has designed.

This directory is committed empty so the room has a name. Naming the empty rooms
is what stops someone building in one by accident.
