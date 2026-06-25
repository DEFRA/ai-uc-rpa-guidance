"""Tests for the publishing QA agent and models."""

import app.publishing.models as models


class TestAnalysisFinding:
    """Test AnalysisFinding model validation."""

    def test_valid_finding(self) -> None:
        """Test creating a valid analysis finding."""
        finding = models.AnalysisFinding(
            category=models.FindingCategory.HEADINGS_AND_LAYOUT,
            section="Introduction",
            issue="Text is unclear",
            why_it_matters="Readers cannot follow the guidance",
            severity=models.SeverityLevel.MEDIUM,
            confidence=models.ConfidenceLevel.HIGH,
            recommendation="Rewrite for clarity",
        )
        assert finding.category == models.FindingCategory.HEADINGS_AND_LAYOUT
        assert finding.section == "Introduction"
        assert finding.severity == models.SeverityLevel.MEDIUM
        assert finding.confidence == models.ConfidenceLevel.HIGH

    def test_finding_severity_values(self) -> None:
        """Test all severity level values are valid."""
        assert models.SeverityLevel.INFO == "info"
        assert models.SeverityLevel.LOW == "low"
        assert models.SeverityLevel.MEDIUM == "medium"
        assert models.SeverityLevel.HIGH == "high"
        assert models.SeverityLevel.CRITICAL == "critical"

    def test_finding_category_values(self) -> None:
        """Test all finding category values are valid."""
        assert models.FindingCategory.HEADINGS_AND_LAYOUT == "headings_and_layout"
        assert models.FindingCategory.IMAGES_AND_FORMATTING == "images_and_formatting"
        assert models.FindingCategory.SENSITIVE_INFORMATION == "sensitive_information"
        assert models.FindingCategory.LINKS == "links"
        assert (
            models.FindingCategory.OVERALL_PUBLISH_READINESS
            == "overall_publish_readiness"
        )

    def test_finding_generates_reasoning_before_severity(self) -> None:
        """why_it_matters must precede severity so the LLM reasons first."""
        fields = list(models.AnalysisFinding.model_fields)
        assert fields.index("why_it_matters") < fields.index("severity")


class TestAnalysisOutput:
    """Test AnalysisOutput model validation."""

    def test_valid_output_with_findings(self) -> None:
        """Test creating a valid analysis output with findings."""
        output = models.AnalysisOutput(
            document_title="Test Guidance",
            findings=[
                models.AnalysisFinding(
                    category=models.FindingCategory.OVERALL_PUBLISH_READINESS,
                    section="Step 3",
                    issue="Missing information",
                    why_it_matters="The document is not complete",
                    severity=models.SeverityLevel.HIGH,
                    confidence=models.ConfidenceLevel.MODERATE,
                    recommendation="Add details",
                )
            ],
            good_points=["Headings are applied consistently"],
            summary="Document needs revision",
            verdict=models.ReadinessVerdict.NOT_READY,
        )
        assert output.verdict == models.ReadinessVerdict.NOT_READY
        assert len(output.findings) == 1

    def test_output_with_empty_findings(self) -> None:
        """Test creating output with no findings."""
        output = models.AnalysisOutput(
            document_title="Test Guidance",
            findings=[],
            good_points=[],
            summary="Document is ready",
            verdict=models.ReadinessVerdict.READY,
        )
        assert len(output.findings) == 0
        assert output.verdict == models.ReadinessVerdict.READY

    def test_output_generates_verdict_after_findings(self) -> None:
        """verdict must come after findings so it is conditioned on them."""
        fields = list(models.AnalysisOutput.model_fields)
        assert fields.index("verdict") > fields.index("findings")
