"""Применить замечания самопроверки к слою: проверка, встроенная в конвейер.

    uv run python -m layer.apply_review runs/<глава>

Берёт runs/<глава>/review_self.json (ответ модуля проверки на наш же слой, review.self_check):
  1) утверждение с замечанием получает исправленный текст (fix), исходный текст и причина — в поле review;
  2) узлы с исправленными утверждениями заново наполняются моделью (суть, механизм…), чтобы ошибка не осталась в тексте шага;
  3) задания, опиравшиеся на исправленные утверждения, убираются (если заданий остаётся ≥ 15).
Цитата и страница не меняются: исправление опирается на ту же страницу (замечание без страницы не применяется).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from glava.chapter import load_chapter
from layer.build import F, domain_pack, pass_c
from layer.units import split_units

MIN_TASKS = 15


def apply(run_dir: Path) -> dict:
    from review.self_check import build_package
    layer_p, rev_p = run_dir / "layer.json", run_dir / "review_self.json"
    layer = json.loads(layer_p.read_text(encoding="utf-8"))
    if not rev_p.exists():
        print("нет review_self.json — нечего применять"); return {"applied": 0}
    remarks = json.loads(rev_p.read_text(encoding="utf-8")).get("remarks", [])
    pkg = build_package(run_dir)
    by_item = {i["id"]: i for i in pkg["items"]}
    claims = {c["id"]: (n, c) for n in layer["nodes"] for c in n["claims"]}
    fixed, touched = [], set()
    for r in remarks:
        it = by_item.get(r.get("target"))
        fix = (r.get("fix") or "").strip()
        if not it or it.get("type") != "claim" or not fix or not r.get("page_pdf") or len(fix) > 500:
            continue
        node, c = claims.get(it.get("orig_id"), (None, None))
        if not c or c.get("review"):
            continue
        c["review"] = {"original_text": c["text"], "category": r.get("category"), "severity": r.get("severity"),
                       "problem": r.get("problem"), "page_pdf": r.get("page_pdf")}
        c["text"] = fix
        fixed.append(c["id"]); touched.add(node["id"])
    if not fixed:
        print("замечаний к утверждениям нет"); return {"applied": 0}

    # заново наполнить затронутые узлы
    ch = load_chapter(layer["chapter"])
    units = {u.unit_id: u for u in split_units(ch)}
    pack = domain_pack(layer.get("domain", "geology"))
    nodes = [n for n in layer["nodes"] if n["id"] in touched]
    cb = {c["id"]: {**c, "tmp_id": c["id"], "unit_id": c.get("unit_id") if c.get("unit_id") in units else next(iter(units))}
          for n in nodes for c in n["claims"]}
    work = [{"title": n["title"], "book_title": n.get("book_title"), "difficulty": n["difficulty"],
             "_claims": [c["id"] for c in n["claims"]]} for n in nodes]
    fills = pass_c(ch, work, cb, units, pack)
    for n, f in zip(nodes, fills):
        n["why_here"] = F(f.get("why_here"))
        for k in ("topic", "main", "mechanism", "practice", "nuances"):
            n["layers"][k] = F(f.get(k))
        n["layers"]["deeper"] = [F(x) for x in f.get("deeper") or [] if x]
        n["data_meaning"] = {"from_chapter": F(f.get("data_from_chapter")), "breaks": F(f.get("data_breaks")),
                             "hypotheses": [F(h, hyp=True) for h in f.get("hypotheses") or [] if h]}
        if f.get("it_analogy"):
            n["it_analogy"] = {"text": f["it_analogy"], "breaks_where": f.get("it_breaks_where") or "", "by_model": True}
        misc = f.get("misconception") or {}
        if misc.get("text"):
            n["misconception"] = {"ref": (n.get("misconception") or {}).get("ref"), "text": misc["text"], "by_model": True}
        for k in ("glossary", "procedure", "numbers"):
            n[k] = f.get(k) or []
        d = f.get("decision_on_data")
        n["decision_on_data"] = d if isinstance(d, dict) and d.get("rule") else None
        n["reviewed"] = True
    layer_p.write_text(json.dumps(layer, ensure_ascii=False, indent=1), encoding="utf-8")

    # задания на исправленных утверждениях
    tp = run_dir / "tasks.json"
    dropped = 0
    if tp.exists():
        td = json.loads(tp.read_text(encoding="utf-8"))
        keep = [t for t in td["tasks"] if not set(t["claim_ids"]) & set(fixed)]
        if len(keep) >= MIN_TASKS:
            dropped = len(td["tasks"]) - len(keep)
            td["tasks"] = keep
            tp.write_text(json.dumps(td, ensure_ascii=False, indent=1), encoding="utf-8")
    res = {"applied": len(fixed), "claims": fixed, "nodes_refilled": len(nodes), "tasks_dropped": dropped}
    print(json.dumps(res, ensure_ascii=False))
    return res


if __name__ == "__main__":
    apply(Path(sys.argv[1]))
