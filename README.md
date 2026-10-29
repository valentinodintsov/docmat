# DocMat

Распознавание текста (Tesseract) и конвертация документов (DOC, DOCX, ODT, ODS, RTF, XLS, XLSX, PDF, PDF/A → DOCX, XLSX, ODT, ODS, PDF, PDF/A) с проверкой орфографии и исправлением ошибок локальной LLM (Ollama). Документы на сервере не хранятся.

Документация: [техническое задание](docs/TZ.md), [руководство пользователя](docs/user-guide.md), [руководство администратора](docs/admin-guide.md).

## Запуск в Docker

```bash
docker compose up -d --build
docker compose run --rm ollama-pull      # один раз: загрузка модели qwen2.5:7b
```

Откройте http://localhost:8000.

## Локальная разработка

Системные зависимости: `tesseract` с языками `rus` и `eng`, `libreoffice`, `enchant-2`, словари `hunspell-ru` и `hunspell-en-us`.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --reload
.venv/bin/pytest
```

Скриншоты для ТЗ: `.venv/bin/python scripts/make_screenshots.py` (при запущенном сервисе).

© Одинцов Валентин Николаевич, 2026
