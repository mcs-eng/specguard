"""Deterministic text-layer integrity screen for untrusted documents.

SCREENING CONTRACT

A submitted PDF is untrusted input. Its text layer is what an automated
reviewer ingests; the painted page is what a human reviewer reads. The two can
disagree. This screen reports several specific disagreements, each produced by
one named detector, and each carrying the page and the evidence the detector
read.

1. Source data. The screen reads the file's bytes once, into one immutable
   snapshot, and derives everything from that snapshot: the SHA-256, the page
   count, and the flag evidence. A replacement of the file during a screen
   therefore cannot make the hash describe one byte stream while the evidence
   describes another. The screen reads PyMuPDF span data and PDF content
   streams only. It renders no image, runs no OCR, and compares no pixels. It
   reads every page of the document.
2. Detectors. Five rules run over every page, in a fixed order, and every flag
   names the rule that produced it:

   ``render_mode_3``
       A PDF text-showing operator carries a render mode (``Tr``). MuPDF
       records the outcome of that mode on every character as ``char_flags``.
       A span whose characters are neither filled nor stroked paints nothing
       while the text layer still carries the characters. That is render mode
       3, the mode an OCR layer uses over a scanned image.
   ``zero_alpha``
       A span that MuPDF reports as filled, not stroked, and not clipped, whose
       fill alpha is 0. The graphics state, not the render mode, made it
       invisible. PyMuPDF 1.28.2 exposes ``alpha`` on every span of
       ``page.get_text("dict")``; ``test_pymupdf_exposes_span_alpha`` pins that
       it still does.
   ``sub_visible_glyph``
       A span whose effective size is below ``MINIMUM_READABLE_POINT_SIZE``.
       MuPDF reports ``size`` after the text matrix is applied, so a 10 pt font
       scaled to a twentieth by ``Tm`` is reported as 0.5 pt and is flagged.
   ``content_stream_render_mode``
       The page's own content streams are tokenized and the ``Tr`` operator is
       tracked across ``q``/``Q`` and across ``Do`` into Form XObjects. Text
       shown under render mode 3 or 7 is flagged. This is the only rule that
       separates clip-only mode 7 from a filled-and-clipped mode 4, 5, or 6,
       because MuPDF reports the same character flags for both.
   ``out_of_crop_box``
       Text that sits inside the media box and outside the crop box. MuPDF
       clips extraction to the crop box, so this rule re-reads each page from a
       second snapshot whose crop box has been widened to the media box, and
       flags any span whose rectangle does not intersect the original crop box.
       The pass is skipped when the two boxes are equal, which is the case for
       every committed fixture.

3. Any flag quarantines. The runtime treats one flag from any detector exactly
   as it treats a render-mode-3 flag: the run stops before any model call.
4. Visible text. Each page report also carries the text of the spans that no
   detector flagged. On a page with no flag that string is byte-identical to
   ``page.get_text()``, so a clean page is reported exactly as the verification
   gate reads it.
5. A flagged page is a disclosure, not a verdict. The screen states that the
   text layer disagrees with the visible page and shows the disagreeing
   evidence. It does not decide why it is there.
6. Determinism. The same bytes always produce the same report. The recorded
   path is resolved, so the same file addressed by a relative and an absolute
   path produces equal reports. Nothing in the report depends on a clock, a
   random value, or a model.

WHAT THIS SCREEN DOES NOT DETECT

- White, or near-background, text over an unknown background. Deciding that a
  fill colour hides text needs the colour of whatever is painted behind it,
  which needs a raster comparison this screen does not make. Real cut sheets
  set white text on dark header boxes, so a colour heuristic here would
  quarantine honest documents.
  ``tests/test_integrity.py::test_white_text_on_a_white_background_is_not_detected``
  pins the gap.
- Text under a covering shape. A rectangle drawn over painted text conceals it,
  and redaction bars in real submittals do exactly that on purpose. Separating
  the two needs the same raster comparison, so this screen reports neither.
  ``tests/test_integrity.py::test_text_under_a_covering_rectangle_is_not_detected``
  pins the gap.
- Rasterized text. Text drawn as an image carries no span and no render mode.
  This screen performs no OCR, by design.
  ``tests/test_integrity.py::test_rasterized_text_is_not_detected`` pins the
  gap.
- Text outside the media box. MuPDF drops those glyphs from every extraction
  path this screen can reach, including the widened-crop-box pass, so the
  screen cannot report them.
  ``tests/test_integrity.py::test_text_outside_the_media_box_is_not_detected``
  pins the gap.
- Intent. A flagged page is evidence, not a motive.

The screen narrows the gap between the text layer and the visible page. It does
not close it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pymupdf
from pydantic import BaseModel, ConfigDict, Field, computed_field

from specguard.models import DocumentRole

#: Versioned identity of this screen, stored beside every integrity record so a
#: later reader knows which rule set produced the evidence. ``v1`` was the
#: render-mode-3 rule alone; ``v2`` is the five-detector set described above.
SCREEN_ID = "text_layer_integrity_v2"

#: Machine-readable reason a run stops before the model reads any text.
QUARANTINE_REASON = "text_layer_integrity_screen"

#: Detector names. Every flag carries exactly one of these.
DETECTOR_RENDER_MODE_3 = "render_mode_3"
DETECTOR_ZERO_ALPHA = "zero_alpha"
DETECTOR_SUB_VISIBLE_GLYPH = "sub_visible_glyph"
DETECTOR_CONTENT_STREAM_RENDER_MODE = "content_stream_render_mode"
DETECTOR_OUT_OF_CROP_BOX = "out_of_crop_box"

#: Every detector, in the order the screen runs them.
DETECTORS = (
    DETECTOR_RENDER_MODE_3,
    DETECTOR_ZERO_ALPHA,
    DETECTOR_SUB_VISIBLE_GLYPH,
    DETECTOR_CONTENT_STREAM_RENDER_MODE,
    DETECTOR_OUT_OF_CROP_BOX,
)

#: MuPDF ``fz_stext_char`` flag bits, mirrored here so the detection rules read
#: as rules rather than as magic numbers. ``test_char_flag_bits_match_mupdf``
#: asserts these against ``pymupdf.mupdf.FZ_STEXT_FILLED``,
#: ``pymupdf.mupdf.FZ_STEXT_STROKED``, and ``pymupdf.mupdf.FZ_STEXT_CLIP``.
CHAR_FLAG_FILLED = 16
CHAR_FLAG_STROKED = 32
CHAR_FLAG_CLIPPED = 64

#: A character that is neither filled nor stroked paints nothing.
PAINTING_CHAR_FLAGS = CHAR_FLAG_FILLED | CHAR_FLAG_STROKED

#: Fill alpha value MuPDF reports for a span the graphics state made fully
#: transparent. MuPDF reports alpha on a 0-255 scale.
TRANSPARENT_ALPHA = 0

#: A glyph smaller than this is not readable at any normal viewing scale. A
#: span at exactly this size is not flagged.
MINIMUM_READABLE_POINT_SIZE = 1.0

#: PDF text render modes that show no ink of their own. Mode 3 paints nothing;
#: mode 7 only adds the glyph outlines to the clipping path.
HIDING_RENDER_MODES = (3, 7)

#: How deep the content-stream scan follows ``Do`` into nested Form XObjects.
MAXIMUM_XOBJECT_DEPTH = 8

#: Coordinates are rounded so a persisted report is stable and readable.
BBOX_DECIMALS = 2


class HiddenSpan(BaseModel):
    """One piece of evidence that the text layer disagrees with the page.

    ``detector`` names the rule that produced the flag and ``evidence`` states
    in one line what that rule read. ``char_flags`` is the raw MuPDF value,
    kept so a reviewer can check a span rule against its own evidence rather
    than trust it. A rule that reads a content stream rather than a span
    carries no rectangle and no character flags.
    """

    model_config = ConfigDict(frozen=True)

    page_number: int = Field(ge=1, description="One-based page the evidence sits on.")
    detector: str = Field(
        default=DETECTOR_RENDER_MODE_3, min_length=1, description="Rule that produced this flag."
    )
    evidence: str = Field(default="", description="One line stating what the rule read.")
    text: str = Field(description="Text exactly as the text layer carries it.")
    font: str = Field(default="", description="Font name recorded for the span.")
    size: float = Field(default=0.0, description="Effective font size recorded for the span.")
    char_flags: int = Field(
        default=0, ge=0, description="MuPDF character flags read by the detection rule."
    )
    bbox: tuple[float, float, float, float] | None = Field(
        default=None,
        description="Span rectangle on the page, rounded for a stable report, or null.",
    )


class PageIntegrityReport(BaseModel):
    """The screening result for one page."""

    model_config = ConfigDict(frozen=True)

    page_number: int = Field(ge=1, description="One-based page number.")
    hidden_spans: list[HiddenSpan] = Field(
        default_factory=list, description="Every detector flag raised on this page, in rule order."
    )
    visible_text: str = Field(description="Text of the spans no detector flagged.")

    @property
    def flagged(self) -> bool:
        """True when this page carries at least one detector flag."""
        return bool(self.hidden_spans)

    @property
    def detectors(self) -> list[str]:
        """Names of the rules that flagged this page, in rule order."""
        return [name for name in DETECTORS if any(s.detector == name for s in self.hidden_spans)]


class DocumentIntegrityReport(BaseModel):
    """The screening result for one document, one entry per page."""

    model_config = ConfigDict(frozen=True)

    pdf_path: str = Field(min_length=1, description="Path of the document the screen read.")
    sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$",
        description="Lowercase hex SHA-256 of the screened byte stream. Chain of custody only.",
    )
    page_count: int = Field(ge=0, description="Number of pages the screen read.")
    pages: list[PageIntegrityReport] = Field(
        default_factory=list, description="One report per page, in page order."
    )

    @computed_field
    @property
    def flagged_pages(self) -> list[int]:
        """One-based page numbers that carry at least one detector flag."""
        return [page.page_number for page in self.pages if page.flagged]

    @computed_field
    @property
    def detectors(self) -> list[str]:
        """Names of the rules that flagged this document, in rule order."""
        return [name for name in DETECTORS if any(s.detector == name for s in self.hidden_spans)]

    @computed_field
    @property
    def clean(self) -> bool:
        """True when no page carries a detector flag."""
        return not [page for page in self.pages if page.flagged]

    @property
    def hidden_spans(self) -> list[HiddenSpan]:
        """Every detector flag in the document, in page order."""
        return [span for page in self.pages for span in page.hidden_spans]


class PersistedIntegrityFinding(BaseModel):
    """The record type written when the screen flags a document.

    This record has its own collection and its own shape. It is not a claim
    finding: no model proposed it, no model may edit it, and it carries no
    quote the verification gate ever saw. Every field is copied from the
    deterministic screen output at write time.
    """

    model_config = ConfigDict(frozen=True)

    integrity_finding_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    screen_id: str = Field(default=SCREEN_ID, min_length=1)
    document_role: DocumentRole
    document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_count: int = Field(ge=1)
    flagged_pages: list[int] = Field(min_length=1)
    detectors: list[str] = Field(default_factory=list)
    hidden_spans: list[HiddenSpan] = Field(min_length=1)


def _is_invisible(char_flags: int) -> bool:
    """True when a span's characters are neither filled nor stroked."""
    return char_flags & PAINTING_CHAR_FLAGS == 0


