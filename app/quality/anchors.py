"""Cut a prototype guide into the parts the checker reads, and anchor quotes in them.

The checker reads one top-level section at a time, with all its subsections:
the same unit of analysis as the publishing checker. The title and anything
before the first section are not checked.

A finding quotes the text it is about. The quote is anchored by the Markdown
just before and after it, so a client can find it again in the rendered guide
and highlight it, as a Word comment highlights its range. A quote anchors
only if it appears exactly once in its part: otherwise the anchor could point
at the wrong place.
"""

from dataclasses import dataclass

from app.guidance.prototype import sections

# How much Markdown either side of a quote the anchor keeps: enough to tell
# one place from another, short enough to stay inside a sentence or two.
CONTEXT_LENGTH = 30

# Unnumbered headings of this kind are known by their first two words, as in
# "Annex A" for "Annex A – Case Types".
_LETTERED_HEADINGS = ("annex", "appendix")


@dataclass(frozen=True)
class Subsection:
    """A section of a part, and where its text starts in the part's Markdown."""

    label: str
    start: int


@dataclass(frozen=True)
class Part:
    """A top-level section with all its subsections: what the checker reads at once."""

    label: str
    markdown: str
    subsections: tuple[Subsection, ...]


@dataclass(frozen=True)
class Anchor:
    """Where a quote is: the quote, the Markdown around it, and its section."""

    prefix: str
    exact: str
    suffix: str
    section: str


def top_level_parts(markdown: str) -> list[Part]:
    """Cut a guide into its top-level sections, each with its subsections.

    The top level is the shallowest heading level in the guide, usually "##".

    Args:
        markdown: The guide's whole content.md.

    Returns:
        Each top-level part, in document order.
    """
    guide_sections = sections.split(markdown)
    if not guide_sections:
        return []

    top_level = min(section.level for section in guide_sections)
    groups: list[list[sections.Section]] = []
    for section in guide_sections:
        if not groups or section.level == top_level:
            groups.append([])
        groups[-1].append(section)

    return [_part(group) for group in groups]


def section_label(number: str, heading: str) -> str:
    """The name a reader knows a section by: "2.1", "Annex A" or its heading.

    Args:
        number: The section's dotted number, or the slug of an unnumbered heading.
        heading: The heading's text, without its number.

    Returns:
        The dotted number for a numbered section. For an annex or appendix,
        its first two words. For any other unnumbered section, its heading.
    """
    if _is_dotted_number(number):
        return number

    words = heading.split()
    if len(words) >= 2 and words[0].lower() in _LETTERED_HEADINGS:
        return f"{words[0]} {words[1].rstrip(':.-–—')}"

    return heading


def anchor(part: Part, exact: str) -> Anchor | None:
    """Anchor a quote in a part, if it appears there exactly once.

    Args:
        part: The part the quote was taken from.
        exact: The quoted Markdown.

    Returns:
        The quote with up to CONTEXT_LENGTH characters of Markdown either side
        and the label of the subsection it starts in, or None if the quote is
        empty, is not in the part, or is in it more than once.
    """
    if not exact or part.markdown.count(exact) != 1:
        return None

    start = part.markdown.index(exact)
    end = start + len(exact)

    return Anchor(
        prefix=part.markdown[max(0, start - CONTEXT_LENGTH) : start],
        exact=exact,
        suffix=part.markdown[end : end + CONTEXT_LENGTH],
        section=_subsection_at(part, start),
    )


def _part(group: list[sections.Section]) -> Part:
    """Join a top-level section and its subsections into one part."""
    subsections = []
    start = 0
    for section in group:
        subsections.append(
            Subsection(
                label=section_label(section.number, section.heading), start=start
            )
        )
        start += len(section.markdown) + 1

    return Part(
        label=subsections[0].label,
        markdown="\n".join(section.markdown for section in group),
        subsections=tuple(subsections),
    )


def _subsection_at(part: Part, position: int) -> str:
    """The label of the subsection whose text holds this position."""
    return [s.label for s in part.subsections if s.start <= position][-1]


def _is_dotted_number(word: str) -> bool:
    """True for "2", "2.1" or "2.1.1": digits separated by single dots."""
    return all(piece.isascii() and piece.isdigit() for piece in word.split("."))
