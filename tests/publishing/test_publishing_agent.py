"""Tests for the publishing QA agent and models."""

import app.publishing.models as models


class TestAnalysisFinding:
    """Test AnalysisFinding model validation."""

    def test_valid_finding(self) -> None:
        """Test creating a valid analysis finding."""
        finding = models.AnalysisFinding(
            section="Introduction",
            issue="Text is unclear",
            severity=models.SeverityLevel.MEDIUM,
            recommendation="Rewrite for clarity",
        )
        assert finding.section == "Introduction"
        assert finding.severity == models.SeverityLevel.MEDIUM

    def test_finding_severity_values(self) -> None:
        """Test all severity level values are valid."""
        assert models.SeverityLevel.LOW == "low"
        assert models.SeverityLevel.MEDIUM == "medium"
        assert models.SeverityLevel.HIGH == "high"
        assert models.SeverityLevel.CRITICAL == "critical"


class TestAnalysisOutput:
    """Test AnalysisOutput model validation."""

    def test_valid_output_with_findings(self) -> None:
        """Test creating a valid analysis output with findings."""
        output = models.AnalysisOutput(
            status="completed",
            findings=[
                models.AnalysisFinding(
                    section="Step 3",
                    issue="Missing information",
                    severity=models.SeverityLevel.HIGH,
                    recommendation="Add details",
                )
            ],
            summary="Document needs revision",
        )
        assert output.status == "completed"
        assert len(output.findings) == 1

    def test_output_with_empty_findings(self) -> None:
        """Test creating output with no findings."""
        output = models.AnalysisOutput(
            status="completed",
            findings=[],
            summary="Document is ready",
        )
        assert len(output.findings) == 0
