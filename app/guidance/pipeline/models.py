from dataclasses import dataclass, field
from typing import Any, cast


@dataclass
class InlineSpan:
    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    hyperlink: str = ""
    color: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"text": self.text}
        if self.bold:
            d["bold"] = True
        if self.italic:
            d["italic"] = True
        if self.underline:
            d["underline"] = True
        if self.hyperlink:
            d["hyperlink"] = self.hyperlink
        if self.color:
            d["color"] = self.color
        return d

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
class ParagraphNode:
    spans: list[InlineSpan]
    node_type: str = field(default="paragraph", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_type": self.node_type,
            "spans": [s.to_dict() for s in self.spans],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ParagraphNode:
        return cls(spans=[InlineSpan.from_dict(s) for s in data["spans"]])


@dataclass
class ListItemNode:
    spans: list[InlineSpan]
    level: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "spans": [s.to_dict() for s in self.spans],
            "level": self.level,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ListItemNode:
        return cls(
            spans=[InlineSpan.from_dict(s) for s in data["spans"]],
            level=data.get("level", 0),
        )


@dataclass
class ListNode:
    items: list[ListItemNode]
    list_type: str = "bullet"
    node_type: str = field(default="list", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_type": self.node_type,
            "list_type": self.list_type,
            "items": [item.to_dict() for item in self.items],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ListNode:
        return cls(
            items=[ListItemNode.from_dict(i) for i in data["items"]],
            list_type=data.get("list_type", "bullet"),
        )


@dataclass
class TableNode:
    headers: list[str]
    rows: list[list[str]]
    node_type: str = field(default="table", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_type": self.node_type,
            "headers": self.headers,
            "rows": self.rows,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TableNode:
        return cls(headers=data["headers"], rows=data["rows"])


@dataclass
class ImageNode:
    rel_path: str
    alt_text: str = ""
    data: bytes = field(default_factory=bytes)
    ext: str = ".png"
    node_type: str = field(default="image", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_type": self.node_type,
            "rel_path": self.rel_path,
            "alt_text": self.alt_text,
        }

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
class SectionNode:
    heading: str
    level: int
    number: str = ""
    children: list[SectionNode] = field(default_factory=list)
    content: list[ContentNode] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "heading": self.heading,
            "level": self.level,
            "number": self.number,
            "children": [c.to_dict() for c in self.children],
            "content": [n.to_dict() for n in self.content],
        }

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
class DocumentTree:
    title: str
    children: list[SectionNode] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "children": [c.to_dict() for c in self.children],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DocumentTree:
        tree = cls(title=data["title"])
        tree.children = [SectionNode.from_dict(c) for c in data.get("children", [])]
        return tree
