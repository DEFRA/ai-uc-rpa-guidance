from logging import getLogger
from typing import Any

from aws_embedded_metrics import metric_scope
from aws_embedded_metrics.storage_resolution import StorageResolution

logger = getLogger(__name__)


@metric_scope
def __put_metric(
    metric_name: str, value: int, unit: str, metrics: Any
) -> None:  # pragma: no cover
    logger.debug("put metric: %s - %s - %s", metric_name, value, unit)
    metrics.put_metric(metric_name, value, unit, StorageResolution.STANDARD)


def counter(metric_name: str, value: int) -> None:
    try:
        __put_metric(metric_name, value, "Count")  # type: ignore[call-arg]
    except Exception as e:
        logger.error("Error calling put_metric: %s", e)
