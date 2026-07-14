"""FindingSource port for the feedback domain."""

import uuid
from typing import Protocol

from app.feedback import models


class FindingSource(Protocol):
    """Port: validates a job+finding reference and returns a snapshot of the finding."""

    async def get_finding_snapshot(
        self, job_id: uuid.UUID, finding_index: int | None
    ) -> models.FindingSnapshot | None:
        """Return a snapshot of the finding, or None if the reference is invalid.

        Args:
            job_id: The job UUID.
            finding_index: 0-based index into the findings list, or None for job-level.

        Returns:
            FindingSnapshot if the job is completed and the index is in range, else None.
        """
        ...
