#!/usr/bin/env python3
"""Parse a .docx into Markdown with the guidance pipeline, without the stack.

Runs the same two steps the application runs when a document is uploaded --
``parse_doc`` to build the tree, then the Markdown renderer over it -- but writes
the result to a local file instead of S3. Nothing else in the service is
involved: ``app.guidance.pipeline`` imports only the standard library and
python-docx, so no configuration, database, S3 or Bedrock access is needed.

Images are optional and off unless ``--images-dir`` is given. The application
assigns each image's path while uploading it to S3
(``PipelineDocumentParser._upload_images``); this script does the same
assignment against a local directory, keeping the application's filenames so the
two outputs stay comparable.

Usage:
  uv run scripts/parse_docx.py <document.docx> <output.md> \
      [--images-dir DIR] [--images-prefix PREFIX]

Called directly, or by ``scripts/convert_doc.py`` in the local-dev orchestrator
repository, which resolves paths and can additionally run the frontend's editor
round trip over the result.
"""

import argparse
import sys
from pathlib import Path

import docx
from docx.opc.exceptions import PackageNotFoundError

# Private, and imported deliberately: _collect_images is the single definition of
# which images a document has -- block figures *and* inline icons, a distinction
# that was its own bug fix -- so re-deriving the list here would risk disagreeing
# with the application about what to write. A rename upstream breaks this import
# loudly, which is the behaviour we want.
from app.guidance.documents.parser import _collect_images
from app.guidance.pipeline import models as pipeline_models
from app.guidance.pipeline import service as pipeline_service
from app.guidance.pipeline.renderers import markdown as markdown_renderer


def write_images(
    tree: pipeline_models.DocumentTree,
    images_dir: Path,
    prefix: str,
) -> int:
    """Write every image in the tree to disk and point the tree's nodes at it.

    Mirrors ``PipelineDocumentParser._upload_images``: the same per-section
    counter and the same ``{section}_img_{n}{ext}`` filenames, so a local run and
    a real upload produce the same names. Setting ``rel_path`` is what makes the
    renderer emit a usable image link -- it renders ``![alt]()`` otherwise.

    Returns the number of images written.
    """
    images_dir.mkdir(parents=True, exist_ok=True)

    section_counters: dict[str, int] = {}
    for section_number, node in _collect_images(tree):
        section_counters[section_number] = section_counters.get(section_number, 0) + 1
        filename = f"{section_number}_img_{section_counters[section_number]}{node.ext}"

        (images_dir / filename).write_bytes(node.data)
        node.rel_path = f"{prefix}{filename}"

    return sum(section_counters.values())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("document", help="Path to the guidance document (.docx).")
    parser.add_argument("output", help="Path to write the rendered Markdown to.")
    parser.add_argument(
        "--images-dir",
        default=None,
        help="Directory to write embedded images to (default: images are dropped).",
    )
    parser.add_argument(
        "--images-prefix",
        default="",
        help=(
            "Prefix for image paths in the Markdown, e.g. 'doc-images/' "
            "(default: bare filenames)."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    document = Path(args.document)
    if not document.is_file():
        message = f"Document not found: {document}"
        raise SystemExit(message)

    try:
        source = docx.Document(str(document))
    except PackageNotFoundError as error:
        # A .docx is a zip archive. Anything else -- an empty file, a .doc saved
        # under the wrong extension, a truncated download -- surfaces here, and a
        # one-line reason is more use than python-docx's traceback.
        message = f"Not a readable .docx file: {document}"
        raise SystemExit(message) from error

    tree = pipeline_service.parse_doc(source)

    written = 0
    if args.images_dir:
        written = write_images(tree, Path(args.images_dir), args.images_prefix)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(markdown_renderer.to_markdown(tree), encoding="utf-8")

    # Progress goes to stderr so that stdout stays free for the Markdown itself,
    # should this ever be asked to stream.
    print(
        f"Parsed {document.name}: {len(tree.sections)} sections, "
        f"{written} images -> {output}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
