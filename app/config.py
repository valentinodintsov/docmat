"""Настройки сервиса. Все значения задаются переменными окружения."""

import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_tmp_dir() -> str:
    # /dev/shm находится в оперативной памяти: временные файлы не попадают на диск.
    shm = Path("/dev/shm")
    return str(shm) if shm.is_dir() and os.access(shm, os.W_OK) else ""


@dataclass(frozen=True)
class Settings:
    max_upload_mb: int = int(os.getenv("DOCMAT_MAX_UPLOAD_MB", "25"))
    max_pages: int = int(os.getenv("DOCMAT_MAX_PAGES", "50"))
    preview_dpi: int = int(os.getenv("DOCMAT_PREVIEW_DPI", "110"))
    ocr_dpi: int = int(os.getenv("DOCMAT_OCR_DPI", "300"))
    ocr_lang: str = os.getenv("DOCMAT_OCR_LANG", "rus+eng")
    spell_langs: tuple[str, ...] = field(
        default_factory=lambda: tuple(os.getenv("DOCMAT_SPELL_LANGS", "ru_RU,en_US").split(","))
    )
    soffice_bin: str = os.getenv("DOCMAT_SOFFICE", "soffice")
    soffice_timeout: int = int(os.getenv("DOCMAT_SOFFICE_TIMEOUT", "120"))
    soffice_concurrency: int = int(os.getenv("DOCMAT_SOFFICE_CONCURRENCY", "3"))
    ollama_url: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    ollama_timeout: int = int(os.getenv("OLLAMA_TIMEOUT", "180"))
    tmp_dir: str = os.getenv("DOCMAT_TMP_DIR", _default_tmp_dir())

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


settings = Settings()
