"""Convert a Markdown file to a styled PDF using reportlab.

Usage:
    python scripts/markdown_to_pdf.py <input.md> <output.pdf>

Supports: headings (h1-h4), paragraphs, ordered/unordered lists, tables,
fenced code blocks, inline bold/italic/code, links, blockquotes, horizontal
rules.
"""

import re
import sys
from pathlib import Path
from html import unescape

import markdown
from bs4 import BeautifulSoup, NavigableString
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

base = getSampleStyleSheet()

H1 = ParagraphStyle(
    name="H1", parent=base["Heading1"], fontSize=18, spaceBefore=14,
    spaceAfter=10, textColor=colors.HexColor("#0a3d62"),
    borderPadding=4, leading=22,
)
H2 = ParagraphStyle(
    name="H2", parent=base["Heading2"], fontSize=14, spaceBefore=12,
    spaceAfter=7, textColor=colors.HexColor("#0a3d62"), leading=18,
)
H3 = ParagraphStyle(
    name="H3", parent=base["Heading3"], fontSize=11.5, spaceBefore=10,
    spaceAfter=5, textColor=colors.HexColor("#333333"), leading=15,
)
H4 = ParagraphStyle(
    name="H4", parent=base["Heading4"], fontSize=10.5, spaceBefore=8,
    spaceAfter=4, textColor=colors.HexColor("#555555"), leading=14,
)
BODY = ParagraphStyle(
    name="Body", parent=base["BodyText"], fontSize=10, leading=14,
    spaceAfter=6,
)
BULLET = ParagraphStyle(
    name="Bullet", parent=BODY, leftIndent=18, bulletIndent=6,
    firstLineIndent=0, spaceAfter=3,
)
NUMBERED = ParagraphStyle(
    name="Numbered", parent=BODY, leftIndent=22, bulletIndent=6,
    firstLineIndent=0, spaceAfter=3,
)
BLOCKQUOTE = ParagraphStyle(
    name="Blockquote", parent=BODY, leftIndent=18, rightIndent=18,
    textColor=colors.HexColor("#444444"), borderColor=colors.HexColor("#cccccc"),
    borderWidth=0, borderPadding=4, italic=True,
)
CODE = ParagraphStyle(
    name="Code", parent=base["Code"], fontSize=9, leading=11,
    backColor=colors.HexColor("#f4f4f4"), borderColor=colors.HexColor("#dddddd"),
    borderWidth=0.5, borderPadding=6, spaceAfter=8,
    leftIndent=4, rightIndent=4,
)


# ---------------------------------------------------------------------------
# Inline conversion: HTML inline tags → reportlab paragraph markup
# ---------------------------------------------------------------------------

def inline_to_rl(node) -> str:
    """Convert a BeautifulSoup element's children to reportlab paragraph markup.

    Reportlab Paragraph accepts a subset of HTML tags: <b>, <i>, <u>, <font>,
    <a href=...>, <br/>. We convert markdown-derived <strong>/<em>/<code>
    into these.
    """
    parts = []
    for child in node.children:
        if isinstance(child, NavigableString):
            parts.append(escape_xml(str(child)))
            continue
        tag = child.name
        inner = inline_to_rl(child)
        if tag in ("strong", "b"):
            parts.append(f"<b>{inner}</b>")
        elif tag in ("em", "i"):
            parts.append(f"<i>{inner}</i>")
        elif tag == "code":
            parts.append(
                f'<font face="Courier" backColor="#f4f4f4">{inner}</font>'
            )
        elif tag == "a":
            href = child.get("href", "")
            parts.append(f'<font color="#1a73e8"><link href="{href}">{inner}</link></font>')
        elif tag == "br":
            parts.append("<br/>")
        elif tag == "del":
            parts.append(f"<strike>{inner}</strike>")
        else:
            parts.append(inner)
    return "".join(parts)


def escape_xml(s: str) -> str:
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
    )


# ---------------------------------------------------------------------------
# Block-level conversion: walk top-level elements, emit Flowables
# ---------------------------------------------------------------------------