def _rounded_bbox(bbox: object) -> tuple[float, float, float, float]:
    """Round one span rectangle so a persisted report is stable."""
    values = tuple(round(float(value), BBOX_DECIMALS) for value in bbox)  # type: ignore[union-attr]
    return (values[0], values[1], values[2], values[3])


def _text_spans(page: pymupdf.Page) -> Iterator[dict]:
    """Yield every text span on one page, in extraction order."""
    for block in page.get_text("dict")["blocks"]:
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            yield from line["spans"]


# --------------------------------------------------------------------------
# Content stream scanning
# --------------------------------------------------------------------------

_WHITESPACE = b"\x00\t\n\x0c\r "
_DELIMITERS = b"()<>[]{}/%"
_SEPARATORS = _WHITESPACE + _DELIMITERS

_ESCAPES = {
    ord("n"): b"\n",
    ord("r"): b"\r",
    ord("t"): b"\t",
    ord("b"): b"\b",
    ord("f"): b"\f",
    ord("("): b"(",
    ord(")"): b")",
    ord("\\"): b"\\",
}


def _read_literal_string(data: bytes, start: int) -> tuple[bytes, int]:
    """Read one ``( ... )`` string, honouring escapes and nested parentheses."""
    out = bytearray()
    depth = 1
    index = start + 1
    while index < len(data) and depth:
        byte = data[index]
        if byte == ord("\\"):
            index += 1
            if index >= len(data):
                break
            following = data[index]
            if following in _ESCAPES:
                out += _ESCAPES[following]
                index += 1
            elif ord("0") <= following <= ord("7"):
                digits = bytearray()
                while index < len(data) and len(digits) < 3 and ord("0") <= data[index] <= ord("7"):
                    digits.append(data[index])
                    index += 1
                out.append(int(digits, 8) & 0xFF)
            else:
                out.append(following)
                index += 1
            continue
        if byte == ord("("):
            depth += 1
        elif byte == ord(")"):
            depth -= 1
            if not depth:
                index += 1
                break
        out.append(byte)
        index += 1
    return bytes(out), index


