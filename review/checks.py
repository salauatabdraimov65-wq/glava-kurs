"""Бесплатные проверки кодом: цитата, страница, расхождение текста и источника, границы аналогий."""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher

from glava.chapter import Chapter, check_claim, norm

from .lexicon import ABSOLUTES, HEDGES, OPPOSITES, SCALING, MEASURED, INFERRED, has, numbers

SEV = {"sign_flip": "critical", "wrong_scaling": "critical", "overgeneralization": "significant",
       "measured_vs_inferred": "significant", "lost_condition": "significant", "missing_caveat": "minor",
       "analogy_without_limit": "significant", "frame_error": "significant", "wrong_mechanism": "critical",
       "outdated_as_current": "significant", "other": "minor"}


def cand(target, category, problem, fix, page, quote, conf, source="code", severity=None):
    return {"target": target, "category": category, "severity": severity or SEV.get(category, "minor"),
            "problem": problem, "fix": fix, "page_pdf": page, "quote": quote, "confidence": round(conf, 2), "source": source}


def best_page(ch: Chapter, text: str) -> tuple[int | None, float]:
    """Страница главы, наиболее похожая на текст (по пересечению слов). Для случаев, когда цитата не найдена."""
    words = {w for w in re.findall(r"\w{4,}", text.lower())}
    if not words:
        return None, 0.0
    best, score = None, 0.0
    for p, t in ch.pages.items():
        tw = set(re.findall(r"\w{4,}", t.lower()))
        sc = len(words & tw) / len(words)
        if sc > score:
            best, score = p, sc
    return best, score


def best_sentence(ch: Chapter, text: str, page: int | None) -> str | None:
    """Предложение автора, наиболее близкое к утверждению (для fix/quote)."""
    pages = [page] if page in ch.pages else list(ch.pages)
    words = set(re.findall(r"\w{4,}", text.lower()))
    best, bs = None, 0.0
    for p in pages:
        for sent in re.split(r"(?<=[.!?])\s+", ch.pages[p].replace("\n", " ")):
            if len(sent) < 25:
                continue
            sw = set(re.findall(r"\w{4,}", sent.lower()))
            sc = len(words & sw) / (len(words | sw) or 1)
            if sc > bs:
                best, bs = sent.strip(), sc
    return best


def compare(text: str, src: str, ctx: str) -> list[tuple[str, str, float]]:
    """Расхождения текста утверждения и источника (цитата src; ctx — вся страница). -> [(категория, что не так, уверенность)]"""
    out = []
    t = text.lower().replace("ё", "е")
    q = src.lower().replace("ё", "е")
    c = ctx.lower().replace("ё", "е")
    # числа
    tn, qn, cn = set(numbers(text)), set(numbers(src)), set(numbers(ctx))
    extra = sorted(n for n in tn if n not in qn)
    if extra:
        in_ctx = [n for n in extra if n in cn]
        cat = "wrong_scaling" if has(SCALING, t) else "other"
        if len(in_ctx) == len(extra):
            out.append((cat, f"числа {extra} есть на странице, но не в приведённой цитате", 0.35))
        else:
            bad = [n for n in extra if n not in cn]
            out.append((cat, f"в утверждении числа {bad}, которых нет в тексте автора", 0.7))
    # абсолюты
    for w in ABSOLUTES:
        w2 = w.strip()
        if f" {w2}" in " " + t and w2 not in q:
            conf = 0.6 if w2 not in c else 0.4
            out.append(("overgeneralization", f"«{w2}» в утверждении, а у автора оно ограничено условиями", conf))
            break
    # переворот
    for a, b in OPPOSITES:
        ta, tb, qa, qb = has(a, t), has(b, t), has(a, q), has(b, q)
        if ta and not tb and qb and not qa:
            out.append(("sign_flip", f"в утверждении «{ta[0]}», у автора противоположное «{qb[0]}»", 0.65))
            break
        if tb and not ta and qa and not qb:
            out.append(("sign_flip", f"в утверждении «{tb[0]}», у автора противоположное «{qa[0]}»", 0.65))
            break
    # потеря оговорок
    lost = [h.strip() for h in HEDGES if h in " " + q + " " and h not in " " + t + " "]
    strong = [h for h in lost if h in ("при прочих равных", "при условии", "как правило", "обычно", "в большинстве", "только при",
                                       "лишь при", "при благоприятных", "usually", "typically", "provided", "only if", "not always")]
    if strong:
        out.append(("lost_condition", f"у автора условие/оговорка «{strong[0]}», в утверждении её нет", 0.4))
    # измерено/выведено
    if has(MEASURED, t) and has(INFERRED, q) and not has(MEASURED, q):
        out.append(("measured_vs_inferred", "выведенное выдано за измеренное", 0.4))
    if has(INFERRED, t) and has(MEASURED, q) and not has(INFERRED, q):
        out.append(("measured_vs_inferred", "измеренное выдано за выведенное", 0.4))
    return out


