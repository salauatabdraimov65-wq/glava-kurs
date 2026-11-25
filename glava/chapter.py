"""Общий фундамент: глава по страницам, отделение описаний рисунков, проверка цитат.

Этим модулем пользуются все треки (layer, review, course, video). Геологии здесь нет.

    from glava.chapter import load_chapter, find_quote
    ch = load_chapter("latyshova_gl5_par17-20")
    ch.pages[95]            # авторский текст страницы 95 (без вставленных описаний рисунков)
    ch.figure_notes[95]     # описания рисунков, написанные моделью при распознавании (НЕ автор)
    find_quote(ch, "лишь на основании количественной интерпретации")  # -> [95]
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE_RE = re.compile(r"<!-- page (\d+) -->")
# Блок, вставленный моделью при распознавании: "----\n━━━ FORMAT N: ... ━━━ ... ----"
FIG_NOTE_RE = re.compile(r"(?:^|\n)(?:-{4}[ \t]*\n)?[ \t]*(━━━ FORMAT.*?(?:\nKEYWORDS:[^\n]*|(?=\n-{4}[ \t]*(?:\n|$))|\Z))", re.S)
SEP_RE = re.compile(r"^\s*-{4}\s*$", re.M)


def materials_dir() -> Path:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    p = Path(os.environ.get("MATERIALS_DIR") or ROOT.parent).expanduser()
    if not p.is_absolute():          # относительный путь — от папки проекта, а не от текущей папки терминала
        p = ROOT / p
    p = p.resolve()
    if not (p / "books md").exists():
        raise FileNotFoundError(
            f"Не нашёл материалы организаторов в {p} (нет папки 'books md').\n"
            f"Либо положите папку glava-kurs внутрь папки с материалами (рядом с 'книги' и 'books md'),\n"
            f"либо укажите в .env полный путь: MATERIALS_DIR=/путь/к/kmg-materials")
    return p


def chapters_config() -> dict:
    return json.loads((ROOT / "config" / "chapters.json").read_text(encoding="utf-8"))["chapters"]


def page_list(ranges) -> list[int]:
    return [p for a, b in ranges for p in range(a, b + 1)]


@dataclass
class Chapter:
    id: str
    meta: dict
    pages: dict[int, str]                       # авторский текст по страницам главы
    figure_notes: dict[int, list[str]] = field(default_factory=dict)  # описания рисунков от модели
    all_pages: dict[int, str] = field(default_factory=dict)  # вся книга (для соседних страниц)

    @property
    def md_path(self) -> Path:
        return materials_dir() / self.meta["md"]

    @property
    def pdf_path(self) -> Path:
        return materials_dir() / self.meta["pdf"]


def split_pages(md_text: str) -> dict[int, str]:
    parts = PAGE_RE.split(md_text)
    return {int(parts[i]): parts[i + 1] for i in range(1, len(parts), 2)}


def strip_figure_notes(text: str) -> tuple[str, list[str]]:
    notes = [m.group(1).strip() for m in FIG_NOTE_RE.finditer("\n" + text + "\n")]
    clean = FIG_NOTE_RE.sub("\n\n", "\n" + text + "\n")
    clean = SEP_RE.sub("", clean).strip("\n")
    return clean, notes


def load_chapter(chapter_id: str, md_path: str | Path | None = None, pages=None) -> Chapter:
    """Глава из config/chapters.json или произвольный md (md_path + pages) — для тестовых глав."""
    meta = dict(chapters_config().get(chapter_id, {}))
    if md_path:
        meta["md"] = str(md_path)
    if pages:
        meta["pages"] = pages
    path = Path(meta["md"])
    if not path.is_absolute():
        path = materials_dir() / path
    raw = split_pages(path.read_text(encoding="utf-8"))
    wanted = page_list(meta["pages"]) if meta.get("pages") else sorted(raw)
    ch = Chapter(chapter_id, meta, {}, {}, {})
    for p, t in raw.items():
        clean, notes = strip_figure_notes(t)
        ch.all_pages[p] = clean
        if p in wanted:
            ch.pages[p] = clean
            ch.figure_notes[p] = notes
    return ch


# ---------- цитаты ----------

def norm(s: str) -> str:
    """Нормализация для сверки цитат: регистр, пробелы, переносы, ё/е, разные тире и кавычки."""
    s = s.lower().replace("ё", "е")
    s = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", s)          # перенос слова
    s = re.sub(r"[‐‑‒–—―−]", "-", s)
    s = re.sub(r"[«»„“”\"']", '"', s)
    s = re.sub(r"\s+", "", s)
    return s


def find_quote(ch: Chapter, quote: str, include_figure_notes: bool = False) -> list[int]:
    """Страницы главы, где цитата встречается дословно (без учёта пробелов и регистра)."""
    q = norm(quote)
    if len(q) < 8:
        return []
    hits = [p for p, t in ch.pages.items() if q in norm(t)]
    if not hits:  # цитата на стыке страниц
        ps = sorted(ch.pages)
        for a, b in zip(ps, ps[1:]):
            if b == a + 1 and q in norm(ch.pages[a] + ch.pages[b]):
                hits.append(a)
    if not hits and include_figure_notes:
        hits = [p for p, ns in ch.figure_notes.items() if any(q in norm(n) for n in ns)]
    return hits


def check_claim(ch: Chapter, quote: str, page_pdf: int) -> dict:
    """{'ok': bool, 'found_on': [...], 'problem': str|None} — проверка цитаты и страницы утверждения."""
    found = find_quote(ch, quote)
    if not found:
        in_notes = find_quote(ch, quote, include_figure_notes=True)
        if in_notes:
            return {"ok": False, "found_on": in_notes, "problem": "цитата из описания рисунка, написанного моделью, а не из текста автора"}
        return {"ok": False, "found_on": [], "problem": "цитата не найдена в тексте главы"}
    if page_pdf not in found:
        return {"ok": False, "found_on": found, "problem": f"цитата есть на стр. {found}, а указана стр. {page_pdf}"}
    return {"ok": True, "found_on": found, "problem": None}


if __name__ == "__main__":
    import sys
    cid = sys.argv[1] if len(sys.argv) > 1 else "latyshova_gl5_par17-20"
    ch = load_chapter(cid)
    words = sum(len(t.split()) for t in ch.pages.values())
    notes = sum(len(n) for n in ch.figure_notes.values())
    print(f"{cid}: стр. {min(ch.pages)}–{max(ch.pages)}, {len(ch.pages)} стр., {words} слов автора, {notes} описаний рисунков от модели")
