"""Domain models for searching the guidance index.

Search runs in three passes. The ranker reads the whole index and proposes
entries; each proposal that names a section is then checked against that
section's real Markdown, in parallel; the answerer writes the overview from
what survived. Every pass is grounded in what the index holds, so a result
can always be resolved to a document and a link.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field

import pydantic

from app.infra.prompts import repository as prompt_repo


class RankedResult(pydantic.BaseModel):
    """One entry the ranker proposes, in the index's own terms.

    It carries no prose. The ranker's whole output is a list of references,
    which is what makes ranking quick: a sentence per result was the largest
    part of a search's wall clock, and for every section it was thrown away
    and replaced by the assessor's, which is written having read the section.
    """

    document_id: str = pydantic.Field(
        ..., description="The id of the document the entry belongs to."
    )
    section_number: str | None = pydantic.Field(
        None,
        description=(
            "The dotted number of the section, or null where the whole "
            "document is the answer."
        ),
    )


class RankedResults(pydantic.BaseModel):
    """What the ranker returns: the index entries that answer the query."""

    results: list[RankedResult] = pydantic.Field(
        default_factory=list,
        description="Up to ten entries, most relevant first.",
    )


class Relevance(pydantic.BaseModel):
    """Whether a section, read in full, actually answers the query."""

    relevant: bool = pydantic.Field(
        ..., description="True only if this section helps answer the query."
    )
    reason: str = pydantic.Field(
        ...,
        description=(
            "One sentence saying what the section gives the operator, or why "
            "it does not answer them after all."
        ),
    )


class Citation(pydantic.BaseModel):
    """An entry the answer was drawn from."""

    document_id: str
    section_number: str | None = None


class SearchAnswer(pydantic.BaseModel):
    """The overview shown above the results."""

    answered: bool = pydantic.Field(
        ...,
        description=(
            "False where the results do not support an answer. The results "
            "are still shown; an answer is not invented for them."
        ),
    )
    answer: str = pydantic.Field(
        "",
        description=(
            "A few sentences answering the operator, drawn only from the "
            "results given. Empty where answered is false."
        ),
    )
    cited: list[Citation] = pydantic.Field(
        default_factory=list,
        description="The results the answer was drawn from.",
    )


def _prompts() -> prompt_repo.AbstractPromptRepository:
    return prompt_repo.FileSystemPromptRepository(
        prompt_directory=os.path.join(os.path.dirname(__file__), "prompts")
    )


@dataclass
class RankDependencies:
    """Dependencies for the ranking pass."""

    query: str
    index: str
    # Every (document id, section number) the index holds: what a result may
    # name. A result naming anything else is a link to nowhere.
    entries: set[tuple[str, str | None]]
    prompt_repository: prompt_repo.AbstractPromptRepository = field(
        default_factory=_prompts
    )


@dataclass
class AssessDependencies:
    """Dependencies for assessing one section against the query."""

    query: str
    document_title: str
    heading: str
    section_markdown: str
    prompt_repository: prompt_repo.AbstractPromptRepository = field(
        default_factory=_prompts
    )


@dataclass
class AnswerDependencies:
    """Dependencies for writing the overview."""

    query: str
    results: str
    cited: set[tuple[str, str | None]]
    prompt_repository: prompt_repo.AbstractPromptRepository = field(
        default_factory=_prompts
    )


@dataclass
class SearchResult:
    """One result, resolved back to the index entry it names."""

    document_id: uuid.UUID
    document_title: str
    section_number: str | None
    heading: str
    summary: str
    reason: str
    start_path: str
    checked: bool
