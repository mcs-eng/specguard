"""Deterministic text-layer integrity screen for untrusted documents.

SCREENING CONTRACT

A submitted PDF is untrusted input. Its text layer is what an automated
reviewer ingests; the painted page is what a human reviewer reads. The two can
disagree. This screen reports one specific disagreement: text that the PDF
render mode keeps off the page while leaving it in the text layer.

1. Source data. The screen reads the file's bytes once, into one immutable
   snapshot, and derives everything from that snapshot: the SHA-256, the page
   count, and the span evidence. A replacement of the file during a screen
   therefore cannot make the hash describe one byte stream while the evidence
   describes another. The screen reads PyMuPDF span data only, through
   ``page.get_text("dict")``. It renders no image, runs no OCR, and compares no
   pixels. It reads every page of the document.
2. Detection rule. A PDF text-showing operator carries a render mode (``Tr``).
   MuPDF records the outcome of that mode on every character as ``char_flags``.
   A span is reported as invisible when its characters are neither filled nor
   stroked, so the reader is shown nothing while the text layer still carries
   the characters. That is render mode 3, the mode an OCR layer uses over a
   scanned image.
3. Visible text. Each page report also carries the text of the spans that were
   filled or stroked, in extraction order. On a page with no invisible span
   that string is byte-identical to ``page.get_text()``, so a clean page is
   reported exactly as the verification gate reads it.
4. A flagged page is a disclosure, not a verdict. The screen states that the
   text layer disagrees with the visible page and shows the disagreeing spans.
   It does not decide why they are there.
5. Determinism. The same bytes always produce the same report. The recorded
   path is resolved, so the same file addressed by a relative and an absolute
   path produces equal reports. Nothing in the report depends on a clock, a
   random value, or a model.

WHAT THIS SCREEN DOES NOT DETECT

- Rasterized text. Text drawn as an image carries no span and no render mode.
- Text hidden by other means: a fill colour matching the background, a zero
  alpha set through the graphics state, a glyph drawn outside the crop box, or
  a covering rectangle drawn over painted text.
- Clip-only render mode 7. MuPDF reports the same ``char_flags`` for a
  clip-only span as for a filled-and-clipped span, so this screen cannot
  separate the two and reports neither.
  ``tests/test_integrity.py::test_clip_only_render_mode_is_a_known_limitation``
  pins that gap so it cannot quietly disappear.

The screen narrows the gap between the text layer and the visible page. It
does not close it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pymupdf
from pydantic import BaseModel, ConfigDict, Field, computed_field

from specguard.models import DocumentRole

#: Versioned identity of this screen, stored beside every integrity record so a
#: later reader knows which rule produced the evidence.
SCREEN_ID = "text_layer_render_mode_v1"

#: Machine-readable reason a run stops before the model reads any text.
QUARANTINE_REASON = "text_layer_integrity_screen"

#: MuPDF ``fz_stext_char`` flag bits, mirrored here so the detection rule reads
#: as a rule rather than as a magic number. ``test_char_flag_bits_match_mupdf``
#: asserts these against ``pymupdf.mupdf.FZ_STEXT_FILLED`` and
#: ``pymupdf.mupdf.FZ_STEXT_STROKED``.
CHAR_FLAG_FILLED = 16
CHAR_FLAG_STROKED = 32

#: A character that is neither filled nor stroked paints nothing.
PAINTING_CHAR_FLAGS = CHAR_FLAG_FILLED | CHAR_FLAG_STROKED

#: Coordinates are rounded so a persisted report is stable and readable.
BBOX_DECIMALS = 2


class HiddenSpan(BaseModel):
    """One span the screen reports as invisible to a human reader.

    ``char_flags`` is the raw MuPDF value the detection rule read. It is kept
    so a reviewer can check the rule against the evidence rather than trust it.
    """

    model_config = ConfigDict(frozen=True)

    page_number: int = Field(ge=1, description="One-based page the span sits on.")
    text: str = Field(description="Span text exactly as the text layer carries it.")
    font: str = Field(description="Font name recorded for the span.")
    size: float = Field(description="Font size recorded for the span.")
    char_flags: int = Field(ge=0, description="MuPDF character flags read by the detection rule.")
    bbox: tuple[float, float, float, float] = Field(
        description="Span rectangle on the page, rounded for a stable report."
    )


class PageIntegrityReport(BaseModel):
    """The screening result for one page."""

    model_config = ConfigDict(frozen=True)

    page_number: int = Field(ge=1, description="One-based page number.")
    hidden_spans: list[HiddenSpan] = Field(
        default_factory=list, description="Spans the render mode keeps off the visible page."
    )
    visible_text: str = Field(description="Text of the spans that are filled or stroked.")

    @property
    def flagged(self) -> bool:
        """True when this page carries at least one invisible span."""
        return bool(self.hidden_spans)


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
        """One-based page numbers that carry at least one invisible span."""
        return [page.page_number for page in self.pages if page.flagged]

    @computed_field
    @property
    def clean(self) -> bool:
        """True when no page carries an invisible span."""
        return not [page for page in self.pages if page.flagged]

    @property
    def hidden_spans(self) -> list[HiddenSpan]:
        """Every invisible span in the document, in page order."""
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
    hidden_spans: list[HiddenSpan] = Field(min_length=1)


def _is_invisible(char_flags: int) -> bool:
    """True when a span's characters are neither filled nor stroked."""
    return char_flags & PAINTING_CHAR_FLAGS == 0


def _screen_page(page: pymupdf.Page, page_number: int) -> PageIntegrityReport:
    """Separate one page's invisible spans from its visible text."""
    hidden_spans: list[HiddenSpan] = []
    visible_lines: list[str] = []

    for block in page.get_text("dict")["blocks"]:
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            visible_parts: list[str] = []
            for span in line["spans"]:
                char_flags = int(span.get("char_flags", 0))
                if _is_invisible(char_flags):
                    hidden_spans.append(
                        HiddenSpan(
                            page_number=page_number,
                            text=span["text"],
                            font=str(span.get("font", "")),
                            size=round(float(span.get("size", 0.0)), BBOX_DECIMALS),
                            char_flags=char_flags,
                            bbox=tuple(
                                round(float(value), BBOX_DECIMALS) for value in span["bbox"]
                            ),
                        )
                    )
                    continue
                visible_parts.append(span["text"])
            visible_lines.append("".join(visible_parts))

    visible_text = "".join(f"{line}\n" for line in visible_lines)
    return PageIntegrityReport(
        page_number=page_number,
        hidden_spans=hidden_spans,
        visible_text=visible_text,
    )


def check_text_layer(pdf_path: str | Path) -> DocumentIntegrityReport:
    """Screen every page of ``pdf_path`` for text hidden by render mode.

    The report names each invisible span, its page, and the visible text of
    that page. Every value comes from one immutable byte snapshot, so the
    reported SHA-256, page count, and span evidence always describe the same
    bytes even if the file is replaced during the screen. Nothing here calls a
    model, and no model may author the result.
    """
    path = Path(pdf_path).resolve()
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    pages: list[PageIntegrityReport] = []
    with pymupdf.open(stream=data, filetype="pdf") as document:
        page_count = document.page_count
        for index, page in enumerate(document, start=1):
            pages.append(_screen_page(page, index))
    return DocumentIntegrityReport(
        pdf_path=str(path),
        sha256=digest,
        page_count=page_count,
        pages=pages,
    )
