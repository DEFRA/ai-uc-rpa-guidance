"""Rendering of the stored index as the text a search agent is given.

The agent is shown the index and nothing else: what it cannot see here, it
cannot return. This module is therefore the whole of what search knows about
the corpus, and its size is what decides how many documents can be searched
at once.
"""

from app.guidance.summaries import models as summary_models


def _acronyms(acronyms: list[summary_models.AcronymEntry]) -> str:
    return "; ".join(
        f"{entry.acronym} = {entry.expansion or 'not expanded'}"
        + (f" [{', '.join(entry.sections)}]" if entry.sections else "")
        for entry in acronyms
    )


def _section_acronyms(acronyms: list[summary_models.SectionAcronym]) -> str:
    return "; ".join(
        f"{entry.acronym} = {entry.expansion or 'not expanded'}" for entry in acronyms
    )


def render(
    summaries: list[summary_models.DocumentSummary],
    sections: dict[str, list[summary_models.SectionSummary]],
) -> str:
    """Render the whole index as the text injected into the ranker's prompt.

    Args:
        summaries: Every document summary held.
        sections: Each document's section entries, keyed by document id.

    Returns:
        The index as Markdown.
    """
    documents = []

    for summary in summaries:
        document_id = str(summary.document_id)
        lines = [
            f"## {summary.title}",
            f"document_id: {document_id}",
            f"About: {summary.about}",
            f"Used for: {summary.used_for}",
        ]

        if summary.keywords:
            lines.append(f"Terms: {', '.join(summary.keywords)}")

        if summary.acronyms:
            lines.append(f"Acronyms: {_acronyms(summary.acronyms)}")

        entries = sections.get(document_id, [])
        if entries:
            lines.append("Sections:")
            lines.extend(
                f"- section_number: {entry.number} | {entry.heading} | "
                f"{entry.summary}"
                + (f" | Terms: {', '.join(entry.keywords)}" if entry.keywords else "")
                + (
                    f" | Acronyms: {_section_acronyms(entry.acronyms)}"
                    if entry.acronyms
                    else ""
                )
                for entry in entries
            )

        documents.append("\n".join(lines))

    return "\n\n".join(documents)
