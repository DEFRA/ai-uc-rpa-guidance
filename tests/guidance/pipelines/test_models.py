import json

import pytest

from app.guidance.pipeline.models import (
    DocumentTree,
    ImageNode,
    InlineSpan,
    ListItemNode,
    ListNode,
    ParagraphNode,
    SectionNode,
    TableNode,
)


class TestInlineSpan:
    def test_to_dict_minimal(self):
        span = InlineSpan(text="hello")
        assert span.to_dict() == {"text": "hello"}

    def test_to_dict_all_fields(self):
        span = InlineSpan(text="click", bold=True, italic=True, hyperlink="http://x")
        d = span.to_dict()
        assert d == {
            "text": "click",
            "bold": True,
            "italic": True,
            "hyperlink": "http://x",
        }

    def test_from_dict(self):
        span = InlineSpan.from_dict({"text": "hi", "bold": True})
        assert span.text == "hi"
        assert span.bold is True
        assert span.italic is False
        assert span.hyperlink == ""


class TestParagraphNode:
    def test_to_dict(self):
        node = ParagraphNode(spans=[InlineSpan(text="Hello world")])
        d = node.to_dict()
        assert d["node_type"] == "paragraph"
        assert d["spans"] == [{"text": "Hello world"}]

    def test_from_dict(self):
        node = ParagraphNode.from_dict({"spans": [{"text": "Hello", "bold": True}]})
        assert len(node.spans) == 1
        assert node.spans[0].bold is True


class TestListNode:
    def test_to_dict(self):
        node = ListNode(
            items=[
                ListItemNode(spans=[InlineSpan(text="Item 1")], level=0),
                ListItemNode(spans=[InlineSpan(text="Sub item", bold=True)], level=1),
            ],
            list_type="bullet",
        )
        d = node.to_dict()
        assert d["node_type"] == "list"
        assert d["list_type"] == "bullet"
        assert len(d["items"]) == 2
        assert d["items"][1]["level"] == 1

    def test_from_dict(self):
        data = {
            "node_type": "list",
            "list_type": "ordered",
            "items": [{"spans": [{"text": "First"}], "level": 0}],
        }
        node = ListNode.from_dict(data)
        assert node.list_type == "ordered"
        assert len(node.items) == 1
        assert node.items[0].spans[0].text == "First"


class TestTableNode:
    def test_to_dict(self):
        node = TableNode(headers=["A", "B"], rows=[["1", "2"], ["3", "4"]])
        d = node.to_dict()
        assert d["node_type"] == "table"
        assert d["headers"] == ["A", "B"]
        assert d["rows"] == [["1", "2"], ["3", "4"]]

    def test_from_dict(self):
        data = {"headers": ["X"], "rows": [["val"]]}
        node = TableNode.from_dict(data)
        assert node.headers == ["X"]
        assert node.rows == [["val"]]


class TestImageNode:
    def test_to_dict(self):
        node = ImageNode(rel_path="images/img_1.png", alt_text="Logo")
        d = node.to_dict()
        assert d == {
            "node_type": "image",
            "rel_path": "images/img_1.png",
            "alt_text": "Logo",
        }

    def test_from_dict_with_alt(self):
        node = ImageNode.from_dict({"rel_path": "x.png", "alt_text": "pic"})
        assert node.rel_path == "x.png"
        assert node.alt_text == "pic"

    def test_from_dict_without_alt(self):
        node = ImageNode.from_dict({"rel_path": "x.png"})
        assert node.alt_text == ""


class TestSectionNode:
    def test_round_trip(self):
        section = SectionNode(
            heading="Overview",
            level=1,
            number="1",
            content=[ParagraphNode(spans=[InlineSpan(text="Intro")])],
            children=[
                SectionNode(
                    heading="Details",
                    level=2,
                    number="1.1",
                    content=[TableNode(headers=["Col"], rows=[["val"]])],
                )
            ],
        )
        d = section.to_dict()
        restored = SectionNode.from_dict(d)
        assert restored.heading == "Overview"
        assert restored.number == "1"
        assert len(restored.children) == 1
        assert restored.children[0].heading == "Details"
        assert isinstance(restored.content[0], ParagraphNode)
        assert isinstance(restored.children[0].content[0], TableNode)


