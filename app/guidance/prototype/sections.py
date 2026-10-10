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

import re
from dataclasses import dataclass

_SECTION_HEADING = re.compile(
    r"^(?P<hashes>#{2,})\s+(?:(?P<number>\d+(?:\.\d+)*)\s+)?(?P<heading>.+?)\s*$",
    re.MULTILINE,
)

_NOT_SLUG = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Section:
    """One section of a guide."""

    number: str
    heading: str
    # 1 for a top-level section: one less than its hashes, because "#" is the
    # guide's title.
    level: int
    markdown: str


def split(markdown: str) -> list[Section]:
    """Split a guide into its sections, in document order.

    Args:
        markdown: The guide's whole content.md.

    Returns:
        Each section with its heading line and its own text.
    """
    headings = list(_SECTION_HEADING.finditer(markdown))
    ends = [heading.start() for heading in headings[1:]] + [len(markdown)]

    return [
        Section(
            number=heading["number"] or _slug(heading["heading"]),
            heading=heading["heading"],
            level=len(heading["hashes"]) - 1,
            markdown=markdown[heading.start() : end].rstrip() + "\n",
        )
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


def _slug(heading: str) -> str:
    """The heading as lowercase letters and digits, hyphenated."""
    return _NOT_SLUG.sub("-", heading.lower()).strip("-")
