import aiofiles
import urllib
import mistune
import os
import re
import zipfile
from html import unescape
from xml.sax.saxutils import escape as xml_escape

async def write_to_file(filename: str, text: str) -> None:
    """Asynchronously write text to a file in UTF-8 encoding.

    Args:
        filename (str): The filename to write to.
        text (str): The text to write.
    """
    # Ensure text is a string
    if not isinstance(text, str):
        text = str(text)

    # Convert text to UTF-8, replacing any problematic characters
    text_utf8 = text.encode('utf-8', errors='replace').decode('utf-8')

    async with aiofiles.open(filename, "w", encoding='utf-8') as file:
        await file.write(text_utf8)

async def write_text_to_md(text: str, filename: str = "") -> str:
    """Writes text to a Markdown file and returns the file path.

    Args:
        text (str): Text to write to the Markdown file.

    Returns:
        str: The file path of the generated Markdown file.
    """
    file_path = f"outputs/{filename[:60]}.md"
    await write_to_file(file_path, text)
    return urllib.parse.quote(file_path)

def _preprocess_images_for_pdf(text: str) -> str:
    """Convert web image URLs to absolute file paths for PDF generation.
    
    Transforms /outputs/images/... URLs to absolute file:// paths that
    weasyprint can resolve.
    """
    import re
    
    base_path = os.path.abspath(".")
    
    # Pattern to find markdown images with /outputs/ URLs
    def replace_image_url(match):
        alt_text = match.group(1)
        url = match.group(2)
        
        # Convert /outputs/... to absolute path
        if url.startswith("/outputs/"):
            abs_path = os.path.join(base_path, url.lstrip("/"))
            return f"![{alt_text}]({abs_path})"
        return match.group(0)
    
    # Match ![alt text](/outputs/images/...)
    pattern = r'!\[([^\]]*)\]\((/outputs/[^)]+)\)'
    return re.sub(pattern, replace_image_url, text)


def _markdown_to_plain_text(text: str) -> str:
    plain_text = text
    plain_text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", plain_text)
    plain_text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", plain_text)
    plain_text = re.sub(r"`{1,3}", "", plain_text)
    plain_text = re.sub(r"^\s{0,3}#{1,6}\s*", "", plain_text, flags=re.MULTILINE)
    plain_text = re.sub(r"^\s*[-*+]\s+", "- ", plain_text, flags=re.MULTILINE)
    plain_text = re.sub(r"^\s*\|", "", plain_text, flags=re.MULTILINE)
    plain_text = re.sub(r"\|\s*$", "", plain_text, flags=re.MULTILINE)
    plain_text = re.sub(r"\n{3,}", "\n\n", plain_text)
    return unescape(plain_text).strip()


def _pdf_escape(text: str) -> str:
    normalized = text.encode("latin-1", errors="replace").decode("latin-1")
    return normalized.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _write_simple_pdf(text: str, file_path: str) -> None:
    plain_text = _markdown_to_plain_text(text)
    lines = plain_text.splitlines() or [" "]
    lines_per_page = 48
    pages = [lines[index:index + lines_per_page] for index in range(0, len(lines), lines_per_page)] or [[" "]]

    objects: dict[int, bytes] = {}
    page_ids = [5 + (index * 2) for index in range(len(pages))]
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{page_id} 0 R' for page_id in page_ids)}] /Count {len(page_ids)} >>"
    ).encode("latin-1")
    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    for index, page_lines in enumerate(pages):
        content_object_id = 4 + (index * 2)
        page_object_id = 5 + (index * 2)
        commands = ["BT", "/F1 10 Tf", "50 750 Td"]
        for line_index, line in enumerate(page_lines):
            if line_index:
                commands.append("0 -14 Td")
            commands.append(f"({_pdf_escape(line)}) Tj")
        commands.append("ET")
        content_stream = "\n".join(commands).encode("latin-1", errors="replace")
        objects[content_object_id] = (
            f"<< /Length {len(content_stream)} >>\nstream\n".encode("latin-1")
            + content_stream
            + b"\nendstream"
        )
        objects[page_object_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {content_object_id} 0 R >>"
        ).encode("latin-1")

    max_object_id = max(objects)
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0] * (max_object_id + 1)
    for object_id in range(1, max_object_id + 1):
        offsets[object_id] = len(output)
        output.extend(f"{object_id} 0 obj\n".encode("latin-1"))
        output.extend(objects[object_id])
        output.extend(b"\nendobj\n")

    xref_start = len(output)
    output.extend(f"xref\n0 {max_object_id + 1}\n".encode("latin-1"))
    output.extend(b"0000000000 65535 f \n")
    for object_id in range(1, max_object_id + 1):
        output.extend(f"{offsets[object_id]:010d} 00000 n \n".encode("latin-1"))
    output.extend(
        (
            f"trailer\n<< /Size {max_object_id + 1} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF"
        ).encode("latin-1")
    )

    with open(file_path, "wb") as pdf_file:
        pdf_file.write(output)


