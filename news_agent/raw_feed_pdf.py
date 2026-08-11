"""Render raw-feed digest markdown into a simple Chinese PDF."""

import html
import os
import re
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer


_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
]


def _register_font():
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            font_name = "NewsAgentCJK"
            pdfmetrics.registerFont(TTFont(font_name, path))
            return font_name
    return "Helvetica"


_FONT_NAME = _register_font()


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "DigestTitle",
            parent=base["Title"],
            fontName=_FONT_NAME,
            fontSize=20,
            leading=26,
            textColor=colors.HexColor("#1f2937"),
            spaceAfter=10,
        ),
        "h2": ParagraphStyle(
            "DigestHeading2",
            parent=base["Heading2"],
            fontName=_FONT_NAME,
            fontSize=14,
            leading=19,
            textColor=colors.HexColor("#1f2937"),
            spaceBefore=12,
            spaceAfter=6,
        ),
        "meta": ParagraphStyle(
            "DigestMeta",
            parent=base["BodyText"],
            fontName=_FONT_NAME,
            fontSize=9,
            leading=13,
            textColor=colors.HexColor("#6b7280"),
            spaceAfter=8,
        ),
        "body": ParagraphStyle(
            "DigestBody",
            parent=base["BodyText"],
            fontName=_FONT_NAME,
            fontSize=10,
            leading=15,
            textColor=colors.HexColor("#111827"),
            spaceAfter=6,
        ),
        "bullet": ParagraphStyle(
            "DigestBullet",
            parent=base["BodyText"],
            fontName=_FONT_NAME,
            fontSize=10,
            leading=15,
            leftIndent=12,
            firstLineIndent=-8,
            textColor=colors.HexColor("#111827"),
            spaceAfter=3,
        ),
        "intro": ParagraphStyle(
            "DigestIntro",
            parent=base["BodyText"],
            fontName=_FONT_NAME,
            fontSize=9,
            leading=14,
            leftIndent=16,
            textColor=colors.HexColor("#374151"),
            spaceAfter=6,
        ),
        "footer": ParagraphStyle(
            "DigestFooter",
            parent=base["BodyText"],
            fontName=_FONT_NAME,
            fontSize=8,
            leading=11,
            textColor=colors.HexColor("#9ca3af"),
        ),
    }


def _safe_filename(text):
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", text or "raw-feed-digest").strip("-._")
    return safe[:100] or "raw-feed-digest"


def _inline_markdown(text):
    text = text or ""
    parts = []
    position = 0
    for match in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", text):
        parts.append(html.escape(text[position:match.start()], quote=False))
        label = html.escape(match.group(1), quote=False)
        url = html.escape(match.group(2), quote=True)
        parts.append(f'<a href="{url}" color="#2563eb">{label}</a>')
        position = match.end()
    parts.append(html.escape(text[position:], quote=False))
    return "".join(parts)


def _page_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont(_FONT_NAME, 8)
    canvas.setFillColor(colors.HexColor("#9ca3af"))
    canvas.drawRightString(A4[0] - 18 * mm, 12 * mm, f"Page {doc.page}")
    canvas.restoreState()


def render_raw_feed_digest_pdf(markdown_body, output_dir="output/pdf", filename_prefix="raw-feed-digest"):
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_path = os.path.abspath(os.path.join(output_dir, f"{_safe_filename(filename_prefix)}-{timestamp}.pdf"))

    styles = _styles()
    story = []

    for raw_line in (markdown_body or "").splitlines():
        line = raw_line.rstrip()
        if not line:
            story.append(Spacer(1, 4))
            continue
        if line.startswith("# "):
            story.append(Paragraph(_inline_markdown(line[2:].strip()), styles["title"]))
        elif line.startswith("## "):
            story.append(Paragraph(_inline_markdown(line[3:].strip()), styles["h2"]))
        elif line.startswith("- "):
            story.append(Paragraph("- " + _inline_markdown(line[2:].strip()), styles["bullet"]))
        elif line.startswith("  "):
            story.append(Paragraph(_inline_markdown(line.strip()), styles["intro"]))
        elif line.startswith("*") and line.endswith("*"):
            story.append(Paragraph(_inline_markdown(line.strip("*")), styles["meta"]))
        else:
            story.append(Paragraph(_inline_markdown(line.strip()), styles["body"]))

    if not story:
        story.append(Paragraph("暂无内容", styles["body"]))

    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title="新增信息流日报",
        author="News Agent",
    )
    doc.build(story, onFirstPage=_page_footer, onLaterPages=_page_footer)
    return output_path
