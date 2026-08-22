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

#: A base-14 font, whose single-byte encoding lets a raw content-stream string
#: operand carry ordinary text. The subset CID encoding of ``FIXTURE_FONT``
#: cannot, so the two builders that append raw operators use this font instead.
SIMPLE_FONT = "helv"

#: A base-14 font, whose single-byte encoding lets a raw content-stream string
#: operand carry ordinary text. The subset CID encoding of ``FIXTURE_FONT``
#: cannot, so the builders that append raw operators use this font instead.

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


def write_pdf(
    path: Path,
    pages: list[list[str]],
    fontsize: int = 11,
    hidden: dict[int, list[str]] | None = None,
) -> Path:
    """Write a text-based PDF: one page per entry, one line per string.

    ``hidden`` maps a one-based page number to lines written in PDF render mode
    3. That mode paints nothing, so those lines never reach a reader while text
    extraction still returns them. The default writes no hidden line at all.
    """
    hidden = hidden or {}
    document = pymupdf.open()
    for page_number, lines in enumerate(pages, start=1):
        page = document.new_page()
        y = 72.0
        for line in lines:
            page.insert_text((72.0, y), line, fontname=FIXTURE_FONT, fontsize=fontsize)
            y += fontsize * 1.6
        for line in hidden.get(page_number, []):
            page.insert_text(
                (72.0, y), line, fontname=FIXTURE_FONT, fontsize=fontsize, render_mode=3
            )
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


# ---------------------------------------------------------------------------
# Builders for the text-layer integrity detectors
#
# Each builder writes one page and takes the parameter that decides whether the
# page is a known-bad case the screen must quarantine or a near-miss the screen
# must leave alone. All content is fictional.
# ---------------------------------------------------------------------------

#: Page size every integrity builder writes, in points.
PAGE_WIDTH = 595.0
PAGE_HEIGHT = 842.0

#: Page-space height of the crop box the crop-box builders keep.
CROP_HEIGHT = 400.0


def _font_reference(page: pymupdf.Page) -> str:
    """Return the resource name of the first font the page already carries."""
    return page.get_fonts()[0][4]


def _font_xref(page: pymupdf.Page, reference: str) -> int:
    """Return the object number of one of the page's font resources."""
    return next(entry[0] for entry in page.get_fonts() if entry[4] == reference)


def _append_operators(document: pymupdf.Document, page: pymupdf.Page, extra: bytes) -> None:
    """Append raw operators to one page, consolidating its content streams.

    The page's streams are joined into the first one and the rest are emptied,
    so the appended operators run exactly once. ``clean_contents`` is not used:
    it drops a resource the appended operators have not referenced yet.
    """
    xrefs = page.get_contents()
    document.update_stream(xrefs[0], page.read_contents() + b"\n" + extra + b"\n")
    for xref in xrefs[1:]:
        document.update_stream(xref, b" ")


def write_cropped_pdf(path: Path, inside: str, outside: str) -> Path:
    """Write one page whose crop box excludes the lower band, with text in both.

    ``outside`` sits in the excluded band, so a reader of the cropped page never
    sees it while the file still carries it. This is the known-bad case for the
    ``out_of_crop_box`` detector.
    """
    document = pymupdf.open()
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text((72.0, 100.0), inside, fontname=FIXTURE_FONT, fontsize=11)
    page.insert_text((72.0, 600.0), outside, fontname=FIXTURE_FONT, fontsize=11)
    page.set_cropbox(pymupdf.Rect(0.0, 0.0, PAGE_WIDTH, CROP_HEIGHT))
    document.save(str(path))
    document.close()
    return path


def write_edge_cropped_pdf(path: Path, text: str) -> Path:
    """Write one page whose crop edge cuts through a line of text.

    The line is partly inside the crop box, so a reader sees part of it. This is
    the near-miss for the ``out_of_crop_box`` detector.
    """
    document = pymupdf.open()
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text((72.0, CROP_HEIGHT + 4.0), text, fontname=FIXTURE_FONT, fontsize=11)
    page.set_cropbox(pymupdf.Rect(0.0, 0.0, PAGE_WIDTH, CROP_HEIGHT))
    document.save(str(path))
    document.close()
    return path


def write_alpha_pdf(path: Path, text: str, fill_opacity: float) -> Path:
    """Write one page whose only line is filled at ``fill_opacity``.

    ``0`` is the known-bad case for the ``zero_alpha`` detector. A small
    non-zero value, such as ``0.2``, is the near-miss: faint text still paints.
    """
    document = pymupdf.open()
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text(
        (72.0, 100.0), text, fontname=FIXTURE_FONT, fontsize=11, fill_opacity=fill_opacity
    )
    document.save(str(path))
    document.close()
    return path


def write_render_mode_pdf(path: Path, visible: str, concealed: str, render_mode: int) -> Path:
    """Write one page carrying a visible line and one line at ``render_mode``.

    ``render_mode=7`` is clip-only: the glyphs paint no ink and only narrow the
    clipping path. It is the known-bad case for the
    ``content_stream_render_mode`` detector.
    """
    document = pymupdf.open()
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text((72.0, 100.0), visible, fontname=FIXTURE_FONT, fontsize=11)
    page.insert_text(
        (72.0, 130.0), concealed, fontname=FIXTURE_FONT, fontsize=11, render_mode=render_mode
    )
    document.save(str(path))
    document.close()
    return path


