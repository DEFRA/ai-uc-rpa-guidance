from docx import Document
from docx.shared import Inches

from app.guidance.pipeline.models import ImageNode, ListNode, ParagraphNode, TableNode
from app.guidance.pipeline.service import _extract_title, parse_doc


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
        tree = parse_doc(doc, title="TestDoc")

        assert tree.title == "TestDoc"
        assert len(tree.children) == 2
        assert tree.children[0].heading == "Introduction"
        assert tree.children[1].heading == "Conclusion"

    def test_subsections_nested(self):
        doc = _make_test_docx()
        tree = parse_doc(doc, title="TestDoc")

        intro = tree.children[0]
        assert len(intro.children) == 2
        assert intro.children[0].heading == "Details"
        assert intro.children[1].heading == "More Info"

    def test_section_numbers(self):
        doc = _make_test_docx()
        tree = parse_doc(doc, title="TestDoc")

        assert tree.children[0].number == "1"
        assert tree.children[0].children[0].number == "1.1"
        assert tree.children[0].children[1].number == "1.2"
        assert tree.children[1].number == "2"

    def test_paragraph_content(self):
        doc = _make_test_docx()
        tree = parse_doc(doc, title="TestDoc")

        intro = tree.children[0]
        paras = [n for n in intro.content if isinstance(n, ParagraphNode)]
        assert any(
            "This is the intro paragraph." in "".join(s.text for s in p.spans)
            for p in paras
        )

    def test_table_extraction(self):
        doc = _make_test_docx()
        tree = parse_doc(doc, title="TestDoc")

        details = tree.children[0].children[0]  # "Details" subsection
        tables = [n for n in details.content if isinstance(n, TableNode)]
        assert len(tables) == 1
        assert tables[0].headers == ["Name", "Value"]
        assert tables[0].rows == [["Alpha", "100"], ["Beta", "200"]]


class TestParserImages:
    def test_image_extraction(self, tmp_path):
        """Test that an inline image is extracted and held in memory."""
        import struct
        import zlib

        def _make_png() -> bytes:
            sig = b"\x89PNG\r\n\x1a\n"
            ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
            ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF
            ihdr = (
                struct.pack(">I", 13)
                + b"IHDR"
                + ihdr_data
                + struct.pack(">I", ihdr_crc)
            )
            raw = b"\x00\xff\x00\x00"  # filter byte + RGB
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

        doc = Document()
        doc.add_heading("With Image", level=1)

        png_path = tmp_path / "test.png"
        png_path.write_bytes(_make_png())
        doc.add_picture(str(png_path), width=Inches(1))

        doc.add_paragraph("After the image.")

        tree = parse_doc(doc, title="ImageTest")

        section = tree.children[0]
        images = [n for n in section.content if isinstance(n, ImageNode)]
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

        tree = parse_doc(doc, title="ListTest")

        section = tree.children[0]
        lists = [n for n in section.content if isinstance(n, ListNode)]
        paras = [n for n in section.content if isinstance(n, ParagraphNode)]

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

        tree = parse_doc(doc, title="BoldTest")

        section = tree.children[0]
        paras = [n for n in section.content if isinstance(n, ParagraphNode)]
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

        tree = parse_doc(doc, title="ColorTest")

        section = tree.children[0]
        paras = [n for n in section.content if isinstance(n, ParagraphNode)]
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

        tree = parse_doc(doc, title="UnderlineTest")

        section = tree.children[0]
        paras = [n for n in section.content if isinstance(n, ParagraphNode)]
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

        tree = parse_doc(doc, title="ValFalseTest")

        section = tree.children[0]
        paras = [n for n in section.content if isinstance(n, ParagraphNode)]
        assert len(paras) == 1
        spans = paras[0].spans
        bold_spans = [s for s in spans if s.bold]
        assert not any("not bold" in s.text for s in bold_spans)


class TestTitleInference:
    def test_explicit_title_wins(self):
        doc = Document()
        doc.core_properties.title = "Core Title"
        doc.add_heading("Heading One", level=1)
        tree = parse_doc(doc, title="Explicit Title")
        assert tree.title == "Explicit Title"

    def test_infers_from_core_properties(self):
        doc = Document()
        doc.core_properties.title = "Core Title"
        doc.add_heading("Heading One", level=1)
        tree = parse_doc(doc)
        assert tree.title == "Core Title"

    def test_infers_from_title_style(self):
        doc = Document()
        doc.core_properties.title = ""
        para = doc.add_paragraph("Style Title")
        para.style = doc.styles["Title"]
        doc.add_heading("Heading One", level=1)
        tree = parse_doc(doc)
        assert tree.title == "Style Title"

    def test_infers_from_first_heading1(self):
        doc = Document()
        doc.core_properties.title = ""
        doc.add_heading("First Heading", level=1)
        doc.add_heading("Second Heading", level=1)
        tree = parse_doc(doc)
        assert tree.title == "First Heading"

    def test_empty_title_when_no_headings(self):
        doc = Document()
        doc.core_properties.title = ""
        doc.add_paragraph("Just a paragraph.")
        tree = parse_doc(doc)
        assert tree.title == ""

    def test_extract_title_core_properties(self):
        doc = Document()
        doc.core_properties.title = "  Trimmed Title  "
        assert _extract_title(doc) == "Trimmed Title"
