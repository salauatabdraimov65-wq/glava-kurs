"""Одна команда на главу: слой → иллюстрации → сценарий ролика → курс → ролик.

    uv run python -m glava.pipeline <chapter_id>                       # всё
    uv run python -m glava.pipeline test1 --md путь/глава.md --pages 12-30 --title "…" [--start-at "§ 9."] [--end-at "§ 11."] [--lang en] [--domain drilling]
    uv run python -m glava.pipeline <chapter_id> --from course           # после ручной правки факта в layer.json:
                                                                         # пересобрать курс и ролик, слой не трогать
    uv run python -m glava.pipeline <chapter_id> --video-node <node_id>  # ролик на назначенный организаторами узел
    uv run python -m glava.pipeline <chapter_id> --no-video              # без рендера ролика (быстро)

Этапы: layer, recall (полнота по идеям + добор), review (самопроверка + исправления), illustrate, scene, video, course (курс последним — в него встраивается собранный ролик). Время этапов — runs/<глава>/stage_times.json; в конце glava.metrics пересобирает metrics.csv (строка на главу и этап по шаблону организаторов).
Модули других треков (course, video) подключаются, если они есть в репозитории.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from glava.chapter import ROOT, chapters_config
from glava.llm import LEDGER, spent_usd

STAGES = ["layer", "recall", "review", "illustrate", "scene", "video", "course"]


def tokens_since(n_lines: int) -> tuple[int, int]:
    if not LEDGER.exists():
        return 0, 0
    lines = LEDGER.read_text(encoding="utf-8").splitlines()[n_lines:]
    rows = [json.loads(l) for l in lines if l.strip()]
    return sum(r["in"] for r in rows), sum(r["out"] for r in rows)


def ledger_len() -> int:
    return len(LEDGER.read_text(encoding="utf-8").splitlines()) if LEDGER.exists() else 0


def metric(chapter, stage, t0, usd0, led0, nodes=0, video_s=0):
    """Время этапа → runs/<глава>/stage_times.json (для доли ручного труда); деньги и токены glava.metrics берёт
    из журнала вызовов, где у каждого вызова есть глава и этап (GLAVA_CHAPTER / GLAVA_STAGE)."""
    p = ROOT / "runs" / chapter / "stage_times.json"
    times = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    times[stage] = round(times.get(stage, 0) + (time.time() - t0), 1)
    if p.parent.exists():
        p.write_text(json.dumps(times, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"── {stage}: {time.time() - t0:.0f} с, ${spent_usd() - usd0:.3f}", flush=True)


def has(module: str) -> bool:
    return (ROOT / module.replace(".", "/")).with_suffix(".py").exists()


def run(cmd: list[str], soft: bool = False) -> bool:
    print("$", " ".join(cmd), flush=True)
    r = subprocess.run([sys.executable, "-m", *cmd], cwd=ROOT)
    if r.returncode:
        if soft:   # ролик не собрался — курс всё равно собираем, шаг закрывают текст и схема
            print(f"⚠ этап не удался, продолжаю: {' '.join(cmd)}", flush=True)
            return False
        raise SystemExit(f"этап упал: {' '.join(cmd)}")
    return True


def existing_video(run_dir: Path):
    """Ролик у главы уже есть (пересборка после правки) — держимся за его узел, формат, шаблон и id,
    чтобы правка факта пересобирала тот же ролик, а не выбирала новый."""
    for sp in sorted((run_dir / "video").glob("*/spec.json")):
        spec = json.loads(sp.read_text(encoding="utf-8"))
        if spec.get("node_id") and spec.get("template"):
            return spec["node_id"], spec.get("format") or "short", spec["template"], spec.get("video_id") or sp.parent.name
    return None


def pick_video_node(run_dir: Path) -> tuple[str, str, str]:
    """Узел с наибольшей видеоёмкостью среди тех, у кого есть шаблон сцены."""
    from layer.scenes import TEMPLATES   # только шаблоны СЦЕН (интерактивы курса — не ролики)
    routes = json.loads((run_dir / "routing.json").read_text(encoding="utf-8"))["routes"]
    def scene_tpl(r):
        return r.get("template") if r.get("template") in TEMPLATES else r.get("scene_template")
    cand = [r for r in routes if scene_tpl(r) in TEMPLATES] or routes
    best = max(cand, key=lambda r: (bool(r.get("video_candidate") or r["format"] == "шортс"), sum(r["video_scores"].values())))
    fmt = "short" if best["format"] == "шортс" or best.get("video_candidate") else "long"
    tpl = scene_tpl(best) if scene_tpl(best) in TEMPLATES else "section_through_time"
    return best["node_id"], fmt, tpl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("chapter")
    ap.add_argument("--md"); ap.add_argument("--pages"); ap.add_argument("--title")
    ap.add_argument("--lang"); ap.add_argument("--domain")
    ap.add_argument("--start-at", help="начало первой единицы главы, напр. «§ 9.» — всё до неё на первой странице не входит в главу")
    ap.add_argument("--end-at", help="начало первой единицы следующего раздела, напр. «§ 11.»")
    ap.add_argument("--from", dest="start", choices=STAGES, default="layer")
    ap.add_argument("--only", choices=STAGES)
    ap.add_argument("--video-node"); ap.add_argument("--video-format", choices=["short", "long"])
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--no-review", action="store_true", help="без самопроверки слоя модулем review (быстрее)")
    a = ap.parse_args()

    # новая (тестовая) глава — дописываем в конфиг, чтобы все этапы её знали
    if a.md:
        cfg_path = ROOT / "config" / "chapters.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        entry = cfg["chapters"].get(a.chapter, {})
        entry.update({"split": entry.get("split", "test"), "md": str(Path(a.md).resolve()) if Path(a.md).exists() else a.md,
                      "lang": a.lang or entry.get("lang", "ru"), "domain": a.domain or entry.get("domain", "geology")})
        if a.pages:
            entry["pages"] = [[int(x) for x in r.split("-")] for r in a.pages.split(",")]
        if a.title:
            entry["title"] = a.title
        if a.start_at:
            entry["start_at"] = a.start_at
        if a.end_at:
            entry["end_at"] = a.end_at
        cfg["chapters"][a.chapter] = entry
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    if a.chapter not in chapters_config():
        sys.exit(f"Глава {a.chapter} не найдена в config/chapters.json — укажите --md и --pages")

    run_dir = ROOT / "runs" / a.chapter
    stages = [a.only] if a.only else STAGES[STAGES.index(a.start):]
    if a.no_video and "video" in stages:
        stages.remove("video")
    T0 = time.time()
    os.environ["GLAVA_CHAPTER"] = a.chapter          # каждый вызов модели в журнале помечается главой и этапом
    os.environ.setdefault("PYTHONUTF8", "1")          # Windows: этапы печатают «✅», «→» — вывод только в UTF-8
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for st in stages:
        os.environ["GLAVA_STAGE"] = st
        t0, usd0, led0 = time.time(), spent_usd(), ledger_len()
        if st == "layer":
            run(["layer.build", a.chapter])
        elif st == "recall":
            # полнота по идеям: независимый «ключ» идей по страницам → добор пропущенных с проверкой цитат
            run(["layer.idea_recall", a.chapter, "--fill"])
        elif st == "review":
            # проверка, встроенная в конвейер: наш слой → модуль проверки → исправления применяются к слою
            if has("review.self_check") and not a.no_review:
                run(["review.self_check", str(run_dir), "-o", str(run_dir / "review_self.json")])
                run(["layer.apply_review", str(run_dir)])
            else:
                print("(самопроверка пропущена)")
        elif st == "illustrate":
            run(["layer.illustrate", a.chapter])
            run(["layer.redraw", a.chapter])   # графики книги, построенные по формулам главы → перерисованы расчётом
        elif st == "scene":
            vid = None
            if a.video_node:
                node, fmt, tpl = a.video_node, a.video_format or "short", None
            elif existing_video(run_dir):
                node, fmt, tpl, vid = existing_video(run_dir)
            else:
                node, fmt, tpl = pick_video_node(run_dir)
                fmt = a.video_format or fmt
            cmd = ["layer.scenes", a.chapter, node, "--format", fmt]
            if tpl:
                cmd += ["--template", tpl]
            if vid:
                cmd += ["--video-id", vid]
            run(cmd, soft=True)
        elif st == "course":
            if has("course.build"):
                run(["layer.route", a.chapter])   # формат шага — по тому, что реально собрано (ролик есть / нет)
                run(["course.build", str(run_dir)])
            else:
                print("(course.build ещё нет в репозитории — пропуск)")
        elif st == "video":
            specs = sorted((run_dir / "video").glob("*/spec.json"))
            if has("video.build") and specs:
                for sp in specs:
                    aspect = json.loads(sp.read_text(encoding="utf-8")).get("aspect", "9:16")
                    run(["video.build", str(sp), "--layout", "16x9" if aspect == "16:9" else "9x16"], soft=True)
            else:
                print("(video.build ещё нет или нет spec.json — пропуск)")
        nodes = 0
        if (run_dir / "layer.json").exists():
            nodes = len(json.loads((run_dir / "layer.json").read_text(encoding="utf-8"))["nodes"])
        metric(a.chapter, st, t0, usd0, led0, nodes=nodes)
    os.environ.pop("GLAVA_STAGE", None)
    run(["glava.metrics"], soft=True)                 # metrics.csv по шаблону организаторов
    print(f"ГОТОВО {a.chapter}: {(time.time() - T0) / 60:.1f} мин, всего потрачено ${spent_usd():.2f} → {run_dir}")


if __name__ == "__main__":
    main()
