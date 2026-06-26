"""Presentation-stage ordering of analysis findings.

Reorders the agent's findings into a deterministic, reader-friendly sequence
before they are mapped to the API response: findings with no section number
first (alphabetically), then by natural section number, then most severe first.
"""

import re

from app.publishing import models

# First dotted-decimal token anywhere in the free-text section, e.g. the
# "4.3.1.2" in "Section 4.3.1.2 Merge — Note paragraph".
_SECTION_NUMBER_RE = re.compile(r"\d+(?:\.\d+)*")

# Most severe first. Derived from the enum (declared in increasing seriousness)
# so it cannot drift if a level is added or reordered.
_SEVERITY_RANK = {
    level: rank for rank, level in enumerate(reversed(list(models.SeverityLevel)))
}


def _finding_sort_key(
    finding: models.AnalysisFinding,
) -> tuple[int, str, tuple[int, ...], int]:
    """Sort key: (group, alpha, number, severity).

    The tuple is uniformly typed so numbered and un-numbered findings stay
    comparable; the field unused by a group is constant within it, so it acts as
    a no-op tiebreaker. ``group`` 0 (no number) sorts ahead of 1 (numbered).
    """
    match = _SECTION_NUMBER_RE.search(finding.section)
    if match:
        group, alpha = 1, ""
        number = tuple(int(part) for part in match.group().split("."))
    else:
        group, alpha = 0, finding.section.casefold()
        number = ()
    return (group, alpha, number, _SEVERITY_RANK[finding.severity])


def order_findings(
    findings: list[models.AnalysisFinding],
) -> list[models.AnalysisFinding]:
    """Return findings ordered by section, then most severe first."""
    return sorted(findings, key=_finding_sort_key)
