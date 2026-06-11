"""Scrape style/content standards into the local context store.

Ported from DEFRA/ai-uc-content-swarm reference/notebooks, then updated:
gov.uk retired the manuals-frontend style guide and content-design manual on
2026-06-05 — both now redirect to the static site
guidance.publishing.service.gov.uk, so the GOV.UK targets scrape that site's
HTML directly (the old content API returns redirects). The DEFRA style guide
target scrapes digital.defra.gov.uk (also plain HTML).

Targets:
  1. GOV.UK style guides (A-to-Z, technical A-to-Z, how-to-use)
       -> data/context/content-style-guide/
  2. GOV.UK writing guidance (tone of voice, text formatting, content design)
       -> data/context/content-guidance/
  3. DEFRA style guide
       -> data/context/defra-style-guide/

Each target emits frontmattered markdown files plus an index.json whose
``file`` values are keys relative to the context root, ready to be passed to
the context tools' get_document_content.

Usage:
    uv run --group scraper python scripts/scrape_context.py
"""

import asyncio
import json
import re
from collections import defaultdict
from pathlib import Path

import aiofiles
import bs4
import httpx
from markdownify import markdownify

GUIDANCE_BASE_URL = "https://guidance.publishing.service.gov.uk"
DEFRA_STYLE_GUIDE_URL = "https://digital.defra.gov.uk/content/defra-style-guide"

CONTEXT_ROOT = Path("data/context")

BATCH_SIZE = 10
BATCH_SLEEP_SECONDS = 2
RULE_LENGTH_THRESHOLD = 500

REQUEST_TIMEOUT = httpx.Timeout(30.0)

# A-to-Z pages are split into per-term rule/definition entries.
STYLE_GUIDE_ATOZ_PATHS = [
    "/writing-to-gov-uk-standards/style-guides/a-to-z-style-guide/",
    "/writing-to-gov-uk-standards/style-guides/technical-a-to-z/",
]
STYLE_GUIDE_WHOLE_PATHS = [
    "/writing-to-gov-uk-standards/style-guides/",
    "/writing-to-gov-uk-standards/style-guides/how-to-use/",
]

# Writing-related GDS guidance only: the ticket scopes review to language,
# grammar, and sentence structure, so gov.uk publishing operations sections
# (accounts-support, publish-update-retire-content, etc.) are not scraped.
CONTENT_GUIDANCE_PREFIXES = (
    "/writing-to-gov-uk-standards/tone-of-voice/",
    "/formatting-content/text-formatting/",
)
CONTENT_GUIDANCE_EXTRA_PATHS = [
    "/writing-to-gov-uk-standards/",
    "/writing-to-gov-uk-standards/plan-manage-content/understand-content-design/",
    "/writing-to-gov-uk-standards/plan-manage-content/understand-accessibility/",
]


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


async def discover_guidance_paths(client: httpx.AsyncClient) -> list[str]:
    """Collect page paths from the guidance site's sitemap page."""
    response = await client.get(f"{GUIDANCE_BASE_URL}/sitemap")
    response.raise_for_status()
    soup = bs4.BeautifulSoup(response.text, "lxml")

    paths = set()
    for link in soup.select("a[href^='/']"):
        href = str(link.get("href"))
        if "." in href.split("/")[-1] or "#" in href:
            continue
        paths.add(href if href.endswith("/") else f"{href}/")

    return sorted(paths)


async def fetch_guidance_page(client: httpx.AsyncClient, path: str) -> dict:
    """Fetch one guidance site page and convert its content column to markdown."""
    response = await client.get(f"{GUIDANCE_BASE_URL}{path}")
    response.raise_for_status()
    soup = bs4.BeautifulSoup(response.text, "lxml")

    main = soup.select_one("main#main-content")
    if main is None:
        msg = f"{path}: main#main-content not found — page layout changed?"
        raise RuntimeError(msg)
    content = (
        main.select_one("div.govuk-grid-column-three-quarters-from-desktop") or main
    )

    h1 = main.select_one("h1")
    title = h1.get_text(strip=True) if h1 else path
    description_meta = soup.select_one("meta[property='og:description']")
    description = str(description_meta.get("content", "")) if description_meta else ""

    return {
        "link": f"/{path.strip('/')}",
        "title": title,
        "description": description,
        "content": markdownify(str(content), heading_style="ATX"),
    }


