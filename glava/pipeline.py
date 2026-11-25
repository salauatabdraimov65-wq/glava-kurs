"""Одна команда на главу: слой → иллюстрации → сценарий ролика → курс → ролик.

    uv run python -m glava.pipeline <chapter_id>                       # всё
    uv run python -m glava.pipeline test1 --md путь/глава.md --pages 12-30 --title "…" [--start-at "§ 9."] [--end-at "§ 11."] [--lang en] [--domain drilling]
    uv run python -m glava.pipeline <chapter_id> --from course           # после ручной правки факта в layer.json:
                                                                         # пересобрать курс и ролик, слой не трогать
    uv run python -m glava.pipeline <chapter_id> --video-node <node_id>  # ролик на назначенный организаторами узел
    uv run python -m glava.pipeline <chapter_id> --no-video              # без рендера ролика (быстро)

Этапы: layer, review (самопроверка + исправления), illustrate, scene, course, video. Каждый этап пишет строку в metrics.csv.
Модули других треков (course, video) подключаются, если они есть в репозитории.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

from glava.chapter import ROOT, chapters_config
from glava.llm import LEDGER, spent_usd

STAGES = ["layer", "review", "illustrate", "scene", "course", "video"]


def tokens_since(n_lines: int) -> tuple[int, int]:
    if not LEDGER.exists():
        return 0, 0
    lines = LEDGER.read_text(encoding="utf-8").splitlines()[n_lines:]
    rows = [json.loads(l) for l in lines if l.strip()]
    return sum(r["in"] for r in rows), sum(r["out"] for r in rows)


def ledger_len() -> int:
    return len(LEDGER.read_text(encoding="utf-8").splitlines()) if LEDGER.exists() else 0


def metric(chapter, stage, t0, usd0, led0, nodes=0, video_s=0):
    tin, tout = tokens_since(led0)
    path = ROOT / "metrics.csv"
    new = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["chapter", "stage", "nodes", "llm_tokens_in", "llm_tokens_out", "money_usd", "person_minutes",
                        "mentor_minutes", "video_seconds", "manual_share"])
        w.writerow([chapter, stage, nodes, tin, tout, round(spent_usd() - usd0, 4), 0, 0, video_s, 0])
    print(f"── {stage}: {time.time() - t0:.0f} с, ${spent_usd() - usd0:.3f}", flush=True)


def has(module: str) -> bool:
    return (ROOT / module.replace(".", "/")).with_suffix(".py").exists()


def run(cmd: list[str]):
    print("$", " ".join(cmd), flush=True)
    r = subprocess.run([sys.executable, "-m", *cmd], cwd=ROOT)
    if r.returncode:
        raise SystemExit(f"этап упал: {' '.join(cmd)}")


def pick_video_node(run_dir: Path) -> tuple[str, str, str]:
    """Узел с наибольшей видеоёмкостью среди тех, у кого есть шаблон сцены."""
    routes = json.loads((run_dir / "routing.json").read_text(encoding="utf-8"))["routes"]
    cand = [r for r in routes if r.get("template")] or routes
    best = max(cand, key=lambda r: sum(r["video_scores"].values()))
    fmt = "short" if best["format"] == "шортс" else "long"
    return best["node_id"], fmt, best.get("template") or "well_tool_curve"


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
    for st in stages:
        t0, usd0, led0 = time.time(), spent_usd(), ledger_len()
        if st == "layer":
            run(["layer.build", a.chapter])
        elif st == "review":
            # проверка, встроенная в конвейер: наш слой → модуль проверки → исправления применяются к слою
            if has("review.self_check") and not a.no_review:
                run(["review.self_check", str(run_dir), "-o", str(run_dir / "review_self.json")])
                run(["layer.apply_review", str(run_dir)])
            else:
                print("(самопроверка пропущена)")
        elif st == "illustrate":
            run(["layer.illustrate", a.chapter])
        elif st == "scene":
            if a.video_node:
                node, fmt, tpl = a.video_node, a.video_format or "short", None
            else:
                node, fmt, tpl = pick_video_node(run_dir)
                fmt = a.video_format or fmt
            cmd = ["layer.scenes", a.chapter, node, "--format", fmt]
            if tpl:
                cmd += ["--template", tpl]
            run(cmd)
        elif st == "course":
            if has("course.build"):
                run(["course.build", str(run_dir)])
            else:
                print("(course.build ещё нет в репозитории — пропуск)")
        elif st == "video":
            specs = sorted((run_dir / "video").glob("*/spec.json"))
            if has("video.build") and specs:
                for sp in specs:
                    aspect = json.loads(sp.read_text(encoding="utf-8")).get("aspect", "9:16")
                    run(["video.build", str(sp), "--layout", "16x9" if aspect == "16:9" else "9x16"])
            else:
                print("(video.build ещё нет или нет spec.json — пропуск)")
        nodes = 0
        if (run_dir / "layer.json").exists():
            nodes = len(json.loads((run_dir / "layer.json").read_text(encoding="utf-8"))["nodes"])
        metric(a.chapter, st, t0, usd0, led0, nodes=nodes)
    print(f"ГОТОВО {a.chapter}: {(time.time() - T0) / 60:.1f} мин, всего потрачено ${spent_usd():.2f} → {run_dir}")


if __name__ == "__main__":
    main()
