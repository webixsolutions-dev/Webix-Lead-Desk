"""Build a private, editable Word document with no remote assets or macros."""

from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from app.proposals.schema import SECTIONS

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def paragraph(text: str, style: str = "Normal") -> str:
    """Escape all owner text into plain Word runs, with paragraph styles for navigation."""
    clean = "".join(c for c in text if c in "\n\t" or ord(c) >= 32)
    runs = "".join(
        '<w:r><w:t xml:space="preserve">'
        + escape(line)
        + "</w:t></w:r>"
        + ("<w:r><w:br/></w:r>" if i < len(clean.splitlines()) - 1 else "")
        for i, line in enumerate(clean.splitlines() or [""])
    )
    return f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr>{runs}</w:p>'


def money(value: int | None, currency: str) -> str:
    """Preserve the exact approved currency and cents."""
    return "Not quoted" if value is None else f"{currency} {value // 100:,}.{value % 100:02d}"


def export(snapshot: dict[str, Any], *, approved: bool) -> bytes:
    """Return an editable branded proposal; exporting never changes delivery state."""
    sender = snapshot["sender"]
    parts = [
        paragraph(sender["brand"], "Subtitle"),
        paragraph(snapshot["title"], "Title"),
        paragraph(f"Prepared for {snapshot['company']} · {snapshot['recipient']}"),
        paragraph(f"{sender['name']} · {sender['email']}"),
        paragraph(
            f"Revision {snapshot['version']} · " + ("Approved" if approved else "Draft for review")
        ),
    ]
    for key, label in SECTIONS:
        parts += [paragraph(label, "Heading1"), paragraph(snapshot["sections"][key])]
    parts += [
        paragraph("Investment", "Heading1"),
        paragraph("Setup: " + money(snapshot["setup_cents"], snapshot["currency"])),
        paragraph(
            "Recurring: "
            + money(snapshot["recurring_cents"], snapshot["currency"])
            + " · "
            + snapshot["period"].replace("_", " ")
        ),
        paragraph(sender["contact"]),
        paragraph(sender["address"]),
    ]
    document = (
        f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{W}"><w:body>' + "".join(parts)
    )
    document += (
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1080" '
        'w:right="1080" w:bottom="1080" '
        'w:left="1080"/></w:sectPr></w:body></w:document>'
    )
    styles = (
        f'<w:styles xmlns:w="{W}"><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts '
        'w:ascii="Arial" w:hAnsi="Arial"/><w:sz w:val="22"/><w:color '
        'w:val="000000"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing'
        ' w:after="140" w:line="276" '
        'w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
    )
    for name, size in (("Normal", 22), ("Title", 38), ("Subtitle", 24), ("Heading1", 26)):
        styles += f'<w:style w:type="paragraph" w:styleId="{name}"><w:name w:val="{name}"/><w:pPr>'
        if name != "Normal":
            styles += '<w:keepNext/><w:spacing w:before="200" w:after="100"/>'
        styles += f'</w:pPr><w:rPr><w:color w:val="000000"/><w:sz w:val="{size}"/>'
        if name in {"Title", "Heading1"}:
            styles += "<w:b/>"
        styles += "</w:rPr></w:style>"
    styles += "</w:styles>"
    types = (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-t'
        'ypes"><Default Extension="rels" ContentType="application/vnd.openxmlfo'
        'rmats-package.relationships+xml"/><Default Extension="xml" '
        'ContentType="application/xml"/><Override PartName="/word/document.xml"'
        ' ContentType="application/vnd.openxmlformats-officedocument.wordproces'
        'singml.document.main+xml"/><Override PartName="/word/styles.xml" Conte'
        'ntType="application/vnd.openxmlformats-officedocument.wordprocessingml'
        '.styles+xml"/></Types>'
    )
    root = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/r'
        'elationships"><Relationship Id="rId1" Type="http://schemas.openxmlform'
        'ats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/></Relationships>'
    )
    rels = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/r'
        'elationships"><Relationship Id="rId1" Type="http://schemas.openxmlform'
        'ats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/></Relationships>'
    )
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for path, text in (
            ("[Content_Types].xml", types),
            ("_rels/.rels", root),
            ("word/document.xml", document),
            ("word/styles.xml", styles),
            ("word/_rels/document.xml.rels", rels),
        ):
            archive.writestr(path, text.encode("utf-8"))
    return output.getvalue()
