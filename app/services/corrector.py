"""Исправление распознанного текста: локальная LLM (Ollama) или словарь."""

import difflib
import re

import httpx

from app.config import settings
from app.services import spellcheck

CHUNK_CHARS = 2500
_TOKEN = re.compile(r"\w+|\s|[^\w\s]")

SYSTEM_PROMPT = (
    "Ты корректор текста, полученного после оптического распознавания (OCR). "
    "Исправь только орфографические ошибки и ошибки распознавания: перепутанные буквы, "
    "латиницу вместо кириллицы, лишние или пропущенные пробелы внутри слов. "
    "Не меняй смысл, порядок слов, пунктуацию без необходимости, переносы строк и форматирование. "
    "Ничего не добавляй и не комментируй. Верни только исправленный текст."
)


class CorrectionResult:
    def __init__(self, text: str, engine: str, warning: str | None = None):
        self.text, self.engine, self.warning = text, engine, warning


def _chunks(text: str) -> list[str]:
    """Делит текст на фрагменты по границам абзацев, сохраняя разделители."""
    parts = re.split(r"(\n\s*\n)", text)
    chunks, current = [], ""
    for part in parts:
        if current and len(current) + len(part) > CHUNK_CHARS:
            chunks.append(current)
            current = ""
        current += part
    if current:
        chunks.append(current)
    return chunks


def _plausible(original: str, corrected: str) -> bool:
    # Защита от «фантазий» модели: исправление не должно сильно менять объём текста.
    if not original.strip():
        return True
    ratio = len(corrected) / max(len(original), 1)
    return 0.8 <= ratio <= 1.25


async def _ollama_fix(client: httpx.AsyncClient, chunk: str) -> str:
    if not chunk.strip():
        return chunk
    response = await client.post(
        "/api/chat",
        json={
            "model": settings.ollama_model,
            "stream": False,
            "options": {"temperature": 0},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": chunk},
            ],
        },
    )
    response.raise_for_status()
    fixed = response.json()["message"]["content"]
    # Сохраняем ведущие/замыкающие пробельные символы фрагмента.
    lead = chunk[: len(chunk) - len(chunk.lstrip())]
    trail = chunk[len(chunk.rstrip()) :]
    fixed = lead + fixed.strip() + trail
    return fixed if _plausible(chunk, fixed) else spellcheck.autocorrect(chunk)


def keep_layout(original: str, fixed: str) -> str:
    """Отменяет правки модели, которые меняют табуляции и переводы строк.

    Это элементы вёрстки базового документа (границы абзацев и ячеек, табуляция
    «г. Москва → дата»), а модель склонна заменять их пробелами. Слова сравниваются
    так, будто любой пробельный символ — пробел: исправления слов принимаются,
    а пробельные символы оригинала сохраняются.
    """
    a = _TOKEN.findall(original)
    b = _TOKEN.findall(fixed)
    norm = lambda tokens: [" " if t.isspace() else t for t in tokens]  # noqa: E731
    layout = ("\t", "\n")
    out: list[str] = []
    trailing_new_space = False  # последний токен — пробел, вставленный моделью
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, norm(a), norm(b), autojunk=False).get_opcodes():
        old = a[i1:i2]
        if tag == "equal" or any(t in layout for t in old):
            if trailing_new_space and old and old[0].isspace():
                out.pop()  # пробел модели + пробел оригинала дали бы двойной пробел
            out.extend(old)
            trailing_new_space = False
            continue
        for t in b[j1:j2]:  # новые абзацы и пустые строки от модели отбрасываются
            if t in layout:
                if out and out[-1] == " ":
                    out.pop()
                continue
            out.append(t)
        trailing_new_space = bool(out) and out[-1] == " "
    return "".join(out)


async def correct(text: str) -> CorrectionResult:
    try:
        async with httpx.AsyncClient(base_url=settings.ollama_url, timeout=settings.ollama_timeout) as client:
            fixed = [await _ollama_fix(client, chunk) for chunk in _chunks(text)]
        return CorrectionResult(keep_layout(text, "".join(fixed)), f"ollama:{settings.ollama_model}")
    except (httpx.HTTPError, KeyError, ValueError):
        return CorrectionResult(
            spellcheck.autocorrect(text),
            "hunspell",
            "Ollama недоступна — выполнено словарное исправление",
        )


async def ollama_status() -> dict:
    try:
        async with httpx.AsyncClient(base_url=settings.ollama_url, timeout=3) as client:
            response = await client.get("/api/tags")
            response.raise_for_status()
            models = [m["name"] for m in response.json().get("models", [])]
        return {"available": True, "model": settings.ollama_model, "model_installed": settings.ollama_model in models}
    except (httpx.HTTPError, KeyError, ValueError):
        return {"available": False, "model": settings.ollama_model, "model_installed": False}
