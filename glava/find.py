"""Найти шаг курса по теме ролика (темы объявляют на тесте) — без модели, мгновенно.

    uv run python -m glava.find <chapter_id> "тема ролика"
    uv run python -m glava.find --test "тема ролика"        # по всем главам теста сразу

Ищет слова темы в названии шага, разделе книги и утверждениях; показывает лучшие шаги с типом трудности,
шаблоном сцены и готовой командой для ролика.
"""
from __future__ import annotations

import json
import re
import sys

from glava.chapter import ROOT

DIFF = json.loads((ROOT / "config" / "difficulty.json").read_text(encoding="utf-8"))
LONG_TYPES = {"Ф", "Н"}   # разбор 16:9 — решение на данных и формулы; остальное — шортс 9:16


def stems(text: str) -> set[str]:
    """Основы слов (первые 5 букв) — грубо, но не зависит от падежа."""
    return {w[:5] for w in re.findall(r"[a-zA-Zа-яА-ЯёЁ]{4,}", text.lower())}


def find(chapter: str, topic: str, top: int = 5) -> list[dict]:
    layer = json.loads((ROOT / "runs" / chapter / "layer.json").read_text(encoding="utf-8"))
    q = stems(topic)
    out = []
    for n in layer["nodes"]:
        title = f"{n.get('title', '')} {n.get('book_title', '')}"
        body = " ".join(c["text"] for c in n["claims"])
        st, sb = stems(title), stems(body)
        score = 3 * len(q & st) + len(q & sb)
        if score:
            prim = n["difficulty"]["primary"]
            out.append({"node_id": n["id"], "title": n["title"], "type": prim, "score": score,
                        "template": DIFF["scene_template"].get(prim),
                        "format": "long" if prim in LONG_TYPES else "short", "pages": n.get("pages")})
    return sorted(out, key=lambda r: -r["score"])[:top]


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    if sys.argv[1] == "--test":   # тема может относиться к любой главе теста — ищем по всем (split=test)
        from glava.chapter import chapters_config
        chs = [c for c, m in chapters_config().items() if m.get("split") == "test" and (ROOT / "runs" / c / "layer.json").exists()]
    else:
        chs = [sys.argv[1]]
    if not chs:
        sys.exit("Глав теста (split=test) с готовым слоем пока нет — сначала прогоните главы.")
    topic = " ".join(sys.argv[2:])
    res = sorted(((c, r) for c in chs for r in find(c, topic)), key=lambda cr: -cr[1]["score"])[:6]
    if not res:
        print("Совпадений нет — попробуйте другие слова темы (термин из главы).")
    for i, (ch, r) in enumerate(res, 1):
        print(f"{i}. [{ch}] {r['node_id']}  «{r['title']}»  тип {r['type']}, стр. {r['pages']}, совпадение {r['score']}")
        if i == 1:
            print(f"   ролик: uv run python -m glava.pipeline {ch} --from scene --video-node {r['node_id']} "
                  f"--video-format {r['format']}")
