"use strict";

// Документ и результаты живут только в памяти браузера: сервер их не хранит.
const ALLOWED = ["doc", "docx", "odt", "ods", "rtf", "xls", "xlsx", "pdf"];
const RECHECK_DELAY_MS = 900;

const $ = (id) => document.getElementById(id);
const ui = {
  dropzone: $("dropzone"),
  fileInput: $("file-input"),
  fileInfo: $("file-info"),
  original: $("original"),
  origMeta: $("orig-meta"),
  recognized: $("recognized"),
  recMeta: $("rec-meta"),
  recPlaceholder: $("rec-placeholder"),
  btnRecognize: $("btn-recognize"),
  btnCorrect: $("btn-correct"),
  btnClear: $("btn-clear"),
  btnConvert: $("btn-convert"),
  optLang: $("opt-lang"),
  optForceOcr: $("opt-force-ocr"),
  downloads: document.querySelectorAll("#download-list a"),
  status: $("status"),
  menu: $("suggest-menu"),
};

const state = {
  file: null,       // исходный файл
  base: null,       // базовый документ с оформлением исходного: { kind, blob }
  text: "",
  errors: [],
  ignored: new Set(),
  blobUrls: [],
  busy: false,
  recheckTimer: null,
};

/* ---------- Вспомогательные функции ---------- */

function setStatus(message, kind = "") {
  ui.status.textContent = message;
  ui.status.className = "status" + (kind ? " " + kind : "");
}

function formatSize(bytes) {
  if (bytes < 1024) return bytes + " Б";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " КБ";
  return (bytes / 1024 / 1024).toFixed(1) + " МБ";
}

function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}

async function api(path, options) {
  const response = await fetch(path, options);
  let body = null;
  try { body = await response.json(); } catch { /* не JSON */ }
  if (!response.ok) {
    const detail = body && body.detail;
    const message = typeof detail === "string" ? detail
      : Array.isArray(detail) ? "Некорректный запрос" : `Ошибка сервера (${response.status})`;
    throw new Error(message);
  }
  return body;
}

function postJson(path, payload) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

function base64ToBlob(data, mime) {
  const bin = atob(data);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Blob([bytes], { type: mime });
}

async function withBusy(button, task) {
  if (state.busy) return;
  state.busy = true;
  button.classList.add("busy");
  updateButtons();
  try {
    await task();
  } catch (err) {
    setStatus(err.message || String(err), "error");
  } finally {
    state.busy = false;
    button.classList.remove("busy");
    updateButtons();
  }
}

function updateButtons() {
  const hasFile = !!state.file;
  const hasText = state.text.trim().length > 0;
  ui.btnRecognize.disabled = state.busy || !hasFile;
  ui.btnCorrect.disabled = state.busy || !hasText;
  ui.btnConvert.disabled = state.busy || !hasText || !state.base;
  ui.btnClear.disabled = state.busy || (!hasFile && !hasText);
  ui.recognized.contentEditable = hasText && !state.busy ? "plaintext-only" : "false";
  ui.recPlaceholder.hidden = hasText;
}

function resetDownloads() {
  state.blobUrls.forEach(URL.revokeObjectURL);
  state.blobUrls = [];
  ui.downloads.forEach((a) => {
    a.removeAttribute("href");
    a.removeAttribute("download");
    a.setAttribute("aria-disabled", "true");
  });
}

/* ---------- Загрузка документа ---------- */

function extensionOf(name) {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i + 1).toLowerCase() : "";
}

async function acceptFile(file) {
  if (!file) return;
  if (!ALLOWED.includes(extensionOf(file.name))) {
    setStatus("Неподдерживаемый формат файла", "error");
    return;
  }
  clearAll();
  state.file = file;
  ui.fileInfo.textContent = `${file.name} · ${formatSize(file.size)}`;
  ui.fileInfo.hidden = false;
  ui.dropzone.classList.add("loaded");
  ui.original.innerHTML = '<p class="placeholder">Загрузка документа…</p>';
  updateButtons();

  await withBusy(ui.btnRecognize, async () => {
    setStatus("Подготовка просмотра…");
    const form = new FormData();
    form.append("file", file);
    const data = await api("/api/preview", { method: "POST", body: form });
    if (state.file !== file) return; // пользователь успел сбросить документ
    renderOriginal(data);
    setStatus("Документ загружен. Нажмите «Распознать».");
  });
  if (!ui.original.querySelector("img")) {
    ui.original.innerHTML = '<p class="placeholder">Не удалось отобразить документ</p>';
  }
}

