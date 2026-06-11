import struct
import zlib

from docx import Document
from docx.shared import Inches

from app.guidance.pipeline import models, service


def _make_png() -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF
    ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", ihdr_crc)
    raw = b"\x00\xff\x00\x00"
    compressed = zlib.compress(raw)
    idat_crc = zlib.crc32(b"IDAT" + compressed) & 0xFFFFFFFF
    idat = (
        struct.pack(">I", len(compressed))
        + b"IDAT"
        + compressed
        + struct.pack(">I", idat_crc)
    )
    iend_crc = zlib.crc32(b"IEND") & 0xFFFFFFFF
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc)
    return sig + ihdr + idat + iend


def _make_test_docx() -> Document:
    """Create a synthetic docx with headings, paragraphs, a table, and an image."""
    doc = Document()

    doc.add_heading("Introduction", level=1)
    doc.add_paragraph("This is the intro paragraph.")

    doc.add_heading("Details", level=2)
    doc.add_paragraph("Some details here.")

    # Add a table
    table = doc.add_table(rows=3, cols=2)
    table.cell(0, 0).text = "Name"
    table.cell(0, 1).text = "Value"
    table.cell(1, 0).text = "Alpha"
    table.cell(1, 1).text = "100"
    table.cell(2, 0).text = "Beta"
    table.cell(2, 1).text = "200"

    doc.add_heading("More Info", level=2)
    doc.add_paragraph("Additional information.")

    doc.add_heading("Conclusion", level=1)
    doc.add_paragraph("Final thoughts.")

    return doc


class TestParserStructure:
    def test_nested_sections(self):
        doc = _make_test_docx()
        tree = service.parse_doc(doc, title="TestDoc")

        assert tree.title == "TestDoc"
        assert len(tree.children) == 2
        assert tree.children[0].heading == "Introduction"
        assert tree.children[1].heading == "Conclusion"

    def test_subsections_nested(self):
        doc = _make_test_docx()
        tree = service.parse_doc(doc, title="TestDoc")

        intro = tree.children[0]
        assert len(intro.children) == 2
        assert intro.children[0].heading == "Details"
        assert intro.children[1].heading == "More Info"

    def test_section_numbers(self):
        doc = _make_test_docx()
        tree = service.parse_doc(doc, title="TestDoc")

        assert tree.children[0].number == "1"
        assert tree.children[0].children[0].number == "1.1"
        assert tree.children[0].children[1].number == "1.2"
        assert tree.children[1].number == "2"

    def test_paragraph_content(self):
        doc = _make_test_docx()
        tree = service.parse_doc(doc, title="TestDoc")

        intro = tree.children[0]
        paras = [n for n in intro.content if isinstance(n, models.ParagraphNode)]
        assert any(
            "This is the intro paragraph." in "".join(s.text for s in p.spans)
            for p in paras
        )

    def test_table_extraction(self):
        doc = _make_test_docx()
        tree = service.parse_doc(doc, title="TestDoc")

        details = tree.children[0].children[0]  # "Details" subsection
        tables = [n for n in details.content if isinstance(n, models.TableNode)]
        assert len(tables) == 1
        assert tables[0].headers == ["Name", "Value"]
        assert tables[0].rows == [["Alpha", "100"], ["Beta", "200"]]


class TestParserImages:
    def test_image_extraction(self, tmp_path):
        """Test that an inline image is extracted and held in memory."""
        doc = Document()
        doc.add_heading("With Image", level=1)

        png_path = tmp_path / "test.png"
        png_path.write_bytes(_make_png())
        doc.add_picture(str(png_path), width=Inches(1))

        doc.add_paragraph("After the image.")

        tree = service.parse_doc(doc, title="ImageTest")

        section = tree.children[0]
        images = [n for n in section.content if isinstance(n, models.ImageNode)]
        assert len(images) == 1
        # rel_path is blank until the caller uploads to S3
        assert images[0].rel_path == ""
        # Raw bytes are in memory
        assert len(images[0].data) > 0
        assert images[0].ext == ".png"


class TestParserLists:
    def test_bullet_list_extraction(self):
        """Test that bullet list paragraphs are grouped into a ListNode."""
        doc = Document()
        doc.add_heading("Steps", level=1)
        doc.add_paragraph("Step one", style="List Bullet")
        doc.add_paragraph("Step two", style="List Bullet")
        doc.add_paragraph("Sub step", style="List Bullet 2")
        doc.add_paragraph("Normal text after list.")

        tree = service.parse_doc(doc, title="ListTest")

        section = tree.children[0]
        lists = [n for n in section.content if isinstance(n, models.ListNode)]
        paras = [n for n in section.content if isinstance(n, models.ParagraphNode)]

        assert len(lists) == 1
        assert len(lists[0].items) == 3
        assert lists[0].items[0].level == 0
        assert lists[0].items[2].level == 1  # List Bullet 2 = nested
        assert len(paras) == 1


