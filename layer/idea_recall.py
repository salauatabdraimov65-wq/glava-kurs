"""Полнота слоя по идеям (аналог критерия жюри «не меньше 90% идей ключа»).

    uv run python -m layer.idea_recall <chapter_id> [...]

1) Сильная модель НЕЗАВИСИМО (не видя слоя) выписывает ключевые идеи каждой страницы главы — как составитель ключа.
2) Дешёвая модель для каждой идеи ищет её среди утверждений слоя с этой и соседних страниц.
Итог: доля покрытых идей и список пропущенных → runs/<глава>/idea_recall.json.
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor

from glava.chapter import ROOT, load_chapter
from glava.llm import ask_json

SYS_IDEAS = """Ты составляешь ключ для проверки учебного курса по главе книги. Выпиши КЛЮЧЕВЫЕ ИДЕИ этой страницы:
то, что эксперт обязательно ждёт увидеть в курсе — механизмы, правила, условия применимости, важные числа и пороги, выводы, оговорки автора.
Не выписывай мелочи, ссылки на рисунки и повторы. Обычно 3–8 идей на страницу; если страница — продолжение таблицы или пустая, можно 0–2.
Верни JSON {"ideas": ["идея одной фразой", ...]}"""

SYS_MATCH = """Тебе дан список идей и список утверждений учебного слоя. Для каждой идеи реши, передана ли она в утверждениях
(полностью или по сути, с главным условием). Частичная передача без ключевого условия — не передана.
Верни JSON {"results": [{"idea": "…", "covered": true|false, "by": "id утверждения или null"}]}"""


def recall(chapter: str) -> dict:
    ch = load_chapter(chapter)
    layer = json.loads((ROOT / "runs" / chapter / "layer.json").read_text(encoding="utf-8"))
    claims = [c for n in layer["nodes"] for c in n["claims"]]
    pages = sorted(ch.pages)
    from layer.units import split_units
    us = split_units(ch)
    outside = {u.page_pdf for u in us if not u.in_scope}

    def per_page(p):
        # «ключ» только по тексту главы: на крайних страницах куски соседних разделов отрезаются
        text = ch.pages[p] if p not in outside else "\n\n".join(u.text for u in us if u.page_pdf == p and u.in_scope)
        if len(text.split()) < 40:
            return p, [], []
        ideas = ask_json(SYS_IDEAS, f"Страница {p}:\n{text}", tier="strong", tag=f"ideas:{chapter}", effort="low").get("ideas", [])
        if not ideas:
            return p, [], []
        near = [c for c in claims if abs(c["page_pdf"] - p) <= 1]
        user = "ИДЕИ:\n" + "\n".join(f"- {i}" for i in ideas) + "\n\nУТВЕРЖДЕНИЯ:\n" + "\n".join(f'{c["id"]}: {c["text"]}' for c in near)
        res = ask_json(SYS_MATCH, user, tier="bulk", tag=f"ideas-match:{chapter}").get("results", [])
        return p, ideas, res

    with ThreadPoolExecutor(8) as ex:
        out = list(ex.map(per_page, pages))
    total = sum(len(r) for _, _, r in out)
    covered = sum(1 for _, _, r in out for x in r if x.get("covered"))
    missed = [{"page": p, "idea": x.get("idea")} for p, _, r in out for x in r if not x.get("covered")]
    doc = {"chapter": chapter, "ideas": total, "covered": covered,
           "recall_pct": round(100 * covered / max(1, total), 1), "missed": missed}
    (ROOT / "runs" / chapter / "idea_recall.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{chapter}: идей {total}, покрыто {covered} = {doc['recall_pct']}%")
    return doc


def _main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    for c in args:
        recall(c)
        if "--fill" in sys.argv:
            fill(c)
            recall(c)   # повторный замер после добора


SYS_FILL = """Тебе дан текст страницы главы и ключевая идея, которой не хватает в учебном слое.
Если идея есть в тексте страницы, верни JSON-утверждение для слоя:
{"found": true, "text": "мысль своими словами по-русски, до 35 слов, с условиями автора; формулу передай словами и самой формулой",
 "quote": "ДОСЛОВНЫЙ непрерывный фрагмент текста страницы, 8–220 символов", "kind": "observation|inference|assumption",
 "source_kind": "text|formula|table"}
Если идеи в тексте нет или она из соседнего раздела — {"found": false}."""


def fill(chapter: str) -> int:
    """Добор пропущенных идей: дословная цитата → проверка кодом → утверждение в шаг той же единицы текста."""
    from glava.chapter import check_claim, norm
    from layer.units import split_units
    run = ROOT / "runs" / chapter
    rec = json.loads((run / "idea_recall.json").read_text(encoding="utf-8"))
    layer = json.loads((run / "layer.json").read_text(encoding="utf-8"))
    ch = load_chapter(chapter)
    units = [u for u in split_units(ch) if u.in_scope and u.kind != "running_header"]
    uorder = {u.unit_id: i for i, u in enumerate(split_units(ch))}
    owner = {c["unit_id"]: n for n in layer["nodes"] for c in n["claims"] if c.get("unit_id")}
    known = {norm(c["quote"]) for n in layer["nodes"] for c in n["claims"]}

    def run1(m):
        return m, ask_json(SYS_FILL, f"Страница {m['page']}:\n{ch.pages.get(m['page'], '')}\n\nИдея: {m['idea']}",
                           tier="bulk", tag=f"fill:{chapter}")
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(run1, rec["missed"]))
    added = 0
    for m, r in res:
        if not r.get("found") or not r.get("quote") or not r.get("text"):
            continue
        q = r["quote"].strip().rstrip("-‐‑ ").strip()
        if norm(q) in known or not check_claim(ch, q, m["page"])["ok"]:
            continue
        u = next((u for u in units if u.page_pdf == m["page"] and norm(q) in norm(u.text)), None)
        if not u:
            continue   # цитата вне единиц главы (соседний раздел) — не берём
        node = owner.get(u.unit_id)
        if node is None:   # ближайший шаг по порядку единиц
            cand = sorted(owner.items(), key=lambda kv: abs(uorder.get(kv[0], 0) - uorder[u.unit_id]))
            node = cand[0][1] if cand else layer["nodes"][0]
        k = max(int(c["id"].rsplit("#c", 1)[1]) for c in node["claims"]) + 1
        node["claims"].append({"id": f'{node["id"]}#c{k}', "text": r["text"], "kind": r.get("kind") if r.get("kind") in ("observation", "inference", "assumption") else "observation",
                               "page_pdf": m["page"], "page_print": None, "quote": q,
                               "source_kind": r.get("source_kind") if r.get("source_kind") in ("text", "formula", "table") else "text",
                               "by_model": False, "unit_id": u.unit_id, "text_by_model": True, "added_by": "idea_recall"})
        if m["page"] not in node["pages"]:
            node["pages"] = sorted(node["pages"] + [m["page"]])
        known.add(norm(q)); added += 1
    (run / "layer.json").write_text(json.dumps(layer, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{chapter}: добрано утверждений {added} из {len(rec['missed'])} пропущенных идей")
    return added


if __name__ == "__main__":
    _main()