def write_path_clipped_pdf(path: Path, text: str) -> Path:
    """Write one page whose text is clipped by a path rather than by a render mode.

    The text is shown under render mode 0 inside a rectangular clip that
    contains it. It paints normally, so it is the near-miss for the
    ``content_stream_render_mode`` detector.
    """
    document = pymupdf.open()
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text((72.0, 100.0), "Visible heading.", fontname=SIMPLE_FONT, fontsize=11)
    reference = _font_reference(page)
    clipped = text.encode("latin-1").replace(b"\\", b"\\\\").replace(b"(", b"\\(")
    _append_operators(
        document,
        page,
        b"q 36 36 523 770 re W n BT /"
        + reference.encode("latin-1")
        + b" 11 Tf 1 0 0 1 72 700 Tm ("
        + clipped
        + b") Tj ET Q",
    )
    document.save(str(path))
    document.close()
    return path


def write_xobject_render_mode_pdf(
    path: Path, visible: str, concealed: str, render_mode: int
) -> Path:
    """Write one page that sets a render mode, then invokes a Form XObject.

    The mode is set on the page and never inside the form, so a scan that
    started each form at mode 0 would miss ``concealed`` entirely. With
    ``render_mode=3`` this is the known-bad case for Form XObject handling;
    with ``0`` it is the near-miss.
    """
    document = pymupdf.open()
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text((72.0, 100.0), visible, fontname=SIMPLE_FONT, fontsize=11)
    reference = _font_reference(page)
    payload = concealed.encode("latin-1").replace(b"\\", b"\\\\").replace(b"(", b"\\(")
    form = (
        b"BT /" + reference.encode("latin-1") + b" 11 Tf 1 0 0 1 72 600 Tm (" + payload + b") Tj ET"
    )
    form_xref = document.get_new_xref()
    document.update_object(
        form_xref,
        "<</Type/XObject/Subtype/Form"
        f"/BBox[0 0 {PAGE_WIDTH} {PAGE_HEIGHT}]"
        f"/Resources<</Font<</{reference} {_font_xref(page, reference)} 0 R>>>>>>",
    )
    document.update_stream(form_xref, form)
    # The page's /Resources is an indirect object, which xref_set_key refuses to
    # traverse, so the key is set on the resource dictionary itself.
    kind, value = document.xref_get_key(page.xref, "Resources")
    resources_xref = int(value.split()[0]) if kind == "xref" else page.xref
    resources_key = "XObject" if kind == "xref" else "Resources/XObject"
    document.xref_set_key(resources_xref, resources_key, f"<</Fm0 {form_xref} 0 R>>")
    _append_operators(document, page, f"q {render_mode} Tr /Fm0 Do Q".encode("latin-1"))
    document.save(str(path))
    document.close()
    return path


def write_saved_render_mode_pdf(path: Path, concealed: str, painted: str) -> Path:
    """Write one page that sets render mode 3 inside ``q`` and paints after ``Q``.

    ``Q`` restores the render mode ``q`` saved. A scan that ignored the
    graphics-state stack would report ``painted`` as concealed too.
    """
    document = pymupdf.open()
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text((72.0, 100.0), "Visible heading.", fontname=SIMPLE_FONT, fontsize=11)
    reference = _font_reference(page).encode("latin-1")
    first = concealed.encode("latin-1").replace(b"\\", b"\\\\").replace(b"(", b"\\(")
    second = painted.encode("latin-1").replace(b"\\", b"\\\\").replace(b"(", b"\\(")
    _append_operators(
        document,
        page,
        b"q 3 Tr BT /" + reference + b" 11 Tf 1 0 0 1 72 600 Tm (" + first + b") Tj ET Q\n"
        b"BT /" + reference + b" 11 Tf 1 0 0 1 72 560 Tm (" + second + b") Tj ET",
    )
    document.save(str(path))
    document.close()
    return path


def write_inline_image_pdf(path: Path, text: str) -> Path:
    """Write one page whose inline image body spells out text-showing operators.

    The bytes between ``ID`` and ``EI`` are image data, not operators. A scan
    that tokenized them would read a forged ``3 Tr`` and flag a clean page.
    """
    document = pymupdf.open()
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text((72.0, 100.0), text, fontname=SIMPLE_FONT, fontsize=11)
    _append_operators(
        document,
        page,
        b"q 200 0 0 40 72 600 cm\nBI /W 8 /H 1 /CS /G /BPC 8 /F /AHx ID\n"
        b"3 Tr (Forged operand.) Tj 00>\nEI Q",
    )
    document.save(str(path))
    document.close()
    return path


def write_glyph_size_pdf(path: Path, text: str, fontsize: float) -> Path:
    """Write one page whose only line is set at ``fontsize`` points.

    A size below one point is the known-bad case for the
    ``sub_visible_glyph`` detector. Exactly ``1.0`` is the near-miss.
    """
    document = pymupdf.open()
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text((72.0, 100.0), text, fontname=FIXTURE_FONT, fontsize=fontsize)
    document.save(str(path))
    document.close()
    return path


def write_matrix_scaled_pdf(path: Path, text: str, fontsize: float, scale: float) -> Path:
    """Write one page whose text matrix scales an ordinary font size down.

    The ``Tf`` operand stays at ``fontsize``; the ``Tm`` operand scales it by
    ``scale``. A reader sees glyphs of ``fontsize * scale`` points, which is
    what the ``sub_visible_glyph`` detector must read.
    """
    document = pymupdf.open()
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text((72.0, 100.0), "Visible heading.", fontname=SIMPLE_FONT, fontsize=11)
    reference = _font_reference(page)
    payload = text.encode("latin-1").replace(b"\\", b"\\\\").replace(b"(", b"\\(")
    _append_operators(
        document,
        page,
        b"q BT /"
        + reference.encode("latin-1")
        + f" {fontsize} Tf {scale} 0 0 {scale} 72 600 Tm (".encode("latin-1")
        + payload
        + b") Tj ET Q",
    )
    document.save(str(path))
    document.close()
    return path
