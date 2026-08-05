"""Mechanical (non-LLM) merging of per-section analysis outputs."""

from app.publishing import models


def compute_verdict(
    section_verdicts: list[models.ReadinessVerdict],
) -> models.ReadinessVerdict:
    """Worst-of: NOT_READY if any section is NOT_READY, else READY.

    An empty list yields READY — vacuously, nothing is wrong.
    """
    if models.ReadinessVerdict.NOT_READY in section_verdicts:
        return models.ReadinessVerdict.NOT_READY
    return models.ReadinessVerdict.READY


def merge_good_points(section_good_points: list[list[str]]) -> list[str]:
    """Concatenate good points across sections in section order, undeduplicated."""
    return [point for points in section_good_points for point in points]