def _read_hex_string(data: bytes, start: int) -> tuple[bytes, int]:
    """Read one ``< ... >`` hexadecimal string."""
    digits = bytearray()
    index = start + 1
    while index < len(data) and data[index] != ord(">"):
        byte = data[index]
        if chr(byte) in "0123456789abcdefABCDEF":
            digits.append(byte)
        index += 1
    if len(digits) % 2:
        digits.append(ord("0"))
    return bytes.fromhex(digits.decode("ascii")), index + 1


def _skip_inline_image(data: bytes, start: int) -> int:
    """Return the offset just past one ``BI ... ID <bytes> EI`` block.

    Inline image data is arbitrary bytes. Tokenizing it would let a crafted
    image body forge operators, so the scan steps over the whole block.
    """
    marker = data.find(b"ID", start)
    if marker < 0:
        return len(data)
    index = marker + 3
    while index < len(data):
        end = data.find(b"EI", index)
        if end < 0:
            return len(data)
        before_is_space = end == 0 or data[end - 1] in _WHITESPACE
        after = data[end + 2 : end + 3]
        if before_is_space and (not after or after[0] in _WHITESPACE):
            return end + 2
        index = end + 2
    return len(data)


def _tokenize(data: bytes) -> Iterator[tuple[str, object]]:
    """Yield ``(kind, value)`` tokens for one PDF content stream."""
    index = 0
    length = len(data)
    while index < length:
        byte = data[index]
        if byte in _WHITESPACE:
            index += 1
            continue
        if byte == ord("%"):
            while index < length and data[index] not in b"\r\n":
                index += 1
            continue
        if byte == ord("("):
            value, index = _read_literal_string(data, index)
            yield ("string", value)
            continue
        if byte == ord("<"):
            if data[index + 1 : index + 2] == b"<":
                index += 2
                continue
            value, index = _read_hex_string(data, index)
            yield ("string", value)
            continue
        if byte == ord(">"):
            index += 2 if data[index + 1 : index + 2] == b">" else 1
            continue
        if byte == ord("/"):
            end = index + 1
            while end < length and data[end] not in _SEPARATORS:
                end += 1
            yield ("name", data[index + 1 : end].decode("latin-1"))
            index = end
            continue
        if byte == ord("["):
            yield ("array_open", None)
            index += 1
            continue
        if byte == ord("]"):
            yield ("array_close", None)
            index += 1
            continue
        if byte in b"{}":
            index += 1
            continue
        end = index
        while end < length and data[end] not in _SEPARATORS:
            end += 1
        token = data[index:end].decode("latin-1")
        index = end if end > index else index + 1
        if token == "BI":
            index = _skip_inline_image(data, index)
            continue
        yield ("token", token)


