"""HTTP API и веб-интерфейс DocMat.

Сервис не имеет состояния: документы и результаты существуют только в рамках
одного запроса и возвращаются клиенту, на сервере ничего не сохраняется.
"""

import base64
import logging
import re
import shutil
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import __version__
from app.config import settings
from app.services import converter, corrector, documents, office, recognizer, spellcheck

STATIC_DIR = Path(__file__).parent / "static"
MAX_TEXT_CHARS = 500_000
OCR_LANGS = {"rus+eng", "rus", "eng"}

# В журнал пишутся только служебные события — без имён и содержимого документов.
logger = logging.getLogger("docmat")

app = FastAPI(title="DocMat", version=__version__, docs_url="/api/docs", redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def no_store(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


class TextIn(BaseModel):
    text: str = Field(max_length=MAX_TEXT_CHARS)


async def read_upload(file: UploadFile) -> bytes:
    data = await file.read(settings.max_upload_bytes + 1)
    await file.close()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, f"Файл больше {settings.max_upload_mb} МБ")
    if not data:
        raise HTTPException(400, "Файл пуст")
    return data


async def load_document(file: UploadFile) -> documents.Document:
    data = await read_upload(file)
    try:
        return await documents.load(file.filename or "", data)
    except documents.DocumentError as exc:
        raise HTTPException(415, str(exc)) from exc


def safe_basename(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    stem = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", stem).strip(" .")
    return stem[:100] or "document"


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/health")
async def health():
    return {
        "version": __version__,
        "tesseract": {"available": shutil.which("tesseract") is not None},
        "libreoffice": {"available": shutil.which(settings.soffice_bin) is not None},
        "spellcheck": {"available": spellcheck.is_available(), "languages": list(settings.spell_langs)},
        "ollama": await corrector.ollama_status(),
        "limits": {"max_upload_mb": settings.max_upload_mb, "max_pages": settings.max_pages},
    }


@app.post("/api/preview")
async def preview(file: UploadFile = File(...)):
    """Возвращает изображения страниц для блока «Оригинал»."""
    doc = await load_document(file)
    pages = await run_in_threadpool(documents.render_pages, doc.pdf, settings.preview_dpi, settings.max_pages)
    return {
        "filename": doc.filename,
        "format": "pdfa" if doc.is_pdfa else doc.ext,
        "page_count": doc.page_count,
        "pages": [f"data:image/png;base64,{b64(p)}" for p in pages],
        "truncated": doc.page_count > len(pages),
    }


@app.post("/api/recognize")
async def recognize(
    file: UploadFile = File(...),
    lang: str = Form("rus+eng"),
    force_ocr: bool = Form(False),
):
    """Строит базовый документ с оформлением исходного и возвращает его текст.

    Базовый документ (DOCX или XLSX) возвращается клиенту и присылается обратно
    при конвертации: сервер его не хранит.
    """
    if lang not in OCR_LANGS:
        raise HTTPException(422, "Недопустимый язык распознавания")
    data = await read_upload(file)
    try:
        result = await recognizer.recognize(file.filename or "", data, lang, force_ocr)
    except documents.DocumentError as exc:
        raise HTTPException(415, str(exc)) from exc
    logger.info("recognize: kind=%s pages=%d", result.kind, len(result.pages))
    errors = await run_in_threadpool(spellcheck.find_errors_json, result.text)
    return {
        "text": result.text,
        "errors": errors,
        "pages": result.pages,
        "base": {"kind": result.kind, "data": b64(result.data)},
    }


@app.post("/api/check")
async def check(body: TextIn):
    return {"errors": await run_in_threadpool(spellcheck.find_errors_json, body.text)}


@app.post("/api/correct")
async def correct(body: TextIn):
    result = await corrector.correct(body.text)
    errors = await run_in_threadpool(spellcheck.find_errors_json, result.text)
    return {"text": result.text, "errors": errors, "engine": result.engine, "warning": result.warning}


@app.post("/api/convert")
async def convert(
    base: UploadFile = File(..., description="Базовый документ из ответа /api/recognize"),
    kind: str = Form(...),
    text: UploadFile = File(..., description="Текущий текст (UTF-8)"),
    filename: str = Form("document", max_length=255),
):
    """Переносит правки текста в базовый документ и формирует все выходные форматы.

    Текст передаётся файлом: у обычных полей формы ограничение 1 МБ.
    """
    if kind not in ("docx", "xlsx"):
        raise HTTPException(422, "Недопустимый тип базового документа")
    base_data = await read_upload(base)
    raw = await text.read(MAX_TEXT_CHARS * 4 + 1)
    await text.close()
    try:
        body = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(422, "Текст должен быть в кодировке UTF-8") from exc
    if len(body) > MAX_TEXT_CHARS:
        raise HTTPException(413, "Текст слишком длинный")
    if not body.strip():
        raise HTTPException(400, "Нет текста для конвертации")
    try:
        result = await converter.convert_all(kind, base_data, body, safe_basename(filename))
    except office.OfficeError as exc:
        raise HTTPException(500, str(exc)) from exc
    except Exception as exc:  # повреждённый или подменённый базовый документ: zip, XML, openpyxl
        raise HTTPException(415, "Базовый документ повреждён — выполните распознавание заново") from exc
    return {
        "files": [
            {"format": f.format, "label": f.label, "filename": f.filename, "mime": f.mime, "data": b64(f.data)}
            for f in result.files
        ],
        "applied": result.applied,
        "skipped": result.skipped,
    }
