# DocMat. Руководство администратора

Редакция 1.0, 2026

## 1. Состав

| Контейнер | Образ | Назначение |
|---|---|---|
| `docmat` | собирается из `Dockerfile` | Веб-интерфейс, OCR (Tesseract), LibreOffice, проверка орфографии |
| `ollama` | `ollama/ollama` | Локальная языковая модель для кнопки «Исправить» |
| `ollama-pull` | `ollama/ollama` | Однократная загрузка модели (профиль `setup`) |

Требования к серверу приведены в п. 4.3.2 ТЗ. Минимум: 4 ядра, 8 ГБ ОЗУ, 15 ГБ диска.

## 2. Установка

```bash
git clone <репозиторий> docmat && cd docmat
docker compose up -d --build
docker compose run --rm ollama-pull     # загрузка модели, ~4,7 ГБ для qwen2.5:7b
```

Проверка:

```bash
curl http://localhost:8000/api/health
```

В ответе все компоненты должны иметь `"available": true`, а у Ollama также `"model_installed": true`.

### Ускорение на GPU NVIDIA

Установите NVIDIA Container Toolkit и раскомментируйте блок `deploy` у сервиса `ollama` в `docker-compose.yml`.

### Другая модель

Слабые машины (до 8 ГБ ОЗУ, без GPU) могут использовать модель поменьше:

```bash
echo "OLLAMA_MODEL=qwen2.5:3b" > .env
docker compose up -d
docker compose run --rm ollama-pull
```

## 3. Настройка

Параметры задаются переменными окружения в `docker-compose.yml` или в файле `.env`. Полный перечень приведён в Приложении Б ТЗ. Основные:

| Переменная | По умолчанию | Когда менять |
|---|---|---|
| `DOCMAT_MAX_UPLOAD_MB` | 25 | Нужны более крупные файлы |
| `DOCMAT_MAX_PAGES` | 50 | Длинные документы; учитывайте время OCR (~3–10 с/стр.) |
| `DOCMAT_OCR_DPI` | 300 | 400 — для мелкого шрифта, 200 — для скорости |
| `DOCMAT_SOFFICE_CONCURRENCY` | 3 | Уменьшить при нехватке памяти |
| `OLLAMA_MODEL` | qwen2.5:7b | См. п. 2 |

Размер tmpfs `/tmp` (1 ГБ) должен быть не меньше, чем `DOCMAT_MAX_UPLOAD_MB × число одновременных пользователей × 4`.

## 4. Конфиденциальность

Сервис спроектирован так, чтобы документы не попадали на диск (п. 4.1.8 ТЗ). Чтобы это свойство сохранялось, администратору следует:

- **не убирать** из `docker-compose.yml` параметры `read_only: true` и `tmpfs: /tmp`;
- **не подключать** тома к контейнеру `docmat`;
- **не включать** журнал доступа uvicorn с телом запросов и не ставить перед сервисом прокси, который сохраняет тела запросов;
- при доступе из сети размещать сервис за обратным прокси с HTTPS.

Пример для nginx:

```nginx
server {
    listen 443 ssl;
    server_name docmat.example.local;
    client_max_body_size 30m;
    proxy_read_timeout 300s;
    proxy_request_buffering off;    # тело запроса не буферизуется на диск
    location / { proxy_pass http://127.0.0.1:8000; }
}
```

### Проверка качества

Скрипт `scripts/eval_correction.py` создаёт сканы разного качества, распознаёт их, исправляет и выводит долю ошибочных слов, время и признак полного переноса правок в документ. Его можно запустить и на собственном скане:

```bash
.venv/bin/python scripts/eval_correction.py --pdf скан.pdf --truth эталон.txt
```

Результаты испытаний приведены в Приложении Г ТЗ.

## 5. Обслуживание

| Задача | Команда |
|---|---|
| Состояние | `docker compose ps`, `curl localhost:8000/api/health` |
| Журналы | `docker compose logs -f docmat` |
| Обновление | `git pull && docker compose up -d --build` |
| Перезапуск | `docker compose restart docmat` |
| Остановка | `docker compose down` (модели Ollama сохраняются в томе `ollama-models`) |
| Полное удаление | `docker compose down -v --rmi local` |

Резервное копирование не требуется: сервис не хранит данных. Модель при необходимости загружается заново.

## 6. Неисправности

| Признак | Причина и решение |
|---|---|
| «Ollama недоступна» в интерфейсе | `docker compose ps ollama`; если модель не загружена, выполните `docker compose run --rm ollama-pull` |
| «Превышено время ожидания LibreOffice» | Документ очень большой или сложный: увеличьте `DOCMAT_SOFFICE_TIMEOUT` |
| Контейнер перезапускается, в журнале `Killed` | Не хватает памяти: уменьшите `DOCMAT_SOFFICE_CONCURRENCY`, число workers или модель Ollama |
| Плохое качество OCR | Скан ниже 200 dpi или с наклоном: пересканируйте с разрешением 300 dpi |
| В PDF вместо кириллицы квадраты | В образе нет нужного шрифта: добавьте пакет шрифтов в `Dockerfile` и пересоберите образ |

## 7. Разработка и тесты

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest                              # автотесты
.venv/bin/python scripts/make_demo_scan.py    # демонстрационный скан
.venv/bin/python scripts/make_screenshots.py  # скриншоты для документации
```

---

© Одинцов Валентин Николаевич, 2026
