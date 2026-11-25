"""uv run python -m review.run <package.json> [--chapter-md X.md] -o answer.json [--no-llm] [--workers N] [--max N] [--deadline-s S]

Главное правило: ответ создаётся ВСЕГДА и проходит review_output.schema.json. Сбой API, исчерпанный бюджет, битые поля пакета,
отсутствующая глава или любое неожиданное исключение дают ответ по проверкам кодом (или пустой список замечаний), но не падение.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

from glava.chapter import load_chapter

from . import llm_check
from .checks import best_page, check_frames, check_items
from .screen import aggregate, rank

MAX_REMARKS = 20
DEADLINE_S = 540  # тест — один прогон; ≤10 мин на всё, оставляем запас
SEVERITIES = ("critical", "significant", "minor")
TYPES = ("claim", "analogy", "script_line", "scene_fact")
PARTIAL: list[dict] = []  # кандидаты, накопленные к моменту возможного сбоя


# ---------- вход ----------

def _s(x, default=""):
    return x if isinstance(x, str) else default


def normalize(pkg) -> dict:
    """Приводит пакет к виду, с которым не падают проверки: пустые и отсутствующие поля, чужие типы, дубли id."""
    pkg = pkg if isinstance(pkg, dict) else {}
    items, seen = [], set()
    for it in pkg.get("items") if isinstance(pkg.get("items"), list) else []:
        if not isinstance(it, dict) or not _s(it.get("id")).strip() or it["id"] in seen:
            continue
        seen.add(it["id"])
        n = dict(it)
        n["type"] = it.get("type") if it.get("type") in TYPES else "claim"
        n["text"] = _s(it.get("text"))
        n["quote"] = it.get("quote") if isinstance(it.get("quote"), str) and it["quote"].strip() else None
        n["page_pdf"] = llm_check.to_int(it.get("page_pdf"))
        for k in ("breaks_where", "claim_ref", "on_screen", "node_id", "video_id", "spec_path"):
            n[k] = it.get(k) if isinstance(it.get(k), str) else None
        if not isinstance(n.get("pages"), list):
            n.pop("pages", None)
        items.append(n)
    frames = []
    for f in pkg.get("frames") if isinstance(pkg.get("frames"), list) else []:
        if isinstance(f, dict) and _s(f.get("id")).strip():
            g = dict(f)
            g.update(video_id=_s(f.get("video_id")), file=_s(f.get("file")), shows=_s(f.get("shows")), label=_s(f.get("label"), "симуляция"),
                     frame_class=_s(f.get("frame_class"), "B"))
            g["t"] = f.get("t") if isinstance(f.get("t"), (int, float)) else 0
            frames.append(g)
    scenes = [s for s in (pkg.get("scenes") if isinstance(pkg.get("scenes"), list) else []) if isinstance(s, dict) and isinstance(s.get("spec"), dict)]
    return {"package_id": _s(pkg.get("package_id"), "unknown"), "chapter": _s(pkg.get("chapter")), "chapter_file": _s(pkg.get("chapter_file")),
            "language": _s(pkg.get("language"), "ru"), "items": items, "scenes": scenes, "frames": frames}


def load_ch(pkg: dict, pkg_path: Path, chapter_md: str | None):
    if chapter_md:
        return load_chapter("test", md_path=Path(chapter_md).resolve())
    local = pkg_path.parent / (pkg.get("chapter_file") or "_нет_")
    if pkg.get("chapter_file") and local.is_file():
        return load_chapter("test", md_path=local.resolve())
    return load_chapter(pkg["chapter"])


# ---------- выход ----------

def sanitize(c: dict, ch) -> dict | None:
    """Замечание -> запись, гарантированно подходящая под схему; None — если цели нет."""
    tgt = _s(c.get("target")).strip()
    if not tgt:
        return None
    cat = c.get("category") if c.get("category") in llm_check.CATS else "other"
    sev = c.get("severity") if c.get("severity") in SEVERITIES else "significant"
    problem, fix = _s(c.get("problem")).strip(), _s(c.get("fix")).strip()
    if not fix:
        return None
    page = llm_check.to_int(c.get("page_pdf"))
    if page is None and ch is not None:
        try:
            page = best_page(ch, fix + " " + problem)[0]
        except Exception:
            page = None
    return {"target": tgt, "category": cat, "severity": sev, "problem": problem or "расхождение с текстом главы", "fix": fix, "page_pdf": page,
            "quote": c.get("quote") if isinstance(c.get("quote"), str) else None}


def write_answer(out: Path, package_id: str, remarks: list[dict]):
    """Пишет ответ; каждая запись проверяется схемой по отдельности, негодные отбрасываются."""
    schema = json.loads((Path(__file__).resolve().parent.parent / "schemas" / "review_output.schema.json").read_text(encoding="utf-8"))
    import jsonschema
    good = []
    for r in remarks:
        try:
            jsonschema.validate({"package_id": package_id, "remarks": [r]}, schema)
            good.append(r)
        except Exception:
            pass
    answer = {"package_id": package_id or "unknown", "remarks": good}
    jsonschema.validate(answer, schema)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(answer, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, out)
    return answer


def code_only(cands: list[dict]) -> list[dict]:
    out = []
    for c in cands:
        if c.get("confidence", 0) >= 0.5 and c.get("fix"):
            c = dict(c)
            c["signals"] = ["code"]
            out.append(c)
    return out


# ---------- основной ход ----------

def _run(a, cands_sink: list) -> int:
    start = time.time()
    pkg_path = Path(a.package).resolve()
    out = Path(a.out).resolve()
    pkg = normalize(json.loads(pkg_path.read_text(encoding="utf-8")))
    try:
        ch = load_ch(pkg, pkg_path, a.chapter_md)
    except Exception as e:
        print(f"! глава не загружена ({type(e).__name__}: {e}); ответ без замечаний")
        write_answer(out, pkg["package_id"], [])
        return 0
    base = pkg_path.parent
    llm_check.reset()
    llm_check.DEADLINE = start + a.deadline_s
    cands = check_items(pkg, ch)
    try:
        cands += check_frames(pkg, ch)
    except Exception as e:
        print(f"! проверки кадров кодом: {e}")
    cands_sink.extend(cands)
    dropped = []

    use_llm = not a.no_llm
    if use_llm:
        from .llm_check import check_frames_with_model, check_with_model, reverse_check, reverse_check_frame, STATS
        # каскад: strong спрашиваем только про цели, которые уже пометили код или bulk (иначе второго сигнала всё равно не будет)
        cands += check_with_model(pkg, ch, a.limit, tier="bulk", workers=a.workers)
        flagged = {c["target"] for c in cands if c["confidence"] >= 0.4 or c["source"] == "llm:bulk"}
        cands += check_with_model(pkg, ch, a.limit, tier="strong", only=flagged, workers=a.workers)
        for tier in ("bulk", "strong"):
            cands += check_frames_with_model(pkg, ch, base, tier=tier, workers=a.workers)
        cands_sink[:] = cands
        avail = 1 + sum(1 for t in ("bulk", "strong") if STATS[t]["ok"] > 0)  # код + рабочие уровни модели
        if avail == 1:
            print("! модель недоступна (ошибка API/бюджет/ключ): ответ по проверкам кодом")
            use_llm = False
    if use_llm:
        survivors = aggregate(cands, ch, min_signals=min(2, avail))
        if not a.no_reverse and avail >= 2:
            frames = {f["id"]: f for f in pkg["frames"]}
            ranked = rank(survivors)

            def rev(c):
                if c["target"] in frames:
                    return reverse_check_frame(pkg, ch, frames[c["target"]], base), "кадр верен по обратной проверке"
                supported, q, pg = reverse_check(pkg, ch, c["target"], base)
                return supported, f"в главе есть дословное подтверждение (стр. {pg}): {q}"

            kept = []
            for c, (supported, why) in zip(ranked, llm_check.pmap(rev, ranked, a.workers, default=(False, ""))):
                if supported:
                    dropped.append({"target": c["target"], "problem": c["problem"], "why": why})
                else:
                    kept.append(c)
            survivors = kept
        chosen = rank(survivors)
    else:
        chosen = sorted(code_only(cands), key=lambda c: -c["confidence"])

    remarks, seen = [], set()
    for c in chosen:
        r = sanitize(c, ch)
        if r and r["target"] not in seen:
            seen.add(r["target"])
            remarks.append(r)
        if len(remarks) >= a.max:
            break
    if a.dump:
        try:
            Path(a.dump).write_text(json.dumps({"candidates": cands, "chosen": chosen, "dropped_by_reverse": dropped, "stats": llm_check.STATS},
                                               ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        except Exception as e:
            print(f"! --dump: {e}")
    write_answer(out, pkg["package_id"], remarks)
    print(f"Кандидатов: {len(cands)}, сняты обратной проверкой: {len(dropped)}, в ответе: {len(remarks)} → {out} ({time.time() - start:.0f} с)")
    for c in chosen[: len(remarks)]:
        print(f"  {'+'.join(x.replace('llm:', '') for x in c.get('signals', [])):18} {c['target']:6} {c.get('severity', ''):11} {c.get('category', ''):22} {str(c.get('problem', ''))[:70]}")
    for d in dropped:
        print(f"  снято {d['target']}: {d['why'][:100]}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("package")
    ap.add_argument("--chapter-md")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--no-llm", action="store_true", help="только проверки кодом (бесплатно)")
    ap.add_argument("--max", type=int, default=MAX_REMARKS)
    ap.add_argument("--limit", type=int, help="проверять моделью только первые N элементов (отладка)")
    ap.add_argument("--workers", type=int, default=8, help="потоков для вызовов модели (1 = последовательно)")
    ap.add_argument("--deadline-s", type=float, default=DEADLINE_S, help="после этого времени новые вызовы модели не начинаются")
    ap.add_argument("--no-reverse", action="store_true", help="не делать обратную проверку «найди подтверждение»")
    ap.add_argument("--dump", help="сохранить всех кандидатов, сигналы и снятые замечания в json")
    a = ap.parse_args(argv)

    sink: list[dict] = []
    try:
        rc = _run(a, sink)
    except Exception:  # последний рубеж: ответ должен появиться при любом сбое
        traceback.print_exc()
        package_id = "unknown"
        try:
            package_id = _s(json.loads(Path(a.package).read_text(encoding="utf-8")).get("package_id"), "unknown")
        except Exception:
            pass
        remarks = []
        try:
            for c in sorted(code_only(sink), key=lambda c: -c["confidence"]):
                r = sanitize(c, None)
                if r and r["target"] not in {x["target"] for x in remarks}:
                    remarks.append(r)
                if len(remarks) >= a.max:
                    break
        except Exception:
            remarks = []
        write_answer(Path(a.out).resolve(), package_id, remarks)
        print(f"! сбой; записан запасной ответ: {len(remarks)} замечаний по проверкам кодом")
        rc = 0
    if llm_check.TIMED_OUT and not os.environ.get("REVIEW_NO_HARD_EXIT"):  # висящие вызовы API не должны держать процесс
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)
    return rc


if __name__ == "__main__":
    sys.exit(main())