def _is_number(token: object) -> bool:
    """True when a token is a PDF numeric object."""
    if not isinstance(token, str):
        return False
    try:
        float(token)
    except ValueError:
        return False
    return True


def _readable(data: bytes) -> str:
    """Render one content-stream string operand as readable evidence.

    The bytes are the font's own codes. They are decoded as Latin-1, which is
    exact for the simple encodings a generated cut sheet uses and approximate
    for a subset-encoded font. The claim this detector makes is the render
    mode, which is read from the operator, not from these bytes.
    """
    return "".join(char if char.isprintable() else " " for char in data.decode("latin-1")).strip()


class _RenderModeScan:
    """Track the ``Tr`` operator through a page's content streams."""

    def __init__(self, document: pymupdf.Document) -> None:
        self._document = document

    def shown_text_by_mode(self, page: pymupdf.Page) -> dict[int, list[str]]:
        """Return the text shown under each hiding render mode, in mode order."""
        found: dict[int, list[str]] = {}
        resources = {name: xref for xref, name, *_ in page.get_xobjects()}
        self._scan(page.read_contents(), resources, 0, 0, set(), found)
        return {mode: found[mode] for mode in HIDING_RENDER_MODES if found.get(mode)}

    def _xobject_resources(self, xref: int) -> dict[str, int]:
        """Map every Form XObject name a stream can invoke to its xref."""
        kind, value = self._document.xref_get_key(xref, "Resources/XObject")
        if kind == "xref":
            value = self._document.xref_object(int(value.split()[0]), compressed=True)
        elif kind != "dict":
            return {}
        names: dict[str, int] = {}
        for entry in value.strip("<>").split("/")[1:]:
            parts = entry.split()
            if len(parts) >= 3 and parts[-1] == "R" and parts[1].isdigit():
                names[parts[0]] = int(parts[1])
        return names

    def _is_form(self, xref: int) -> bool:
        return self._document.xref_get_key(xref, "Subtype")[1] == "/Form"

    def _scan(
        self,
        data: bytes,
        resources: dict[str, int],
        render_mode: int,
        depth: int,
        path: set[int],
        found: dict[int, list[str]],
    ) -> None:
        """Interpret one content stream's render-mode and text-showing operators."""
        saved: list[int] = []
        operands: list[object] = []
        array: list[bytes] | None = None
        for kind, value in _tokenize(data):
            if kind == "array_open":
                array = []
                continue
            if kind == "array_close":
                operands.append(b"".join(array or []))
                array = None
                continue
            if kind == "string":
                if array is None:
                    operands.append(value)
                else:
                    array.append(value)  # type: ignore[arg-type]
                continue
            if kind == "name":
                operands.append(f"/{value}")
                continue
            if _is_number(value):
                operands.append(value)
                continue
            operator = value
            if operator == "q":
                saved.append(render_mode)
            elif operator == "Q":
                render_mode = saved.pop() if saved else render_mode
            elif operator == "Tr" and operands and _is_number(operands[-1]):
                render_mode = int(float(str(operands[-1])))
            elif operator in ("Tj", "TJ", "'", '"'):
                shown = operands[-1] if operands and isinstance(operands[-1], bytes) else None
                if shown is not None and render_mode in HIDING_RENDER_MODES:
                    text = _readable(shown)
                    if text:
                        found.setdefault(render_mode, []).append(text)
            elif operator == "Do" and depth < MAXIMUM_XOBJECT_DEPTH:
                self._follow(operands, resources, render_mode, depth, path, found)
            operands = []

    def _follow(
        self,
        operands: list[object],
        resources: dict[str, int],
        render_mode: int,
        depth: int,
        path: set[int],
        found: dict[int, list[str]],
    ) -> None:
        """Descend into one invoked Form XObject with the caller's render mode."""
        name = operands[-1] if operands and isinstance(operands[-1], str) else None
        if not name or not name.startswith("/"):
            return
        xref = resources.get(name[1:])
        if xref is None or xref in path or not self._is_form(xref):
            return
        try:
            stream = self._document.xref_stream(xref)
        except (RuntimeError, ValueError):
            return
        if stream is None:
            return
        path.add(xref)
        self._scan(stream, self._xobject_resources(xref), render_mode, depth + 1, path, found)
        path.discard(xref)


