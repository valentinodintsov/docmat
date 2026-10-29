"""Снимает скриншоты интерфейса для docs/TZ.md.

Требует запущенного сервиса и Chromium:
    .venv/bin/uvicorn app.main:app --port 8000 &
    .venv/bin/python scripts/make_screenshots.py [--url http://127.0.0.1:8000] [--chromium /usr/bin/chromium-browser]
"""

import argparse
import shutil
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "screenshots"
DEMO = ROOT / "docs" / "samples" / "demo-scan.pdf"


def formatting_comparison(url: str) -> None:
    """Исходный скан и PDF, полученный после распознавания и исправления, рядом."""
    import base64
    import io

    import httpx
    import pymupdf
    from PIL import Image, ImageDraw, ImageFont

    with httpx.Client(base_url=url, timeout=600) as client:
        body = client.post("/api/recognize", files={"file": (DEMO.name, DEMO.read_bytes())}).json()
        fixed = client.post("/api/correct", json={"text": body["text"]}).json()["text"]
        out = client.post(
            "/api/convert",
            files={"base": ("base", base64.b64decode(body["base"]["data"])), "text": ("t.txt", fixed.encode())},
            data={"kind": body["base"]["kind"], "filename": DEMO.name},
        ).json()
    result = base64.b64decode(next(f["data"] for f in out["files"] if f["format"] == "pdfa"))

    def render(pdf: bytes) -> Image.Image:
        with pymupdf.open(stream=pdf, filetype="pdf") as doc:
            page = doc[0]
            clip = pymupdf.Rect(0, 0, page.rect.width, page.rect.height * 0.62)
            return Image.open(io.BytesIO(page.get_pixmap(dpi=110, clip=clip).tobytes("png"))).convert("RGB")

    left, right = render(DEMO.read_bytes()), render(result)
    pad, head = 24, 44
    canvas = Image.new("RGB", (left.width + right.width + pad * 3, max(left.height, right.height) + head + pad), "#eef1f5")
    draw = ImageDraw.Draw(canvas)
    font_path = "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf"
    try:
        font = ImageFont.truetype(font_path, 18)
    except OSError:
        font = ImageFont.load_default()
    for x, img, title in ((pad, left, "Исходный скан (demo-scan.pdf)"), (pad * 2 + left.width, right, "Результат: PDF/A после распознавания и исправления")):
        draw.text((x, 14), title, fill="#1d2430", font=font)
        canvas.paste(img, (x, head))
    canvas.save(OUT / "09-formatting.png")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--chromium", default=shutil.which("chromium-browser") or shutil.which("chromium"))
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.chromium)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1, locale="ru-RU")
        page.goto(args.url)
        page.screenshot(path=OUT / "01-main.png")

        page.evaluate("document.getElementById('dropzone').classList.add('dragover')")
        page.locator("#dropzone").screenshot(path=OUT / "02-dragover.png")
        page.evaluate("document.getElementById('dropzone').classList.remove('dragover')")

        page.set_input_files("#file-input", DEMO)
        page.wait_for_selector("#original img")
        page.wait_for_function("!document.getElementById('btn-recognize').disabled")
        page.screenshot(path=OUT / "03-loaded.png")

        page.click("#btn-recognize")
        page.wait_for_selector("#recognized mark.err", timeout=120_000)
        page.wait_for_function("!document.getElementById('btn-recognize').classList.contains('busy')")
        page.screenshot(path=OUT / "04-recognized.png")

        page.locator("#recognized mark.err").first.click()
        page.wait_for_selector("#suggest-menu:not([hidden])")
        page.screenshot(path=OUT / "05-suggestions.png")
        page.keyboard.press("Escape")

        page.click("#btn-correct")
        page.wait_for_function("!document.getElementById('btn-correct').classList.contains('busy')", timeout=300_000)
        page.screenshot(path=OUT / "06-corrected.png")

        page.click("#btn-convert")
        page.wait_for_selector('#download-list a[data-format="pdfa"][aria-disabled="false"]', timeout=180_000)
        page.screenshot(path=OUT / "07-converted.png")

        formatting_comparison(args.url)

        mobile = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2, locale="ru-RU")
        mobile.goto(args.url)
        mobile.screenshot(path=OUT / "08-mobile.png", full_page=True)

        browser.close()
    print("Скриншоты сохранены в", OUT)


if __name__ == "__main__":
    main()