async def fetch_guidance_pages(
    client: httpx.AsyncClient, paths: list[str]
) -> list[dict]:
    pages: list[dict] = []
    batches = [paths[i : i + BATCH_SIZE] for i in range(0, len(paths), BATCH_SIZE)]
    for i, batch in enumerate(batches):
        pages.extend(
            await asyncio.gather(*[fetch_guidance_page(client, path) for path in batch])
        )
        print(f"  fetched batch {i + 1}/{len(batches)}")
        if i + 1 < len(batches):
            await asyncio.sleep(BATCH_SLEEP_SECONDS)
    return pages


def split_by_heading(page: dict, level: int, classify: bool) -> list[dict]:
    """Split a page's markdown into sections at the given heading level.

    With ``classify`` enabled (A-to-Z style guides), sections at or above
    RULE_LENGTH_THRESHOLD become ``rule`` entries and shorter ones
    ``definition`` entries, filed per the original notebook's layout.
    """
    slug = page["link"].split("/")[-1]
    hashes = "#" * level
    sections = []

    for part in re.split(rf"(?=^{hashes} )", page["content"], flags=re.MULTILINE):
        part = part.strip()
        if not part:
            continue
        first_line = part.split("\n")[0]
        title = (
            first_line[level + 1 :].strip()
            if first_line.startswith(f"{hashes} ")
            else "Overview"
        )
        section_slug = slugify(title)
        section = {
            "link": f"{page['link']}/{section_slug}",
            "title": title,
            "description": page["description"],
            "content": part,
        }
        if classify:
            entry_type = "rule" if len(part) >= RULE_LENGTH_THRESHOLD else "definition"
            letter = section_slug[0] if section_slug else "_"
            section["type"] = entry_type
            section["file"] = (
                f"{slug}/rules/{section_slug}.md"
                if entry_type == "rule"
                else f"{slug}/{letter}/{section_slug}.md"
            )
        else:
            section["file"] = f"{slug}/{section_slug}.md"
        sections.append(section)

    return sections


def merge_definitions(pages: list[dict]) -> list[dict]:
    """Merge short A-to-Z definitions into one file per letter (notebook logic)."""
    rules = [p for p in pages if p.get("type") == "rule"]
    definitions = [p for p in pages if p.get("type") == "definition"]
    other = [p for p in pages if p.get("type") not in ("rule", "definition")]

    groups = defaultdict(list)
    for definition in definitions:
        slug, letter = definition["file"].split("/")[0:2]
        groups[(slug, letter)].append(definition)

    merged = []
    for (slug, letter), defs in sorted(groups.items()):
        parent_link = "/".join(defs[0]["link"].split("/")[:-1])
        merged.append(
            {
                "link": f"{parent_link}/{letter}",
                "title": f"{slug} — {letter.upper()}",
                "description": defs[0]["description"],
                "content": "\n\n---\n\n".join(d["content"] for d in defs),
                "type": "definition",
                "file": f"{slug}/{letter}.md",
            }
        )

    return other + rules + merged