# --------------------------------------------------------------------------
# Per-page detectors
# --------------------------------------------------------------------------


def _span_flags(span: dict, page_number: int) -> list[HiddenSpan]:
    """Run every span-level rule over one span, in rule order."""
    char_flags = int(span.get("char_flags", 0))
    alpha = int(span.get("alpha", 255))
    size = float(span.get("size", 0.0))
    common = {
        "page_number": page_number,
        "text": span["text"],
        "font": str(span.get("font", "")),
        "size": round(size, BBOX_DECIMALS),
        "char_flags": char_flags,
        "bbox": _rounded_bbox(span["bbox"]),
    }
    flags: list[HiddenSpan] = []
    if _is_invisible(char_flags):
        flags.append(
            HiddenSpan(
                **common,
                detector=DETECTOR_RENDER_MODE_3,
                evidence=(
                    f"Characters are neither filled nor stroked (char_flags={char_flags}), "
                    "which is PDF text render mode 3."
                ),
            )
        )
    elif (
        char_flags & CHAR_FLAG_FILLED
        and not char_flags & CHAR_FLAG_STROKED
        and not char_flags & CHAR_FLAG_CLIPPED
        and alpha == TRANSPARENT_ALPHA
    ):
        flags.append(
            HiddenSpan(
                **common,
                detector=DETECTOR_ZERO_ALPHA,
                evidence=(
                    "The span is filled and not stroked, and the graphics state sets its "
                    f"fill alpha to {alpha} of 255, so the fill paints nothing."
                ),
            )
        )
    if size < MINIMUM_READABLE_POINT_SIZE:
        flags.append(
            HiddenSpan(
                **common,
                detector=DETECTOR_SUB_VISIBLE_GLYPH,
                evidence=(
                    f"Effective glyph size is {round(size, BBOX_DECIMALS)} pt, below the "
                    f"{MINIMUM_READABLE_POINT_SIZE} pt floor. MuPDF reports this size after "
                    "the text matrix is applied."
                ),
            )
        )
    return flags


