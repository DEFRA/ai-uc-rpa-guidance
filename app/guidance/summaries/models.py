"""Domain models for per-document guidance summaries.

A summary is what a reader needs in order to decide whether a document is the
one they want: what it is about, and what it is used for. One is held per
guidance document, keyed by that document's id, so the relationship is
one-to-one by construction rather than by convention.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pydantic

from app.infra.prompts import repository as prompt_repo

# Where a reader starts reading the document a summary describes. That is its
# first section: a reader who has read the summary has had the contents page's
# job done for them already, and wants the guidance itself. A document the
# parse found no sections in has none to open, so its contents page stands in.
# The index stores the path so that whatever reads the index — a search agent,
# the admin page, a reader opening summary.md — has the way into the document.
SECTION_PATH_TEMPLATE = "/guidance-documents/{document_id}/sections/{section_number}"
CONTENTS_PATH_TEMPLATE = "/guidance-documents/{document_id}/view"


def start_path_for(document_id: uuid.UUID, first_section: str | None) -> str:
    """Return the path at which a reader starts reading a document.

    Args:
        document_id: The guidance document UUID.
        first_section: The number of the document's first section, or None if
            the parse produced no sections.

    Returns:
        The path of that first section, or of the contents page.
    """
    if first_section is None:
        return CONTENTS_PATH_TEMPLATE.format(document_id=document_id)

    return SECTION_PATH_TEMPLATE.format(
        document_id=document_id, section_number=first_section
    )


class SummaryOutput(pydantic.BaseModel):
    """What the summariser agent returns about one guidance document."""

    about: str = pydantic.Field(
        ...,
        description=(
            "What the document is about: the subject matter it covers, in two "
            "or three sentences of plain English."
        ),
    )
    used_for: str = pydantic.Field(
        ...,
        description=(
            "What the document is used for: the task it supports and who "
            "reaches for it, in two or three sentences of plain English."
        ),
    )
    keywords: list[str] = pydantic.Field(
        default_factory=list,
        description=(
            "The terms this document would be searched by, including the "
            "initialism a reader would call it by."
        ),
    )


class SectionSummaryOutput(pydantic.BaseModel):
    """What the section summariser returns about one section of a document."""

    number: str = pydantic.Field(
        ...,
        description="The section's dotted number, exactly as it was given.",
    )
    summary: str = pydantic.Field(
        ...,
        description=(
            "What this section covers and when a reader turns to it, in one "
            "or two sentences of plain English, naming the terms someone "
            "would search for."
        ),
    )
    keywords: list[str] = pydantic.Field(
        default_factory=list,
        description=(
            "The terms this section would be searched by: schemes, codes, "
            "systems, case types and acronyms it names."
        ),
    )
    acronyms: list[AcronymOutput] = pydantic.Field(
        default_factory=list,
        description="Every acronym this section uses, with its expansion.",
    )


class AcronymOutput(pydantic.BaseModel):
    """An acronym as one section of a document uses it."""

    acronym: str = pydantic.Field(..., description="The acronym as written.")
    expansion: str | None = pydantic.Field(
        None,
        description=(
            "What it stands for, where this document says so. Null where the "
            "document uses it without ever expanding it."
        ),
    )


class SectionSummariesOutput(pydantic.BaseModel):
    """What the section summariser returns about a whole document."""

    sections: list[SectionSummaryOutput] = pydantic.Field(
        default_factory=list, description="One entry per section of the document."
    )


@dataclass
class SummaryDependencies:
    """Dependencies provided to the summariser agent."""

    document_markdown: str
    prompt_repository: prompt_repo.AbstractPromptRepository = field(
        default_factory=lambda: prompt_repo.FileSystemPromptRepository(
            prompt_directory=os.path.join(os.path.dirname(__file__), "prompts")
        )
    )


@dataclass
class SectionAcronym:
    """An acronym as one section uses it, with what it stands for."""

    acronym: str
    expansion: str | None


@dataclass
class AcronymEntry:
    """One entry of a document's acronym index.

    The index is a reverse one: the acronym is the key, and it names the
    sections it appears in. `expansion` is None where the document uses the
    acronym without ever saying what it stands for — recorded rather than
    dropped, because a reader searching for it still needs to be brought here.
    """

    acronym: str
    expansion: str | None
    sections: list[str]


@dataclass
class SectionSummaryDependencies:
    """Dependencies provided to the section summariser agent."""

    document_title: str
    document_markdown: str
    # The sections the parse found, as (number, heading): what the agent must
    # cover, and the only numbers it may return.
    sections: list[tuple[str, str]]
    prompt_repository: prompt_repo.AbstractPromptRepository = field(
        default_factory=lambda: prompt_repo.FileSystemPromptRepository(
            prompt_directory=os.path.join(os.path.dirname(__file__), "prompts")
        )
    )


@dataclass
class SectionSummary:
    """One section's index entry, as held in MongoDB.

    The Mongo `_id` is the document id and section number together, so a
    section has one entry or none and a rebuild replaces it in place. `order`
    is the section's position in the manifest, because a dotted number does
    not sort: "1.10" precedes "1.9" as text and follows it on the page.
    """

    document_id: uuid.UUID
    number: str
    heading: str
    level: int
    summary: str
    keywords: list[str]
    acronyms: list[SectionAcronym]
    start_path: str
    order: int
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    @property
    def entry_id(self) -> str:
        """Return the Mongo `_id` identifying this section's entry."""
        return f"{self.document_id}/{self.number}"

    @classmethod
    def from_mongo_doc(cls, doc: dict[str, Any]) -> SectionSummary:
        """Map a MongoDB document to a SectionSummary model.

        Args:
            doc: The MongoDB document dictionary.

        Returns:
            A SectionSummary model instance.
        """
        return cls(
            document_id=doc["document_id"],
            number=doc["number"],
            heading=doc["heading"],
            level=doc["level"],
            summary=doc["summary"],
            keywords=doc.get("keywords", []),
            acronyms=[
                SectionAcronym(
                    acronym=entry["acronym"], expansion=entry.get("expansion")
                )
                for entry in doc.get("acronyms", [])
            ],
            start_path=doc["start_path"],
            order=doc["order"],
            created_at=doc["created_at"],
            updated_at=doc["updated_at"],
        )


