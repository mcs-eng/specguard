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


def write_transparent_stroke_pdf(path: Path, text: str, stroke_opacity: float) -> Path:
    """Write one page whose only line is stroke-only at ``stroke_opacity``.

    Render mode 1 outlines glyphs and fills nothing. MuPDF reports the stroke
    alpha as the span's alpha, so ``0`` is a line that paints no ink at all.
    """
    document = pymupdf.open()
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text(
        (72.0, 100.0),
        text,
        fontname=SIMPLE_FONT,
        fontsize=11,
        render_mode=1,
        stroke_opacity=stroke_opacity,
    )
    document.save(str(path))
    document.close()
    return path


def write_transparent_fill_stroked_pdf(path: Path, text: str) -> Path:
    """Write one page in fill-and-stroke mode 2 whose fill alone is transparent.

    MuPDF reports this span with the filled flag and an alpha of 0, and gives
    no way to see that the stroke still paints. It is the known conservative
    bias of the zero-alpha rule.
    """
    document = pymupdf.open()
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text(
        (72.0, 100.0),
        text,
        fontname=SIMPLE_FONT,
        fontsize=11,
        render_mode=2,
        fill_opacity=0,
        stroke_opacity=1,
    )
    document.save(str(path))
    document.close()
    return path


def write_forged_inline_image_pdf(path: Path, concealed: str, *, filtered: bool) -> Path:
    """Write one page whose inline image body carries a whitespace-delimited ``EI``.

    Render mode 7 is set before the image and the concealed line is shown after
    it. The image body also carries ``0 Tr``. A scan that stopped at the forged
    ``EI`` would read those pixel bytes as operators, reset its tracked mode to
    0, and report the page as clean while the renderer stays in mode 7.

    ``filtered=False`` writes an unfiltered image, whose exact length the scan
    computes from ``/W``, ``/H``, ``/BPC``, and ``/CS``. ``filtered=True``
    writes the same body behind ``/F /AHx``, whose length is not computable.
    """
    document = pymupdf.open()
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text((72.0, 100.0), "Visible heading.", fontname=SIMPLE_FONT, fontsize=11)
    reference = _font_reference(page).encode("latin-1")
    payload = concealed.encode("latin-1").replace(b"\\", b"\\\\").replace(b"(", b"\\(")
    body = b"xx EI 0 Tr yy"
    body += b"Z" * (40 - len(body))
    header = b"/W 40 /H 1 /CS /G /BPC 8" + (b" /F /AHx" if filtered else b"")
    _append_operators(
        document,
        page,
        b"7 Tr\nBI " + header + b" ID\n" + body + b"\nEI\n"
        b"BT /" + reference + b" 11 Tf 1 0 0 1 72 500 Tm (" + payload + b") Tj ET",
    )
    document.save(str(path))
    document.close()
    return path


def write_fanned_out_xobject_pdf(path: Path, concealed: str, levels: int, fan: int) -> Path:
    """Write one page whose Form XObjects invoke each other ``fan`` times per level.

    Without a per-form cache the scan would expand to ``fan ** levels`` stream
    reads. The deepest form carries ``concealed`` under render mode 3, so a
    cache that skipped work must still report it.
    """
    document = pymupdf.open()
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text((72.0, 100.0), "Visible heading.", fontname=SIMPLE_FONT, fontsize=11)
    reference = _font_reference(page)
    font_xref = _font_xref(page, reference)
    payload = concealed.encode("latin-1").replace(b"\\", b"\\\\").replace(b"(", b"\\(")

    previous: int | None = None
    for _level in range(levels):
        if previous is None:
            stream = (
                b"BT /"
                + reference.encode("latin-1")
                + b" 11 Tf 3 Tr 1 0 0 1 72 600 Tm ("
                + payload
                + b") Tj ET"
            )
            resources = f"/Font<</{reference} {font_xref} 0 R>>"
        else:
            stream = b" ".join([b"/Fm Do"] * fan)
            resources = f"/XObject<</Fm {previous} 0 R>>"
        xref = document.get_new_xref()
        document.update_object(
            xref,
            "<</Type/XObject/Subtype/Form"
            f"/BBox[0 0 {PAGE_WIDTH} {PAGE_HEIGHT}]/Resources<<{resources}>>>>",
        )
        document.update_stream(xref, stream)
        previous = xref

    kind, value = document.xref_get_key(page.xref, "Resources")
    resources_xref = int(value.split()[0]) if kind == "xref" else page.xref
    resources_key = "XObject" if kind == "xref" else "Resources/XObject"
    document.xref_set_key(resources_xref, resources_key, f"<</Fm0 {previous} 0 R>>")
    _append_operators(document, page, b" ".join([b"/Fm0 Do"] * fan))
    document.save(str(path))
    document.close()
    return path