def _clip_only_spans(page: pymupdf.Page) -> list[dict]:
    """Return the spans MuPDF recorded as clipped with no painted twin.

    Render modes 4, 5, and 6 both paint and clip, and MuPDF records two spans
    for them: one painted, one clipped, with the same text and the same
    rectangle. Mode 7 clips without painting, so its clipped span stands alone.
    An unpaired clipped span is therefore mode-7 text, and its own text is
    exact, which the raw content-stream operand is not.
    """
    painted = {
        (span["text"], _rounded_bbox(span["bbox"]))
        for span in _text_spans(page)
        if span.get("char_flags", 0) & PAINTING_CHAR_FLAGS
        and not span.get("char_flags", 0) & CHAR_FLAG_CLIPPED
    }
    return [
        span
        for span in _text_spans(page)
        if span.get("char_flags", 0) & CHAR_FLAG_CLIPPED
        and (span["text"], _rounded_bbox(span["bbox"])) not in painted
    ]


def _content_stream_flags(
    scan: _RenderModeScan, page: pymupdf.Page, page_number: int, span_flagged_mode_3: bool
) -> tuple[list[HiddenSpan], set[tuple[str, tuple[float, float, float, float]]]]:
    """Flag text the content stream shows under a hiding render mode.

    The content stream is what proves the mode. Mode 7 also leaves an unpaired
    clipped span, so each such span becomes its own flag and carries the exact
    text; when no such span exists the flag falls back to the raw operand. A
    mode-3 flag is suppressed when the span rule already reported this page,
    because both rules then describe the same concealment and the span rule
    carries the richer evidence.

    The second return value names the clip-only spans, so the caller can keep
    them out of the page's readable text.
    """
    flags: list[HiddenSpan] = []
    concealed: set[tuple[str, tuple[float, float, float, float]]] = set()
    for mode, shown in scan.shown_text_by_mode(page).items():
        if mode == 3 and span_flagged_mode_3:
            continue
        description = "paints nothing" if mode == 3 else "only adds the glyphs to the clip path"
        evidence = (
            f"The content stream shows {len(shown)} text operand(s) under PDF text render "
            f"mode {mode}, which {description}."
        )
        spans = _clip_only_spans(page) if mode == 7 else []
        if not spans:
            flags.append(
                HiddenSpan(
                    page_number=page_number,
                    detector=DETECTOR_CONTENT_STREAM_RENDER_MODE,
                    evidence=evidence,
                    text=" ".join(shown),
                )
            )
            continue
        for span in spans:
            bbox = _rounded_bbox(span["bbox"])
            concealed.add((span["text"], bbox))
            flags.append(
                HiddenSpan(
                    page_number=page_number,
                    detector=DETECTOR_CONTENT_STREAM_RENDER_MODE,
                    evidence=(
                        f"{evidence} MuPDF records this span as clipped with no painted twin, "
                        "which no character flag can separate from painted text."
                    ),
                    text=span["text"],
                    font=str(span.get("font", "")),
                    size=round(float(span.get("size", 0.0)), BBOX_DECIMALS),
                    char_flags=int(span.get("char_flags", 0)),
                    bbox=bbox,
                )
            )
    return flags, concealed


