"""Оценка качества распознавания и исправления на сканах разного качества.

Из демонстрационного документа делаются «сканы» с шумом, наклоном, низким
разрешением и артефактами JPEG. Для каждого считается доля ошибочных слов (WER)
относительно эталона: после OCR, после словарного исправления и после Ollama.

Запуск (Ollama должна быть доступна по OLLAMA_URL):
    .venv/bin/python scripts/eval_correction.py [--pdf свой_скан.pdf --truth эталон.txt]
"""

import argparse
import asyncio
import io
import re
import sys
import time
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import corrector, editable, recognizer, spellcheck  # noqa: E402
from scripts.make_demo_scan import CONTENT, build_docx  # noqa: E402

DEMO = Path(__file__).resolve().parents[1] / "docs" / "samples" / "demo-scan.pdf"
# Эталон — текст демонстрационного документа без намеренных опечаток.
FIXES = {"распознования": "распознавания", "докуметов": "документов", "информационых": "информационных"}
TRUTH = " ".join(t for t, *_ in CONTENT)
for wrong, right in FIXES.items():
    TRUTH = TRUTH.replace(wrong, right)

# (название, dpi, наклон в градусах, шум, качество JPEG, размытие)
LEVELS = [
    ("хороший: 300 dpi", 300, 0.0, 0, None, 0),
    ("средний: 200 dpi, наклон 0,8°, шум, JPEG 60", 200, 0.8, 12, 60, 0),
    ("плохой: 150 dpi, наклон 1,5°, сильный шум, JPEG 35, размытие", 150, 1.5, 28, 35, 0.6),
    ("очень плохой: 110 dpi, наклон 2°, сильный шум, JPEG 25, размытие", 110, 2.0, 35, 25, 0.8),
]


def words(text: str) -> list[str]:
    return re.findall(r"[\wё]+", text.lower().replace("ё", "е"))


def wer(reference: str, hypothesis: str) -> float:
    ref, hyp = words(reference), words(hypothesis)
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1] / max(len(ref), 1)


def degrade(pdf_bytes: bytes, dpi: int, skew: float, noise: int, jpeg: int | None, blur: float) -> bytes:
    rng = np.random.default_rng(42)
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as src, pymupdf.open() as out:
        for page in src:
            img = Image.open(io.BytesIO(page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY).tobytes("png")))
            if skew:
                img = img.rotate(skew, resample=Image.BICUBIC, fillcolor=255)
            if blur:
                img = img.filter(ImageFilter.GaussianBlur(blur))
            if noise:
                arr = np.asarray(img, dtype=np.int16) + rng.normal(0, noise, (img.height, img.width))
                img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=jpeg) if jpeg else img.save(buf, "PNG")
            new = out.new_page(width=page.rect.width, height=page.rect.height)
            new.insert_image(new.rect, stream=buf.getvalue())
        return out.tobytes()


async def evaluate(name: str, pdf: bytes, truth: str) -> None:
    t0 = time.time()
    result = await recognizer.recognize("scan.pdf", pdf, "rus+eng", force_ocr=True)
    t_ocr = time.time() - t0
    text = result.text
    dictionary = spellcheck.autocorrect(text)
    t0 = time.time()
    llm = await corrector.correct(text)
    t_llm = time.time() - t0
    # Правки переносятся в базовый документ — проверяем, что текст документа совпал.
    edited = editable.apply_text(result.kind, result.data, llm.text)
    in_doc = editable.text_of(result.kind, edited.data)

    print(f"\n### {name}")
    print(f"OCR: {t_ocr:.1f} с, исправление ({llm.engine}): {t_llm:.1f} с")
    print("| Этап | WER | Ошибок орфографии |")
    print("|---|---|---|")
    for label, t in [("После OCR", text), ("Словарь Hunspell", dictionary), ("Ollama", llm.text)]:
        print(f"| {label} | {wer(truth, t):.1%} | {len(spellcheck.find_errors(t))} |")
    print(f"Правки перенесены в документ: {in_doc == llm.text} (применено {edited.applied}, пропущено {edited.skipped})")
    if llm.warning:
        print("Предупреждение:", llm.warning)
    print("Ollama:", llm.text.replace("\n", " ⏎ ")[:600])


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, help="собственный скан (PDF)")
    parser.add_argument("--truth", type=Path, help="эталонный текст для собственного скана")
    args = parser.parse_args()

    if args.pdf:
        truth = args.truth.read_text(encoding="utf-8") if args.truth else ""
        await evaluate(args.pdf.name, args.pdf.read_bytes(), truth)
        return

    # Эталонный PDF с текстом (без опечаток), из которого делаются сканы.
    import tempfile

    from app.services import office

    typo_docx = build_docx()
    clean_docx = typo_docx
    for wrong, right in FIXES.items():
        clean_docx = editable.apply_text("docx", clean_docx, editable.text_of("docx", clean_docx).replace(wrong, right)).data
    pdfs = {}
    with tempfile.TemporaryDirectory() as tmp:
        for key, docx in (("clean", clean_docx), ("typos", typo_docx)):
            src = Path(tmp) / f"{key}.docx"
            src.write_bytes(docx)
            pdfs[key] = (await office.convert(src, "pdf", Path(tmp) / key)).read_bytes()
    for name, *params in LEVELS:
        await evaluate(f"Скан {name}", degrade(pdfs["clean"], *params), TRUTH)
    # Опечатки в самом документе (3 слова): их должна исправить коррекция.
    await evaluate(f"Опечатки в оригинале, скан {LEVELS[1][0]}", degrade(pdfs["typos"], *LEVELS[1][1:]), TRUTH)


if __name__ == "__main__":
    asyncio.run(main())
