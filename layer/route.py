"""Маршрутизация: формат каждого шага — только из тех, что курс и станок реально делают.

    uv run python -m layer.route <chapter_id> [...]

Правила (по типу трудности, видеоёмкости и данным шага):
  • есть «решение на данных» (decision_on_data)        → практикум на данных (интерактив «признаки → правило → вывод» в курсе)
  • тип Ф или формулы в шаге                            → интерактив (калькулятор-ползунки по формуле, если шаблон есть; иначе текст)
  • типы М, В, И и видеоёмкость ≥ 6 из 10               → шортс (кандидат в ролик; шаблон сцены по типу)
  • тип Р                                                → карточки (узнавание: признак → объект)
  • остальное                                            → текст (+ иллюстрация-схема к каждому шагу)
Шортс-кандидат без отрендеренного ролика закрывается в курсе иллюстрацией-схемой; secondary_format это называет.
"""
from __future__ import annotations

import json
import re
import sys

from glava.chapter import ROOT

DIFF = json.loads((ROOT / "config" / "difficulty.json").read_text(encoding="utf-8"))
INTERACTIVE_TEMPLATES = {"Ф": "pressure_explorer"}   # интерактивы, которые есть в курсе


def route_node(n: dict) -> dict:
    vs = {k: int(max(0, min(2, (n.get("video_scores") or {}).get(k, 0) or 0)))
          for k in ("time", "invisible", "scale", "counterintuitive", "cause_effect")}
    score = sum(vs.values())
    prim, sec = n["difficulty"]["primary"], n["difficulty"].get("secondary")
    has_formula = any(c.get("source_kind") == "formula" or re.search(r"[=∂Δ√]", c.get("quote", "")) for c in n["claims"])
    if n.get("decision_on_data"):
        fmt, tpl, why2 = "практикум на данных", "decision_practicum", "есть правило автора «признаки → вывод»"
    elif prim == "Ф" or (has_formula and prim in ("Ф", "Н")):
        fmt, tpl, why2 = "интерактив", INTERACTIVE_TEMPLATES.get("Ф"), "формула/модель — понимание через параметры"
    elif prim in ("М", "В", "И") and score >= 6:
        fmt, tpl, why2 = "шортс", DIFF["scene_template"].get(prim), f"видеоёмкость {score}/10"
    elif prim == "Р":
        fmt, tpl, why2 = "карточки", None, "распознавание: признак → объект"
    else:
        fmt, tpl, why2 = "текст", None, "текст шага и схема"
    secondary = "карточки" if fmt != "карточки" else "текст"
    if fmt == "шортс":
        secondary = "текст"   # если ролик не собран — шаг закрывает текст со схемой
    return {"node_id": n["id"], "format": fmt, "secondary_format": secondary, "video_scores": vs,
            "hook_short": False, "template": tpl,
            "why": f"тип {prim}/{sec}; {why2}"}


def route(chapter: str) -> dict:
    run = ROOT / "runs" / chapter
    layer = json.loads((run / "layer.json").read_text(encoding="utf-8"))
    doc = {"chapter": chapter, "routes": [route_node(n) for n in layer["nodes"]]}
    (run / "routing.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    from collections import Counter
    print(chapter, dict(Counter(r["format"] for r in doc["routes"])))
    return doc


if __name__ == "__main__":
    for c in sys.argv[1:]:
        route(c)
