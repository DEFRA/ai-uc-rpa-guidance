from app.guidance.pipeline.models import (
    DocumentTree,
    ImageNode,
    ImageSpan,
    InlineSpan,
    ListItemNode,
    ListNode,
    ParagraphNode,
    SectionNode,
    TableNode,
)
from app.guidance.pipeline.renderers.markdown import section_to_markdown, to_markdown


class TestMarkdownRenderer:
    def _make_tree(self) -> DocumentTree:
        return DocumentTree(
            title="Test Document",
            children=[
                SectionNode(
                    heading="Overview",
                    level=1,
                    number="1",
                    content=[
                        ParagraphNode(spans=[InlineSpan(text="Intro text here.")]),
                    ],
                    children=[
                        SectionNode(
                            heading="Sub Topic",
                            level=2,
                            number="1.1",
                            content=[
                                TableNode(
                                    headers=["Col A", "Col B"],
                                    rows=[["r1a", "r1b"], ["r2a", "r2b"]],
                                ),
                                ImageNode(
                                    rel_path="output/images/img_1.png",
                                    alt_text="Diagram",
                                ),
                            ],
                        )
                    ],
                ),
                SectionNode(
                    heading="Conclusion",
                    level=1,
                    number="2",
                    content=[ParagraphNode(spans=[InlineSpan(text="Done.")])],
                ),
            ],
        )

    def test_title(self):
        md = to_markdown(self._make_tree())
        assert md.startswith("# Test Document\n")

    def test_numbered_headings(self):
        md = to_markdown(self._make_tree())
        assert "## 1 Overview" in md
        assert "### 1.1 Sub Topic" in md
        assert "## 2 Conclusion" in md

    def test_paragraph_content(self):
        md = to_markdown(self._make_tree())
        assert "Intro text here." in md
        assert "Done." in md

    def test_table_rendering(self):
        md = to_markdown(self._make_tree())
        assert "| Col A | Col B |" in md
        assert "| --- | --- |" in md
        assert "| r1a | r1b |" in md
        assert "| r2a | r2b |" in md

    def test_image_rendering(self):
        md = to_markdown(self._make_tree())
        assert "![Diagram](output/images/img_1.png)" in md

    def test_inline_image_span_rendered_within_text(self):
        tree = DocumentTree(
            title="T",
            children=[
                SectionNode(
                    heading="S",
                    level=1,
                    number="1",
                    content=[
                        ListNode(
                            items=[
                                ListItemNode(
                                    spans=[
                                        InlineSpan(text="Select the "),
                                        ImageSpan(rel_path="/img/icon.png"),
                                        InlineSpan(text="binocular icon"),
                                    ]
                                )
                            ]
                        )
                    ],
                )
            ],
        )
        md = to_markdown(tree)
        assert "- Select the ![](/img/icon.png)binocular icon" in md

    def test_single_cell_table_renders_as_blockquote(self):
        tree = DocumentTree(
            title="T",
            children=[
                SectionNode(
                    heading="S",
                    level=1,
                    number="1",
                    content=[TableNode(headers=["Note: review this policy."], rows=[])],
                )
            ],
        )
        md = to_markdown(tree)
        assert "> Note: review this policy." in md
        assert "|" not in md

    def test_empty_tree(self):
        tree = DocumentTree(title="Empty")
        md = to_markdown(tree)
        assert md == "# Empty\n"


