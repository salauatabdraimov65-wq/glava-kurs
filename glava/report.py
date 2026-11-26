"""Отчёт «что конвейер сделал сам» — одна страница для защиты: runs/REPORT.html.

    uv run python -m glava.report                 # главы теста (split=test), а если их нет — обучение и контроль
    uv run python -m glava.report <id> [<id> ...]  # выбранные главы
    uv run python -m glava.report --all            # все главы, включая репетиции

Все числа — из файлов результата: report.json, coverage.json, idea_recall.json, layer.json, stage_times.json,
metrics.csv, manual_edits.csv и проверки glava.check (схемы, цитаты, покрытие, задания, иллюстрации, курс, ролики).
"""
from __future__ import annotations

import contextlib
import csv
import html
import io
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from glava.chapter import ROOT, chapters_config

RUNS = ROOT / "runs"


def _j(p: Path, default=None):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def chapter_row(ch: str, meta: dict, money: dict, manual: dict) -> dict | None:
    run = RUNS / ch
    layer = _j(run / "layer.json")
    if not layer:
        return None
    from glava.check import check
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        check(ch)
    out = buf.getvalue()
    quotes = re.search(r"цитаты и страницы сверены с главой: (\d+)/(\d+)", out)
    nodes = layer["nodes"]
    rec = _j(run / "idea_recall.json", {})
    cov = _j(run / "report.json", {})
    times = _j(run / "stage_times.json", {})
    ill = _j(run / "illustrations.json", {"illustrations": []})["illustrations"]
    videos = []
    for sp in sorted(run.glob("video/*/spec.json")):
        spec = _j(sp, {})
        mp4 = next(iter(sorted(sp.parent.glob("video*.mp4"))), None)
        videos.append({"title": spec.get("title") or sp.parent.name, "aspect": spec.get("aspect", "9:16"),
                       "template": spec.get("template"), "file": mp4.relative_to(RUNS).as_posix() if mp4 else None})
    return {
        "id": ch, "title": meta.get("title") or layer.get("title") or ch, "split": meta.get("split", ""),
        "domain": meta.get("domain", "geology"), "lang": meta.get("lang", "ru"),
        "steps": len(nodes), "claims": sum(len(n["claims"]) for n in nodes),
        "tasks": len((_j(run / "tasks.json", {"tasks": []}))["tasks"]),
        "coverage": cov.get("coverage_pct"), "recall": rec.get("recall_pct"),
        "quotes": f"{quotes.group(1)}/{quotes.group(2)}" if quotes else "—",
        "fixes": sum(1 for n in nodes for c in n["claims"] if c.get("review")) + sum(len(n.get("review_texts", [])) for n in nodes),
        "redrawn": sum(1 for i in ill if i.get("origin") == "redrawn_from_book"),
        "minutes": round(sum(times.values()) / 60, 1) if "layer" in times else None,   # только полный прогон
        "usd": round(money.get(ch, 0.0), 2), "manual_min": round(manual.get(ch, 0.0), 1),
        "ok": out.count("✅"), "bad": out.count("❌"), "videos": videos,
        "course": f"{ch}/course/index.html" if (run / "course" / "index.html").exists() else None,
    }


def pick(cfg: dict, argv: list[str]) -> list[str]:
    ids = [a for a in argv if not a.startswith("--")]
    if ids:
        return ids
    if "--all" in argv:
        return list(cfg)
    test = [c for c, m in cfg.items() if m.get("split") == "test"]
    return test or [c for c, m in cfg.items() if m.get("split") != "rehearsal"]