function renderOriginal(data) {
  ui.original.replaceChildren();
  data.pages.forEach((src, i) => {
    const img = document.createElement("img");
    img.src = src;
    img.alt = `Страница ${i + 1}`;
    img.loading = "lazy";
    ui.original.append(img);
  });
  if (data.truncated) {
    const note = document.createElement("p");
    note.className = "placeholder";
    note.textContent = `Показаны первые ${data.pages.length} из ${data.page_count} страниц`;
    ui.original.append(note);
  }
  const fmt = data.format === "pdfa" ? "PDF/A" : data.format.toUpperCase();
  ui.origMeta.textContent = `${fmt} · ${data.page_count} ${plural(data.page_count, "страница", "страницы", "страниц")}`;
}

ui.dropzone.addEventListener("click", () => ui.fileInput.click());
ui.dropzone.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); ui.fileInput.click(); }
});
ui.fileInput.addEventListener("change", () => {
  acceptFile(ui.fileInput.files[0]);
  ui.fileInput.value = "";
});
["dragenter", "dragover"].forEach((type) =>
  ui.dropzone.addEventListener(type, (e) => { e.preventDefault(); ui.dropzone.classList.add("dragover"); }));
["dragleave", "drop"].forEach((type) =>
  ui.dropzone.addEventListener(type, (e) => { e.preventDefault(); ui.dropzone.classList.remove("dragover"); }));
ui.dropzone.addEventListener("drop", (e) => acceptFile(e.dataTransfer.files[0]));
// Файл, брошенный мимо поля, не должен открываться браузером.
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => e.preventDefault());

/* ---------- Отображение текста с подсветкой ошибок ---------- */

function visibleErrors() {
  return state.errors.filter((e) => !state.ignored.has(e.word.toLowerCase()));
}

function renderText() {
  const errors = visibleErrors();
  const fragment = document.createDocumentFragment();
  let pos = 0;
  for (const err of errors) {
    if (err.start < pos || state.text.slice(err.start, err.end) !== err.word) continue;
    fragment.append(state.text.slice(pos, err.start));
    const mark = document.createElement("mark");
    mark.className = "err";
    mark.textContent = err.word;
    mark.dataset.start = err.start;
    mark.title = err.suggestions.length ? "Варианты: " + err.suggestions.join(", ") : "Нет вариантов";
    fragment.append(mark);
    pos = err.end;
  }
  fragment.append(state.text.slice(pos));
  ui.recognized.replaceChildren(fragment);

  const words = (state.text.match(/[A-Za-zА-Яа-яЁё]+/g) || []).length;
  ui.recMeta.textContent = state.text
    ? `${words} ${plural(words, "слово", "слова", "слов")} · ошибок: ${errors.length}`
    : "";
  updateButtons();
}

function caretOffset() {
  const sel = window.getSelection();
  if (!sel.rangeCount || !ui.recognized.contains(sel.focusNode)) return null;
  const range = document.createRange();
  range.setStart(ui.recognized, 0);
  range.setEnd(sel.focusNode, sel.focusOffset);
  return range.toString().length;
}

function restoreCaret(offset) {
  if (offset == null) return;
  const walker = document.createTreeWalker(ui.recognized, NodeFilter.SHOW_TEXT);
  let node, remaining = offset;
  while ((node = walker.nextNode())) {
    if (remaining <= node.length) {
      window.getSelection().collapse(node, remaining);
      return;
    }
    remaining -= node.length;
  }
}

async function recheck() {
  const text = state.text;
  const { errors } = await postJson("/api/check", { text });
  if (text !== state.text) return; // текст изменился, пока шла проверка
  state.errors = errors;
  const caret = caretOffset();
  renderText();
  restoreCaret(caret);
}

ui.recognized.addEventListener("input", () => {
  state.text = ui.recognized.textContent;
  resetDownloads();
  updateButtons();
  clearTimeout(state.recheckTimer);
  state.recheckTimer = setTimeout(() => recheck().catch((e) => setStatus(e.message, "error")), RECHECK_DELAY_MS);
});

/* ---------- Меню вариантов исправления ---------- */

function closeMenu() { ui.menu.hidden = true; }

function replaceWord(start, word, replacement) {
  if (state.text.slice(start, start + word.length) !== word) return;
  state.text = state.text.slice(0, start) + replacement + state.text.slice(start + word.length);
  const delta = replacement.length - word.length;
  state.errors = state.errors
    .filter((e) => e.start !== start)
    .map((e) => (e.start > start ? { ...e, start: e.start + delta, end: e.end + delta } : e));
  resetDownloads();
  renderText();
  recheck().catch(() => {});
}

