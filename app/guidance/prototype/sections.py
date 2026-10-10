"""Pure logic for splitting a prototype guide's Markdown into its sections.

A prototype guide is one content.md, not a file per section as the guidance
pipeline writes. A section starts at a heading of two or more hashes
("## 1 Background", "### 2.1 Allocating a Quality Check") and runs to the next
such heading of any level, so a section holds its own text and not its
subsections', as a parsed section file does. These are the headings the
manifest counts as sections. The title ("# ...") and anything before the first
section belong to no section.

A section is known by its dotted number. Some headings have none ("## Annex A
– Case Types"); such a section is known by its heading as a slug instead
("annex-a-case-types"), which is as good in a path.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Section:
    """One section of a guide."""

    number: str
    heading: str
    # 1 for a top-level section: one less than its hashes, because "#" is the
    # guide's title.
    level: int
    markdown: str


@dataclass(frozen=True)
class _Heading:
    """A section heading line, and where it starts in the guide."""

    start: int
    hashes: int
    text: str


def split(markdown: str) -> list[Section]:
    """Split a guide into its sections, in document order.

    Args:
        markdown: The guide's whole content.md.

    Returns:
        Each section with its heading line and its own text.
    """
    headings = _headings(markdown)
    ends = [heading.start for heading in headings[1:]] + [len(markdown)]

    return [
        _section(heading, markdown[heading.start : end])
        for heading, end in zip(headings, ends, strict=False)
    ]


def find(markdown: str, number: str) -> Section | None:
    """Return the section with this number, or None if the guide has none.

    Args:
        markdown: The guide's whole content.md.
        number: The section's dotted number, e.g. "2.1", or the slug of an
            unnumbered section's heading.

    Returns:
        The section, or None.
    """
    return next((s for s in split(markdown) if s.number == number), None)


def _headings(markdown: str) -> list[_Heading]:
    """Every section heading in the guide, with where its line starts."""
    headings = []
    start = 0

    for line in markdown.split("\n"):
        heading = _heading(line, start)
        if heading is not None:
            headings.append(heading)
        start += len(line) + 1

    return headings


def _heading(line: str, start: int) -> _Heading | None:
    """The line as a section heading, or None if it is not one.

    A section heading is two or more hashes, a space or tab, then some text.
    """
    hashes = len(line) - len(line.lstrip("#"))
    rest = line[hashes:]

    if hashes < 2 or not rest[:1].isspace() or not rest.strip():
        return None

    return _Heading(start=start, hashes=hashes, text=rest.strip())


def _section(heading: _Heading, markdown: str) -> Section:
    """Build a section from its heading and its own text."""
    number, heading_text = _split_number(heading.text)

    return Section(
        number=number or _slug(heading_text),
        heading=heading_text,
        level=heading.hashes - 1,
        markdown=markdown.rstrip() + "\n",
    )


def _split_number(text: str) -> tuple[str | None, str]:
    """A heading's dotted number and the rest of its text.

    The number is the first word, if it is digits separated by dots and other
    words follow it ("2.1 Allocating"); otherwise the heading has no number.
    """
    words = text.split(maxsplit=1)

    if len(words) == 2 and _is_dotted_number(words[0]):
        return words[0], words[1]

    return None, text


def _is_dotted_number(word: str) -> bool:
    """True for "2", "2.1" or "2.1.1": digits separated by single dots."""
    return all(part.isascii() and part.isdigit() for part in word.split("."))


def _slug(heading: str) -> str:
    """The heading as lowercase letters and digits, hyphenated."""
    kept = "".join(
        char if char.isascii() and char.isalnum() else " " for char in heading.lower()
    )
    return "-".join(kept.split())
