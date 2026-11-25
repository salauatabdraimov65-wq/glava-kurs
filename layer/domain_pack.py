"""Собрать доменный пакет из готовых слоёв: глоссарий RU/EN и ИТ-мосты.

    uv run python -m layer.domain_pack geology

Пакет лежит в domain/<домен>/ отдельно от кода. Для нового домена (бурение):
  1) mkdir domain/drilling, скопировать styleguide.json и поправить цвета/обозначения;
  2) misconceptions.json, physics_checklist.json, pronunciation.md — из заготовок организаторов или вручную;
  3) прогнать главу: uv run python -m glava.pipeline <id> --md … --pages … --domain drilling;
  4) этой командой собрать glossary.md и it_bridges.md из полученного слоя; время записать в domain/<домен>/README.md.
"""
from __future__ import annotations

import json
import sys

from glava.chapter import ROOT, chapters_config


def build(domain: str):
    terms, bridges = {}, []
    for cid, meta in chapters_config().items():
        if meta.get("domain", "geology") != domain:
            continue
        p = ROOT / "runs" / cid / "layer.json"
        if not p.exists():
            continue
        for n in json.loads(p.read_text(encoding="utf-8"))["nodes"]:
            for g in n.get("glossary") or []:
                t = (g.get("term") or "").strip()
                if t and t.lower() not in terms:
                    terms[t.lower()] = (t, g.get("definition") or "", g.get("en") or "", cid, n["pages"][0] if n["pages"] else "")
            a = n.get("it_analogy") or {}
            if a.get("text"):
                bridges.append((n["title"], a["text"], a.get("breaks_where", ""), cid))
    d = ROOT / "domain" / domain
    d.mkdir(parents=True, exist_ok=True)
    g = ["# Глоссарий RU/EN", "", "Собран из учебных слоёв глав (`layer.domain_pack`). Определения написаны моделью по тексту глав.", "",
         "| Термин | Определение | English | Глава, стр. |", "| --- | --- | --- | --- |"]
    g += [f"| {t} | {df} | {en} | {c}, {pg} |" for t, df, en, c, pg in sorted(terms.values(), key=lambda x: x[0].lower())]
    (d / "glossary.md").write_text("\n".join(g) + "\n", encoding="utf-8")
    b = ["# ИТ-мосты и где они ломаются", "", "Собраны из учебных слоёв. Каждая аналогия — с границей применимости.", ""]
    b += [f"- **{title}** ({c}): {txt} — *ломается:* {br}" for title, txt, br, c in bridges]
    (d / "it_bridges.md").write_text("\n".join(b) + "\n", encoding="utf-8")
    print(f"{domain}: терминов {len(terms)}, ИТ-мостов {len(bridges)} → {d}")


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "geology")
