"""Phase 7b-redteam: measured concealment-mechanism coverage of the screen.

For each mechanism the test records three measured facts and pins them:

- ``extracted``: does ``pymupdf`` text extraction return the marker? If not,
  an automated reviewer never ingests the hidden text either, so the mechanism
  cannot poison an extraction-based review. That is a non-threat to this
  screen, not a blind spot.
- ``invisible``: do the marked page and a marker-free control page render to
  identical pixels? Only then is the marker truly unseen by a reader.
- ``detectors``: what does ``integrity.check_text_layer`` report for the marked
  page?

The expected values below were measured, then written here, so the suite pins
the measured state of PyMuPDF 1.28.2 and the screen. A later change in either
fails the matching case, which is the point: this file is the receipt behind
the README "Detected / Not detected" list.

Threat classes:

- ``DETECTED``: extracted and invisible, and a detector fires.
- ``MISS``: extracted and invisible, and the screen is clean. A real gap.
- ``NON_THREAT``: extraction returns nothing, so the marker never reaches the
  reviewer. The screen is correctly clean.
- ``VISIBLE``: the marker leaves a pixel trace, so it is not concealed.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from specguard import integrity
from tests import redteam_fixtures as rf

#: Rasterization density for the render-visibility comparison.
RENDER_DPI = 150


class Case:
    """One measured concealment mechanism and its pinned expectations."""

    def __init__(
        self,
        row: str,
        builder,
        *,
        threat_class: str,
        extracted: bool,
        invisible: bool,
        detectors: list[str],
    ) -> None:
        self.row = row
        self.builder = builder
        self.threat_class = threat_class
        self.extracted = extracted
        self.invisible = invisible
        self.detectors = detectors


CASES = [
    Case(
        "00-visible-baseline",
        rf.build_visible_marker,
        threat_class="VISIBLE",
        extracted=True,
        invisible=False,
        detectors=[],
    ),
    Case(
        "01-optional-content-off",
        rf.build_optional_content_off,
        threat_class="NON_THREAT",
        extracted=False,
        invisible=True,
        detectors=[],
    ),
    Case(
        # The glyphless Type3 font renders .notdef boxes (a reader sees boxes)
        # and extraction returns nothing. It hides nothing from a reviewer and
        # is not even invisible, so it is not a working concealment.
        "04-type3-glyphless",
        rf.build_type3_glyphless,
        threat_class="NON_THREAT",
        extracted=False,
        invisible=False,
        detectors=[],
    ),
    Case(
        # Closed by the soft_mask_hidden detector: the mask's group luminosity
        # is read from the content stream and the near-zero backdrop is flagged.
        "07-soft-mask-zero",
        rf.build_soft_mask_zero,
        threat_class="DETECTED",
        extracted=True,
        invisible=True,
        detectors=["soft_mask_hidden"],
    ),
    Case(
        "09-horizontal-scale-zero",
        rf.build_horizontal_scale_zero,
        threat_class="DETECTED",
        extracted=True,
        invisible=True,
        detectors=["sub_visible_glyph"],
    ),
    Case(
        "12-zero-area-form-bbox",
        rf.build_zero_area_form_bbox,
        threat_class="NON_THREAT",
        extracted=False,
        invisible=True,
        detectors=[],
    ),
    Case(
        "13-degenerate-cm",
        rf.build_degenerate_cm,
        threat_class="NON_THREAT",
        extracted=False,
        invisible=True,
        detectors=[],
    ),
    Case(
        "16-zero-area-clip",
        rf.build_zero_area_clip,
        threat_class="NON_THREAT",
        extracted=False,
        invisible=True,
        detectors=[],
    ),
    Case(
        "17-outside-media-box",
        rf.build_outside_media_box,
        threat_class="NON_THREAT",
        extracted=False,
        invisible=True,
        detectors=[],
    ),
    Case(
        "18-white-text",
        rf.build_white_text,
        threat_class="MISS",
        extracted=True,
        invisible=True,
        detectors=[],
    ),
    Case(
        "19-near-white-text",
        rf.build_near_white_text,
        threat_class="VISIBLE",
        extracted=True,
        invisible=False,
        detectors=[],
    ),
]

CASES_BY_ROW = {case.row: case for case in CASES}


def _marker(row: str) -> str:
    return f"SGMARKER{row.split('-')[0]}"


def _extracted(pdf_path: Path, marker: str) -> bool:
    with pymupdf.open(pdf_path) as document:
        text = "".join(page.get_text() for page in document)
    return marker in text


def _render_bytes(pdf_path: Path) -> bytes:
    with pymupdf.open(pdf_path) as document:
        return document[0].get_pixmap(dpi=RENDER_DPI).tobytes("ppm")


def _renders_invisible(marked: Path, control: Path) -> bool:
    return _render_bytes(marked) == _render_bytes(control)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.row)
def test_mechanism_matches_its_pinned_measurement(case: Case, tmp_path: Path) -> None:
    marker = _marker(case.row)
    marked = case.builder(tmp_path / f"{case.row}_marked.pdf", marker)
    control = case.builder(tmp_path / f"{case.row}_control.pdf", "")

    extracted = _extracted(marked, marker)
    invisible = _renders_invisible(marked, control)
    report = integrity.check_text_layer(marked)

    assert extracted is case.extracted, f"{case.row}: extraction changed"
    assert invisible is case.invisible, f"{case.row}: rendered visibility changed"
    assert report.detectors == case.detectors, f"{case.row}: screen result changed"


def test_threat_classes_are_internally_consistent() -> None:
    """The three measured facts must agree with the disposition label."""
    for case in CASES:
        if case.threat_class == "DETECTED":
            assert case.extracted and case.invisible and case.detectors, case.row
        elif case.threat_class == "MISS":
            assert case.extracted and case.invisible and not case.detectors, case.row
        elif case.threat_class == "NON_THREAT":
            assert not case.extracted and not case.detectors, case.row
        elif case.threat_class == "VISIBLE":
            assert not case.invisible, case.row
        else:  # pragma: no cover - guard against a typo'd label
            raise AssertionError(f"unknown threat class {case.threat_class!r}")


def test_the_soft_mask_miss_was_closed_by_a_detector() -> None:
    """The soft-mask gap this phase found is now caught, not merely disclosed.

    The soft_mask_hidden detector reads the mask's group luminosity and flags a
    near-black mask over text. Row 07 is therefore DETECTED, not a MISS.
    """
    case = CASES_BY_ROW["07-soft-mask-zero"]
    assert case.threat_class == "DETECTED"
    assert case.extracted and case.invisible
    assert case.detectors == ["soft_mask_hidden"]


def test_white_text_remains_the_one_disclosed_miss() -> None:
    """White text stays an accepted, disclosed gap: it needs a raster compare.

    It extracts and renders invisible while the screen stays clean, which is
    correct for a rule set that reads structure and paints no pixels. If a
    later detector catches it, this test fails and the disposition moves.
    """
    case = CASES_BY_ROW["18-white-text"]
    assert case.threat_class == "MISS"
    assert case.extracted and case.invisible and not case.detectors
    misses = [c.row for c in CASES if c.threat_class == "MISS"]
    assert misses == ["18-white-text"]


def test_no_committed_fixture_regressed_here() -> None:
    """The visible baseline must read as visible, or the harness is lying.

    A builder that silently produced an invisible baseline would let a real
    concealment read as ``VISIBLE`` and escape. This guards the control.
    """
    baseline = CASES_BY_ROW["00-visible-baseline"]
    assert baseline.threat_class == "VISIBLE"
    assert baseline.extracted and not baseline.invisible
