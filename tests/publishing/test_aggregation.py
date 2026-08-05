"""Tests for mechanical merging of per-section analysis outputs."""

from app.publishing import aggregation, models


class TestComputeVerdict:
    def test_all_ready_is_ready(self) -> None:
        verdicts = [models.ReadinessVerdict.READY, models.ReadinessVerdict.READY]

        assert aggregation.compute_verdict(verdicts) == models.ReadinessVerdict.READY

    def test_any_not_ready_is_not_ready(self) -> None:
        verdicts = [
            models.ReadinessVerdict.READY,
            models.ReadinessVerdict.NOT_READY,
            models.ReadinessVerdict.READY,
        ]

        assert (
            aggregation.compute_verdict(verdicts) == models.ReadinessVerdict.NOT_READY
        )

    def test_all_not_ready_is_not_ready(self) -> None:
        verdicts = [models.ReadinessVerdict.NOT_READY]

        assert (
            aggregation.compute_verdict(verdicts) == models.ReadinessVerdict.NOT_READY
        )

    def test_empty_list_is_vacuously_ready(self) -> None:
        assert aggregation.compute_verdict([]) == models.ReadinessVerdict.READY


class TestMergeGoodPoints:
    def test_concatenates_in_section_order(self) -> None:
        merged = aggregation.merge_good_points([["a", "b"], ["c"], ["d"]])

        assert merged == ["a", "b", "c", "d"]

    def test_empty_sections_contribute_nothing(self) -> None:
        assert aggregation.merge_good_points([[], ["a"], []]) == ["a"]

    def test_duplicates_are_preserved(self) -> None:
        assert aggregation.merge_good_points([["a"], ["a"]]) == ["a", "a"]
