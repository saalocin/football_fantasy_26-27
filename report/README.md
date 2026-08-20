# report/ — generated output only

Everything in this directory is written by a command and committed. Nothing here
is hand-edited; an edit would be overwritten by the next run.

The split that matters: **generated output lives here, hand-written reference
lives in `docs/`.** Keeping them apart is what lets you trust that a file under
`docs/` was written by a person on purpose.

| file | written by | notes |
| --- | --- | --- |
| `qa.md` | `uv run fpl-qa` | The bronze integrity sweep. ⚠ Carries no timestamp on purpose — a generated-at line would make every run a diff, and diffs nobody reads are diffs that hide real change. |
