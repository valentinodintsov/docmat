from app.services import spellcheck


def test_correct_words_pass():
    assert spellcheck.find_errors("Документы не хранятся на сервере. Hello world.") == []


def test_misspelled_word_found_with_suggestions():
    errors = spellcheck.find_errors("Загрузите доккумент для распознавания")
    assert [e.word for e in errors] == ["доккумент"]
    assert "документ" in errors[0].suggestions


def test_mixed_script_flagged_and_fixed():
    word = "Пpивeт"  # латинские p и e
    errors = spellcheck.find_errors(word)
    assert errors and errors[0].suggestions == ["Привет"]


def test_abbreviations_ignored():
    assert spellcheck.find_errors("ГОСТ РФ ООО") == []


def test_offsets_are_utf16():
    text = "😀 доккумент"
    (err,) = spellcheck.find_errors(text)
    assert (err.start, err.end) == (3, 12)  # эмодзи занимает две единицы UTF-16


def test_autocorrect_keeps_case():
    assert spellcheck.autocorrect("Доккумент готов") == "Документ готов"


def test_autocorrect_skips_doubtful_words():
    # Искажённое OCR название не должно превращаться в случайное слово.
    assert spellcheck.autocorrect("сервис РосМа готов") == "сервис РосМа готов"


def test_autocorrect_fixes_mixed_script():
    assert spellcheck.autocorrect("Пpивeт") == "Привет"


def test_edit_distance():
    assert spellcheck.edit_distance("доккумент", "документ") == 1
    assert spellcheck.edit_distance("ab", "ba") == 1
    assert spellcheck.edit_distance("кот", "кит") == 1
    assert spellcheck.edit_distance("", "abc") == 3