def write_invalid_render_mode_pdf(path: Path, text: str) -> Path:
    """Write one page whose ``Tr`` operand is outside the defined range 0 to 7.

    PDF defines eight render modes. An out-of-range operand sets no mode, and
    honouring it would also let one page mint unbounded keys for the scan's
    per-form cache.
    """
    document = pymupdf.open()
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text((72.0, 100.0), text, fontname=SIMPLE_FONT, fontsize=11)
    reference = _font_reference(page).encode("latin-1")
    _append_operators(
        document,
        page,
        b"q 99 Tr BT /" + reference + b" 11 Tf 1 0 0 1 72 600 Tm (Ordinary second line.) Tj ET Q",
    )
    document.save(str(path))
    document.close()
    return path


# ---------------------------------------------------------------------------
# Builders for the soft-mask detector
#
# A luminosity soft mask sets opacity from the graphics state, outside the
# span, so MuPDF reports the masked span's own alpha as opaque. These builders
# drive the mask from a solid fill whose luminosity decides whether the text is
# hidden. All content is fictional.
# ---------------------------------------------------------------------------


def _wire_extgstate(document: pymupdf.Document, page: pymupdf.Page, value: str) -> None:
    """Set the page's ``/ExtGState`` resource sub-dictionary."""
    kind, raw = document.xref_get_key(page.xref, "Resources")
    if kind == "xref":
        resources_xref, key = int(raw.split()[0]), "ExtGState"
    else:
        resources_xref, key = page.xref, "Resources/ExtGState"
    document.xref_set_key(resources_xref, key, value)


def _luminosity_smask(document: pymupdf.Document, luminosity: float, subtype: str) -> int:
    """Return an ExtGState xref whose soft mask paints one uniform luminosity."""
    group = document.get_new_xref()
    document.update_object(
        group,
        "<</Type/XObject/Subtype/Form"
        f"/BBox[0 0 {PAGE_WIDTH} {PAGE_HEIGHT}]"
        "/Group<</S/Transparency/CS/DeviceGray>>>>",
    )
    document.update_stream(group, f"{luminosity} g 0 0 {PAGE_WIDTH} {PAGE_HEIGHT} re f".encode())
    graphics_state = document.get_new_xref()
    document.update_object(
        graphics_state, f"<</Type/ExtGState/SMask<</S/{subtype}/G {group} 0 R>>>>"
    )
    return graphics_state


def write_soft_mask_pdf(path: Path, visible: str, concealed: str, luminosity: float = 0.0) -> Path:
    """Write one page whose ``concealed`` line is drawn under a luminosity mask.

    ``luminosity`` is the uniform backdrop the mask paints. At ``0.0`` the mask
    drives the text to zero opacity and it is the known-bad case. A luminosity
    at or above the detector threshold leaves the text visible and is the
    near-miss.
    """
    document = pymupdf.open()
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text((72.0, 100.0), visible, fontname=SIMPLE_FONT, fontsize=11)
    reference = _font_reference(page).encode("latin-1")
    graphics_state = _luminosity_smask(document, luminosity, "Luminosity")
    _wire_extgstate(document, page, f"<</GSsm {graphics_state} 0 R>>")
    payload = concealed.encode("latin-1").replace(b"\\", b"\\\\").replace(b"(", b"\\(")
    _append_operators(
        document,
        page,
        b"q /GSsm gs BT /" + reference + b" 11 Tf 1 0 0 1 72 130 Tm (" + payload + b") Tj ET Q",
    )
    document.save(str(path))
    document.close()
    return path


