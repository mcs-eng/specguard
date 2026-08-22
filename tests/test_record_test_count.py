"""The README test count is read from a pytest receipt, not typed by hand.

Nothing here runs the suite. These tests drive the parsing and the rendering
that turn one pytest summary line into the published sentence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.record_test_count import (
    README_COUNT_END,
    README_COUNT_START,
    parse_passed_count,
    render_test_count,
    write_readme_count,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_the_count_comes_from_the_pytest_summary_line() -> None:
    assert parse_passed_count("400 passed, 2 warnings in 32.33s") == 400


def test_a_failing_run_publishes_no_count() -> None:
    """A count taken from a failing run describes nothing, so it is refused."""
    assert parse_passed_count("396 passed, 4 failed, 2 warnings in 31.00s") is None
    assert parse_passed_count("2 errors in 0.10s") is None


def test_output_with_no_summary_line_publishes_no_count() -> None:
    assert parse_passed_count("collected 0 items") is None
    assert parse_passed_count("") is None


def test_the_last_summary_line_wins() -> None:
    """pytest prints one summary at the end; earlier text may quote another."""
    assert parse_passed_count("was 374 passed before\n400 passed in 32.33s") == 400


def test_the_rendered_line_names_the_count_the_command_and_the_revision() -> None:
    block = render_test_count(400, "6f27d92", "uv run pytest -q")

    assert block.startswith(README_COUNT_START)
    assert block.endswith(README_COUNT_END)
    assert "**400 passed**" in block
    assert "`6f27d92`" in block
    assert "`uv run pytest -q` exited 0" in block


def test_a_dirty_revision_is_published_as_dirty() -> None:
    """The revision string is whatever the harness reports, uncommitted included."""
    block = render_test_count(400, "6f27d92 plus uncommitted changes to a.py", "uv run pytest -q")

    assert "plus uncommitted changes to a.py" in block


def test_the_block_replaces_only_what_sits_between_the_markers() -> None:
    readme = f"before\n{README_COUNT_START}\nold text\n{README_COUNT_END}\nafter"

    rewritten = write_readme_count(readme, render_test_count(7, "abc1234", "uv run pytest -q"))

    assert rewritten.startswith("before\n")
    assert rewritten.endswith("\nafter")
    assert "old text" not in rewritten
    assert "**7 passed**" in rewritten


def test_a_readme_without_markers_is_refused_rather_than_appended_to() -> None:
    with pytest.raises(ValueError):
        write_readme_count("no markers here", "block")


def test_the_committed_readme_carries_the_markers_and_a_generated_count() -> None:
    """The published README holds a generated block, not a hand-typed number."""
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")

    assert README_COUNT_START in readme
    assert README_COUNT_END in readme
    published = readme.split(README_COUNT_START)[1].split(README_COUNT_END)[0]
    assert "passed** on code revision" in published
    assert "scripts/record_test_count.py" in published
