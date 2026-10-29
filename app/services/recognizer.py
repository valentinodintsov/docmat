"""Распознавание: построение базового документа с сохранённым оформлением.

| Источник                 | Базовый документ                                  |
|--------------------------|---------------------------------------------------|
| DOCX                     | сам файл                                          |
| DOC, ODT, RTF            | DOCX, сконвертированный LibreOffice               |
| XLSX                     | сам файл                                          |
| XLS, ODS                 | XLSX, сконвертированный LibreOffice               |
| PDF (текстовый слой)     | DOCX с вёрсткой, восстановленной по текстовому слою |
| PDF-скан, «Только OCR»   | DOCX с вёрсткой, восстановленной по результатам OCR |

Текст для правого блока — это текст базового документа (см. editable.py),
поэтому правки пользователя можно перенести обратно в документ.
"""

import io
from dataclasses import dataclass

import pymupdf
from docx import Document as DocxDocument
from fastapi.concurrency import run_in_threadpool

from app.config import settings
from app.services import documents, editable, layout

# Страница считается отсканированной, если текстового слоя меньше этого числа символов.
MIN_TEXT_LAYER_CHARS = 20


@dataclass
class Recognized:
    kind: str  # "docx" или "xlsx"
    data: bytes
    text: str
    pages: list[dict]  # [{"page": n | None, "method": "document" | "text" | "ocr"}]


def pdf_to_docx(pdf_bytes: bytes, lang: str, force_ocr: bool) -> tuple[bytes, list[dict]]:
    pages, info = [], []
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as pdf:
        for page in pdf.pages(0, min(settings.max_pages, pdf.page_count)):
            text_layer = "" if force_ocr else page.get_text("text")
            if len(text_layer.strip()) >= MIN_TEXT_LAYER_CHARS:
                pages.append(layout.from_text_layer(page))
                info.append({"page": page.number + 1, "method": "text"})
            else:
                pages.append(layout.from_ocr(page, lang))
                info.append({"page": page.number + 1, "method": "ocr"})
    return layout.to_docx(pages), info


def _opens_as_docx(data: bytes) -> bool:
    try:
        DocxDocument(io.BytesIO(data))
        return True
    except Exception:  # .docm, Strict OOXML и прочие варианты, которые python-docx не читает
        return False


async def recognize(filename: str, data: bytes, lang: str, force_ocr: bool) -> Recognized:
    ext = documents.validate(filename, data)

    if ext in documents.SPREADSHEETS and not force_ocr:
        xlsx = data if ext == "xlsx" else await documents.office_convert(ext, data, "xlsx")
        try:
            text = await run_in_threadpool(editable.text_of, "xlsx", xlsx)
        except Exception as exc:  # openpyxl бросает разные исключения на повреждённых файлах
            raise documents.DocumentError("Не удалось прочитать таблицу") from exc
        return Recognized("xlsx", xlsx, text, [{"page": None, "method": "document"}])

    if ext in documents.TEXT_DOCUMENTS and not force_ocr:
        docx = data if ext == "docx" and _opens_as_docx(data) else await documents.office_convert(ext, data, "docx")
        text = await run_in_threadpool(editable.text_of, "docx", docx)
        if len(text.strip()) >= MIN_TEXT_LAYER_CHARS:
            return Recognized("docx", docx, text, [{"page": None, "method": "document"}])
        # Текста нет — вероятно, в документ вставлен скан: распознаём страницы.

    doc = await documents.load(filename, data)
    docx, pages = await run_in_threadpool(pdf_to_docx, doc.pdf, lang, force_ocr)
    text = await run_in_threadpool(editable.text_of, "docx", docx)
    return Recognized("docx", docx, text, pages)
