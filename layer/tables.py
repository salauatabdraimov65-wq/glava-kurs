"""Таблицы книги целиком → поле book_tables у шага (ТЗ: «Таблицы книги — целиком, исправления OCR помечены»).

    uv run python -m layer.tables <chapter_id> [...]

Без модели: таблица берётся из md как есть (markdown), шаг — из таблицы покрытия (coverage.json).
Подпись «Таблица N…» над таблицей склеивается с телом. Таблица распознана автоматически: в поле ocr_note
об этом сказано, а явные артефакты распознавания (латиница вместо кириллицы в обозначениях, «нрзб», пустые
ячейки-обрывки) перечислены — курс показывает их пометкой.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter

from glava.chapter import ROOT, load_chapter
from layer.units import TABLE_CAPTION_RE, split_units

OCR_SUSPECT = [
    (re.compile(r"\[?нрзб\]?", re.I), "есть нераспознанные места [нрзб]"),
    (re.compile(r"\b[a-zA-Z]{2,}[а-яА-Я]|[а-яА-Я][a-zA-Z]{2,}\b"), "смешаны латиница и кириллица в слове"),
    (re.compile(r"\|\s*\|\s*\|"), "есть пустые ячейки (возможно, объединённые ячейки оригинала)"),
]


def ocr_notes(md: str) -> list[str]:
    return [msg for rx, msg in OCR_SUSPECT if rx.search(md)]


def attach(chapter: str) -> int:
    run = ROOT / "runs" / chapter
    layer_p = run / "layer.json"
    layer = json.loads(layer_p.read_text(encoding="utf-8"))
    cov = {r["unit_id"]: r for r in json.loads((run / "coverage.json").read_text(encoding="utf-8"))["rows"]}
    units = [u for u in split_units(load_chapter(chapter)) if u.in_scope]
    tables, cur = [], None
    for u in units:
        is_caption = u.kind in ("table", "paragraph") and TABLE_CAPTION_RE.match(u.text.split("\n")[0])
        is_body = u.kind == "table" and u.text.lstrip().startswith("|")
        if is_caption and not is_body:
            cur = {"caption": u.text.strip().strip("*"), "units": [u], "body": []}
            tables.append(cur)
        elif is_body:
            if cur is None or (cur["body"] and cur["units"][-1].page_pdf not in (u.page_pdf, u.page_pdf - 1)):
                cur = {"caption": None, "units": [], "body": []}
                tables.append(cur)
            cur["units"].append(u); cur["body"].append(u.text)
        elif u.kind not in ("running_header",) and cur and cur["body"]:
            cur = None
    nodes = {n["id"]: n for n in layer["nodes"]}
    for n in nodes.values():
        n["book_tables"] = []
    done = 0
    for tb in tables:
        if not tb["body"]:
            continue
        owners = Counter(nid for u in tb["units"] for nid in (cov.get(u.unit_id, {}).get("node_ids") or []))
        if not owners:
            continue
        order = {n["id"]: n["order"] for n in layer["nodes"]}
        nid = min(owners, key=lambda x: order.get(x, 10**6))   # шаг, где таблица появляется впервые
        md = "\n".join(tb["body"])
        pages = sorted({u.page_pdf for u in tb["units"]})
        num = re.search(r"(?:Таблица|Табл\.|Table)\s*(\d+)", tb["caption"] or "", re.I)
        nodes[nid]["book_tables"].append({
            "number": num.group(1) if num else None,
            "caption": tb["caption"], "pages": pages, "markdown": md,
            "source": f"таблица {num.group(1) if num else ''} книги, стр. {', '.join(map(str, pages))}".replace("  ", " "),
            "ocr_note": "Таблица перенесена из распознанного текста без изменений." + (
                " Возможные ошибки распознавания: " + "; ".join(ocr_notes(md)) + "." if ocr_notes(md) else ""),
            "by_model": False})
        done += 1
    layer_p.write_text(json.dumps(layer, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{chapter}: таблиц {done} → шаги {sorted({n['id'] for n in nodes.values() if n['book_tables']})}")
    return done


if __name__ == "__main__":
    for c in sys.argv[1:]:
        attach(c)
