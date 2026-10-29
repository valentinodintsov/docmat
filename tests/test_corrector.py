import asyncio
import json

import httpx
import pytest

from app.services import corrector


@pytest.fixture
def fake_ollama(monkeypatch):
    """Подменяет HTTP-клиент: ответ модели формирует переданная функция."""
    calls = []

    def install(reply):
        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            text = payload["messages"][-1]["content"]
            calls.append(text)
            return httpx.Response(200, json={"message": {"role": "assistant", "content": reply(text)}})

        real_client = httpx.AsyncClient

        def client(**kwargs):
            return real_client(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(corrector.httpx, "AsyncClient", client)
        return calls

    return install


def test_ollama_correction_used(fake_ollama):
    fake_ollama(lambda text: text.replace("распознования", "распознавания"))
    result = asyncio.run(corrector.correct("Сервис распознования документов"))
    assert result.text == "Сервис распознавания документов"
    assert result.engine.startswith("ollama:")
    assert result.warning is None


def test_hallucinated_answer_rejected(fake_ollama):
    fake_ollama(lambda text: "Конечно! Вот исправленный текст с пояснениями: " + text * 3)
    result = asyncio.run(corrector.correct("Пример доккумента"))
    assert result.text == "Пример документа"  # словарное исправление вместо ответа модели


def test_whitespace_around_chunk_preserved(fake_ollama):
    fake_ollama(lambda text: text.strip())
    text = "  Первый абзац текста\n\nВторой абзац текста\n"
    assert asyncio.run(corrector.correct(text)).text == text


def test_long_text_split_by_paragraphs(fake_ollama):
    calls = fake_ollama(lambda text: text)
    paragraph = "Слово " * 300  # около 1800 символов
    text = "\n\n".join([paragraph] * 4)
    result = asyncio.run(corrector.correct(text))
    assert result.text == text
    assert len(calls) > 1
    assert all(len(c) <= corrector.CHUNK_CHARS + len(paragraph) for c in calls)


def test_layout_characters_kept(fake_ollama):
    fake_ollama(lambda text: text.replace("\t", " ").replace("ошибкка", "ошибка"))
    text = "г. Москва\t15 октября\nВ тексте ошибкка"
    assert asyncio.run(corrector.correct(text)).text == "г. Москва\t15 октября\nВ тексте ошибка"


def test_keep_layout_allows_word_fixes_next_to_tabs():
    assert corrector.keep_layout("а\tдокумнет", "а документ") == "а\tдокумент"


def test_keep_layout_drops_inserted_line_breaks():
    assert corrector.keep_layout("один\nдва, три", "один\n\nдва. \nтри") == "один\nдва. три"