def _screen_crop_boxes(data: bytes) -> dict[int, list[HiddenSpan]]:
    """Re-read every cropped page with its crop box widened to its media box.

    MuPDF clips text extraction to the crop box, so a glyph placed in the
    margin outside it is absent from every normal extraction. This pass opens a
    second document from the same immutable byte snapshot, widens the crop box
    of each page whose two boxes differ, and flags any span whose rectangle
    does not intersect the original crop box. Both rectangles are in the page
    coordinates PyMuPDF reports relative to the media box, so no mapping is
    needed. A document whose pages are all uncropped costs one parse and no
    extraction, and every committed fixture is such a document.
    """
    found: dict[int, list[HiddenSpan]] = {}
    with pymupdf.open(stream=data, filetype="pdf") as document:
        for index, page in enumerate(document):
            crop = pymupdf.Rect(page.cropbox)
            if crop == pymupdf.Rect(page.mediabox):
                continue
            page.set_cropbox(pymupdf.Rect(page.mediabox))
            flags: list[HiddenSpan] = []
            for span in _text_spans(page):
                rectangle = pymupdf.Rect(span["bbox"])
                if rectangle.intersects(crop):
                    continue
                flags.append(
                    HiddenSpan(
                        page_number=index + 1,
                        detector=DETECTOR_OUT_OF_CROP_BOX,
                        evidence=(
                            f"The span rectangle {_rounded_bbox(rectangle)} does not intersect "
                            f"the crop box {_rounded_bbox(crop)}, so a reader of the cropped "
                            "page never sees it."
                        ),
                        text=span["text"],
                        font=str(span.get("font", "")),
                        size=round(float(span.get("size", 0.0)), BBOX_DECIMALS),
                        char_flags=int(span.get("char_flags", 0)),
                        bbox=_rounded_bbox(rectangle),
                    )
                )
            if flags:
                found[index + 1] = flags
    return found


def _screen_page(
    scan: _RenderModeScan,
    page: pymupdf.Page,
    page_number: int,
    crop_flags: list[HiddenSpan],
) -> PageIntegrityReport:
    """Run every detector over one page and separate its readable text."""
    span_flags: list[HiddenSpan] = []
    hidden_by_span_rule: set[tuple[str, tuple[float, float, float, float]]] = set()

    for span in _text_spans(page):
        flags = _span_flags(span, page_number)
        if flags:
            span_flags.extend(flags)
            hidden_by_span_rule.add((span["text"], _rounded_bbox(span["bbox"])))

    ordered = [
        flag
        for name in (DETECTOR_RENDER_MODE_3, DETECTOR_ZERO_ALPHA, DETECTOR_SUB_VISIBLE_GLYPH)
        for flag in span_flags
        if flag.detector == name
    ]
    mode_3_seen = any(flag.detector == DETECTOR_RENDER_MODE_3 for flag in ordered)
    content_flags, concealed = _content_stream_flags(scan, page, page_number, mode_3_seen)
    ordered.extend(content_flags)
    ordered.extend(crop_flags)

    unreadable = hidden_by_span_rule | concealed
    visible_lines: list[str] = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            visible_lines.append(
                "".join(
                    span["text"]
                    for span in line["spans"]
                    if (span["text"], _rounded_bbox(span["bbox"])) not in unreadable
                )
            )

    visible_text = "".join(f"{line}\n" for line in visible_lines)
    return PageIntegrityReport(
        page_number=page_number,
        hidden_spans=ordered,
        visible_text=visible_text,
    )


def check_text_layer(pdf_path: str | Path) -> DocumentIntegrityReport:
    """Screen every page of ``pdf_path`` with every detector.

    The report names each flag, the detector that raised it, its page, its
    evidence, and the readable text of that page. Every value comes from one
    immutable byte snapshot, so the reported SHA-256, page count, and evidence
    always describe the same bytes even if the file is replaced during the
    screen. Nothing here calls a model, and no model may author the result.
    """
    path = Path(pdf_path).resolve()
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    pages: list[PageIntegrityReport] = []
    crop_flags = _screen_crop_boxes(data)
    with pymupdf.open(stream=data, filetype="pdf") as document:
        page_count = document.page_count
        scan = _RenderModeScan(document)
        for index, page in enumerate(document, start=1):
            pages.append(_screen_page(scan, page, index, crop_flags.get(index, [])))
    return DocumentIntegrityReport(
        pdf_path=str(path),
        sha256=digest,
        page_count=page_count,
        pages=pages,
    )
