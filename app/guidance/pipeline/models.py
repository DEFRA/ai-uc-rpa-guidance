from dataclasses import dataclass, field, fields
from typing import Any, cast


class Serializable:
    """Mixin providing generic to_dict() for dataclasses.

    Skips fields listed in _skip_fields. Omits falsy primitive values to keep
    output compact (matching the original InlineSpan sparse-dict behavior).
    """

    _skip_fields: frozenset[str] = frozenset()

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        for f in fields(self):  # type: ignore[arg-type]
            if f.name in self._skip_fields:
                continue
            val = getattr(self, f.name)
            if isinstance(val, list):
                d[f.name] = [v.to_dict() if hasattr(v, "to_dict") else v for v in val]
            elif hasattr(val, "to_dict"):
                d[f.name] = val.to_dict()
            elif val is True or (val and val is not False):
                d[f.name] = val
        return d


@dataclass
class InlineSpan(Serializable):
    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    hyperlink: str = ""
    color: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InlineSpan:
        return cls(
            text=data["text"],
            bold=data.get("bold", False),
            italic=data.get("italic", False),
            underline=data.get("underline", False),
            hyperlink=data.get("hyperlink", ""),
            color=data.get("color", ""),
        )


@dataclass
class ParagraphNode(Serializable):
    spans: list[InlineSpan]
    node_type: str = field(default="paragraph", init=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ParagraphNode:
        return cls(spans=[InlineSpan.from_dict(s) for s in data["spans"]])


@dataclass
class ListItemNode(Serializable):
    spans: list[InlineSpan]
    level: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ListItemNode:
        return cls(
            spans=[InlineSpan.from_dict(s) for s in data["spans"]],
            level=data.get("level", 0),
        )


@dataclass
class ListNode(Serializable):
    items: list[ListItemNode]
    list_type: str = "bullet"
    node_type: str = field(default="list", init=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ListNode:
        return cls(
            items=[ListItemNode.from_dict(i) for i in data["items"]],
            list_type=data.get("list_type", "bullet"),
        )


@dataclass
class TableNode(Serializable):
    headers: list[str]
    rows: list[list[str]]
    node_type: str = field(default="table", init=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TableNode:
        return cls(headers=data["headers"], rows=data["rows"])


@dataclass
class ImageNode(Serializable):
    rel_path: str
    alt_text: str = ""
    data: bytes = field(default_factory=bytes)
    ext: str = ".png"
    node_type: str = field(default="image", init=False)
    _skip_fields: frozenset[str] = field(
        default=frozenset({"data", "ext", "_skip_fields"}), init=False, repr=False
    )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImageNode:
        return cls(rel_path=data["rel_path"], alt_text=data.get("alt_text", ""))


ContentNode = ParagraphNode | ListNode | TableNode | ImageNode

_NODE_REGISTRY: dict[str, type] = {
    "paragraph": ParagraphNode,
    "list": ListNode,
    "table": TableNode,
    "image": ImageNode,
}


def _content_node_from_dict(data: dict[str, Any]) -> ContentNode:
    cls = cast(type[ContentNode], _NODE_REGISTRY[data["node_type"]])
    return cls.from_dict(data)


@dataclass
class SectionNode(Serializable):
    heading: str
    level: int
    number: str = ""
    children: list[SectionNode] = field(default_factory=list)
    content: list[ContentNode] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SectionNode:
        node = cls(
            heading=data["heading"],
            level=data["level"],
            number=data.get("number", ""),
        )
        node.children = [SectionNode.from_dict(c) for c in data.get("children", [])]
        node.content = [_content_node_from_dict(n) for n in data.get("content", [])]
        return node


@dataclass
class DocumentTree(Serializable):
    title: str
    children: list[SectionNode] = field(default_factory=list)

    # _index and _order are plain instance attributes (not dataclass fields),
    # so they are invisible to to_dict() and built lazily on first access.

    def _ensure_index(self) -> None:
        if hasattr(self, "_index"):
            return

        self._index: dict[str, SectionNode] = {}
        self._order: list[str] = []

        def _walk(sections: list[SectionNode]) -> None:
            for s in sections:
                self._index[s.number] = s
                self._order.append(s.number)
                _walk(s.children)

        _walk(self.children)

    def section(self, number: str) -> SectionNode:
        """Return the section with the given number. O(1)."""
        self._ensure_index()
        return self._index[number]

    def extract(self, number: str) -> DocumentTree:
        """Return the section as a self-contained DocumentTree."""
        s = self.section(number)
        chunk = DocumentTree(title=f"{self.title} — {s.number} {s.heading}")
        chunk.children = [s]
        return chunk

    @property
    def sections(self) -> list[SectionNode]:
        """All sections in document order, flattened."""
        self._ensure_index()
        return [self._index[n] for n in self._order]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DocumentTree:
        tree = cls(title=data["title"])
        tree.children = [SectionNode.from_dict(c) for c in data.get("children", [])]
        return tree


@dataclass(frozen=True)
class ManifestSectionNode:
    """A single node in the document manifest graph."""

    number: str
    heading: str
    level: int
    parent: str | None
    children: list[str]
    links: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DocumentManifest:
    """Flat adjacency-list graph of all sections in a parsed guidance document.

    Sections are in document order; hierarchy is encoded via parent/children
    references (section numbers as strings). O(n) to iterate, O(1) to look up
    by number once indexed in memory.
    """

    document_id: str
    title: str
    sections: list[ManifestSectionNode]
