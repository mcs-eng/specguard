"""Write the test count into README from a pytest receipt, never by hand.

README stated a test count as prose. A count typed by hand drifts from the
suite the moment a test is added, and a reader has no way to tell whether the
number describes the tree in front of them. This script runs the suite, reads
the count out of pytest's own summary line, and writes it into README together
with the code revision it describes and the exit code it came from.

Run it from the repository root:

    uv run python scripts/record_test_count.py

It exits 0 when the suite passed and README was rewritten, 1 when the suite did
not pass, and 2 when the summary line could not be read.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
README_PATH = REPOSITORY_ROOT / "README.md"
README_COUNT_START = "<!-- test-count-start -->"
README_COUNT_END = "<!-- test-count-end -->"

#: pytest's own summary line. Only a run that reports passes and no failures is
#: accepted, because a count taken from a failing run describes nothing.
PASSED_PATTERN = re.compile(r"\b(\d+) passed\b")
FAILED_PATTERN = re.compile(r"\b\d+ (?:failed|error|errors)\b")


def parse_passed_count(output: str) -> int | None:
    """Return the number of passing tests pytest reported, or ``None``.

    ``None`` means the count could not be read: no summary line, or a summary
    line that also reports a failure or an error. Either way there is no number
    worth publishing.
    """
    if FAILED_PATTERN.search(output):
        return None
    matches = PASSED_PATTERN.findall(output)
    if not matches:
        return None
    return int(matches[-1])


def render_test_count(count: int, revision: str, command: str) -> str:
    """Render the README block, naming the count, the command, and the revision."""
    return "\n".join(
        [
            README_COUNT_START,
            "",
            f"`{command}` exited 0 with **{count} passed** on code revision "
            f"`{revision}`. This line is written by "
            "`scripts/record_test_count.py` from that run's own summary line; "
            "it is not typed by hand. No test requires the network or "
            "credentials; every model, Firestore, and storage dependency is an "
            "in-process fake.",
            "",
            README_COUNT_END,
        ]
    )


def write_readme_count(readme_text: str, block: str) -> str:
    """Replace the marked README block, or refuse when no block exists."""
    start = readme_text.find(README_COUNT_START)
    end = readme_text.find(README_COUNT_END)
    if start == -1 or end == -1:
        raise ValueError("README.md must contain the test-count markers")
    return readme_text[:start] + block + readme_text[end + len(README_COUNT_END) :]


def main(argv: Sequence[str] | None = None) -> int:
    """Run the suite, read its count, and rewrite the README block."""
    from scripts.eval_fixtures import current_code_revision

    command = "uv run pytest -q"
    completed = subprocess.run(
        ["uv", "run", "pytest", "-q"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    output = completed.stdout + completed.stderr
    if completed.returncode != 0:
        print(output)
        print(f"{command} exited {completed.returncode}; README was not changed.")
        return 1

    count = parse_passed_count(output)
    if count is None:
        print(output)
        print("No pytest summary line could be read; README was not changed.")
        return 2

    revision = current_code_revision()
    readme = README_PATH.read_text(encoding="utf-8")
    README_PATH.write_text(
        write_readme_count(readme, render_test_count(count, revision, command)),
        encoding="utf-8",
    )
    print(f"Recorded {count} passed on {revision}.")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(REPOSITORY_ROOT))
    raise SystemExit(main())
