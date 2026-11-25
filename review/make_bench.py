"""Свой полигон без ключа организаторов: берёт наш слой главы, портит ~20% утверждений скриптом, пишет package.json и key.json.

    uv run python -m review.make_bench barenblatt_par4 -o review/bench/barenblatt_par4 [--n 90] [--seed 7]

Порча — по категориям ТЗ (sign_flip, overgeneralization, lost_condition, wrong_scaling, measured_vs_inferred). Портится только `text`
утверждения; цитата автора остаётся, и испорченное слово/число обязательно есть в цитате — значит, ошибка настоящая.
Плюс «верные, но неожиданные»: нетронутые утверждения, где абсолют стоит и в тексте, и в цитате автора.
Предметных знаний здесь нет: только русская грамматика и общие слова.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

from glava.chapter import ROOT, check_claim, load_chapter

SWAPS = [("выше", "ниже"), ("растёт", "падает"), ("возрастает", "убывает"), ("увеличивается", "уменьшается"), ("увеличение", "уменьшение"),
         ("больше", "меньше"), ("больший", "меньший"), ("повышается", "понижается"), ("повышение", "понижение"), ("быстрее", "медленнее"),
         ("ускоряется", "замедляется"), ("высокое", "низкое"), ("высокая", "низкая"), ("высокие", "низкие"), ("рост", "падение"), ("прямо", "обратно")]
HEDGES = ["как правило", "обычно", "в большинстве случаев", "часто", "нередко", "в основном", "в ряде случаев"]
ABSOLUTES = ["всегда", "любой", "любая", "любое", "любые", "любого", "никогда", "во всех", "только", "все "]
SEVERITY = {"sign_flip": "critical", "wrong_scaling": "critical", "overgeneralization": "significant", "lost_condition": "significant",
            "measured_vs_inferred": "significant"}
UNITS = r"(?:мД|мкм|м|%|с|сут|мин|ч|км|см|мм|МПа|атм|°C|кг|Па|г/см3|Ом·м|мс)"


def _word(w: str, s: str):
    return re.search(rf"(?<![\w]){re.escape(w)}(?![\w])", s, re.I)


def _case(src: str, new: str) -> str:
    return new.capitalize() if src[:1].isupper() else new


def corrupt_sign_flip(text: str, quote: str):
    q = quote.lower()
    for a, b in SWAPS:
        for x, y in ((a, b), (b, a)):
            m = _word(x, text)
            if m and _word(x, q) and not _word(y, q):
                new = text[:m.start()] + _case(m.group(0), y) + text[m.end():]
                return new, f"«{x}» заменено на «{y}»"
    return None


def corrupt_overgen(text: str, quote: str):
    q = quote.lower()
    for h in HEDGES:
        m = _word(h, text)
        if m and h in q:
            new = text[:m.start()] + _case(m.group(0), "всегда") + text[m.end():]
            return new, f"«{h}» заменено на «всегда»"
    return None


def corrupt_lost_condition(text: str, quote: str):
    q = quote.lower()
    for pat, repl in ((r",\s*(?:если|когда|при условии,? что|в случае,? если)\s[^,.;]{10,}(?=[.;])", ""), (r"^(?:Если|Когда|При)\s[^,]{10,},\s*", "")):
        m = re.search(pat, text)
        if m:
            words = [w for w in re.findall(r"\w{5,}", m.group(0).lower())][:3]
            if words and sum(w[:5] in q for w in words) >= max(1, len(words) - 1):
                new = text[:m.start()] + repl + text[m.end():]
                new = new[:1].upper() + new[1:] if repl == "" and m.start() == 0 else new
                return new, f"выброшено условие «{m.group(0).strip(' ,')}»"
    return None


def _num(n: str) -> str:
    v = float(n.replace(",", ".")) * 10
    return str(int(v)) if v == int(v) else str(v).replace(".", ",")


def corrupt_scaling(text: str, quote: str):
    q = quote.lower()
    m = re.search(r"(?<!обратно )пропорционально", text)
    if m and "пропорционально" in q and "обратно пропорционально" not in q:
        return text[:m.start()] + "обратно пропорционально" + text[m.end():], "«пропорционально» заменено на «обратно пропорционально»"
    m = re.search(r"обратно пропорционально", text)
    if m and "обратно пропорционально" in q:
        return text[:m.start()] + "пропорционально" + text[m.end():], "«обратно пропорционально» заменено на «пропорционально»"
    for m in re.finditer(rf"(?<![\w.,])(\d+(?:[.,]\d+)?)(\s?{UNITS}|\s+раз)(?![\w])", text):
        if m.group(1) in quote.replace(",", ",") and float(m.group(1).replace(",", ".")) > 0:
            return text[:m.start()] + _num(m.group(1)) + m.group(2) + text[m.end():], f"число {m.group(1)} увеличено в 10 раз"
    return None


def corrupt_measured(text: str, quote: str, kind: str):
    if kind != "inference":
        return None
    return "Прямым измерением установлено: " + text[:1].lower() + text[1:], "вывод выдан за прямое измерение"


CORRUPTORS = {"sign_flip": corrupt_sign_flip, "overgeneralization": corrupt_overgen, "lost_condition": corrupt_lost_condition, "wrong_scaling": corrupt_scaling}


def make(chapter_id: str, out: Path, n: int = 90, seed: int = 7, error_share: float = 0.2, n_tricky: int = 10):
    layer = json.loads((ROOT / "runs" / chapter_id / "layer.json").read_text(encoding="utf-8"))
    ch = load_chapter(chapter_id)
    pool = []
    for node in layer["nodes"]:
        for c in node.get("claims", []):
            q, p = c.get("quote") or "", c.get("page_pdf")
            if len(q) > 40 and p and check_claim(ch, q, p)["ok"] and len(c["text"]) > 40:
                pool.append({"node": node["id"], **c})
    rnd = random.Random(seed)
    rnd.shuffle(pool)
    n_err = round(n * error_share)
    cats = ["sign_flip", "overgeneralization", "lost_condition", "wrong_scaling", "measured_vs_inferred"]
    quota = {c: n_err // len(cats) + (1 if i < n_err % len(cats) else 0) for i, c in enumerate(cats)}

    # кандидаты на порчу по каждой категории; квоты добираем по кругу из категорий, где ещё что-то осталось
    cand = {cat: [] for cat in cats}
    for c in pool:
        for cat in cats:
            r = corrupt_measured(c["text"], c["quote"], c.get("kind")) if cat == "measured_vs_inferred" else CORRUPTORS[cat](c["text"], c["quote"])
            if r and r[0] != c["text"]:
                cand[cat].append((c, cat, r[0], r[1]))
    used, errors = set(), {}
    while len(errors) < n_err and any(cand.values()):
        for cat in sorted(cats, key=lambda k: sum(1 for e in errors.values() if e[1] == k)):
            while cand[cat] and cand[cat][0][0]["id"] in used:
                cand[cat].pop(0)
            if cand[cat] and len(errors) < n_err:
                e = cand[cat].pop(0)
                errors[e[0]["id"]] = e
                used.add(e[0]["id"])
    UNEXPECTED = [r"не зависит", r"не влияет", r"независимо", r"наоборот", r"вопреки", r"нельзя", r"невозможн", r"не обязательно", r"не всегда", r"не только", r"не может"]
    tricky = []
    for strict in (True, False):
        for c in pool:
            if len(tricky) >= n_tricky:
                break
            if c["id"] in used:
                continue
            t, q = c["text"].lower(), c["quote"].lower()
            hit = [a_ for a_ in ABSOLUTES if a_ in t and a_ in q] if strict else [m for m in UNEXPECTED if re.search(m, t) and re.search(m, q)]
            if hit:
                tricky.append((c, hit[0].strip() if strict else re.sub(r"[\\]", "", hit[0])))
                used.add(c["id"])
    rest = [c for c in pool if c["id"] not in used]
    chosen = [(e[0], e) for e in errors.values()] and list(errors.values())
    normal = rest[: max(0, n - len(errors) - len(tricky))]

    entries = [("err", e[0], e) for e in errors.values()] + [("tricky", c, h) for c, h in tricky] + [("ok", c, None) for c in normal]
    order = {n_["id"]: i for i, n_ in enumerate(layer["nodes"])}
    entries.sort(key=lambda e: (order[e[1]["node"]], e[1]["id"]))
    items, key_err, key_tr = [], [], []
    for i, (kind, c, extra) in enumerate(entries, 1):
        iid = f"C{i:03d}"
        if kind == "err":
            _, cat, new_text, note = extra
            text = new_text
            kk = "observation" if cat == "measured_vs_inferred" else c.get("kind")
            key_err.append({"target": iid, "category": cat, "severity": SEVERITY[cat], "correct": c["text"], "pages": [c["page_pdf"]],
                            "quote": c["quote"], "note": note})
        else:
            text, kk = c["text"], c.get("kind")
            if kind == "tricky":
                key_tr.append({"target": iid, "why_tricky": f"формулировка «{extra}» есть и в тексте автора", "pages": [c["page_pdf"]]})
        items.append({"id": iid, "type": "claim", "node_id": c["node"], "text": text, "kind": kk, "page_pdf": c["page_pdf"], "quote": c["quote"], "orig_id": c["id"]})
    md = Path(ch.meta["md"]).name
    pkg = {"package_id": f"bench_{chapter_id}", "chapter": chapter_id, "chapter_file": md, "language": "ru", "items": items, "scenes": [], "frames": []}
    key = {"package_id": pkg["package_id"], "errors": key_err, "tricky_true": key_tr}
    out.mkdir(parents=True, exist_ok=True)
    (out / "package.json").write_text(json.dumps(pkg, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "key.json").write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding="utf-8")
    by = {c: sum(1 for e in key_err if e["category"] == c) for c in cats}
    print(f"{chapter_id}: пул {len(pool)}, пакет {len(items)} утверждений, ошибок {len(key_err)} {by}, «верных, но неожиданных» {len(key_tr)} → {out}")
    return pkg, key


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("chapter_id")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--n", type=int, default=90)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(argv)
    make(a.chapter_id, Path(a.out), a.n, a.seed)


if __name__ == "__main__":
    sys.exit(main())