def write_alpha_soft_mask_pdf(path: Path, visible: str, concealed: str) -> Path:
    """Write one page whose ``concealed`` line sits under an alpha-type soft mask.

    The mask paints zero, but the rule evaluates only luminosity masks, so this
    is a near-miss the rule leaves alone. It is the disclosed alpha-mask limit.
    """
    document = pymupdf.open()
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text((72.0, 100.0), visible, fontname=SIMPLE_FONT, fontsize=11)
    reference = _font_reference(page).encode("latin-1")
    graphics_state = _luminosity_smask(document, 0.0, "Alpha")
    _wire_extgstate(document, page, f"<</GSsm {graphics_state} 0 R>>")
    payload = concealed.encode("latin-1").replace(b"\\", b"\\\\").replace(b"(", b"\\(")
    _append_operators(
        document,
        page,
        b"q /GSsm gs BT /" + reference + b" 11 Tf 1 0 0 1 72 130 Tm (" + payload + b") Tj ET Q",
    )
    document.save(str(path))
    document.close()
    return path


def write_soft_mask_none_pdf(path: Path, visible: str, concealed: str) -> Path:
    """Write one page whose ``concealed`` line sits under an explicit ``/SMask /None``.

    The graphics state names a soft mask and clears it to ``/None``, so the text
    is painted normally. It is the near-miss for a cleared mask.
    """
    document = pymupdf.open()
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text((72.0, 100.0), visible, fontname=SIMPLE_FONT, fontsize=11)
    reference = _font_reference(page).encode("latin-1")
    graphics_state = document.get_new_xref()
    document.update_object(graphics_state, "<</Type/ExtGState/SMask/None>>")
    _wire_extgstate(document, page, f"<</GSnone {graphics_state} 0 R>>")
    payload = concealed.encode("latin-1").replace(b"\\", b"\\\\").replace(b"(", b"\\(")
    _append_operators(
        document,
        page,
        b"q /GSnone gs BT /" + reference + b" 11 Tf 1 0 0 1 72 130 Tm (" + payload + b") Tj ET Q",
    )
    document.save(str(path))
    document.close()
    return path


def write_image_soft_mask_pdf(path: Path, text: str) -> Path:
    """Write one page that applies a zero-luminosity mask to an image, not to text.

    The mask is set and restored around an image draw, then ``text`` is shown
    with no mask in effect. It is the near-miss that proves the rule flags text
    under a mask, not every page that carries a soft mask.
    """
    document = pymupdf.open()
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text((72.0, 100.0), text, fontname=SIMPLE_FONT, fontsize=11)
    reference = _font_reference(page).encode("latin-1")
    graphics_state = _luminosity_smask(document, 0.0, "Luminosity")
    _wire_extgstate(document, page, f"<</GSsm {graphics_state} 0 R>>")
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 10, 10))
    pixmap.set_rect(pixmap.irect, (10, 10, 10))
    page.insert_image(pymupdf.Rect(72.0, 600.0, 120.0, 648.0), pixmap=pixmap)
    _append_operators(
        document,
        page,
        b"q /GSsm gs Q BT /" + reference + b" 11 Tf 1 0 0 1 72 160 Tm (Ordinary line.) Tj ET",
    )
    document.save(str(path))
    document.close()
    return path


# ---------------------------------------------------------------------------
# Corrections from the Phase 7d-eval Codex review
# ---------------------------------------------------------------------------


