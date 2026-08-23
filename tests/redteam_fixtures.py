"""Builders for the Phase 7b-redteam concealment-mechanism fixtures.

Each builder writes one page whose visible content is fixed fictional filler.
When ``marker`` is a non-empty string the page also carries that marker through
one concealment mechanism; when ``marker`` is empty the page is the visual
control that carries no marker at all. A mechanism is a true concealment only
when the marked page and the control page render to identical pixels: the
marker adds nothing a reader can see.

The marker is always the benign token the caller supplies (the test uses
``SG-MARKER-<row>``). No builder writes directive, reviewer-addressed, or
instruction-style text; the mechanism is the unit under test and the bytes
inside the hidden span are irrelevant to the screen. All names and values are
fictional.

These builders exist to measure the text-layer integrity screen, not to defeat
it in production: every fixture is fed to ``integrity.check_text_layer`` and the
measured result is pinned in ``tests/test_integrity_redteam.py``.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

#: A base-14 font whose single-byte encoding lets a raw content-stream string
#: operand carry an ASCII marker. The subset CID encoding of an embedded font
#: cannot, so every raw-operator builder here uses this font.
SIMPLE_FONT = "helv"
PAGE_WIDTH = 595.0
PAGE_HEIGHT = 842.0
VISIBLE_HEADING = "Visible heading."


def _escape(text: str) -> bytes:
    """Escape a marker for a PDF literal string operand."""
    return (
        text.encode("latin-1").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
    )


def _font_reference(page: pymupdf.Page) -> bytes:
    """Return the resource name of the first font the page already carries."""
    return page.get_fonts()[0][4].encode("latin-1")


def _font_xref(page: pymupdf.Page, reference: bytes) -> int:
    """Return the xref of the page font whose resource name is ``reference``."""
    name = reference.decode("latin-1")
    for descriptor in page.get_fonts(full=True):
        if descriptor[4] == name:
            return descriptor[0]
    return page.get_fonts()[0][0]


def _append_operators(document: pymupdf.Document, page: pymupdf.Page, extra: bytes) -> None:
    """Append raw operators to one page, consolidating its content streams."""
    xrefs = page.get_contents()
    document.update_stream(xrefs[0], page.read_contents() + b"\n" + extra + b"\n")
    for xref in xrefs[1:]:
        document.update_stream(xref, b" ")


def _new_page(document: pymupdf.Document) -> pymupdf.Page:
    """Open one page carrying only the fixed visible heading."""
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text((72.0, 100.0), VISIBLE_HEADING, fontname=SIMPLE_FONT, fontsize=11)
    return page


def _wire_resource(document: pymupdf.Document, page: pymupdf.Page, subkey: str, value: str) -> None:
    """Set ``/<subkey>`` on the page's resource dictionary, indirect or inline."""
    kind, raw = document.xref_get_key(page.xref, "Resources")
    if kind == "xref":
        resources_xref, key = int(raw.split()[0]), subkey
    else:
        resources_xref, key = page.xref, f"Resources/{subkey}"
    document.xref_set_key(resources_xref, key, value)


# --- baseline -------------------------------------------------------------


def build_visible_marker(path: Path, marker: str) -> Path:
    """Paint the marker normally (render mode 0): visible, extracted, clean.

    This is the control on the control: it proves the harness registers a
    visible marker as visible, so a false "invisible" reading cannot pass
    silently.
    """
    document = pymupdf.open()
    page = _new_page(document)
    if marker:
        page.insert_text((72.0, 130.0), marker, fontname=SIMPLE_FONT, fontsize=11)
    document.save(str(path))
    document.close()
    return path


# --- structural mechanisms extraction drops (non-threats) -----------------


def build_optional_content_off(path: Path, marker: str) -> Path:
    """Marker inside an optional content group whose default state is OFF."""
    document = pymupdf.open()
    page = _new_page(document)
    if marker:
        ocg = document.add_ocg("Concealed layer", on=False)
        page.insert_text((72.0, 130.0), marker, fontname=SIMPLE_FONT, fontsize=11, oc=ocg)
    document.save(str(path))
    document.close()
    return path