def _spell_out(acronyms: list[SectionAcronym]) -> str:
    """Render acronyms as "SDA (Severely Disadvantaged Area)" in one line."""
    return ", ".join(
        f"{entry.acronym} ({entry.expansion})" if entry.expansion else entry.acronym
        for entry in acronyms
    )


def build_acronym_index(
    by_section: list[tuple[str, list[SectionAcronym]]],
) -> list[AcronymEntry]:
    """Turn the acronyms each section uses into the document's reverse index.

    The index is derived from the section entries rather than asked for
    separately, so it cannot disagree with them: every acronym a section
    names appears here against that section, and nothing appears here that no
    section named. An acronym expanded in one section is expanded in the
    index, even where the sections that follow use it bare.

    Args:
        by_section: Each section's number and the acronyms it uses, in
            document order.

    Returns:
        One entry per distinct acronym, alphabetically.
    """
    index: dict[str, AcronymEntry] = {}

    for number, acronyms in by_section:
        for used in acronyms:
            entry = index.setdefault(
                used.acronym,
                AcronymEntry(acronym=used.acronym, expansion=None, sections=[]),
            )

            if entry.expansion is None and used.expansion:
                entry.expansion = used.expansion

            if number not in entry.sections:
                entry.sections.append(number)

    return sorted(index.values(), key=lambda entry: entry.acronym)


@dataclass
class DocumentSummary:
    """A guidance document's summary, as held in MongoDB.

    `document_id` is the Mongo `_id`: a document has one summary or none.
    `path` points at the rendered Markdown in S3, which is derived from the
    prose held here rather than a second copy of the truth.
    """

    document_id: uuid.UUID
    title: str
    about: str
    used_for: str
    keywords: list[str]
    acronyms: list[AcronymEntry]
    path: str
    start_path: str
    model: str
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    @classmethod
    def from_mongo_doc(cls, doc: dict[str, Any]) -> DocumentSummary:
        """Map a MongoDB document to a DocumentSummary model.

        Args:
            doc: The MongoDB document dictionary.

        Returns:
            A DocumentSummary model instance.
        """
        return cls(
            document_id=doc["_id"],
            title=doc["title"],
            about=doc["about"],
            used_for=doc["used_for"],
            keywords=doc.get("keywords", []),
            acronyms=[
                AcronymEntry(
                    acronym=entry["acronym"],
                    expansion=entry.get("expansion"),
                    sections=entry.get("sections", []),
                )
                for entry in doc.get("acronyms", [])
            ],
            path=doc["path"],
            # A summary written before the index carried the link knows which
            # document it describes but not where that document's sections
            # begin, so it opens at the contents page until it is rebuilt.
            start_path=doc.get("start_path") or start_path_for(doc["_id"], None),
            model=doc["model"],
            created_at=doc["created_at"],
            updated_at=doc["updated_at"],
        )

    def render_markdown(self, sections: list[SectionSummary]) -> str:
        """Render the whole index entry for this document as Markdown.

        This is the artefact stored in S3: the document's summary followed by
        one entry per section, each linking to the section it describes — the
        same index the admin page shows, in a form anything can read.

        Args:
            sections: The document's section entries, in document order.

        Returns:
            The rendered Markdown.
        """
        parts = [
            f"# {self.title}\n",
            f"[Open this document]({self.start_path})\n",
            "## What this document is about\n",
            f"{self.about}\n",
            "## What it is used for\n",
            f"{self.used_for}\n",
        ]

        if self.keywords:
            parts.append("## Terms\n")
            parts.append(f"{', '.join(self.keywords)}\n")

        if self.acronyms:
            parts.append("## Acronyms\n")
            parts.extend(
                f"- **{entry.acronym}** — "
                f"{entry.expansion or 'not expanded in this document'}"
                + (f" ({', '.join(entry.sections)})" if entry.sections else "")
                for entry in self.acronyms
            )
            parts.append("")

        if sections:
            parts.append("## Sections\n")
            parts.extend(
                f"### {section.number} {section.heading}\n\n"
                f"[Open this section]({section.start_path})\n\n"
                f"{section.summary}\n"
                + (
                    f"\nTerms: {', '.join(section.keywords)}\n"
                    if section.keywords
                    else ""
                )
                + (
                    f"\nAcronyms: {_spell_out(section.acronyms)}\n"
                    if section.acronyms
                    else ""
                )
                for section in sections
            )

        return "\n".join(parts)