ui.recognized.addEventListener("click", (e) => {
  const mark = e.target.closest("mark.err");
  if (!mark) return closeMenu();
  const start = Number(mark.dataset.start);
  const err = state.errors.find((x) => x.start === start);
  if (!err) return;

  ui.menu.replaceChildren();
  if (err.suggestions.length) {
    err.suggestions.forEach((s) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = s;
      b.addEventListener("click", () => { closeMenu(); replaceWord(err.start, err.word, s); });
      ui.menu.append(b);
    });
  } else {
    const p = document.createElement("div");
    p.className = "empty";
    p.textContent = "Нет вариантов";
    ui.menu.append(p);
  }
  const ignore = document.createElement("button");
  ignore.type = "button";
  ignore.className = "ignore";
  ignore.textContent = "Пропустить слово";
  ignore.addEventListener("click", () => { closeMenu(); state.ignored.add(err.word.toLowerCase()); renderText(); });
  ui.menu.append(ignore);

  const rect = mark.getBoundingClientRect();
  ui.menu.style.left = `${rect.left + window.scrollX}px`;
  ui.menu.style.top = `${rect.bottom + window.scrollY + 4}px`;
  ui.menu.hidden = false;
});
document.addEventListener("click", (e) => {
  if (!ui.menu.contains(e.target) && !e.target.closest("mark.err")) closeMenu();
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenu(); });
ui.recognized.addEventListener("scroll", closeMenu);

/* ---------- Кнопки управления ---------- */

ui.btnRecognize.addEventListener("click", () => withBusy(ui.btnRecognize, async () => {
  setStatus("Распознавание…");
  const form = new FormData();
  form.append("file", state.file);
  form.append("lang", ui.optLang.value);
  form.append("force_ocr", ui.optForceOcr.checked ? "true" : "false");
  const data = await api("/api/recognize", { method: "POST", body: form });
  const mime = data.base.kind === "xlsx"
    ? "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    : "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
  state.base = { kind: data.base.kind, blob: base64ToBlob(data.base.data, mime) };
  state.text = data.text;
  state.errors = data.errors;
  resetDownloads();
  renderText();
  const ocrPages = data.pages.filter((p) => p.method === "ocr").length;
  if (!data.text) setStatus("Текст не найден", "error");
  else if (data.pages[0].method === "document") setStatus("Готово. Текст извлечён из документа.", "ok");
  else if (ocrPages) setStatus(`Готово. OCR: ${ocrPages} из ${data.pages.length} стр.`, "ok");
  else setStatus("Готово. Использован текстовый слой PDF.", "ok");
}));

ui.btnCorrect.addEventListener("click", () => withBusy(ui.btnCorrect, async () => {
  setStatus("Исправление ошибок локальной моделью… Без GPU это занимает до минуты на страницу.");
  clearTimeout(state.recheckTimer);
  const data = await postJson("/api/correct", { text: state.text });
  state.text = data.text;
  state.errors = data.errors;
  resetDownloads();
  renderText();
  setStatus(data.warning || `Исправлено (${data.engine})`, data.warning ? "" : "ok");
}));

ui.btnConvert.addEventListener("click", () => withBusy(ui.btnConvert, async () => {
  setStatus("Конвертация…");
  resetDownloads();
  if (!state.base) throw new Error("Сначала распознайте документ");
  const form = new FormData();
  form.append("base", state.base.blob, `base.${state.base.kind}`);
  form.append("kind", state.base.kind);
  form.append("text", new Blob([state.text], { type: "text/plain;charset=utf-8" }), "text.txt");
  form.append("filename", state.file ? state.file.name : "document");
  const data = await api("/api/convert", { method: "POST", body: form });
  for (const f of data.files) {
    const link = document.querySelector(`#download-list a[data-format="${f.format}"]`);
    if (!link) continue;
    const url = URL.createObjectURL(base64ToBlob(f.data, f.mime));
    state.blobUrls.push(url);
    link.href = url;
    link.download = f.filename;
    link.setAttribute("aria-disabled", "false");
  }
  setStatus(data.skipped
    ? `Документы готовы. Не перенесено правок: ${data.skipped} (объединение или разбиение абзацев)`
    : "Документы готовы к скачиванию", data.skipped ? "" : "ok");
}));

function clearAll() {
  clearTimeout(state.recheckTimer);
  closeMenu();
  state.file = null;
  state.base = null;
  state.text = "";
  state.errors = [];
  state.ignored.clear();
  resetDownloads();
  ui.fileInfo.hidden = true;
  ui.dropzone.classList.remove("loaded");
  ui.original.innerHTML = '<p class="placeholder">Здесь появится загруженный документ</p>';
  ui.origMeta.textContent = "";
  renderText();
  setStatus("");
}

ui.btnClear.addEventListener("click", clearAll);

updateButtons();