class TestParserInlineFormatting:
    def test_bold_spans(self):
        """Test that bold runs are captured in spans."""
        doc = Document()
        doc.add_heading("Formatted", level=1)
        para = doc.add_paragraph()
        para.add_run("Normal text ")
        para.add_run("bold text").bold = True
        para.add_run(" more normal")

        tree = service.parse_doc(doc, title="BoldTest")

        section = tree.children[0]
        paras = [n for n in section.content if isinstance(n, models.ParagraphNode)]
        assert len(paras) == 1
        spans = paras[0].spans
        bold_spans = [s for s in spans if s.bold]
        assert any("bold text" in s.text for s in bold_spans)

    def test_color_spans(self):
        """Test that font colour is captured in spans."""
        from docx.shared import RGBColor

        doc = Document()
        doc.add_heading("Coloured", level=1)
        para = doc.add_paragraph()
        run = para.add_run("red warning")
        run.font.color.rgb = RGBColor(0xFF, 0x00, 0x00)

        tree = service.parse_doc(doc, title="ColorTest")

        section = tree.children[0]
        paras = [n for n in section.content if isinstance(n, models.ParagraphNode)]
        assert len(paras) == 1
        colored = [s for s in paras[0].spans if s.color]
        assert len(colored) == 1
        assert colored[0].color == "FF0000"

    def test_underline_spans(self):
        """Test that underlined runs are captured in spans."""
        doc = Document()
        doc.add_heading("Underlined", level=1)
        para = doc.add_paragraph()
        run = para.add_run("underlined text")
        run.underline = True

        tree = service.parse_doc(doc, title="UnderlineTest")

        section = tree.children[0]
        paras = [n for n in section.content if isinstance(n, models.ParagraphNode)]
        assert len(paras) == 1
        underlined = [s for s in paras[0].spans if s.underline]
        assert any("underlined text" in s.text for s in underlined)

    def test_bold_val_false_not_bold(self):
        """w:b w:val='false' must not be treated as bold (explicit-off overrides presence)."""
        from docx.oxml.ns import qn
        from lxml import etree

        doc = Document()
        doc.add_heading("Toggle", level=1)
        para = doc.add_paragraph()
        run = para.add_run("not bold")

        # Inject w:b w:val="false" directly into the run's rPr to simulate a
        # style that explicitly disables bold on an otherwise-bold base style.
        rpr = run._r.get_or_add_rPr()
        b_elem = etree.SubElement(rpr, qn("w:b"))
        b_elem.set(qn("w:val"), "false")

        tree = service.parse_doc(doc, title="ValFalseTest")

        section = tree.children[0]
        paras = [n for n in section.content if isinstance(n, models.ParagraphNode)]
        assert len(paras) == 1
        spans = paras[0].spans
        bold_spans = [s for s in spans if s.bold]
        assert not any("not bold" in s.text for s in bold_spans)


class TestTitleInference:
    def test_explicit_title_wins(self):
        doc = Document()
        doc.core_properties.title = "Core Title"
        doc.add_heading("Heading One", level=1)
        tree = service.parse_doc(doc, title="Explicit Title")
        assert tree.title == "Explicit Title"

    def test_infers_from_core_properties(self):
        doc = Document()
        doc.core_properties.title = "Core Title"
        doc.add_heading("Heading One", level=1)
        tree = service.parse_doc(doc)
        assert tree.title == "Core Title"

    def test_infers_from_title_style(self):
        doc = Document()
        doc.core_properties.title = ""
        para = doc.add_paragraph("Style Title")
        para.style = doc.styles["Title"]
        doc.add_heading("Heading One", level=1)
        tree = service.parse_doc(doc)
        assert tree.title == "Style Title"

    def test_infers_from_first_heading1(self):
        doc = Document()
        doc.core_properties.title = ""
        doc.add_heading("First Heading", level=1)
        doc.add_heading("Second Heading", level=1)
        tree = service.parse_doc(doc)
        assert tree.title == "First Heading"

    def test_empty_title_when_no_headings(self):
        doc = Document()
        doc.core_properties.title = ""
        doc.add_paragraph("Just a paragraph.")
        tree = service.parse_doc(doc)
        assert tree.title == ""

    def test_extract_title_core_properties(self):
        doc = Document()
        doc.core_properties.title = "  Trimmed Title  "
        assert service.DocxParser._extract_title(doc) == "Trimmed Title"