def render(rows: list[dict]) -> str:
    e = html.escape
    tot_min = sum(r["minutes"] or 0 for r in rows)
    tot_usd = sum(r["usd"] for r in rows)
    tot_man = sum(r["manual_min"] for r in rows)
    cards = []
    for r in rows:
        vids = "".join(
            f'<li><a href="{e(v["file"])}">{e(v["title"])}</a> <span class="m">{e(v["aspect"])} · {e(v["template"] or "")}</span></li>'
            if v["file"] else f'<li>{e(v["title"])} <span class="m">ролик не собран</span></li>' for v in r["videos"])
        status = "ok" if r["bad"] == 0 else "warn"
        cards.append(f"""
<section class="card">
  <header><h2>{e(r['title'])}</h2>
    <p class="m">{e(r['id'])} · {e(r['split'])} · домен {e(r['domain'])} · язык {e(r['lang'])}</p></header>
  <dl class="grid">
    <div><dt>Время конвейера</dt><dd>{'—' if r['minutes'] is None else f"{r['minutes']} мин"}</dd></div>
    <div><dt>Модель</dt><dd>${r['usd']:.2f}</dd></div>
    <div><dt>Ручные правки</dt><dd>{r['manual_min']} чел.-мин</dd></div>
    <div><dt>Шаги / утверждения / задания</dt><dd>{r['steps']} / {r['claims']} / {r['tasks']}</dd></div>
    <div><dt>Цитаты сверены с книгой</dt><dd>{e(r['quotes'])}</dd></div>
    <div><dt>Покрытие текста главы</dt><dd>{'' if r['coverage'] is None else f"{r['coverage']}%"}</dd></div>
    <div><dt>Полнота по идеям</dt><dd>{'—' if r['recall'] is None else f"{r['recall']}%"}</dd></div>
    <div><dt>Исправлено самопроверкой</dt><dd>{r['fixes']}</dd></div>
    <div><dt>Графики по формулам</dt><dd>{r['redrawn']}</dd></div>
    <div><dt>Проверки ТЗ</dt><dd class="{status}">{r['ok']} ✓{'' if not r['bad'] else f" · {r['bad']} ✗"}</dd></div>
  </dl>
  <p>{f'<a class="btn" href="{e(r["course"])}">Открыть курс</a>' if r['course'] else 'курс не собран'}</p>
  {f'<ul class="v">{vids}</ul>' if vids else ''}
</section>""")
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Глава → курс: что конвейер сделал сам</title>
<style>
:root{{color-scheme:light dark;--bg:#f6f3ec;--fg:#1f1d1a;--mut:#6b655c;--card:#fffdf8;--line:#e4ddd0;--acc:#b5651d;--ok:#2e7d4f;--warn:#b3261e}}
@media (prefers-color-scheme:dark){{:root{{--bg:#16150f;--fg:#ece7dc;--mut:#a59e91;--card:#1f1d17;--line:#38342b;--acc:#e0a45e;--ok:#6fcf97;--warn:#ff8a80}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:17px/1.5 Georgia,"PT Serif",serif}}
main{{max-width:1180px;margin:0 auto;padding:32px 20px 60px}}h1{{font-size:clamp(26px,4vw,40px);margin:0 0 6px}}
.lead{{color:var(--mut);margin:0 0 22px}}.tot{{display:flex;flex-wrap:wrap;gap:14px;margin:0 0 26px}}
.tot div{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px 18px}}
.tot b{{display:block;font-size:28px;color:var(--acc)}}.card{{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:20px 22px;margin:0 0 18px}}
.card h2{{margin:0;font-size:22px}}.m{{color:var(--mut);font-size:14px;margin:2px 0 0}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px 18px;margin:16px 0}}
dt{{color:var(--mut);font-size:13px;font-family:system-ui,sans-serif}}dd{{margin:2px 0 0;font-size:20px;font-weight:600}}
.ok{{color:var(--ok)}}.warn{{color:var(--warn)}}a{{color:var(--acc)}}
.btn{{display:inline-block;padding:9px 16px;border:1px solid var(--acc);border-radius:999px;text-decoration:none;font-family:system-ui,sans-serif}}
.v{{margin:10px 0 0;padding-left:20px}}footer{{color:var(--mut);font-size:14px;margin-top:26px}}
</style></head><body><main>
<h1>Что конвейер сделал сам</h1>
<p class="lead">Одна команда на главу: <code>uv run python -m glava.pipeline &lt;глава&gt;</code>. Все числа — из файлов результата и автоматических проверок по требованиям ТЗ.</p>
<div class="tot"><div><b>{len(rows)}</b>глав</div><div><b>{tot_min:.0f} мин</b>машинного времени</div>
<div><b>${tot_usd:.2f}</b>на модели</div><div><b>{tot_man:.0f} чел.-мин</b>ручных правок</div></div>
{''.join(cards)}
<footer>Собрано командой <code>uv run python -m glava.report</code>. Принцип: модель предлагает — код проверяет (цитата и страница каждого утверждения сверены с текстом главы).</footer>
</main></body></html>"""


def main():
    cfg = chapters_config()
    money: dict = defaultdict(float)
    if (ROOT / "metrics.csv").exists():
        with (ROOT / "metrics.csv").open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                money[r["chapter"]] += float(r.get("money_usd") or 0)
    manual: dict = defaultdict(float)
    if (ROOT / "manual_edits.csv").exists():
        with (ROOT / "manual_edits.csv").open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    manual[r["chapter"]] += float(r.get("person_minutes") or 0)
                except ValueError:
                    pass
    rows = [r for c in pick(cfg, sys.argv[1:]) if (r := chapter_row(c, cfg.get(c, {}), money, manual))]
    out = RUNS / "REPORT.html"
    out.write_text(render(rows), encoding="utf-8")
    for r in rows:
        print(f"{r['id']:26} {r['minutes'] or '—':>5} мин  ${r['usd']:.2f}  шагов {r['steps']:3}  цитаты {r['quotes']:>9}  "
              f"полнота {r['recall'] or '—'}%  проверки {r['ok']}✓ {r['bad']}✗")
    print(f"→ {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
