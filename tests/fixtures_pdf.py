"""Helpers that build small text-based PDF fixtures with PyMuPDF.

All content is fictional. The section numbers, the project name, and the
equipment values below describe an invented project. Nothing here comes from a
real project, a real vendor, or a real document set.

The fixtures embed the Noto Sans font from ``pymupdf-fonts``. The PyMuPDF
base14 fonts do not round-trip a soft hyphen (U+00AD) or an fi ligature
(U+FB01) through text extraction, so they cannot carry the characters the
known-good cases need.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

FIXTURE_FONT = "notos"

SOFT_HYPHEN = "\u00ad"
NO_BREAK_SPACE = "\u00a0"
FI_LIGATURE = "\ufb01"

#: One fictional specification: four pages, one list of lines per page.
SPEC_PAGES: list[list[str]] = [
    [
        "SECTION 26 27 26 - WIRING DEVICES",
        "Receptacles shall be specification grade, rated 20 amperes at 125 volts.",
        "Device plates shall be smooth thermoplastic in a color",
        "selected by the Architect.",
    ],
    [
        "Panelboard MDP-2 shall be rated 208 volts, three phase, four wire.",
        "The medium voltage feeder shall be rated 15 kV with shielded conductors.",
        "Terminations shall be rated for the Northgate Civic Annex trans" + SOFT_HYPHEN,
        "former room ambient of 40 degrees Celsius.",
        "The " + FI_LIGATURE + "xture schedule lists a 30" + NO_BREAK_SPACE + "ampere branch",
        "circuit for the unit heater.",
    ],
    [
        "Grounding conductors shall be sized in accordance with",
    ],
    [
        "Table 4 of this Section and shall be copper throughout.",
        "The Contractor shall submit certified test reports for each feeder.",
    ],
]


def write_pdf(path: Path, pages: list[list[str]], fontsize: int = 11) -> Path:
    """Write a text-based PDF: one page per entry, one line per string."""
    document = pymupdf.open()
    for lines in pages:
        page = document.new_page()
        y = 72.0
        for line in lines:
            page.insert_text((72.0, y), line, fontname=FIXTURE_FONT, fontsize=fontsize)
            y += fontsize * 1.6
    document.save(str(path))
    document.close()
    return path


def write_pdf_with_hidden_text(path: Path, visible: str, hidden: str) -> Path:
    """Write one page whose visible text and invisible text layer disagree.

    ``render_mode=3`` is the PDF "invisible" text render mode, the same mode an
    OCR layer uses over a scanned image. A reader sees ``visible``; extraction
    returns both lines.
    """
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72.0, 100.0), visible, fontname=FIXTURE_FONT, fontsize=11)
    page.insert_text((72.0, 130.0), hidden, fontname=FIXTURE_FONT, fontsize=11, render_mode=3)
    document.save(str(path))
    document.close()
    return path


def write_image_only_pdf(path: Path) -> Path:
    """Write one page that carries an image and no text layer at all."""
    document = pymupdf.open()
    page = document.new_page()
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 20))
    pixmap.set_rect(pixmap.irect, (10, 10, 10))
    page.insert_image(pymupdf.Rect(72.0, 72.0, 300.0, 148.0), pixmap=pixmap)
    document.save(str(path))
    document.close()
    return path


def write_spec_pdf(directory: Path, name: str = "fictional_spec.pdf") -> Path:
    """Write the fictional four-page specification fixture."""
    return write_pdf(directory / name, SPEC_PAGES)