class TestMarkdownInlineFormatting:
    def test_plain_text_unmodified(self):
        tree = DocumentTree(
            title="Plain",
            children=[
                SectionNode(
                    heading="Test",
                    level=1,
                    number="1",
                    content=[
                        ParagraphNode(
                            spans=[
                                InlineSpan(text="Normal "),
                                InlineSpan(text="important", bold=True),
                                InlineSpan(text=" end"),
                            ]
                        )
                    ],
                )
            ],
        )
        md = to_markdown(tree)
        assert "Normal " in md
        assert " end" in md

    def test_bold_rendering(self):
        tree = DocumentTree(
            title="Bold",
            children=[
                SectionNode(
                    heading="Test",
                    level=1,
                    number="1",
                    content=[
                        ParagraphNode(spans=[InlineSpan(text="important", bold=True)])
                    ],
                )
            ],
        )
        md = to_markdown(tree)
        assert "<strong>important</strong>" in md

    def test_italic_rendering(self):
        tree = DocumentTree(
            title="Italic",
            children=[
                SectionNode(
                    heading="Test",
                    level=1,
                    number="1",
                    content=[
                        ParagraphNode(spans=[InlineSpan(text="emphasis", italic=True)])
                    ],
                )
            ],
        )
        md = to_markdown(tree)
        assert "<em>emphasis</em>" in md

    def test_hyperlink_rendering(self):
        tree = DocumentTree(
            title="Links",
            children=[
                SectionNode(
                    heading="Test",
                    level=1,
                    number="1",
                    content=[
                        ParagraphNode(
                            spans=[
                                InlineSpan(text="Click "),
                                InlineSpan(
                                    text="here", hyperlink="https://example.com"
                                ),
                            ]
                        )
                    ],
                )
            ],
        )
        md = to_markdown(tree)
        assert "[here](<https://example.com>)" in md

    def _section_with_span(self, title: str, span: InlineSpan) -> DocumentTree:
        return DocumentTree(
            title=title,
            children=[
                SectionNode(
                    heading="Test",
                    level=1,
                    number="1",
                    content=[ParagraphNode(spans=[span])],
                )
            ],
        )

    def _para_tree(self, title: str, spans: list[InlineSpan]) -> DocumentTree:
        return DocumentTree(
            title=title,
            children=[
                SectionNode(
                    heading="Test",
                    level=1,
                    number="1",
                    content=[ParagraphNode(spans=spans)],
                )
            ],
        )

    def test_bold_italic_rendering(self):
        md = to_markdown(
            self._section_with_span(
                "BI", InlineSpan(text="word", bold=True, italic=True)
            )
        )
        assert "<strong><em>word</em></strong>" in md

    def test_bold_hyperlink_rendering(self):
        md = to_markdown(
            self._section_with_span(
                "BL",
                InlineSpan(text="here", bold=True, hyperlink="https://example.com"),
            )
        )
        assert "<strong>[here](<https://example.com>)</strong>" in md

    def test_italic_hyperlink_rendering(self):
        md = to_markdown(
            self._section_with_span(
                "IL",
                InlineSpan(text="here", italic=True, hyperlink="https://example.com"),
            )
        )
        assert "<em>[here](<https://example.com>)</em>" in md

    def test_bold_italic_hyperlink_rendering(self):
        md = to_markdown(
            self._section_with_span(
                "BIL",
                InlineSpan(
                    text="here",
                    bold=True,
                    italic=True,
                    hyperlink="https://example.com",
                ),
            )
        )
        assert "<strong><em>[here](<https://example.com>)</em></strong>" in md

    def test_underline_rendering(self):
        md = to_markdown(
            self._section_with_span("U", InlineSpan(text="underlined", underline=True))
        )
        assert "<u>underlined</u>" in md

    def test_underlined_hyperlink_strips_underline(self):
        """A hyperlink's intrinsic underline is decorative and must not be emitted."""
        md = to_markdown(
            self._section_with_span(
                "UL",
                InlineSpan(
                    text="here",
                    underline=True,
                    hyperlink="https://example.com",
                ),
            )
        )
        assert "[here](<https://example.com>)" in md
        assert "<u>" not in md

    def test_adjacent_bold_spans_no_collision(self):
        """Adjacent bold spans must not produce delimiter-collision artifacts."""
        md = to_markdown(
            self._para_tree(
                "Adj",
                [
                    InlineSpan(text="Open", bold=True),
                    InlineSpan(text=" relevant existing", bold=True),
                ],
            )
        )
        assert "<strong>Open</strong>" in md
        assert "<strong> relevant existing</strong>" in md
        assert "****" not in md

    def test_bold_link_then_bold_text(self):
        """Bold hyperlink followed by bold plain text renders cleanly."""
        url = "https://example.com"
        md = to_markdown(
            self._para_tree(
                "BLB",
                [
                    InlineSpan(text="Convert an Activity", bold=True, hyperlink=url),
                    InlineSpan(text=" Case", bold=True),
                ],
            )
        )
        assert f"<strong>[Convert an Activity](<{url}>)</strong>" in md
        assert "<strong> Case</strong>" in md

    def test_hyperlink_text_with_closing_bracket_escaped(self):
        """A ']' in link text must not prematurely close the markdown link."""
        md = to_markdown(
            self._section_with_span(
                "Esc",
                InlineSpan(text="see [note]", hyperlink="https://example.com/note"),
            )
        )
        assert "[see [note\\]](<https://example.com/note>)" in md

    def test_mixed_formatting(self):
        """Spans with different emphasis render with the correct tags."""
        md = to_markdown(
            self._para_tree(
                "Mix",
                [
                    InlineSpan(text="bold", bold=True),
                    InlineSpan(text=" plain "),
                    InlineSpan(text="italic", italic=True),
                ],
            )
        )
        assert "<strong>bold</strong>" in md
        assert "<em>italic</em>" in md
        assert " plain " in md

    def test_html_special_chars_escaped(self):
        """Text containing HTML metacharacters must be escaped."""
        md = to_markdown(
            self._section_with_span("Esc", InlineSpan(text="a < b & c > d"))
        )
        assert "a &lt; b &amp; c &gt; d" in md


