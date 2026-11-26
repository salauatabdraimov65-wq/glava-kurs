"""Правка факта в учебном слое одной командой: заменить фрагмент текста и пересобрать зависящее.

    uv run python -m glava.set_text <chapter_id> "старое" "новое"            # слой → курс (секунды)
    uv run python -m glava.set_text <chapter_id> "старое" "новое" --full     # слой → схемы, графики, сценарий, ролик, курс

Ищет фрагмент во всех текстовых полях runs/<глава>/layer.json (утверждения, тексты шага, аналогии…),
заменяет, печатает шаг и страницу, где искать на странице курса, и запускает пересборку.
Цитаты автора (quote) не меняются: они — дословный текст книги.
Ручная правка результата на тесте записывается в журнал: glava.edit (см. TEST_DAY.md).
"""
from __future__ import annotations

import json
import subprocess
import sys

from glava.chapter import ROOT


def replace(obj, old: str, new: str, path: str, hits: list):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "quote":
                continue
            if isinstance(v, str) and old in v:
                obj[k] = v.replace(old, new)
                hits.append(f"{path}.{k}")
            else:
                replace(v, old, new, f"{path}.{k}", hits)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str) and old in v:
                obj[i] = v.replace(old, new)
                hits.append(f"{path}[{i}]")
            else:
                replace(v, old, new, f"{path}[{i}]", hits)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 3:
        sys.exit(__doc__)
    chapter, old, new = args
    p = ROOT / "runs" / chapter / "layer.json"
    layer = json.loads(p.read_text(encoding="utf-8"))
    where = []
    for i, n in enumerate(layer["nodes"], 1):
        hits: list[str] = []
        replace(n, old, new, n["id"], hits)
        if hits:
            where.append((i, n, hits))
    if not where:
        sys.exit(f"«{old}» в слое главы {chapter} не найдено (цитаты автора не правятся).")
    p.write_text(json.dumps(layer, ensure_ascii=False, indent=1), encoding="utf-8")
    for i, n, hits in where:
        print(f"шаг {i} «{n['title']}» (стр. {', '.join(map(str, n.get('pages') or []))}): {len(hits)} мест(о)", flush=True)
    stage = ["--from", "illustrate"] if "--full" in sys.argv else ["--only", "course"]
    r = subprocess.run([sys.executable, "-m", "glava.pipeline", chapter, *stage], cwd=ROOT)
    course = ROOT / "runs" / chapter / "course" / "index.html"
    print(f"\nГотово: «{old}» → «{new}». Обновите страницу курса: {course}")
    sys.exit(r.returncode)


if __name__ == "__main__":
    main()
