"""Bounded page windows that show a verified quote inside its cited page.

This module is a read-side view. It decides nothing and it writes nothing. It
takes the text the gate extracted from a cited page, locates the quote the gate
verified, and returns a bounded window of text around it so a human reviewer
can read the quote in place.

Two rules keep this view honest.

1. The window is built from the *normalized* page text, using
   :func:`specguard.gate.normalize`. The gate compares normalized text, so a
   window built from the raw page would show a reader something other than what
   the gate matched.
2. The occurrence this module reports is the occurrence the gate accepted. The
   token-boundary rule is not restated here; it is called from
   :mod:`specguard.gate` so that a change to the gate cannot leave this view
   describing an older rule.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from specguard import gate

#: Characters of normalized page text shown on each side of the quote.
DEFAULT_RADIUS = 180


class QuoteWindow(BaseModel):
    """One bounded window of normalized page text around a located quote."""

    model_config = ConfigDict(frozen=True)

    found: bool = Field(description="True when the quote sits on the page on token boundaries.")
    before: str = Field(default="", description="Normalized page text before the quote.")
    match: str = Field(default="", description="The normalized quote as the page carries it.")
    after: str = Field(default="", description="Normalized page text after the quote.")
    truncated_before: bool = Field(
        default=False, description="True when text was cut from the start of the window."
    )
    truncated_after: bool = Field(
        default=False, description="True when text was cut from the end of the window."
    )


def find_boundary_occurrence(normalized_page: str, normalized_quote: str) -> int | None:
    """Return the index of the occurrence the gate accepts, or ``None``.

    Every occurrence is examined in page order, exactly as
    :func:`specguard.gate.contains_on_boundaries` examines them. The boundary
    test itself is the gate's own, so this function reports the gate's answer
    rather than a second implementation of it.
    """
    if not normalized_quote:
        return None
    start = normalized_page.find(normalized_quote)
    while start != -1:
        if gate._sits_on_token_boundaries(normalized_page, normalized_quote, start):
            return start
        start = normalized_page.find(normalized_quote, start + 1)
    return None


def quote_window(page_text: str, quote: str, radius: int = DEFAULT_RADIUS) -> QuoteWindow:
    """Return a bounded window of normalized page text around ``quote``.

    Return ``QuoteWindow(found=False)`` when the gate would not accept the
    quote on this page text. This view never widens a miss into a near match.
    """
    normalized_page = gate.normalize(page_text)
    normalized_quote = gate.normalize(quote)
    start = find_boundary_occurrence(normalized_page, normalized_quote)
    if start is None:
        return QuoteWindow(found=False)

    end = start + len(normalized_quote)
    window_start = max(0, start - radius)
    window_end = min(len(normalized_page), end + radius)
    return QuoteWindow(
        found=True,
        before=normalized_page[window_start:start],
        match=normalized_page[start:end],
        after=normalized_page[end:window_end],
        truncated_before=window_start > 0,
        truncated_after=window_end < len(normalized_page),
    )