class TestParserHyperlinks:
    def test_hyperlink_in_span(self):
        """w:hyperlink elements in a paragraph produce InlineSpan.hyperlink with the target URL."""
        from docx.opc.constants import RELATIONSHIP_TYPE as RT
        from docx.oxml.ns import qn
        from lxml import etree

        url = "https://example.com"

        doc = Document()
        doc.add_heading("Links", level=1)
        para = doc.add_paragraph()

        # Register an external hyperlink relationship on the paragraph's part.
        r_id = para.part.rels.get_or_add_ext_rel(RT.HYPERLINK, url)

        # Build a w:hyperlink element with the relationship id and a run inside it.
        hyperlink_elem = etree.SubElement(para._element, qn("w:hyperlink"))
        hyperlink_elem.set(qn("r:id"), r_id)
        run_elem = etree.SubElement(hyperlink_elem, qn("w:r"))
        t_elem = etree.SubElement(run_elem, qn("w:t"))
        t_elem.text = "click here"

        tree = service.parse_doc(doc, title="HyperlinkTest")

        section = tree.children[0]
        paras = [n for n in section.content if isinstance(n, models.ParagraphNode)]
        assert len(paras) == 1
        hyperlinked = [s for s in paras[0].spans if s.hyperlink]
        assert len(hyperlinked) == 1
        assert hyperlinked[0].text == "click here"
        assert hyperlinked[0].hyperlink == url


class TestParserImageEdgeCases:
    def test_image_missing_embed_id(self, tmp_path):
        """A blip element with no r:embed attribute is silently skipped — no ImageNode, no error."""
        from docx.oxml.ns import qn

        doc = Document()
        doc.add_heading("Section", level=1)

        png_path = tmp_path / "test.png"
        png_path.write_bytes(_make_png())
        doc.add_picture(str(png_path), width=Inches(1))

        # Find the blip element and strip its r:embed attribute.
        body = doc.element.body
        blip = body.find(f".//{qn('a:blip')}")
        assert blip is not None
        embed_attr = qn("r:embed")
        if embed_attr in blip.attrib:
            del blip.attrib[embed_attr]

        tree = service.parse_doc(doc, title="NoEmbedTest")

        section = tree.children[0]
        images = [n for n in section.content if isinstance(n, models.ImageNode)]
        assert len(images) == 0

    def test_image_missing_relationship(self, tmp_path):
        """A blip with an r:embed that has no matching rel is silently skipped."""
        from docx.oxml.ns import qn

        doc = Document()
        doc.add_heading("Section", level=1)

        png_path = tmp_path / "test.png"
        png_path.write_bytes(_make_png())
        doc.add_picture(str(png_path), width=Inches(1))

        # Replace the r:embed value with an ID that doesn't exist in part.rels.
        body = doc.element.body
        blip = body.find(f".//{qn('a:blip')}")
        assert blip is not None
        blip.set(qn("r:embed"), "rId999")

        tree = service.parse_doc(doc, title="MissingRelTest")

        section = tree.children[0]
        images = [n for n in section.content if isinstance(n, models.ImageNode)]
        assert len(images) == 0


class TestParserContentOutsideSection:
    def test_table_before_heading_ignored(self):
        """A table that appears before any heading is silently discarded (empty stack)."""
        doc = Document()
        table = doc.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "H1"
        table.cell(0, 1).text = "H2"
        table.cell(1, 0).text = "A"
        table.cell(1, 1).text = "B"

        tree = service.parse_doc(doc, title="TableBeforeHeading")

        assert tree.children == []

    def test_image_before_heading_ignored(self, tmp_path):
        """An image that appears before any heading is silently discarded (empty stack)."""
        doc = Document()
        png_path = tmp_path / "test.png"
        png_path.write_bytes(_make_png())
        doc.add_picture(str(png_path), width=Inches(1))

        tree = service.parse_doc(doc, title="ImageBeforeHeading")

        assert tree.children == []

    def test_list_before_heading_ignored(self):
        """List items before the first heading are discarded when the stack is flushed empty."""
        doc = Document()
        doc.add_paragraph("Item one", style="List Bullet")
        doc.add_paragraph("Item two", style="List Bullet")
        # Adding a heading flushes the pending list with an empty stack.
        doc.add_heading("First Section", level=1)

        tree = service.parse_doc(doc, title="ListBeforeHeading")

        # The section exists but has no list content from the pre-heading items.
        assert len(tree.children) == 1
        section = tree.children[0]
        lists = [n for n in section.content if isinstance(n, models.ListNode)]
        assert lists == []


class TestParserListLevel:
    def test_numpr_ilvl_level(self):
        """A List Paragraph with w:numPr/w:ilvl w:val='2' reports level 2."""
        from docx.oxml.ns import qn
        from lxml import etree

        doc = Document()
        doc.add_heading("Numbered", level=1)
        para = doc.add_paragraph("Deep item", style="List Paragraph")

        # Inject w:numPr containing w:ilvl w:val="2" and a stub w:numId.
        ppr = para._element.get_or_add_pPr()
        num_pr = etree.SubElement(ppr, qn("w:numPr"))
        ilvl = etree.SubElement(num_pr, qn("w:ilvl"))
        ilvl.set(qn("w:val"), "2")
        num_id = etree.SubElement(num_pr, qn("w:numId"))
        num_id.set(qn("w:val"), "1")

        tree = service.parse_doc(doc, title="IlvlTest")

        section = tree.children[0]
        lists = [n for n in section.content if isinstance(n, models.ListNode)]
        assert len(lists) == 1
        assert lists[0].items[0].level == 2