def check_items(pkg: dict, ch: Chapter) -> list[dict]:
    items = {i["id"]: i for i in pkg["items"]}
    res = []
    claims_by_node: dict[str, list[dict]] = {}
    for i in pkg["items"]:
        if i["type"] == "claim":
            claims_by_node.setdefault(i.get("node_id"), []).append(i)

    def one(it):
        tid, typ = it["id"], it["type"]
        if typ == "claim":
            q, pg = it.get("quote") or "", it.get("page_pdf")
            if q:
                r = check_claim(ch, q, pg)
                if not r["ok"] and r["found_on"]:
                    p = r["found_on"][0]
                    res.append(cand(tid, "other", f"неверная страница: {r['problem']}", f"Указать страницу {p}", p, q, 0.75, severity="minor"))
                elif not r["ok"]:
                    p, sc = best_page(ch, q)
                    res.append(cand(tid, "other", f"{r['problem']} (возможно, цитата искажена)",
                                    "Заменить цитату дословной: " + (best_sentence(ch, q, p) or ""), p, best_sentence(ch, q, p), 0.55 if sc > 0.5 else 0.3, severity="significant"))
                    pg = p
            ctx = ch.pages.get(pg, "") if pg else ""
            src = q
            if not q:  # текст шага без цитаты: источник — все страницы узла
                ctx = "\n".join(ch.pages.get(p_, "") for p_ in (it.get("pages") or ([pg] if pg else [])))
                src = ctx
            for cat, prob, conf in compare(it["text"], src, ctx):
                p = pg
                res.append(cand(tid, cat, prob, "Привести формулировку к тексту автора: «" + (best_sentence(ch, q or it["text"], p) or q) + "»", p, best_sentence(ch, q or it["text"], p), conf))
        elif typ == "script_line":
            ref = items.get(it.get("claim_ref") or "")
            if ref and ref.get("quote"):
                p = ref.get("page_pdf")
                ctx = ch.pages.get(p, "")
                for cat, prob, conf in compare(it["text"], ref["quote"], ctx):
                    res.append(cand(tid, cat, prob + f" (опора — {ref['id']})", "Привести фразу к тексту автора: «" + (best_sentence(ch, it["text"], p) or ref["quote"]) + "»", p, best_sentence(ch, it["text"], p) or ref["quote"], conf * 0.9))
        elif typ == "analogy":
            bw = (it.get("breaks_where") or "").strip()
            sibs = claims_by_node.get(it.get("node_id")) or []
            page = sibs[0]["page_pdf"] if sibs else best_page(ch, it["text"] + " " + bw)[0]
            quote = sibs[0].get("quote") if sibs else None
            weak = re.search(r"аналогия\s+(точн|полн|идеальн)|не\s+перестаёт|не\s+нарушается|нет\s+границ|exact analogy|holds always|no limit", bw.lower().replace("ё", "е"))
            if len(bw) < 20 or weak:
                res.append(cand(tid, "analogy_without_limit", "аналогия без границы или с утверждением, что она точная", "Указать, где аналогия перестаёт работать, опираясь на условия применимости из главы", page, quote, 0.8 if weak else 0.5))
            for w in ABSOLUTES:
                if w.strip() and f" {w.strip()}" in " " + bw.lower() and not weak:
                    res.append(cand(tid, "analogy_without_limit", f"граница аналогии содержит абсолют «{w.strip()}»", "Заменить границу аналогии условной формулировкой по тексту главы", page, quote, 0.5))
                    break
        elif typ == "scene_fact":
            spec = next((s["spec"] for s in pkg["scenes"] if s["video_id"] == it.get("video_id")), None)
            if spec:
                dump = " ".join(numbers(json.dumps(spec, ensure_ascii=False)))
                dset = set(dump.split())
                miss = [n for n in numbers(it["text"]) if n not in dset]
                if miss:
                    res.append(cand(tid, "frame_error", f"числа {miss} из факта нет в спецификации сцены", "Привести факт в соответствие со спецификацией сцены", None, None, 0.3))
    for it in pkg["items"]:
        try:
            one(it)
        except Exception as e:  # одна кривая запись не должна валить проверку остальных
            print(f"  ! код: {it.get('id')}: {type(e).__name__}: {e}")
    return res



def check_frames(pkg: dict, ch: Chapter) -> list[dict]:
    res = []
    # крупные числа (≥100: глубины и т.п.) из `shows` должны быть в спецификации сцены
    for f in pkg["frames"]:
        spec = next((s["spec"] for s in pkg["scenes"] if s["video_id"] == f["video_id"]), None)
        if not spec:
            continue
        have = set(numbers(json.dumps(spec, ensure_ascii=False)))
        miss = [n for n in numbers(f["shows"]) if float(n) >= 100 and n not in have]
        if miss:
            res.append(cand(f["id"], "frame_error", f"в описании кадра числа {miss}, которых нет в спецификации сцены",
                            "Привести описание кадра к значениям спецификации сцены", None, None, 0.45))
    from collections import Counter
    for vid in {f["video_id"] for f in pkg["frames"]}:
        fr = [f for f in pkg["frames"] if f["video_id"] == vid]
        maj, n = Counter(f["label"] for f in fr).most_common(1)[0]
        for f in fr:
            if f["label"] != maj and n >= 0.6 * len(fr):
                res.append(cand(f["id"], "frame_error", f"метка происхождения «{f['label']}» отличается от остальных кадров ролика («{maj}»)",
                                f"Поставить метку «{maj}» — кадр построен так же, как остальные", None, None, 0.45))
    return res