def table_from_html(tbl) -> Table:
    rows = []
    has_header = bool(tbl.find("thead"))

    # Header
    if has_header:
        header_cells = tbl.find("thead").find("tr").find_all(["th", "td"])
        rows.append([Paragraph(inline_to_rl(c), BODY) for c in header_cells])

    # Body
    body = tbl.find("tbody") or tbl
    for tr in body.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue
        rows.append([Paragraph(inline_to_rl(c), BODY) for c in cells])

    if not rows:
        return Spacer(1, 0.1 * inch)

    # Compute reasonable column widths: equal split within a 7-inch usable width
    col_count = max(len(r) for r in rows)
    usable_width = 7.0 * inch
    col_widths = [usable_width / col_count] * col_count

    t = Table(rows, colWidths=col_widths, hAlign="LEFT", repeatRows=1 if has_header else 0)
    style = [
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if has_header:
        style += [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef3")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ]
    t.setStyle(TableStyle(style))
    return t


def list_items(lst, ordered: bool, depth: int = 0):
    """Yield Flowables for each <li> in the list."""
    style = NUMBERED if ordered else BULLET
    for i, li in enumerate(lst.find_all("li", recursive=False), start=1):
        # Strip any nested block elements out for recursive handling
        nested = []
        for child in li.find_all(["ul", "ol"], recursive=False):
            nested.append(child)
            child.extract()
        bullet = f"{i}." if ordered else "&bull;"
        text = inline_to_rl(li).strip()
        yield Paragraph(f"{bullet} {text}", style)
        for nlist in nested:
            yield from list_items(
                nlist, ordered=(nlist.name == "ol"), depth=depth + 1
            )


def convert_block(el):
    """Convert one top-level block element to one or more Flowables."""
    name = el.name
    if name == "h1":
        return [Paragraph(inline_to_rl(el), H1)]
    if name == "h2":
        return [Paragraph(inline_to_rl(el), H2)]
    if name == "h3":
        return [Paragraph(inline_to_rl(el), H3)]
    if name in ("h4", "h5", "h6"):
        return [Paragraph(inline_to_rl(el), H4)]
    if name == "p":
        return [Paragraph(inline_to_rl(el), BODY)]
    if name == "ul":
        return list(list_items(el, ordered=False))
    if name == "ol":
        return list(list_items(el, ordered=True))
    if name == "table":
        return [table_from_html(el), Spacer(1, 0.08 * inch)]
    if name == "pre":
        code = el.get_text()
        return [Preformatted(code.rstrip(), CODE)]
    if name == "blockquote":
        # Use italic body style with left indent
        out = []
        for child in el.find_all("p"):
            out.append(Paragraph(inline_to_rl(child), BLOCKQUOTE))
        return out or [Paragraph(inline_to_rl(el), BLOCKQUOTE)]
    if name == "hr":
        return [HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc"),
                           spaceBefore=8, spaceAfter=8)]
    # Fallback: render as paragraph
    text = inline_to_rl(el).strip()
    return [Paragraph(text, BODY)] if text else []


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def convert(md_path: Path, pdf_path: Path):
    md_text = md_path.read_text(encoding="utf-8")

    html = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "sane_lists"],
        output_format="html5",
    )

    soup = BeautifulSoup(html, "html.parser")

    flowables = []
    for el in soup.children:
        if isinstance(el, NavigableString):
            text = str(el).strip()
            if text:
                flowables.append(Paragraph(escape_xml(text), BODY))
            continue
        flowables.extend(convert_block(el))

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title=md_path.stem.replace("_", " ").title(),
        author="StockPortfolioManager dashboard",
    )
    doc.build(flowables)
    print(f"Wrote {pdf_path}")


def main():
    if len(sys.argv) != 3:
        print("Usage: markdown_to_pdf.py <input.md> <output.pdf>")
        sys.exit(1)
    convert(Path(sys.argv[1]), Path(sys.argv[2]))


if __name__ == "__main__":
    main()