def _black_luminosity_group(document: pymupdf.Document) -> int:
    """Return a transparency group whose stream paints the whole box black."""
    group = document.get_new_xref()
    document.update_object(
        group,
        "<</Type/XObject/Subtype/Form"
        f"/BBox[0 0 {PAGE_WIDTH} {PAGE_HEIGHT}]"
        "/Group<</S/Transparency/CS/DeviceGray>>>>",
    )
    document.update_stream(group, f"0.0 g 0 0 {PAGE_WIDTH} {PAGE_HEIGHT} re f".encode())
    return group


def _empty_luminosity_group(document: pymupdf.Document) -> int:
    """Return a transparency group whose stream paints nothing at all."""
    group = document.get_new_xref()
    document.update_object(
        group,
        "<</Type/XObject/Subtype/Form"
        f"/BBox[0 0 {PAGE_WIDTH} {PAGE_HEIGHT}]"
        "/Group<</S/Transparency/CS/DeviceGray>>>>",
    )
    document.update_stream(group, b"")
    return group


def _show_under(reference: bytes, names: bytes, text: str) -> bytes:
    """Return operators that show ``text`` after applying each named ExtGState."""
    payload = text.encode("latin-1").replace(b"\\", b"\\\\").replace(b"(", b"\\(")
    return (
        b"q "
        + names
        + b" BT /"
        + reference
        + b" 11 Tf 1 0 0 1 72 130 Tm ("
        + payload
        + b") Tj ET Q"
    )


def write_indirect_soft_mask_pdf(path: Path, visible: str, concealed: str) -> Path:
    """Write one page whose hiding mask is reached through an indirect reference.

    ``/SMask`` may be an indirect object rather than an inline dictionary. Both
    forms are valid and both hide the text, so a rule that reads only the
    inline form leaves this one clean.
    """
    document = pymupdf.open()
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text((72.0, 100.0), visible, fontname=SIMPLE_FONT, fontsize=11)
    reference = _font_reference(page).encode("latin-1")
    group = _black_luminosity_group(document)
    mask = document.get_new_xref()
    document.update_object(mask, f"<</S/Luminosity/G {group} 0 R>>")
    graphics_state = document.get_new_xref()
    document.update_object(graphics_state, f"<</Type/ExtGState/SMask {mask} 0 R>>")
    _wire_extgstate(document, page, f"<</GSsm {graphics_state} 0 R>>")
    _append_operators(document, page, _show_under(reference, b"/GSsm gs", concealed))
    document.save(str(path))
    document.close()
    return path


def write_soft_mask_then_unrelated_extgstate_pdf(path: Path, visible: str, concealed: str) -> Path:
    """Write one page that applies a hiding mask, then an ExtGState with no ``/SMask``.

    An ExtGState that sets other parameters leaves the current soft mask in
    force. Only an explicit ``/SMask /None`` or a replacement mask changes it,
    so the text after the second ``gs`` is still hidden.
    """
    document = pymupdf.open()
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text((72.0, 100.0), visible, fontname=SIMPLE_FONT, fontsize=11)
    reference = _font_reference(page).encode("latin-1")
    group = _black_luminosity_group(document)
    hiding = document.get_new_xref()
    document.update_object(hiding, f"<</Type/ExtGState/SMask<</S/Luminosity/G {group} 0 R>>>>")
    unrelated = document.get_new_xref()
    document.update_object(unrelated, "<</Type/ExtGState/LW 1>>")
    _wire_extgstate(document, page, f"<</GShide {hiding} 0 R/GSother {unrelated} 0 R>>")
    _append_operators(document, page, _show_under(reference, b"/GShide gs /GSother gs", concealed))
    document.save(str(path))
    document.close()
    return path


