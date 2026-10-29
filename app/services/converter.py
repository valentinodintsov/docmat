"""Формирование выходных документов из базового документа с правками пользователя.

Все форматы получаются из базового документа (DOCX или XLSX, см. recognizer.py),
поэтому сохраняют его оформление: шрифты, абзацы, таблицы, поля, изображения.

| Базовый | DOCX         | ODT           | PDF, PDF/A   | XLSX            | ODS          |
|---------|--------------|---------------|--------------|-----------------|--------------|
| DOCX    | базовый      | LibreOffice   | LibreOffice  | абзацы и таблицы → листы | из XLSX |
| XLSX    | XLSX → HTML → DOCX (LibreOffice) | из DOCX | LibreOffice Calc | базовый | LibreOffice |
"""

import asyncio
import io
from dataclasses import dataclass

from docx import Document as DocxDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.table import Table
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, Side

from app.services import editable, office
from app.services.storage import ephemeral_dir


@dataclass
class OutputFile:
    format: str
    label: str
    filename: str
    mime: str
    data: bytes


@dataclass
class ConvertResult:
    files: list[OutputFile]
    applied: int
    skipped: int


FORMATS = {
    "docx": ("DOCX", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
    "xlsx": ("XLSX", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
    "odt": ("ODT", "application/vnd.oasis.opendocument.text", "odt"),
    "ods": ("ODS", "application/vnd.oasis.opendocument.spreadsheet", "ods"),
    "pdf": ("PDF", "application/pdf", "pdf"),
    "pdfa": ("PDF/A", "application/pdf", "pdfa.pdf"),
}

_H_ALIGN = {
    WD_ALIGN_PARAGRAPH.CENTER: "center",
    WD_ALIGN_PARAGRAPH.RIGHT: "right",
    WD_ALIGN_PARAGRAPH.JUSTIFY: "justify",
}
_THIN = Side(style="thin")


def _font_of(paragraph) -> Font:
    runs = [r for r in paragraph.runs if r.text.strip()]
    if not runs:
        return Font()
    first = runs[0]
    size = first.font.size.pt if first.font.size else None
    style_font = paragraph.style.font if paragraph.style is not None else None
    if size is None and style_font is not None and style_font.size:
        size = style_font.size.pt
    color = None
    if first.font.color is not None and first.font.color.type is not None and first.font.color.rgb is not None:
        color = str(first.font.color.rgb)
    return Font(
        name=first.font.name or (style_font.name if style_font is not None else None),
        size=size,
        bold=all(r.bold or (style_font is not None and style_font.bold) for r in runs) or None,
        italic=all(r.italic for r in runs) or None,
        color=color,
    )


def docx_to_xlsx(docx: bytes, title: str) -> bytes:
    """Абзацы — строки в колонке A, таблицы — диапазоны ячеек; начертание и кегль сохраняются."""
    doc = DocxDocument(io.BytesIO(docx))
    wb = Workbook()
    ws = wb.active
    ws.title = "Документ"
    wb.properties.title = title
    widths: dict[int, float] = {1: 60}
    row = 1
    for block in doc.iter_inner_content():
        if isinstance(block, Table):
            for tr in block.rows:
                seen = set()
                for c, cell in enumerate(tr.cells, start=1):
                    if id(cell._tc) in seen:
                        continue  # объединённая ячейка повторяется в каждой колонке
                    seen.add(id(cell._tc))
                    target = ws.cell(row=row, column=c, value=cell.text or None)
                    if cell.paragraphs:
                        target.font = _font_of(cell.paragraphs[0])
                    target.border = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
                    target.alignment = Alignment(wrap_text=True, vertical="top")
                    widths[c] = max(widths.get(c, 10), min(len(cell.text) + 2, 50))
                row += 1
            row += 1
            continue
        text = block.text
        if text.strip():
            cell = ws.cell(row=row, column=1, value=text)
            cell.font = _font_of(block)
            cell.alignment = Alignment(horizontal=_H_ALIGN.get(block.alignment), wrap_text=True, vertical="top")
        row += 1
    for col, width in widths.items():
        ws.column_dimensions[ws.cell(row=1, column=col).column_letter].width = width
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def _from_docx(docx: bytes, basename: str) -> dict[str, bytes]:
    xlsx = docx_to_xlsx(docx, basename)
    with ephemeral_dir() as work:
        src_docx, src_xlsx = work / "document.docx", work / "document.xlsx"
        src_docx.write_bytes(docx)
        src_xlsx.write_bytes(xlsx)
        jobs = {
            "odt": office.convert(src_docx, "odt", work / "odt"),
            "pdf": office.convert(src_docx, "pdf", work / "pdf"),
            "pdfa": office.convert(src_docx, "pdfa", work / "pdfa"),
            "ods": office.convert(src_xlsx, "ods", work / "ods"),
        }
        paths = await asyncio.gather(*jobs.values())
        return {"docx": docx, "xlsx": xlsx} | {fmt: p.read_bytes() for fmt, p in zip(jobs, paths, strict=True)}


async def _from_xlsx(xlsx: bytes, recalc: bool) -> dict[str, bytes]:
    with ephemeral_dir() as work:
        src = work / "document.xlsx"
        src.write_bytes(xlsx)
        if recalc:
            # openpyxl не вычисляет формулы: пересохраняем через LibreOffice, чтобы
            # в файле были актуальные значения формул после правок.
            src = await office.convert(src, "xlsx", work / "recalc")
            xlsx = src.read_bytes()
        html_job = office.convert(src, "html", work / "html")
        jobs = {
            "ods": office.convert(src, "ods", work / "ods"),
            "pdf": office.convert(src, "pdf", work / "pdf"),
            "pdfa": office.convert(src, "pdfa", work / "pdfa"),
        }
        html, *paths = await asyncio.gather(html_job, *jobs.values())
        docx_path = await office.convert(html, "docx", work / "docx")
        odt_path = await office.convert(docx_path, "odt", work / "odt")
        return {"xlsx": xlsx, "docx": docx_path.read_bytes(), "odt": odt_path.read_bytes()} | {
            fmt: p.read_bytes() for fmt, p in zip(jobs, paths, strict=True)
        }


async def convert_all(kind: str, base: bytes, text: str, basename: str) -> ConvertResult:
    edited = editable.apply_text(kind, base, text)
    if kind == "docx":
        results = await _from_docx(edited.data, basename)
    else:
        results = await _from_xlsx(edited.data, recalc=edited.applied > 0)
    files = [
        OutputFile(fmt, label, f"{basename}.{suffix}", mime, results[fmt])
        for fmt, (label, mime, suffix) in FORMATS.items()
    ]
    return ConvertResult(files, edited.applied, edited.skipped)