def build_zero_area_form_bbox(path: Path, marker: str) -> Path:
    """Marker inside a Form XObject whose ``/BBox`` has zero area."""
    document = pymupdf.open()
    page = _new_page(document)
    if marker:
        reference = _font_reference(page)
        form = b"BT /" + reference + b" 11 Tf 1 0 0 1 5 5 Tm (" + _escape(marker) + b") Tj ET"
        form_xref = document.get_new_xref()
        document.update_object(
            form_xref,
            "<</Type/XObject/Subtype/Form/BBox[0 0 0 0]"
            f"/Resources<</Font<</{reference.decode()} {_font_xref(page, reference)} 0 R>>>>>>",
        )
        document.update_stream(form_xref, form)
        _wire_resource(document, page, "XObject", f"<</Fm0 {form_xref} 0 R>>")
        _append_operators(document, page, b"q /Fm0 Do Q")
    document.save(str(path))
    document.close()
    return path


def build_degenerate_cm(path: Path, marker: str) -> Path:
    """Marker inside a Form XObject invoked under a zero-scale ``cm``."""
    document = pymupdf.open()
    page = _new_page(document)
    if marker:
        reference = _font_reference(page)
        form = b"BT /" + reference + b" 11 Tf 1 0 0 1 10 10 Tm (" + _escape(marker) + b") Tj ET"
        form_xref = document.get_new_xref()
        document.update_object(
            form_xref,
            "<</Type/XObject/Subtype/Form/BBox[0 0 595 842]"
            f"/Resources<</Font<</{reference.decode()} {_font_xref(page, reference)} 0 R>>>>>>",
        )
        document.update_stream(form_xref, form)
        _wire_resource(document, page, "XObject", f"<</Fm0 {form_xref} 0 R>>")
        _append_operators(document, page, b"q 0 0 0 0 72 130 cm /Fm0 Do Q")
    document.save(str(path))
    document.close()
    return path


def build_zero_area_clip(path: Path, marker: str) -> Path:
    """Marker painted (render mode 0) inside a zero-area clip rectangle."""
    document = pymupdf.open()
    page = _new_page(document)
    if marker:
        reference = _font_reference(page)
        _append_operators(
            document,
            page,
            b"q 72 130 0 0 re W n BT /"
            + reference
            + b" 11 Tf 1 0 0 1 72 130 Tm ("
            + _escape(marker)
            + b") Tj ET Q",
        )
    document.save(str(path))
    document.close()
    return path


def build_outside_media_box(path: Path, marker: str) -> Path:
    """Marker painted below the media box, at a negative y coordinate."""
    document = pymupdf.open()
    page = _new_page(document)
    if marker:
        reference = _font_reference(page)
        _append_operators(
            document,
            page,
            b"BT /" + reference + b" 11 Tf 1 0 0 1 72 -50 Tm (" + _escape(marker) + b") Tj ET",
        )
    document.save(str(path))
    document.close()
    return path


