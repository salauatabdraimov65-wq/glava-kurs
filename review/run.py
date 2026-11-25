"""uv run python -m review.run <package.json> [--chapter-md X.md] -o answer.json [--llm N|all] [--frames] [--max N] [--min-conf X]"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from glava.chapter import load_chapter

from .checks import check_frames, check_items
from .screen import aggregate, rank
from .llm_check import pmap

MAX_REMARKS = 20


def load_ch(pkg: dict, pkg_path: Path, chapter_md: str | None):
    if chapter_md:
        return load_chapter("test", md_path=Path(chapter_md).resolve())
    local = pkg_path.parent / (pkg.get("chapter_file") or "_нет_")
    if local.is_file():
        return load_chapter("test", md_path=local.resolve())
    return load_chapter(pkg["chapter"])


def to_remark(c: dict) -> dict:
    return {k: c[k] for k in ("target", "category", "severity", "problem", "fix", "page_pdf", "quote")}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("package")
    ap.add_argument("--chapter-md")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--no-llm", action="store_true", help="только проверки кодом (бесплатно)")
    ap.add_argument("--max", type=int, default=MAX_REMARKS)
    ap.add_argument("--limit", type=int, help="проверять моделью только первые N элементов (отладка)")
    ap.add_argument("--workers", type=int, default=8, help="потоков для вызовов модели (1 = последовательно)")
    ap.add_argument("--no-reverse", action="store_true", help="не делать обратную проверку «найди подтверждение»")
    ap.add_argument("--dump", help="сохранить всех кандидатов, сигналы и снятые замечания в json")
    a = ap.parse_args(argv)

    pkg_path = Path(a.package).resolve()
    pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    ch = load_ch(pkg, pkg_path, a.chapter_md)
    base = pkg_path.parent
    cands = check_items(pkg, ch) + check_frames(pkg, ch)
    dropped = []

    if a.no_llm:
        # без модели один сигнал — код: отдаём лучшие замечания кода как есть
        survivors = []
        for c in cands:
            if c["confidence"] >= 0.5 and c.get("fix") and c.get("page_pdf") is not None:
                c["signals"] = ["code"]
                survivors.append(c)
    else:
        from .llm_check import check_with_model, check_frames_with_model, reverse_check, reverse_check_frame
        # каскад: strong спрашиваем только про цели, которые уже пометили код или bulk (иначе второго сигнала всё равно не будет)
        cands += check_with_model(pkg, ch, a.limit, tier="bulk", workers=a.workers)
        flagged = {c["target"] for c in cands if c["confidence"] >= 0.4 or c["source"] == "llm:bulk"}
        cands += check_with_model(pkg, ch, a.limit, tier="strong", only=flagged, workers=a.workers)
        for tier in ("bulk", "strong"):
            cands += check_frames_with_model(pkg, ch, base, tier=tier, workers=a.workers)
        survivors = aggregate(cands, ch)
        if not a.no_reverse:
            kept = []
            frames = {f["id"]: f for f in pkg["frames"]}
            ranked = rank(survivors)

            def rev(c):
                try:
                    if c["target"] in frames:
                        return reverse_check_frame(pkg, ch, frames[c["target"]], base), "кадр верен по обратной проверке"
                    supported, q, pg = reverse_check(pkg, ch, c["target"], base)
                    return supported, f"в главе есть дословное подтверждение (стр. {pg}): {q}"
                except Exception as e:
                    print(f"  ! reverse {c['target']}: {e}")
                    return False, ""

            for c, (supported, why) in zip(ranked, pmap(rev, ranked, a.workers)):  # порядок — как в ранге
                if supported:
                    dropped.append({"target": c["target"], "problem": c["problem"], "why": why})
                else:
                    kept.append(c)
            survivors = kept

    chosen = rank(survivors)[: a.max] if not a.no_llm else sorted(survivors, key=lambda c: -c["confidence"])[: a.max]
    if a.dump:
        Path(a.dump).write_text(json.dumps({"candidates": cands, "chosen": chosen, "dropped_by_reverse": dropped}, ensure_ascii=False, indent=1), encoding="utf-8")
    answer = {"package_id": pkg["package_id"], "remarks": [to_remark(c) for c in chosen]}

    schema = json.loads((Path(__file__).resolve().parent.parent / "schemas" / "review_output.schema.json").read_text(encoding="utf-8"))
    import jsonschema
    jsonschema.validate(answer, schema)
    Path(a.out).write_text(json.dumps(answer, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Кандидатов: {len(cands)}, прошли согласие сигналов: {len(survivors) + len(dropped)}, сняты обратной проверкой: {len(dropped)}, в ответе: {len(chosen)} → {a.out}")
    for c in chosen:
        print(f"  {'+'.join(x.replace('llm:', '') for x in c['signals']):18} {c['target']:5} {c['severity']:11} {c['category']:22} {c['problem'][:70]}")
    for d in dropped:
        print(f"  снято {d['target']}: {d['why'][:100]}")


if __name__ == "__main__":
    sys.exit(main())
