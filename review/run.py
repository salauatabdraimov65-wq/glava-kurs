"""uv run python -m review.run <package.json> [--chapter-md X.md] -o answer.json [--llm N|all] [--frames] [--max N] [--min-conf X]"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from glava.chapter import load_chapter

from .checks import check_frames, check_items

MAX_REMARKS = 25


def load_ch(pkg: dict, pkg_path: Path, chapter_md: str | None):
    if chapter_md:
        return load_chapter("test", md_path=Path(chapter_md).resolve())
    local = pkg_path.parent / pkg["chapter_file"]
    if local.exists():
        return load_chapter("test", md_path=local.resolve())
    return load_chapter(pkg["chapter"])


def select(cands: list[dict], min_conf: float, max_n: int) -> list[dict]:
    """По одной (лучшей) записи на цель; нужны fix и page_pdf; сортировка по уверенности."""
    best: dict[str, dict] = {}
    for c in cands:
        if not c.get("fix") or c.get("page_pdf") is None:
            continue
        if c["confidence"] < min_conf:
            continue
        if c["target"] not in best or c["confidence"] > best[c["target"]]["confidence"]:
            best[c["target"]] = c
    return sorted(best.values(), key=lambda c: -c["confidence"])[:max_n]


def to_remark(c: dict) -> dict:
    return {k: c[k] for k in ("target", "category", "severity", "problem", "fix", "page_pdf", "quote")}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("package")
    ap.add_argument("--chapter-md")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--llm", default="0", help="0 = без модели; N = первые N целей; all = все")
    ap.add_argument("--frames", action="store_true", help="проверять кадры моделью (tier strong)")
    ap.add_argument("--max", type=int, default=MAX_REMARKS)
    ap.add_argument("--min-conf", type=float, default=0.5)
    ap.add_argument("--dump", help="сохранить всех кандидатов (до отсева) в этот json")
    a = ap.parse_args(argv)

    pkg_path = Path(a.package).resolve()
    pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    ch = load_ch(pkg, pkg_path, a.chapter_md)
    cands = check_items(pkg, ch) + check_frames(pkg, ch)

    if a.llm != "0" or a.frames:
        from .llm_check import check_with_model, check_frames_with_model
        if a.llm != "0":
            cands += check_with_model(pkg, ch, None if a.llm == "all" else int(a.llm))
        if a.frames:
            cands += check_frames_with_model(pkg, ch, pkg_path.parent)

    if a.dump:
        Path(a.dump).write_text(json.dumps(cands, ensure_ascii=False, indent=1), encoding="utf-8")
    chosen = select(cands, a.min_conf, a.max)
    answer = {"package_id": pkg["package_id"], "remarks": [to_remark(c) for c in chosen]}

    schema = json.loads((Path(__file__).resolve().parent.parent / "schemas" / "review_output.schema.json").read_text(encoding="utf-8"))
    import jsonschema
    jsonschema.validate(answer, schema)
    Path(a.out).write_text(json.dumps(answer, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Кандидатов: {len(cands)}, в ответе: {len(chosen)} → {a.out}")
    for c in chosen:
        print(f"  {c['confidence']:.2f} {c['target']:5} {c['category']:22} {c['problem'][:90]}")


if __name__ == "__main__":
    sys.exit(main())
