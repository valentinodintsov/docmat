"""Конвертация через LibreOffice в режиме headless."""

import asyncio
import weakref
from pathlib import Path

from app.config import settings
from app.services.storage import ephemeral_dir

# SelectPdfVersion=2 — PDF/A-2b (ГОСТ Р ИСО 19005-2).
_PDFA = '{"SelectPdfVersion":{"type":"long","value":"2"}}'
SPREADSHEET_SUFFIXES = {".xlsx", ".xls", ".ods"}


def _filters(src: Path, target: str) -> tuple[str, str | None]:
    """Фильтр экспорта и, при необходимости, фильтр импорта LibreOffice."""
    calc = src.suffix.lower() in SPREADSHEET_SUFFIXES
    app = "calc" if calc else "writer"
    if target == "pdf":
        return f"pdf:{app}_pdf_Export", None
    if target == "pdfa":
        return f"pdf:{app}_pdf_Export:{_PDFA}", None
    if target == "html":
        return "html:HTML (StarCalc)" if calc else "html:HTML (StarWriter)", None
    if target == "docx":
        # HTML по умолчанию открывается в Writer/Web, а не в Writer.
        return "docx:MS Word 2007 XML", "HTML (StarWriter)" if src.suffix.lower() == ".html" else None
    if target == "xlsx":
        return "xlsx:Calc MS Excel 2007 XML", None
    return target, None


# Ограничение числа одновременно запущенных LibreOffice в одном рабочем процессе:
# каждый экземпляр занимает 150–300 МБ памяти.
# Семафор привязывается к циклу событий, поэтому создаётся отдельно для каждого цикла.
_slots: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = weakref.WeakKeyDictionary()


def _semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if loop not in _slots:
        _slots[loop] = asyncio.Semaphore(settings.soffice_concurrency)
    return _slots[loop]


class OfficeError(RuntimeError):
    pass


async def convert(src: Path, target: str, out_dir: Path) -> Path:
    """Конвертирует файл src в формат target и возвращает путь к результату.

    Каждый вызов использует собственный профиль LibreOffice, поэтому
    несколько конвертаций могут выполняться параллельно.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    async with _semaphore():
        return await _run(src, target, out_dir)


async def _run(src: Path, target: str, out_dir: Path) -> Path:
    export, infilter = _filters(src, target)
    with ephemeral_dir() as profile:
        proc = await asyncio.create_subprocess_exec(
            settings.soffice_bin,
            f"-env:UserInstallation={profile.as_uri()}",
            "--headless",
            "--norestore",
            "--nolockcheck",
            *([f"--infilter={infilter}"] if infilter else []),
            "--convert-to",
            export,
            "--outdir",
            str(out_dir),
            str(src),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), settings.soffice_timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise OfficeError("Превышено время ожидания LibreOffice") from None

    ext = "pdf" if target == "pdfa" else target
    result = out_dir / f"{src.stem}.{ext}"
    if proc.returncode != 0 or not result.exists():
        raise OfficeError(f"LibreOffice не смог сконвертировать файл: {stderr.decode(errors='replace')[:300]}")
    return result
