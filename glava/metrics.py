"""metrics.csv по шаблону организаторов: строка на главу и этап (layer, routing, review, course, video).

    uv run python -m glava.metrics            # пересобрать metrics.csv из журнала расходов, runs/ и manual_edits.csv

Откуда числа:
  • токены и деньги — журнал вызовов модели cache/spend.jsonl (каждый реальный вызов, ответы из кэша бесплатны);
    глава и этап у вызова — поля chapter/stage (их ставит glava.pipeline через окружение GLAVA_CHAPTER/GLAVA_STAGE);
    у старых записей без них — по пометке вызова (tag), а неразмеченные относятся к главе по времени, только если
    в окне ±90 с были вызовы ровно одной главы; остальное — честная строка «_общие» (сравнение моделей, параллельные прогоны);
  • узлы — runs/<глава>/layer.json; секунды видео — длительность готовых mp4;
  • человеко-минуты — manual_edits.csv (этап — по изменённому файлу); минуты ментора — config/mentor_minutes.csv, если есть;
  • доля ручного труда = человеко-минуты правок / (человеко-минуты правок + минуты работы конвейера на этапе);
    минуты конвейера — runs/<глава>/stage_times.json (пишет glava.pipeline), иначе — по отметкам времени вызовов модели.
Пересборка идемпотентна; строки глав, которых нет в журнале этого компьютера, сохраняются (главы гоняют на двух машинах).
"""
from __future__ import annotations

import csv
import json
import re
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from glava.chapter import ROOT, chapters_config

LEDGER = ROOT / "cache" / "spend.jsonl"
OUT = ROOT / "metrics.csv"
COLUMNS = ["chapter", "stage", "nodes", "llm_tokens_in", "llm_tokens_out", "money_usd", "person_minutes",
           "mentor_minutes", "video_seconds", "manual_share"]
STAGES = ["layer", "routing", "review", "course", "video"]
SHARED = "_общие"

# этап конвейера (glava.pipeline) → этап шаблона организаторов
PIPELINE_TO_ORG = {"layer": "layer", "recall": "layer", "review": "review", "illustrate": "course",
                   "scene": "video", "video": "video", "course": "course"}
# пометка вызова (tag) → этап, для записей без поля stage
TAG_TO_STAGE = {"A": "layer", "A-fix": "layer", "B": "layer", "C": "layer", "D": "layer", "ideas": "layer",
                "ideas-match": "layer", "fill": "layer", "classify": "layer", "ab-extract": "layer",
                "review": "review", "reverse": "review", "novice": "review", "apply-review-text": "review",
                "apply-review-analogy": "review", "bench": "review",
                "redraw": "course", "scene": "video"}


def _ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")


def _ledger() -> list[dict]:
    if not LEDGER.exists():
        return []
    return [json.loads(l) for l in LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]


def attribute(entries: list[dict], chapters: set[str]) -> list[tuple[str, str, dict]]:
    """(глава, этап, запись) для каждой записи журнала."""
    out = []
    for e in entries:
        tag = e.get("tag") or ""
        prefix = tag.split(":")[0]
        ch = e.get("chapter")
        if not ch:
            tail = tag.rsplit(":", 1)[-1] if ":" in tag else ""
            ch = tail if tail in chapters else None
        st = PIPELINE_TO_ORG.get(e.get("stage") or "", None) or TAG_TO_STAGE.get(prefix)
        if prefix.startswith("smoke"):
            ch, st = SHARED, "layer"
        out.append([ch, st or "layer", e])
    # неразмеченные по главе: по соседним по времени вызовам, если в окне ±90 с глава ровно одна
    import bisect
    tagged = sorted((_ts(e["t"]).timestamp(), ch) for ch, _, e in out if ch and ch != SHARED)
    times = [t for t, _ in tagged]
    for row in out:
        if row[0]:
            continue
        t = _ts(row[2]["t"]).timestamp()
        i, j = bisect.bisect_left(times, t - 90), bisect.bisect_right(times, t + 90)
        near = {c for _, c in tagged[i:j]}
        row[0] = near.pop() if len(near) == 1 else SHARED
    return [tuple(r) for r in out]


def video_seconds(run: Path) -> float:
    try:
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return 0.0
    total = 0.0
    for mp4 in sorted(run.glob("video/*/video*.mp4")):
        r = subprocess.run([ff, "-i", str(mp4)], capture_output=True, text=True)
        m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
        if m:
            total += int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return round(total, 1)


