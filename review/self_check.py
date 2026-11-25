"""Проверка собственного курса: runs/<глава>/layer.json + video/*/spec.json → пакет по review_package.schema.json → тот же модуль проверки.

    uv run python -m review.self_check runs/<глава> -o runs/<глава>/review_self.json
Пакет сохраняется рядом: <out>_package.json. Ответ — по review_output.schema.json (замечания к нашим же материалам).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from glava.chapter import ROOT


def _text(x):
    return x.get("text") if isinstance(x, dict) else x


def build_package(run_dir: Path) -> dict:
    layer = json.loads((run_dir / "layer.json").read_text(encoding="utf-8"))
    chapter = layer["chapter"]
    items, cid, claim_map = [], 0, {}
    for node in layer["nodes"]:
        for c in node.get("claims", []):
            cid += 1
            iid = f"C{cid:03d}"
            claim_map[c["id"]] = iid
            items.append({"id": iid, "type": "claim", "node_id": node["id"], "text": c["text"], "kind": c.get("kind"),
                          "page_pdf": c.get("page_pdf"), "quote": c.get("quote"), "orig_id": c["id"]})
    for k, node in enumerate((n for n in layer["nodes"] if _text(n.get("it_analogy"))), 1):
        a = node["it_analogy"]
        items.append({"id": f"A{k:02d}", "type": "analogy", "node_id": node["id"], "text": a["text"], "breaks_where": a.get("breaks_where")})

    scenes, frames = [], []
    for vi, spec_path in enumerate(sorted((run_dir / "video").glob("*/spec.json")), 1):
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        vid = spec.get("video_id") or spec_path.parent.name
        scenes.append({"video_id": vid, "spec": spec})
        node_id = spec.get("node_id")
        t = 0.0
        for bi, b in enumerate(spec.get("beats", []), 1):
            say = (b.get("say") or "").strip()
            if not say:
                continue
            dur = max(2.0, len(say.split()) / 2.3)  # ≈140 слов/мин, если паспорт без таймкодов
            items.append({"id": f"L{vi:02d}.{bi:02d}", "type": "script_line", "node_id": node_id, "text": say, "video_id": vid,
                          "t_start": round(t, 2), "t_end": round(t + dur, 2), "claim_ref": claim_map.get(b.get("claim_id")),
                          "on_screen": b.get("on_screen")})
            t += dur + (b.get("pause_s") or 0)
        si = 0

        def fact(text, path):
            nonlocal si
            si += 1
            items.append({"id": f"S{vi:02d}.{si:02d}", "type": "scene_fact", "node_id": node_id, "text": text, "video_id": vid, "spec_path": path})
        for i, inv in enumerate(spec.get("invariants", [])):
            fact(inv, f"invariants[{i}]")
        for i, la in enumerate((spec.get("params") or {}).get("layers", [])):
            fact(f"Слой {la.get('top')}–{la.get('bottom')} м: {la.get('label')}; свойства {json.dumps(la.get('props'), ensure_ascii=False)}", f"params.layers[{i}]")
        if spec.get("plaque"):
            fact("Плашка «" + " ".join(spec["plaque"]) + "» называет все преувеличения сцены: " + "; ".join(spec.get("exaggerations", [])), "plaque; exaggerations")
        for i, a in enumerate(spec.get("assumptions", [])):
            fact(a, f"assumptions[{i}]")
        # кадры, если ролик уже собран
        pp = spec_path.parent / "passport.json"
        if pp.exists():
            for fr in json.loads(pp.read_text(encoding="utf-8")).get("frames", []):
                if fr.get("file") and (spec_path.parent / fr["file"]).exists():
                    frames.append({"id": f"F{len(frames) + 1:02d}", "video_id": vid, "file": str((spec_path.parent / fr["file"]).resolve()), "t": fr.get("t", 0),
                                   "frame_class": fr.get("frame_class", "B"), "label": fr.get("label", "симуляция"), "shows": fr.get("shows", ""), "plaque": fr.get("plaque")})
    return {"package_id": f"self_{chapter}", "chapter": chapter, "chapter_file": "", "language": "ru", "items": items, "scenes": scenes, "frames": frames}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--chapter-md")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args(argv)
    run_dir = Path(a.run_dir).resolve()
    pkg = build_package(run_dir)
    out = Path(a.out).resolve()
    pkg_path = out.with_name(out.stem + "_package.json")
    pkg_path.write_text(json.dumps(pkg, ensure_ascii=False, indent=1), encoding="utf-8")
    kinds = {t: sum(1 for i in pkg["items"] if i["type"] == t) for t in ("claim", "analogy", "script_line", "scene_fact")}
    print(f"Пакет {pkg_path.name}: {kinds}, сцен {len(pkg['scenes'])}, кадров {len(pkg['frames'])}")
    from . import run
    args = [str(pkg_path), "-o", str(out)] + (["--chapter-md", a.chapter_md] if a.chapter_md else []) + (["--no-llm"] if a.no_llm else []) + (["--limit", str(a.limit)] if a.limit else [])
    return run.main(args)


if __name__ == "__main__":
    sys.exit(main())
