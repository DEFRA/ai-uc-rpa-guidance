"""Context document tools shared by the critic and writer agents.

Ported from DEFRA/ai-uc-content-swarm-runtime app/swarm/context/tools.py,
backed by the filesystem context repository and extended with the DEFRA
style guide index.
"""

import logging

import pydantic_ai

from app.critique import models
from app.infra.context import repository as context_repo

logger = logging.getLogger(__name__)

STYLE_GUIDE_INDEX = "content-style-guide/index.json"
CONTENT_GUIDANCE_INDEX = "content-guidance/index.json"
DEFRA_STYLE_GUIDE_INDEX = "defra-style-guide/index.json"

context_documents_toolset: pydantic_ai.FunctionToolset[models.AgentDependencies] = (
    pydantic_ai.FunctionToolset()
)


async def _get_index(
    ctx: pydantic_ai.RunContext[models.AgentDependencies], key: str
) -> str:
    try:
        return await ctx.deps.context_repository.get_context(key)
    except context_repo.ContextRepositoryError as e:
        return f"Error retrieving index '{key}': {e}"


@context_documents_toolset.tool
async def list_style_guide_documents(
    ctx: pydantic_ai.RunContext[models.AgentDependencies],
) -> str:
    """List the GOV.UK (GDS) content style guide rules available in the context store.

    Returns a JSON array of objects with title, type, and file fields.
    Use the file value with get_document_content to retrieve the full content of the rule.
    """
    logger.info("[Tool Call] ContextDocumentsToolset: list_style_guide_documents")
    return await _get_index(ctx, STYLE_GUIDE_INDEX)


@context_documents_toolset.tool
async def list_content_guidance(
    ctx: pydantic_ai.RunContext[models.AgentDependencies],
) -> str:
    """List the GOV.UK (GDS) content design guidance documents available in the context store.

    Returns a JSON array of objects with id, title, description, and file fields.
    Use the file value with get_document_content to retrieve the full content of the guidance.
    """
    logger.info("[Tool Call] ContextDocumentsToolset: list_content_guidance")
    return await _get_index(ctx, CONTENT_GUIDANCE_INDEX)


@context_documents_toolset.tool
async def list_defra_style_guide_documents(
    ctx: pydantic_ai.RunContext[models.AgentDependencies],
) -> str:
    """List the DEFRA style guide sections available in the context store.

    Returns a JSON array of objects with title, description, and file fields.
    Use the file value with get_document_content to retrieve the full content of the section.
    """
    logger.info("[Tool Call] ContextDocumentsToolset: list_defra_style_guide_documents")
    return await _get_index(ctx, DEFRA_STYLE_GUIDE_INDEX)


@context_documents_toolset.tool
async def get_document_content(
    ctx: pydantic_ai.RunContext[models.AgentDependencies], file: str
) -> str:
    """Retrieve the full content of a context document by its file path.

    Use the file path returned by list_style_guide_documents, list_content_guidance,
    or list_defra_style_guide_documents.
    """
    logger.info("[Tool Call] ContextDocumentsToolset: get_document_content")
    try:
        return await ctx.deps.context_repository.get_context(file)
    except context_repo.ContextRepositoryError as e:
        return f"Error retrieving document content for file '{file}': {e}"