async def save_pages(pages: list[dict], target_dir_name: str) -> None:
    """Write markdown files + index.json for one scrape target."""
    output_dir = CONTEXT_ROOT / target_dir_name
    output_dir.mkdir(parents=True, exist_ok=True)

    async def save_page(page: dict) -> None:
        relative_file = page.get("file", f"{page['link'].split('/')[-1]}.md")
        filepath = output_dir / relative_file
        filepath.parent.mkdir(parents=True, exist_ok=True)
        frontmatter = (
            f"---\ntitle: {page['title']}\ndescription: {page['description']}\n---\n\n"
        )
        async with aiofiles.open(filepath, "w", encoding="utf-8") as f:
            await f.write(frontmatter + page["content"])

    await asyncio.gather(*[save_page(page) for page in pages])

    # `file` values in the index are keys relative to the context root so
    # agents can pass them straight to get_document_content.
    index = [
        {
            "id": page["link"].split("/")[-1],
            "title": page["title"],
            "description": page["description"],
            "type": page.get("type", "rule"),
            "file": (
                f"{target_dir_name}/"
                f"{page.get('file', page['link'].split('/')[-1] + '.md')}"
            ),
        }
        for page in pages
    ]
    async with aiofiles.open(output_dir / "index.json", "w", encoding="utf-8") as f:
        await f.write(json.dumps(index, indent=2))

    print(f"  saved {len(pages)} documents + index.json to {output_dir}")


async def scrape_govuk_style_guide(client: httpx.AsyncClient) -> None:
    print("Scraping GOV.UK style guides...")
    pages = await fetch_guidance_pages(client, STYLE_GUIDE_WHOLE_PATHS)

    atoz_pages = await fetch_guidance_pages(client, STYLE_GUIDE_ATOZ_PATHS)
    for page in atoz_pages:
        sections = split_by_heading(page, level=3, classify=True)
        pages.extend(sections)
        print(f"  split '{page['title']}' into {len(sections)} sections")

    pages = merge_definitions(pages)
    await save_pages(pages, "content-style-guide")


async def scrape_govuk_content_guidance(client: httpx.AsyncClient) -> None:
    print("Scraping GOV.UK writing guidance...")
    sitemap_paths = await discover_guidance_paths(client)
    paths = [
        path for path in sitemap_paths if path.startswith(CONTENT_GUIDANCE_PREFIXES)
    ]
    for extra in CONTENT_GUIDANCE_EXTRA_PATHS:
        if extra not in paths:
            paths.append(extra)

    pages = await fetch_guidance_pages(client, paths)
    await save_pages(pages, "content-guidance")


async def scrape_defra_style_guide(client: httpx.AsyncClient) -> None:
    """Scrape the DEFRA style guide (single static page, split per letter)."""
    print("Scraping DEFRA style guide...")
    response = await client.get(DEFRA_STYLE_GUIDE_URL)
    response.raise_for_status()
    soup = bs4.BeautifulSoup(response.text, "lxml")

    main = soup.select_one("main#main-content")
    if main is None:
        msg = "DEFRA style guide: main#main-content not found — page layout changed?"
        raise RuntimeError(msg)

    # The A-to-Z content sits in the two-thirds column with the letter headings.
    columns = main.select("div.govuk-grid-column-two-thirds")
    content_column = max(
        columns,
        key=lambda c: len(c.select("h2.govuk-heading-m")),
        default=None,
    )
    if content_column is None or not content_column.select("h2.govuk-heading-m"):
        msg = "DEFRA style guide: content column not found — page layout changed?"
        raise RuntimeError(msg)

    description = (
        "Defra style guide: terminology and usage conventions for Defra content, "
        "complementing the GDS style guide."
    )
    page = {
        "link": "/content/defra-style-guide",
        "title": "Defra style guide",
        "description": description,
        "content": markdownify(str(content_column), heading_style="ATX"),
    }

    sections = split_by_heading(page, level=2, classify=False)
    for section in sections:
        if section["title"] != "Overview":
            section["title"] = f"Defra style guide — {section['title']}"
    print(f"  split into {len(sections)} sections")

    await save_pages(sections, "defra-style-guide")


async def main() -> None:
    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT, follow_redirects=True
    ) as client:
        await scrape_govuk_style_guide(client)
        await scrape_govuk_content_guidance(client)
        await scrape_defra_style_guide(client)
    print(f"Done. Context store populated at {CONTEXT_ROOT.absolute()}")


if __name__ == "__main__":
    asyncio.run(main())