class TestMarkdownListRendering:
    def test_bullet_list(self):
        tree = DocumentTree(
            title="Lists",
            children=[
                SectionNode(
                    heading="Steps",
                    level=1,
                    number="1",
                    content=[
                        ListNode(
                            items=[
                                ListItemNode(spans=[InlineSpan(text="First")], level=0),
                                ListItemNode(
                                    spans=[InlineSpan(text="Second")], level=0
                                ),
                                ListItemNode(
                                    spans=[InlineSpan(text="Nested")], level=1
                                ),
                            ],
                            list_type="bullet",
                        )
                    ],
                )
            ],
        )
        md = to_markdown(tree)
        assert "- First" in md
        assert "- Second" in md
        assert "  - Nested" in md

    def test_ordered_list(self):
        tree = DocumentTree(
            title="Ordered",
            children=[
                SectionNode(
                    heading="Numbered",
                    level=1,
                    number="1",
                    content=[
                        ListNode(
                            items=[
                                ListItemNode(spans=[InlineSpan(text="One")], level=0),
                                ListItemNode(spans=[InlineSpan(text="Two")], level=0),
                            ],
                            list_type="ordered",
                        )
                    ],
                )
            ],
        )
        md = to_markdown(tree)
        assert "1. One" in md
        assert "2. Two" in md


class TestSectionToMarkdown:
    def _make_section_with_child(self) -> SectionNode:
        return SectionNode(
            heading="Overview",
            level=1,
            number="1",
            content=[ParagraphNode(spans=[InlineSpan(text="Direct content.")])],
            children=[
                SectionNode(
                    heading="Sub Topic",
                    level=2,
                    number="1.1",
                    content=[ParagraphNode(spans=[InlineSpan(text="Child content.")])],
                )
            ],
        )

    def test_heading_rendered(self) -> None:
        md = section_to_markdown(self._make_section_with_child())
        assert "## 1 Overview" in md

    def test_direct_content_rendered(self) -> None:
        md = section_to_markdown(self._make_section_with_child())
        assert "Direct content." in md

    def test_children_not_included(self) -> None:
        md = section_to_markdown(self._make_section_with_child())
        assert "Sub Topic" not in md
        assert "Child content." not in md

    def test_empty_content_section(self) -> None:
        section = SectionNode(heading="Empty", level=1, number="1", content=[])
        md = section_to_markdown(section)
        assert "## 1 Empty" in md

    def test_heading_level_matches_section_level(self) -> None:
        deep = SectionNode(
            heading="Deep",
            level=3,
            number="1.1.1",
            content=[],
        )
        md = section_to_markdown(deep)
        assert "#### 1.1.1 Deep" in md