class TestDocumentTree:
    def test_round_trip_json(self):
        tree = DocumentTree(
            title="Test Doc",
            children=[
                SectionNode(
                    heading="Section A",
                    level=1,
                    number="1",
                    content=[
                        ParagraphNode(spans=[InlineSpan(text="text")]),
                        ListNode(
                            items=[ListItemNode(spans=[InlineSpan(text="bullet")])],
                        ),
                        ImageNode(rel_path="img.png", alt_text="alt"),
                    ],
                    children=[SectionNode(heading="Sub", level=2, number="1.1")],
                )
            ],
        )
        d = tree.to_dict()
        json_str = json.dumps(d)
        restored = DocumentTree.from_dict(json.loads(json_str))

        assert restored.title == "Test Doc"
        assert len(restored.children) == 1
        assert restored.children[0].heading == "Section A"
        assert len(restored.children[0].content) == 3
        assert isinstance(restored.children[0].content[1], ListNode)
        assert isinstance(restored.children[0].content[2], ImageNode)
        assert restored.children[0].children[0].number == "1.1"

    def test_empty_tree(self):
        tree = DocumentTree(title="Empty")
        d = tree.to_dict()
        restored = DocumentTree.from_dict(d)
        assert restored.title == "Empty"
        assert restored.children == []


def _make_tree() -> DocumentTree:
    return DocumentTree(
        title="Doc",
        children=[
            SectionNode(
                heading="Intro",
                level=1,
                number="1",
                content=[ParagraphNode(spans=[InlineSpan(text="intro text")])],
                children=[
                    SectionNode(heading="Background", level=2, number="1.1"),
                    SectionNode(heading="Scope", level=2, number="1.2"),
                ],
            ),
            SectionNode(
                heading="Details",
                level=1,
                number="2",
                content=[ParagraphNode(spans=[InlineSpan(text="details text")])],
            ),
        ],
    )


class TestDocumentTreeIndex:
    def test_section_lookup(self):
        tree = _make_tree()
        assert tree.section("1").heading == "Intro"
        assert tree.section("1.1").heading == "Background"
        assert tree.section("1.2").heading == "Scope"
        assert tree.section("2").heading == "Details"

    def test_section_missing_raises(self):
        tree = _make_tree()
        with pytest.raises(KeyError):
            tree.section("9")

    def test_sections_order(self):
        tree = _make_tree()
        numbers = [s.number for s in tree.sections]
        assert numbers == ["1", "1.1", "1.2", "2"]

    def test_sections_headings(self):
        tree = _make_tree()
        headings = [s.heading for s in tree.sections]
        assert headings == ["Intro", "Background", "Scope", "Details"]

    def test_extract_title(self):
        tree = _make_tree()
        chunk = tree.extract("1")
        assert chunk.title == "Doc — 1 Intro"

    def test_extract_contains_section(self):
        tree = _make_tree()
        chunk = tree.extract("1")
        assert len(chunk.children) == 1
        assert chunk.children[0].heading == "Intro"

    def test_extract_preserves_children(self):
        tree = _make_tree()
        chunk = tree.extract("1")
        assert len(chunk.children[0].children) == 2

    def test_extract_preserves_content(self):
        tree = _make_tree()
        chunk = tree.extract("2")
        content = chunk.children[0].content
        assert len(content) == 1
        assert isinstance(content[0], ParagraphNode)

    def test_extract_renderable(self):
        from app.guidance.pipeline.renderers.markdown import to_markdown

        tree = _make_tree()
        md = to_markdown(tree.extract("1"))
        assert "# Doc — 1 Intro" in md
        assert "## 1 Intro" in md
