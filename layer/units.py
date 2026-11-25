"""Нарезка главы на единицы с устойчивыми id (p095.03) — основа таблицы покрытия. Без модели.

Единица: абзац, заголовок, формула, таблица, подпись рисунка, задача, колонтитул.
Колонтитулы (номер страницы, повторяющийся заголовок главы) помечаются и в покрытие идут с причиной.

    uv run python -m layer.units <chapter_id>       # статистика + первые единицы
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from dataclasses import dataclass, asdict

from glava.chapter import Chapter, load_chapter

FIG_RE = re.compile(r"^\s*(\*\*)?\s*(Рис\.?|Рисунок|Fig\.?|Figure)\s*\d", re.I)
TABLE_CAPTION_RE = re.compile(r"^\s*(\*\*)?\s*(Таблица|Табл\.|Table)\s*\d", re.I)
TASK_HEAD_RE = re.compile(r"^\s*#*\s*(Задач[аи]|Упражнени[яе]|Problems?|Exercises?)\s*$", re.I)
NUMBERED_RE = re.compile(r"^\s*(\d{1,3})\.\s+\S")
PAGE_NUM_RE = re.compile(r"^\s*\d{1,4}\s*$")
MAX_UNIT_WORDS = 220   # длиннее — режем по строкам, затем по предложениям


@dataclass
class Unit:
    unit_id: str
    page_pdf: int
    kind: str            # paragraph | heading | formula | table | figure | task | running_header
    text: str
    excerpt: str
    in_scope: bool = True   # False — кусок соседнего раздела на крайней странице

    def to_dict(self):
        return asdict(self)


def _excerpt(text: str, n: int = 12) -> str:
    words = re.sub(r"[#*|$\\{}]", " ", text).split()
    return " ".join(words[:n])


def _running_headers(ch: Chapter) -> set[str]:
    """Короткие строки, которые повторяются в начале многих страниц книги — колонтитулы."""
    c = Counter()
    for t in ch.all_pages.values():
        for line in [l.strip() for l in t.strip().splitlines() if l.strip()][:3]:
            if len(line) < 90 and not NUMBERED_RE.match(line):
                c[line.strip("# *").upper()] += 1
    return {k for k, v in c.items() if v >= 3}


def _blocks(text: str) -> list[str]:
    """Блоки по пустым строкам; строки таблицы склеиваются в один блок."""
    blocks, cur = [], []
    for line in text.splitlines():
        if not line.strip():
            if cur:
                blocks.append("\n".join(cur)); cur = []
            continue
        if cur and (line.lstrip().startswith("|") != cur[-1].lstrip().startswith("|")):
            blocks.append("\n".join(cur)); cur = []
        cur.append(line)
    if cur:
        blocks.append("\n".join(cur))
    # таблица, разорванная пустой строкой, — склеить
    merged = []
    for b in blocks:
        if merged and b.lstrip().startswith("|") and merged[-1].lstrip().startswith("|"):
            merged[-1] += "\n" + b
        else:
            merged.append(b)
    # абзацы, разделённые одиночным переносом, и слишком длинные абзацы — разрезать
    out = []
    for b in merged:
        if b.lstrip().startswith("|") or len(b.split()) <= MAX_UNIT_WORDS:
            out.append(b); continue
        lines = [l for l in b.splitlines() if l.strip()]
        parts = lines if len(lines) > 1 else [b]
        for part in parts:
            if len(part.split()) <= MAX_UNIT_WORDS:
                out.append(part); continue
            sents, cur = re.split(r"(?<=[.!?…])\s+", part), []
            for s in sents:
                cur.append(s)
                if len(" ".join(cur).split()) >= MAX_UNIT_WORDS // 2:
                    out.append(" ".join(cur)); cur = []
            if cur:
                out.append(" ".join(cur))
    return out


def classify(block: str, headers: set[str], in_tasks: bool) -> str:
    s = block.strip()
    first = s.splitlines()[0]
    bare = s.strip("# *")
    if (PAGE_NUM_RE.match(s) or len(bare) <= 2 or (len(bare) < 25 and re.fullmatch(r"[\d\s.,;:]+", bare)) or set(bare) <= set("-—_ ")
            or (6 <= len(bare) < 90 and "\n" not in s and bare.upper() in headers and not TASK_HEAD_RE.match(s))):
        return "running_header"   # колонтитул, номер страницы или обрывок распознавания
    if s.startswith("|"):
        return "table"
    if s.startswith("$$") and s.endswith("$$"):
        return "formula"
    if FIG_RE.match(first.lstrip("# ")):
        return "figure"
    if TASK_HEAD_RE.match(first):
        return "heading"
    if s.startswith("#"):
        return "heading"
    if TABLE_CAPTION_RE.match(first):
        return "table"
    if in_tasks and NUMBERED_RE.match(first):
        return "task"
    return "paragraph"


def split_units(ch: Chapter) -> list[Unit]:
    headers = _running_headers(ch)
    units: list[Unit] = []
    in_tasks = False
    for p in sorted(ch.pages):
        n = 0
        for b in _blocks(ch.pages[p]):
            if not b.strip():
                continue
            kind = classify(b, headers, in_tasks)
            first = b.strip().splitlines()[0]
            if TASK_HEAD_RE.match(first):
                in_tasks = True
            elif kind == "heading":
                in_tasks = False
            elif kind == "paragraph" and in_tasks and not NUMBERED_RE.match(first):
                in_tasks = False
            # формула, приклеенная к абзацу, остаётся в абзаце; одиночная строка-формула — отдельно
            n += 1
            units.append(Unit(f"p{p:03d}.{n:02d}", p, kind, b.strip(), _excerpt(b)))
    # границы главы: куски соседних разделов на крайних страницах
    start, end = ch.meta.get("start_at"), ch.meta.get("end_at")
    i0 = next((i for i, u in enumerate(units) if start and u.text.lstrip("# *").startswith(start)), 0)
    i1 = next((i for i, u in enumerate(units) if end and i > i0 and u.text.lstrip("# *").startswith(end)), len(units))
    for i, u in enumerate(units):
        if i < i0 or i >= i1:
            u.in_scope = False
    return units


# kind единицы → kind строки покрытия (schemas/coverage.schema.json)
COVERAGE_KIND = {"paragraph": "paragraph", "heading": "heading", "formula": "formula", "table": "table",
                 "figure": "figure", "task": "task", "running_header": "heading"}


if __name__ == "__main__":
    cid = sys.argv[1] if len(sys.argv) > 1 else "latyshova_gl5_par17-20"
    us = split_units(load_chapter(cid))
    print(cid, len(us), "единиц:", dict(Counter(u.kind for u in us)))
    for u in us[:int(sys.argv[2]) if len(sys.argv) > 2 else 12]:
        print(f"  {u.unit_id} {u.kind:15s} {u.excerpt[:80]}")
