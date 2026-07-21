"""Tests for presentation-stage ordering of review findings."""

from app.review import models, ordering


def _finding(
    section: str,
    severity: models.SeverityLevel = models.SeverityLevel.MEDIUM,
) -> models.ReviewFinding:
    """Build a finding carrying just the fields the ordering depends on."""
    return models.ReviewFinding(
        principle=models.Principle.PLAIN_ENGLISH,
        section=section,
        quote="the text",
        issue="issue",
        why_it_matters="why",
        severity=severity,
        confidence=models.ConfidenceLevel.HIGH,
        recommendation="fix",
    )


def _sections(findings: list[models.ReviewFinding]) -> list[str]:
    return [f.section for f in findings]


def test_unnumbered_sections_lead_and_sort_alphabetically() -> None:
    """Digit-less sections come first, case-insensitively alphabetical."""
    findings = [
        _finding("Section 2 Overview"),
        _finding("zebra block"),
        _finding("Document title / cover block"),
        _finding("Annex"),
    ]
    ordered = ordering.order_findings(findings)
    assert _sections(ordered) == [
        "Annex",
        "Document title / cover block",
        "zebra block",
        "Section 2 Overview",
    ]


def test_section_numbers_sort_naturally() -> None:
    """Dotted numbers order numerically; 10 follows 9, not 1."""
    findings = [
        _finding("Section 10 Closure"),
        _finding("Section 4.1.1 Check"),
        _finding("Section 2 Find"),
        _finding("Section 4.1 Tenure"),
        _finding("Section 9 Change"),
    ]
    ordered = ordering.order_findings(findings)
    assert _sections(ordered) == [
        "Section 2 Find",
        "Section 4.1 Tenure",
        "Section 4.1.1 Check",
        "Section 9 Change",
        "Section 10 Closure",
    ]


def test_deeper_subsection_sorts_after_its_parent() -> None:
    """4.3.1 precedes 4.3.1.2."""
    findings = [
        _finding("Section 4.3.1.2 Merge"),
        _finding("Section 4.3.1 Split/Merge"),
    ]
    ordered = ordering.order_findings(findings)
    assert _sections(ordered) == [
        "Section 4.3.1 Split/Merge",
        "Section 4.3.1.2 Merge",
    ]


def test_same_section_orders_most_severe_first() -> None:
    """Within a section, critical precedes info."""
    findings = [
        _finding("Section 7.1 Telephone", models.SeverityLevel.INFO),
        _finding("Section 7.1 Telephone", models.SeverityLevel.CRITICAL),
        _finding("Section 7.1 Telephone", models.SeverityLevel.MEDIUM),
    ]
    ordered = ordering.order_findings(findings)
    assert [f.severity for f in ordered] == [
        models.SeverityLevel.CRITICAL,
        models.SeverityLevel.MEDIUM,
        models.SeverityLevel.INFO,
    ]


def test_multi_number_section_keys_off_first_token() -> None:
    """A plural/multi-number section sorts by its first dotted token."""
    findings = [
        _finding("Section 5.1 Check JMS Flow"),
        _finding("Sections 4.3.1.1 Split, 4.3.1.2 Merge"),
    ]
    ordered = ordering.order_findings(findings)
    assert _sections(ordered) == [
        "Sections 4.3.1.1 Split, 4.3.1.2 Merge",
        "Section 5.1 Check JMS Flow",
    ]
