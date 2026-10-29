"""Приём документа: проверка формата, приведение к PDF и другим форматам, рендер страниц."""

from dataclasses import dataclass

import pymupdf

from app.services import office
from app.services.storage import ephemeral_dir

TEXT_DOCUMENTS = {"doc", "docx", "odt", "rtf"}
SPREADSHEETS = {"xls", "xlsx", "ods"}
SUPPORTED_EXTENSIONS = TEXT_DOCUMENTS | SPREADSHEETS | {"pdf"}

# Сигнатуры файлов: защищают от подмены расширения.
_SIGNATURES = {
    "pdf": (b"%PDF-",),
    "rtf": (b"{\\rtf",),
    "doc": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),  # OLE2 Compound File
    "xls": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
    "docx": (b"PK\x03\x04",),  # ZIP-контейнер OOXML/ODF
    "xlsx": (b"PK\x03\x04",),
    "odt": (b"PK\x03\x04",),
    "ods": (b"PK\x03\x04",),
}


class DocumentError(ValueError):
    pass


@dataclass
class Document:
    filename: str
    ext: str
    pdf: bytes
    is_pdfa: bool
    page_count: int


def extension(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def validate(filename: str, data: bytes) -> str:
    ext = extension(filename)
    if ext not in SUPPORTED_EXTENSIONS:
        raise DocumentError(
            "Неподдерживаемый формат. Допустимы: " + ", ".join(sorted(SUPPORTED_EXTENSIONS)) + ", PDF/A"
        )
    head = data[:1024]
    if not any(head.startswith(sig) for sig in _SIGNATURES[ext]) and not (ext == "pdf" and b"%PDF-" in head):
        raise DocumentError("Содержимое файла не соответствует его расширению")
    return ext


def is_pdfa(pdf: pymupdf.Document) -> bool:
    xmp = pdf.get_xml_metadata() or ""
    return "pdfaid:part" in xmp


async def office_convert(ext: str, data: bytes, target: str) -> bytes:
    with ephemeral_dir() as work:
        src = work / f"source.{ext}"
        src.write_bytes(data)
        try:
            return (await office.convert(src, target, work / "out")).read_bytes()
        except office.OfficeError as exc:
            raise DocumentError(str(exc)) from exc


async def load(filename: str, data: bytes) -> Document:
    """Приводит любой поддерживаемый документ к PDF в памяти."""
    ext = validate(filename, data)
    pdf_bytes = data if ext == "pdf" else await office_convert(ext, data, "pdf")

    try:
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as pdf:
            if pdf.needs_pass:
                raise DocumentError("Документ защищён паролем")
            return Document(filename, ext, pdf_bytes, is_pdfa(pdf), pdf.page_count)
    except (pymupdf.FileDataError, RuntimeError) as exc:
        raise DocumentError("Файл повреждён или не является документом") from exc


def render_pages(pdf_bytes: bytes, dpi: int, limit: int) -> list[bytes]:
    """Рендерит первые limit страниц в PNG."""
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as pdf:
        return [page.get_pixmap(dpi=dpi).tobytes("png") for page in pdf.pages(0, min(limit, pdf.page_count))]