def manual_minutes() -> dict[tuple[str, str], float]:
    p = ROOT / "manual_edits.csv"
    out: dict[tuple[str, str], float] = defaultdict(float)
    if not p.exists():
        return out
    with p.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            fpath = r.get("file", "")
            st = "video" if "/video/" in fpath else "course" if "/course/" in fpath or "illustration" in fpath \
                else "routing" if "routing" in fpath else "review" if "review" in fpath else "layer"
            try:
                out[(r["chapter"], st)] += float(r.get("person_minutes") or 0)
            except ValueError:
                pass
    return out


def mentor_minutes() -> dict[tuple[str, str], float]:
    p = ROOT / "config" / "mentor_minutes.csv"   # chapter,stage,minutes — заполняет человек, если ментор помогал
    out: dict[tuple[str, str], float] = defaultdict(float)
    if p.exists():
        with p.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                out[(r["chapter"], r["stage"])] += float(r.get("minutes") or 0)
    return out


def build() -> list[dict]:
    cfg = chapters_config()
    chapters = set(cfg)
    rows: dict[tuple[str, str], dict] = {}

    def row(ch, st):
        return rows.setdefault((ch, st), {"chapter": ch, "stage": st, "nodes": 0, "llm_tokens_in": 0,
                                           "llm_tokens_out": 0, "money_usd": 0.0, "person_minutes": 0,
                                           "mentor_minutes": 0, "video_seconds": 0, "manual_share": 0.0,
                                           "_t": []})

    for ch, st, e in attribute(_ledger(), chapters):
        r = row(ch, st)
        r["llm_tokens_in"] += e.get("in", 0)
        r["llm_tokens_out"] += e.get("out", 0)
        r["money_usd"] += e.get("usd", 0.0)
        r["_t"].append(_ts(e["t"]))

    man, ment = manual_minutes(), mentor_minutes()
    for ch in cfg:
        run = ROOT / "runs" / ch
        if not (run / "layer.json").exists():
            continue
        n = len(json.loads((run / "layer.json").read_text(encoding="utf-8"))["nodes"])
        times = {}
        if (run / "stage_times.json").exists():
            for st_p, sec in json.loads((run / "stage_times.json").read_text(encoding="utf-8")).items():
                st = PIPELINE_TO_ORG.get(st_p, st_p)
                times[st] = times.get(st, 0) + sec / 60
        for st in STAGES:
            r = row(ch, st)
            r["nodes"] = n
            if st == "video":
                r["video_seconds"] = video_seconds(run)
                r["nodes"] = len(list(run.glob("video/*/spec.json")))
            r["person_minutes"] = round(man.get((ch, st), 0), 1)
            r["mentor_minutes"] = round(ment.get((ch, st), 0), 1)
            machine = times.get(st)
            if machine is None and len(r["_t"]) >= 2:
                machine = (max(r["_t"]) - min(r["_t"])).total_seconds() / 60
            machine = max(machine or 0, 0.5)
            pm = r["person_minutes"] + r["mentor_minutes"]
            r["manual_share"] = round(pm / (pm + machine), 3) if pm else 0.0

    order = {c: i for i, c in enumerate(cfg)}
    out = sorted(rows.values(), key=lambda r: (order.get(r["chapter"], 10**6), STAGES.index(r["stage"])
                                               if r["stage"] in STAGES else 99))
    for r in out:
        r.pop("_t", None)
        r["money_usd"] = round(r["money_usd"], 4)
    return out


def write(rows: list[dict]) -> None:
    keep = []
    if OUT.exists():   # строки глав с другого компьютера (их нет в локальном журнале) сохраняем
        mine = {r["chapter"] for r in rows}
        with OUT.open(encoding="utf-8") as f:
            keep = [r for r in csv.DictReader(f) if r.get("chapter") and r["chapter"] not in mine]
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in keep + rows:
            w.writerow({k: r.get(k, 0) for k in COLUMNS})


def summary(rows: list[dict]) -> str:
    by = defaultdict(lambda: [0.0, 0, 0.0])
    for r in rows:
        b = by[r["chapter"]]
        b[0] += float(r["money_usd"])
        b[1] = max(b[1], int(r["nodes"]) if r["stage"] != "video" else b[1])
        b[2] += float(r["video_seconds"])
    lines = [f"{'глава':28} {'$':>7} {'узлов':>6} {'$ / узел':>9} {'видео, с':>9}"]
    for ch, (usd, n, vs) in by.items():
        lines.append(f"{ch:28} {usd:7.2f} {n:6d} {usd / n if n else 0:9.3f} {vs:9.0f}")
    return "\n".join(lines)


if __name__ == "__main__":
    rows = build()
    write(rows)
    print(summary(rows))
    print(f"→ {OUT.relative_to(ROOT)}: {len(rows)} строк")
