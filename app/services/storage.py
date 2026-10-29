"""Эфемерное рабочее пространство.

Сервис не хранит документы: всё обрабатывается в памяти, а внешним утилитам
(LibreOffice) файлы передаются через временный каталог, который удаляется
сразу после завершения операции, в том числе при ошибке.
"""

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.config import settings


@contextmanager
def ephemeral_dir() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="docmat-", dir=settings.tmp_dir or None) as path:
        yield Path(path)
