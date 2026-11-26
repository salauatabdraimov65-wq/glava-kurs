"""Черновик доменного пакета для нового домена — из самой главы, за 1–2 минуты (глава-сдвиг на тесте).

    uv run python -m layer.domain_new <домен> <chapter_id> [--base geology] [--force]

Глава должна быть в config/chapters.json (glava.pipeline с --md/--pages записывает её сам и вызывает этот
модуль автоматически, если папки domain/<домен>/ нет).

Модель предлагает — код проверяет:
  • оформление (styleguide.json), портрет зрителя (audience.md) и общие пункты чек-листа физики — из базового пакета;
  • обозначения, заблуждения, произношение, масштабы и предметные пункты чек-листа — сильная модель по тексту главы;
  • код оставляет обозначение, только если оно встречается в тексте главы, и масштаб — только если его числа
    стоят в главе; всё, написанное моделью, помечено by_model; время сборки записывается в README.md пакета.
После прогона главы: uv run python -m layer.domain_pack <домен> (глоссарий и мосты в ИТ из слоя).
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import time

from glava.chapter import ROOT, load_chapter, norm
from glava.llm import ask_json

SYS = """Ты методист и инженер-нефтяник. По тексту главы учебника собери черновик ДОМЕННОГО ПАКЕТА для учебного курса
по этой области нефтегаза. Бери только то, что есть в тексте главы или прямо следует из него; не выдумывай.
Верни JSON:
{"domain_title": "название области по-русски, 1–4 слова",
 "notation": [{"symbol": "обозначение как в тексте", "meaning": "смысл и единицы"}],          // 5–20 обозначений и аббревиатур из главы
 "misconceptions": [{"id": "misc.<латиница_через_подчёркивание>", "text": "типичное заблуждение новичка, которое глава опровергает или уточняет"}],  // 4–10
 "pronunciation": [{"written": "аббревиатура", "read": "как читать вслух", "full": "расшифровка"}],  // аббревиатуры из главы
 "stress": ["термин с ударением, знак ударения — акут после ударной гласной, например «пáкер»"],          // 0–15
 "scales": [{"quantity": "величина", "range": "диапазон с единицами, ЧИСЛА — как в главе"}],          // 0–10, только из главы
 "checklist": [{"group": "раздел", "item": "что проверить в кадре ролика или схеме этой области (физика, порядок операций, направления)"}]  // 4–10
}"""


def _numbers(s: str) -> list[str]:
    return re.findall(r"\d+(?:[.,]\d+)?", s)


def build(domain: str, chapter: str, base: str = "geology", force: bool = False) -> dict:
    t0 = time.time()
    d = ROOT / "domain" / domain
    if d.exists() and not force:
        print(f"domain/{domain}/ уже есть — не трогаю (--force, чтобы пересобрать)")
        return {}
    b = ROOT / "domain" / base
    d.mkdir(parents=True, exist_ok=True)
    for f in ("styleguide.json", "audience.md"):
        if (b / f).exists():
            shutil.copy(b / f, d / f)
    ch = load_chapter(chapter)
    text = "\n\n".join(ch.pages[p] for p in sorted(ch.pages))
    plain = norm(text)
    r = ask_json(SYS, f"ГЛАВА ({chapter}):\n{text[:60000]}", tier="strong", tag=f"domain_new:{domain}", effort="low")

    notation = [x for x in r.get("notation", []) if x.get("symbol") and norm(x["symbol"]) in plain]
    scales = [x for x in r.get("scales", []) if x.get("range") and _numbers(x["range"])
              and all(n in text or n.replace(",", ".") in text or n.replace(".", ",") in text for n in _numbers(x["range"]))]
    misc = [{"id": x["id"], "text": x["text"], "by_model": True} for x in r.get("misconceptions", [])
            if x.get("id") and x.get("text")]
    base_check = json.loads((b / "physics_checklist.json").read_text(encoding="utf-8")) if (b / "physics_checklist.json").exists() else []
    general = [c for c in base_check if any(k in (c.get("group") or "").lower() for k in ("процесс", "причин", "кадр", "масштаб"))]
    check = general + [{"id": f"pc.{domain}.{i}", "group": c.get("group") or domain, "item": c["item"], "by_model": True}
                       for i, c in enumerate(r.get("checklist", []), 1) if c.get("item")]
    title = r.get("domain_title") or domain

    (d / "misconceptions.json").write_text(json.dumps(misc, ensure_ascii=False, indent=1), encoding="utf-8")
    (d / "physics_checklist.json").write_text(json.dumps(check, ensure_ascii=False, indent=1), encoding="utf-8")
    (d / "notation.md").write_text(
        f"# Обозначения: {title} (доменный пакет: {domain})\n\nПодаётся в промпт при наполнении узлов. Черновик собран "
        f"моделью по главе `{chapter}`; оставлены только обозначения, которые есть в тексте главы.\n\n"
        "| Обозначение | Смысл |\n| --- | --- |\n" + "".join(f"| {x['symbol']} | {x.get('meaning', '')} |\n" for x in notation),
        encoding="utf-8")
    (d / "pronunciation.md").write_text(
        f"# Словарь ударений и произношений — {title}\n\n| Пишется | Читается | Расшифровка |\n| --- | --- | --- |\n"
        + "".join(f"| {x['written']} | {x.get('read', '')} | {x.get('full', '')} |\n" for x in r.get("pronunciation", [])
                  if x.get("written"))
        + ("\nУдарения: " + ", ".join(r.get("stress") or []) + ".\n" if r.get("stress") else ""), encoding="utf-8")
    (d / "scales.md").write_text(
        f"# Типичные масштабы (доменный пакет: {domain})\n\nТолько величины, числа которых есть в главе `{chapter}`.\n\n"
        "| Величина | Диапазон |\n| --- | --- |\n" + "".join(f"| {x['quantity']} | {x['range']} |\n" for x in scales),
        encoding="utf-8")
    sec = time.time() - t0
    (d / "README.md").write_text(
        f"# Доменный пакет «{title}» (`{domain}`)\n\n"
        f"Черновик собран автоматически: `uv run python -m layer.domain_new {domain} {chapter}` "
        f"({time.strftime('%Y-%m-%d %H:%M')}, {sec:.0f} с машинного времени).\n\n"
        f"| Файл | Откуда |\n| --- | --- |\n"
        f"| styleguide.json, audience.md | копия пакета `{base}` (оформление, портрет зрителя) |\n"
        f"| physics_checklist.json | общие пункты `{base}` + {len(check) - len(general)} предметных по главе (by_model) |\n"
        f"| misconceptions.json | {len(misc)} заблуждений по главе (by_model) |\n"
        f"| notation.md | {len(notation)} обозначений — только встречающиеся в тексте главы |\n"
        f"| pronunciation.md, scales.md | по главе; масштабы — только с числами из главы |\n"
        f"| glossary.md, it_bridges.md | после прогона главы: `uv run python -m layer.domain_pack {domain}` |\n\n"
        f"## Время\n- Сборка черновика: {sec / 60:.1f} мин машинного времени, 0 человеко-минут.\n"
        f"- Проверка и правка человеком: ___ мин (записать).\n", encoding="utf-8")
    print(f"domain/{domain}/: «{title}» — обозначений {len(notation)}, заблуждений {len(misc)}, масштабов {len(scales)}, "
          f"пунктов чек-листа {len(check)}; {sec:.0f} с")
    return {"notation": len(notation), "misconceptions": len(misc), "seconds": sec}


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        sys.exit(__doc__)
    base = sys.argv[sys.argv.index("--base") + 1] if "--base" in sys.argv else "geology"
    if "--base" in sys.argv:
        args = [a for a in args if a != base]
    build(args[0], args[1], base=base, force="--force" in sys.argv)
