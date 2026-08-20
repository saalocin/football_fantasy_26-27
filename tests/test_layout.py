"""Guards on the repo layout itself.

These do not test behaviour — there is no behaviour yet. They test the two
structural rules that are easy to break by accident and expensive to notice
late, both of which are stated in CLAUDE.md.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

needs_git = pytest.mark.skipif(
    shutil.which("git") is None or not (REPO / ".git").exists(),
    reason="needs a git checkout",
)


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=False)


def test_package_imports() -> None:
    import fpl

    assert fpl.__version__


@pytest.mark.parametrize(
    "path",
    ["data/bronze", "data/bronze/manual", "data/silver", "data/gold", "docs", "report"],
)
def test_medallion_directory_exists(path: str) -> None:
    assert (REPO / path).is_dir(), f"{path} is missing — see CLAUDE.md"


def test_silver_and_gold_are_marked_not_started() -> None:
    """The empty rooms have names so nobody builds in one by accident."""
    for layer in ("silver", "gold"):
        readme = (REPO / "data" / layer / "README.md").read_text(encoding="utf-8")
        assert "NOT STARTED" in readme


@needs_git
@pytest.mark.parametrize(
    "path",
    [
        "data/bronze/manual/sources.csv",
        "data/bronze/fpl-api/bootstrap-static.json",
        "data/bronze/fpl-api/manifest.json",
        "data/silver/anything.parquet",
        "data/gold/anything.json",
    ],
)
def test_nothing_under_data_is_gitignored(path: str) -> None:
    """⚠ The hardest rule in the project.

    The FPL API serves only current state, so a gameweek we did not commit is
    gone for good. An ignore rule that swallows part of data/ would not fail
    anything — it would just quietly stop recording the season.

    `git check-ignore` exits 1 when a path is NOT ignored, which is what we want.
    """
    result = _git("check-ignore", "-v", path)
    assert result.returncode == 1, (
        f"{path} is gitignored by: {result.stdout.strip()}\n"
        "Nothing under data/ may ever be ignored — see .gitignore and CLAUDE.md."
    )


@needs_git
@pytest.mark.parametrize(
    "path",
    ["data/bronze/fpl-api/bootstrap-static.json", "data/bronze/fpl-api/fixtures.json"],
)
def test_fetched_bronze_is_never_line_ending_normalised(path: str) -> None:
    """Bronze bytes must survive a checkout unchanged.

    Every artifact carries a sha256 taken over the bytes as served. If git
    normalises line endings, those bytes change and every hash in every manifest
    goes wrong at once — silently, and only on the machine that checked out.
    """
    result = _git("check-attr", "text", "--", path)
    assert "text: unset" in result.stdout, (
        f"{path} is not marked -text (got: {result.stdout.strip()}).\n"
        "Fetched bronze must be byte-exact — see .gitattributes."
    )


@needs_git
def test_curated_csvs_are_still_normalised() -> None:
    """The one bronze exception: hand-authored CSVs are diffed by humans."""
    result = _git("check-attr", "text", "--", "data/bronze/manual/sources.csv")
    assert "text: set" in result.stdout, result.stdout.strip()
