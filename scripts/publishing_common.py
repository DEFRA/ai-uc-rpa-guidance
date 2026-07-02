"""Primitives shared by the publishing evaluation and stability harnesses.

Pure, dependency-light helpers for comparing checker findings: the severity
scale, section-number extraction, and lexical similarity of issue text. Both
``publishing_evaluate.py`` (produced-vs-ground-truth) and
``publishing_stability.py`` (run-to-run) build on these.
"""

import re

SEVERITY_ORDER = ("info", "low", "medium", "high", "critical")
HYPERLINK_PATTERN = re.compile(r"https?://\S+|www\.\S+")
_SECTION_NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)*")


def severity_rank(value: str) -> int:
    """Position of a severity in the ordered scale, or -1 if unrecognised."""
    try:
        return SEVERITY_ORDER.index(value.lower())
    except ValueError:
        return -1


def extract_section_number(section: str) -> str | None:
    """The leading numeric token of a section reference, e.g. '3.2' from '3.2 Tenure'."""
    match = _SECTION_NUMBER_PATTERN.search(section)
    return match.group(0) if match else None


def extract_section_numbers(section: str) -> list[str]:
    """Every numeric section token in a reference, deduplicated, in order of appearance.

    A location like 'Sections 3.2 and 5.1' names more than one section; each token is
    a distinct place the finding applies to.
    """
    return list(dict.fromkeys(_SECTION_NUMBER_PATTERN.findall(section)))


def issue_terms(text: str) -> set[str]:
    """Comparable terms of an issue: hyperlinks kept whole, other words lower-cased.

    Hyperlinks are extracted first so they are not split apart; everything else is
    lower-cased with punctuation stripped.
    """
    links = {link.rstrip(".,);:]'\"") for link in HYPERLINK_PATTERN.findall(text)}
    without_links = HYPERLINK_PATTERN.sub(" ", text)
    words = set(re.findall(r"[a-z0-9]+", without_links.lower()))
    return links | words


def issue_jaccard(first_issue: str, second_issue: str) -> float:
    """Jaccard similarity of two issues' terms (0.0-1.0), symmetric in its arguments."""
    first = issue_terms(first_issue)
    second = issue_terms(second_issue)
    union = first | second
    return len(first & second) / len(union) if union else 0.0