def build_type3_glyphless(path: Path, marker: str) -> Path:
    """Marker set in a Type3 font whose every glyph procedure paints nothing."""
    document = pymupdf.open()
    page = _new_page(document)
    if marker:
        codes = sorted(set(marker))
        proc = document.get_new_xref()
        document.update_object(proc, "<<>>")
        document.update_stream(proc, b"0 0 0 0 0 0 d0\n", new=True)
        char_procs = "".join(f"/g{ord(c)} {proc} 0 R" for c in codes)
        first, last = min(ord(c) for c in codes), max(ord(c) for c in codes)
        widths = " ".join("0" for _ in range(first, last + 1))
        differences = "".join(f"{ord(c)}/g{ord(c)}" for c in codes)
        to_unicode = document.get_new_xref()
        entries = "".join(f"<{ord(c):04X}><{ord(c):04X}>" for c in codes)
        cmap = (
            "/CIDInit/ProcSet findresource begin 12 dict begin begincmap"
            "/CMapName/Adobe-Identity-UCS def 1 begincodespacerange<0000><FFFF>endcodespacerange "
            f"{len(codes)} beginbfrange {entries} endbfrange endcmap CMapName currentdict "
            "/CMap defineresource pop end end"
        )
        document.update_object(to_unicode, "<<>>")
        document.update_stream(to_unicode, cmap.encode("latin-1"), new=True)
        font_xref = document.get_new_xref()
        document.update_object(
            font_xref,
            "<</Type/Font/Subtype/Type3/FontBBox[0 0 0 0]/FontMatrix[0.001 0 0 0.001 0 0]"
            f"/FirstChar {first}/LastChar {last}/Widths[{widths}]/CharProcs<<{char_procs}>>"
            f"/Encoding<</Type/Encoding/Differences[{differences}]>>/ToUnicode {to_unicode} 0 R>>",
        )
        _wire_resource(document, page, "Font", f"<</F3x {font_xref} 0 R>>")
        _append_operators(
            document, page, b"BT /F3x 11 Tf 1 0 0 1 72 130 Tm (" + _escape(marker) + b") Tj ET"
        )
    document.save(str(path))
    document.close()
    return path


# --- mechanisms that extract and are invisible ----------------------------


def build_horizontal_scale_zero(path: Path, marker: str) -> Path:
    """Marker collapsed to a sliver by a near-zero horizontal scaling ``Tz``."""
    document = pymupdf.open()
    page = _new_page(document)
    if marker:
        reference = _font_reference(page)
        _append_operators(
            document,
            page,
            b"BT /"
            + reference
            + b" 11 Tf 0.01 Tz 1 0 0 1 72 130 Tm ("
            + _escape(marker)
            + b") Tj ET",
        )
    document.save(str(path))
    document.close()
    return path


def build_soft_mask_zero(path: Path, marker: str) -> Path:
    """Marker drawn under an ExtGState soft mask that masks it to nothing.

    A luminosity soft mask whose group paints solid black yields zero opacity
    everywhere it applies. The marker paints no visible ink, while the text
    operator and its characters stay in the content stream and extraction
    returns them. The span's own ``alpha`` is 255, so the ``zero_alpha``
    detector, which reads span alpha, does not see this.
    """
    document = pymupdf.open()
    page = _new_page(document)
    if marker:
        reference = _font_reference(page)
        group = document.get_new_xref()
        document.update_object(
            group,
            "<</Type/XObject/Subtype/Form/BBox[0 0 595 842]"
            "/Group<</S/Transparency/CS/DeviceGray>>>>",
        )
        document.update_stream(group, b"0 g 0 0 595 842 re f")
        graphics_state = document.get_new_xref()
        document.update_object(
            graphics_state, f"<</Type/ExtGState/SMask<</S/Luminosity/G {group} 0 R>>>>"
        )
        _wire_resource(document, page, "ExtGState", f"<</GSsm {graphics_state} 0 R>>")
        _append_operators(
            document,
            page,
            b"q /GSsm gs BT /"
            + reference
            + b" 11 Tf 1 0 0 1 72 130 Tm ("
            + _escape(marker)
            + b") Tj ET Q",
        )
    document.save(str(path))
    document.close()
    return path


def build_white_text(path: Path, marker: str) -> Path:
    """Marker painted white (render mode 0, alpha 255) on the white page."""
    document = pymupdf.open()
    page = _new_page(document)
    if marker:
        page.insert_text((72.0, 130.0), marker, fontname=SIMPLE_FONT, fontsize=11, color=(1, 1, 1))
    document.save(str(path))
    document.close()
    return path


def build_near_white_text(path: Path, marker: str) -> Path:
    """Marker painted at 0.995 gray: a reader cannot tell it from white."""
    document = pymupdf.open()
    page = _new_page(document)
    if marker:
        page.insert_text(
            (72.0, 130.0), marker, fontname=SIMPLE_FONT, fontsize=11, color=(0.995, 0.995, 0.995)
        )
    document.save(str(path))
    document.close()
    return path