def _build_docx_document_xml(text: str) -> str:
    plain_text = _markdown_to_plain_text(text)
    paragraphs = []
    for line in plain_text.splitlines() or [" "]:
        safe_line = xml_escape(line or " ")
        paragraphs.append(
            f'<w:p><w:r><w:t xml:space="preserve">{safe_line}</w:t></w:r></w:p>'
        )
    paragraphs_xml = "".join(paragraphs)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{paragraphs_xml}"
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" '
        'w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>'
        "</w:body></w:document>"
    )


def _write_simple_docx(text: str, file_path: str) -> None:
    content_types_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>
"""
    root_rels_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""
    document_rels_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>
"""
    styles_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
    <w:name w:val="Normal"/>
  </w:style>
</w:styles>
"""

    with zipfile.ZipFile(file_path, "w", compression=zipfile.ZIP_DEFLATED) as docx_file:
        docx_file.writestr("[Content_Types].xml", content_types_xml)
        docx_file.writestr("_rels/.rels", root_rels_xml)
        docx_file.writestr("word/document.xml", _build_docx_document_xml(text))
        docx_file.writestr("word/_rels/document.xml.rels", document_rels_xml)
        docx_file.writestr("word/styles.xml", styles_xml)


async def write_md_to_pdf(text: str, filename: str = "") -> str:
    """Converts Markdown text to a PDF file and returns the file path.

    Args:
        text (str): Markdown text to convert.

    Returns:
        str: The encoded file path of the generated PDF.
    """
    file_path = f"outputs/{filename[:60]}.pdf"

    try:
        # Resolve css path relative to this backend module to avoid
        # dependency on the current working directory.
        current_dir = os.path.dirname(os.path.abspath(__file__))
        css_path = os.path.join(current_dir, "styles", "pdf_styles.css")
        
        # Preprocess image URLs for PDF compatibility
        processed_text = _preprocess_images_for_pdf(text)
        
        # Set base_url to current directory for resolving any remaining relative paths
        base_url = os.path.abspath(".")

        from md2pdf.core import md2pdf
        md2pdf(file_path,
               md_content=processed_text,
               # md_file_path=f"{file_path}.md",
               css_file_path=css_path,
               base_url=base_url)
        print(f"Report written to {file_path}")
    except Exception as e:
        print(f"Error in converting Markdown to PDF: {e}")
        _write_simple_pdf(text, file_path)
        print(f"Fallback PDF written to {file_path}")

    encoded_file_path = urllib.parse.quote(file_path)
    return encoded_file_path

async def write_md_to_word(text: str, filename: str = "") -> str:
    """Converts Markdown text to a DOCX file and returns the file path.

    Args:
        text (str): Markdown text to convert.

    Returns:
        str: The encoded file path of the generated DOCX.
    """
    file_path = f"outputs/{filename[:60]}.docx"

    try:
        from docx import Document
        from htmldocx import HtmlToDocx
        # Convert report markdown to HTML
        html = mistune.html(text)
        # Create a document object
        doc = Document()
        # Convert the html generated from the report to document format
        HtmlToDocx().add_html_to_document(html, doc)

        # Saving the docx document to file_path
        doc.save(file_path)

        print(f"Report written to {file_path}")

        encoded_file_path = urllib.parse.quote(file_path)
        return encoded_file_path

    except Exception as e:
        print(f"Error in converting Markdown to DOCX: {e}")
        _write_simple_docx(text, file_path)
        print(f"Fallback DOCX written to {file_path}")
        return urllib.parse.quote(file_path)
