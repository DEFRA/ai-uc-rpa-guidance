"""The passage of a section that a search result shows.

The assessor marks the passage that answers the query by quoting its first and
last words. The passage itself is cut from the section's own Markdown, never
taken from what the model wrote, so its words and links are the guide's: a
link the operator follows is the one the guide gives.
"""

import textwrap


def cut(markdown: str, start: str, end: str) -> str | None:
    """The whole lines of `markdown` from the one holding `start` to the one
    holding `end`, after it.

    Whole lines, so a link, a table row or a list item is never cut in half,
    and without the indent they share, so a nested list item is not shown as
    code.

    Returns:
        The passage, or None where either quote is not in the section as given,
        or the end does not come after the start.
    """
    begins = markdown.find(start)
    if begins == -1:
        return None

    ends = markdown.find(end, begins)
    if ends == -1:
        return None

    first_line = markdown.rfind("\n", 0, begins) + 1
    last_line = markdown.find("\n", ends + len(end))
    if last_line == -1:
        last_line = len(markdown)

    return textwrap.dedent(markdown[first_line:last_line]).strip()


def link_addresses(markdown: str) -> list[str]:
    """The address of each inline link in `markdown`, in order."""
    addresses = []
    at = markdown.find("](")

    while at != -1:
        closes = markdown.find(")", at + 2)
        if closes == -1:
            break

        addresses.append(markdown[at + 2 : closes])
        at = markdown.find("](", closes)

    return addresses
