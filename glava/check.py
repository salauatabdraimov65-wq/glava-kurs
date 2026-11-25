"""Самопроверка результата по главе — все требования ТЗ, которые проверяются кодом.

    uv run python -m glava.check <chapter_id> [<chapter_id> ...]
    uv run python -m glava.check --all

Печатает ✅/❌ по каждому пункту; код возврата 1, если есть ❌.
"""
from __future__ import annotations

import json
import sys

from glava.chapter import ROOT, chapters_config, check_claim, load_chapter

SCHEMAS = [("layer", "node"), ("tasks", "task"), ("routing", "routing"), ("coverage", "coverage"),
           ("illustrations", "illustration")]


def check(cid: str) -> bool:
    import jsonschema
    run = ROOT / "runs" / cid
    ok_all = True

    def say(ok, text):
        nonlocal ok_all
        ok_all &= bool(ok)
        print(f"  {'✅' if ok else '❌'} {text}")

    print(f"== {cid}")
    if not (run / "layer.json").exists():
        say(False, "нет runs/<id>/layer.json — сначала glava.pipeline"); return False
    for f, s in SCHEMAS:
        p = run / f"{f}.json"
        if not p.exists():
            say(False, f"нет {f}.json"); continue
        try:
            jsonschema.validate(json.loads(p.read_text(encoding="utf-8")),
                                json.loads((ROOT / "schemas" / f"{s}.schema.json").read_text(encoding="utf-8")))
            say(True, f"{f}.json по схеме организаторов")
        except jsonschema.ValidationError as e:
            say(False, f"{f}.json: {e.message[:120]}")
    layer = json.loads((run / "layer.json").read_text(encoding="utf-8"))
    nodes = layer["nodes"]
    ch = load_chapter(cid)
    claims = [c for n in nodes for c in n["claims"]]
    bad = [c["id"] for c in claims if not check_claim(ch, c["quote"], c["page_pdf"])["ok"]]
    say(not bad, f"цитаты и страницы сверены с главой: {len(claims) - len(bad)}/{len(claims)}" + (f" — ошибки: {bad[:5]}" if bad else ""))
    cov = json.loads((run / "coverage.json").read_text(encoding="utf-8"))["rows"]
    unc = [r["unit_id"] for r in cov if (r["reason"] or "").startswith("НЕ ПОКРЫТО")]
    say(len(unc) <= max(2, len(cov) // 50), f"покрытие единиц главы: непокрыто {len(unc)} из {len(cov)}" + (f" {unc[:5]}" if unc else ""))
    say(all(n["claims"] for n in nodes), f"шагов {len(nodes)}, у каждого есть утверждения")
    if (run / "tasks.json").exists():
        tasks = json.loads((run / "tasks.json").read_text(encoding="utf-8"))["tasks"]
        types = {t["type"] for t in tasks}
        say(len(tasks) >= 15, f"заданий {len(tasks)} (нужно ≥15)")
        say(len(types) >= 4, f"типов заданий {len(types)}: {sorted(types)} (нужно ≥4 из 5)")
    if (run / "illustrations.json").exists():
        ill = json.loads((run / "illustrations.json").read_text(encoding="utf-8"))["illustrations"]
        steps = {i["step_id"] for i in ill if (run / i["file"]).exists()}
        miss = [n["id"] for n in nodes if n["id"] not in steps]
        say(not miss, f"иллюстрация на каждый шаг: {len(nodes) - len(miss)}/{len(nodes)}")
    course = run / "course" / "index.html"
    if course.exists():
        size = sum(p.stat().st_size for p in (run / "course").rglob("*") if p.is_file() and p.suffix != ".mp4") / 1e6
        say(size <= 30, f"курс собран: {course.relative_to(ROOT)}, {size:.1f} МБ без видео (≤30)")
    else:
        say(False, "курс не собран (runs/<id>/course/index.html)")
    vids = sorted((run / "video").glob("*/spec.json"))
    for sp in vids:
        spec = json.loads(sp.read_text(encoding="utf-8"))
        ids = {c["id"] for c in claims}
        badref = [b["claim_id"] for b in spec.get("beats", []) if b.get("claim_id") and b["claim_id"] not in ids]
        say(not badref, f"ролик {sp.parent.name}: фразы опираются на утверждения слоя" + (f" — чужие id {badref[:3]}" if badref else ""))
        mp4 = list(sp.parent.glob("*.mp4"))
        say(bool(mp4), f"ролик {sp.parent.name}: mp4 {'есть' if mp4 else 'не отрендерен'}")
    return ok_all


if __name__ == "__main__":
    # --all — главы обучения и контроля; репетиционные (split=rehearsal) проверяются, только если названы явно
    ids = ([c for c, m in chapters_config().items() if m.get("split") != "rehearsal"]
           if "--all" in sys.argv or len(sys.argv) == 1 else sys.argv[1:])
    res = [check(c) for c in ids]
    sys.exit(0 if all(res) else 1)