def write_inherited_extgstate_soft_mask_pdf(path: Path, visible: str, concealed: str) -> Path:
    """Write one page that inherits its ``/ExtGState`` from the ``/Pages`` node.

    A page may omit ``/Resources`` entirely; a viewer then reads it from the
    nearest ancestor before it draws. A screen that reads the page object alone
    resolves no ``gs`` name on such a page.
    """
    staged = path.with_name(f"{path.stem}-staged.pdf")
    document = pymupdf.open()
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text((72.0, 100.0), visible, fontname=SIMPLE_FONT, fontsize=11)
    reference = _font_reference(page).encode("latin-1")
    group = _black_luminosity_group(document)
    graphics_state = document.get_new_xref()
    document.update_object(
        graphics_state, f"<</Type/ExtGState/SMask<</S/Luminosity/G {group} 0 R>>>>"
    )
    _wire_extgstate(document, page, f"<</GSsm {graphics_state} 0 R>>")
    _append_operators(document, page, _show_under(reference, b"/GSsm gs", concealed))
    document.save(str(staged))
    document.close()

    # Move the page's own /Resources on to its /Pages parent, then clear it, so
    # the resources reach the page only through inheritance.
    document = pymupdf.open(str(staged))
    page = document[0]
    kind, raw = document.xref_get_key(page.xref, "Resources")
    resources = raw if kind != "xref" else document.xref_object(int(raw.split()[0]))
    parent_kind, parent = document.xref_get_key(page.xref, "Parent")
    if parent_kind != "xref":  # pragma: no cover - PyMuPDF always writes a /Pages parent
        raise RuntimeError("the staged page has no /Pages parent to inherit from")
    document.xref_set_key(int(parent.split()[0]), "Resources", resources)
    document.xref_set_key(page.xref, "Resources", "null")
    document.save(str(path))
    document.close()
    staged.unlink()
    return path


def write_white_backdrop_soft_mask_pdf(path: Path, visible: str, shown: str) -> Path:
    """Write one page whose luminosity mask paints nothing over a white ``/BC``.

    The group's stream is empty, so every point takes the mask's ``/BC``
    backdrop. At ``[1]`` that backdrop is white, the mask is fully opaque, and
    ``shown`` is visible on the page. It is the near-miss that a rule reading
    an empty group as black would quarantine.
    """
    document = pymupdf.open()
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text((72.0, 100.0), visible, fontname=SIMPLE_FONT, fontsize=11)
    reference = _font_reference(page).encode("latin-1")
    group = _empty_luminosity_group(document)
    graphics_state = document.get_new_xref()
    document.update_object(
        graphics_state,
        f"<</Type/ExtGState/SMask<</S/Luminosity/G {group} 0 R/BC[1]>>>>",
    )
    _wire_extgstate(document, page, f"<</GSsm {graphics_state} 0 R>>")
    _append_operators(document, page, _show_under(reference, b"/GSsm gs", shown))
    document.save(str(path))
    document.close()
    return path


def write_black_backdrop_soft_mask_pdf(path: Path, visible: str, concealed: str) -> Path:
    """Write one page whose empty luminosity mask carries a black ``/BC``.

    The companion to the white-backdrop page: an empty group over ``[0]`` is
    black everywhere, so the text under it is hidden and must still flag. It
    pins that reading ``/BC`` did not open a way past the rule.
    """
    document = pymupdf.open()
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text((72.0, 100.0), visible, fontname=SIMPLE_FONT, fontsize=11)
    reference = _font_reference(page).encode("latin-1")
    group = _empty_luminosity_group(document)
    graphics_state = document.get_new_xref()
    document.update_object(
        graphics_state,
        f"<</Type/ExtGState/SMask<</S/Luminosity/G {group} 0 R/BC[0]>>>>",
    )
    _wire_extgstate(document, page, f"<</GSsm {graphics_state} 0 R>>")
    _append_operators(document, page, _show_under(reference, b"/GSsm gs", concealed))
    document.save(str(path))
    document.close()
    return path
