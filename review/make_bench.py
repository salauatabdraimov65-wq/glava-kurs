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

RU = dict(
    SWAPS=[("выше", "ниже"), ("растёт", "падает"), ("возрастает", "убывает"), ("увеличивается", "уменьшается"), ("увеличение", "уменьшение"),
           ("больше", "меньше"), ("больший", "меньший"), ("повышается", "понижается"), ("повышение", "понижение"), ("быстрее", "медленнее"),
           ("ускоряется", "замедляется"), ("высокое", "низкое"), ("высокая", "низкая"), ("высокие", "низкие"), ("рост", "падение"), ("прямо", "обратно")],
    HEDGES=["как правило", "обычно", "в большинстве случаев", "часто", "нередко", "в основном", "в ряде случаев"],
    OVERGEN_WORD="всегда",
    ABSOLUTES=["всегда", "любой", "любая", "любое", "любые", "любого", "никогда", "во всех", "только", "все "],
    UNEXPECTED=[r"не зависит", r"не влияет", r"независимо", r"наоборот", r"вопреки", r"нельзя", r"невозможн", r"не обязательно", r"не всегда", r"не только", r"не может"],
    COND_PATTERNS=[(r",\s*(?:если|когда|при условии,? что|в случае,? если)\s[^,.;]{10,}(?=[.;])", ""), (r"^(?:Если|Когда|При)\s[^,]{10,},\s*", "")],
    INV_PREFIX="обратно ", DIRECT="пропорционально", TIMES=r"\s+раз",
    UNITS=r"(?:мД|мкм|м|%|с|сут|мин|ч|км|см|мм|МПа|атм|°C|кг|Па|г/см3|Ом·м|мс)",
    MEASURED_PREFIX="Прямым измерением установлено: ",
    ANALOGY=["Аналогия точная: здесь она не перестаёт работать, величина постоянна и одинакова для любых объектов.",
             "Аналогия полностью точная и справедлива всегда, без ограничений на условия применения.",
             "Это точное соответствие: аналогия работает для любых случаев и границ у неё нет."],
    DEPTH=[("увеличивается вниз", "вниз", "вверх"), ("растёт вниз", "вниз", "вверх")],
    FACT_UNITS=r"(?:м|мД|%|с|Ом·м|мкм)",
)
EN = dict(
    SWAPS=[("higher", "lower"), ("increases", "decreases"), ("increase", "decrease"), ("rises", "falls"), ("greater", "smaller"), ("larger", "smaller"),
           ("more", "less"), ("faster", "slower"), ("above", "below"), ("upward", "downward"), ("positive", "negative"), ("longer", "shorter"), ("high", "low")],
    HEDGES=["usually", "typically", "generally", "often", "as a rule", "in most cases", "sometimes", "commonly"],
    OVERGEN_WORD="always",
    ABSOLUTES=["always", "any ", "never", "all ", "only ", "every", "in all cases", "entirely", "completely"],
    UNEXPECTED=[r"does not depend", r"independent of", r"regardless", r"contrary to", r"cannot", r"impossible", r"not only", r"not always", r"not necessarily", r"unlike"],
    COND_PATTERNS=[(r",\s*(?:if|when|provided that|as long as|unless)\s[^,.;]{10,}(?=[.;])", ""), (r"^(?:If|When|Provided that)\s[^,]{10,},\s*", "")],
    INV_PREFIX="inversely ", DIRECT="proportional", TIMES=r"\s+times",
    UNITS=r"(?:mD|µm|km|cm|mm|ms|min|MPa|atm|°C|kg|Pa|ohm|m|%|s|h)",
    MEASURED_PREFIX="It was directly measured that ",
    ANALOGY=["The analogy is exact: it never breaks down, and the quantity is constant and identical for any object.",
             "The analogy holds completely and always, with no limits on the conditions of use.",
             "This is an exact match: the analogy works in all cases and has no boundary."],
    DEPTH=[("depth increases downward", "downward", "upward"), ("increases downward", "downward", "upward"), ("increases downwards", "downwards", "upwards")],
    FACT_UNITS=r"(?:m|mD|%|s|ohm|µm|km)",
)
SWAPS, HEDGES, OVERGEN_WORD, ABSOLUTES, UNEXPECTED = RU["SWAPS"], RU["HEDGES"], RU["OVERGEN_WORD"], RU["ABSOLUTES"], RU["UNEXPECTED"]
COND_PATTERNS, INV_PREFIX, DIRECT, TIMES, UNITS = RU["COND_PATTERNS"], RU["INV_PREFIX"], RU["DIRECT"], RU["TIMES"], RU["UNITS"]
MEASURED_PREFIX, ANALOGY, DEPTH, FACT_UNITS = RU["MEASURED_PREFIX"], RU["ANALOGY"], RU["DEPTH"], RU["FACT_UNITS"]


def use_lang(lang: str):
    """Переключает наборы порч (слова, шаблоны) на язык главы: ru или en."""
    d = EN if (lang or "ru").startswith("en") else RU
    globals().update(SWAPS=d["SWAPS"], HEDGES=d["HEDGES"], OVERGEN_WORD=d["OVERGEN_WORD"], ABSOLUTES=d["ABSOLUTES"], UNEXPECTED=d["UNEXPECTED"],
                     COND_PATTERNS=d["COND_PATTERNS"], INV_PREFIX=d["INV_PREFIX"], DIRECT=d["DIRECT"], TIMES=d["TIMES"], UNITS=d["UNITS"],
                     MEASURED_PREFIX=d["MEASURED_PREFIX"], ANALOGY=d["ANALOGY"], DEPTH=d["DEPTH"], FACT_UNITS=d["FACT_UNITS"])


def chapter_lang(chapter_id: str) -> str:
    from glava.chapter import chapters_config
    return chapters_config().get(chapter_id, {}).get("lang", "ru")


SEVERITY = {"sign_flip": "critical", "wrong_scaling": "critical", "overgeneralization": "significant", "lost_condition": "significant",
            "measured_vs_inferred": "significant"}


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
            new = text[:m.start()] + _case(m.group(0), OVERGEN_WORD) + text[m.end():]
            return new, f"«{h}» заменено на «{OVERGEN_WORD}»"
    return None


def corrupt_lost_condition(text: str, quote: str):
    q = quote.lower()
    for pat, repl in COND_PATTERNS:
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
    inv = INV_PREFIX + DIRECT
    m = re.search(rf"(?<!{INV_PREFIX}){DIRECT}", text)
    if m and DIRECT in q and inv not in q:
        return text[:m.start()] + inv + text[m.end():], f"«{DIRECT}» заменено на «{inv}»"
    m = re.search(inv, text)
    if m and inv in q:
        return text[:m.start()] + DIRECT + text[m.end():], f"«{inv}» заменено на «{DIRECT}»"
    for m in re.finditer(rf"(?<![\w.,])(\d+(?:[.,]\d+)?)(\s?{UNITS}|{TIMES})(?![\w])", text):
        if m.group(1) in quote and float(m.group(1).replace(",", ".")) > 0:
            return text[:m.start()] + _num(m.group(1)) + m.group(2) + text[m.end():], f"число {m.group(1)} увеличено в 10 раз"
    return None


def corrupt_measured(text: str, quote: str, kind: str):
    if kind != "inference":
        return None
    return MEASURED_PREFIX + text[:1].lower() + text[1:], "вывод выдан за прямое измерение"


CORRUPTORS = {"sign_flip": corrupt_sign_flip, "overgeneralization": corrupt_overgen, "lost_condition": corrupt_lost_condition, "wrong_scaling": corrupt_scaling}


def make(chapter_id: str, out: Path, n: int = 90, seed: int = 7, error_share: float = 0.2, n_tricky: int = 10):
    use_lang(chapter_lang(chapter_id))
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
    pkg = {"package_id": f"bench_{chapter_id}", "chapter": chapter_id, "chapter_file": md, "language": chapter_lang(chapter_id), "items": items, "scenes": [], "frames": []}
    key = {"package_id": pkg["package_id"], "errors": key_err, "tricky_true": key_tr}
    out.mkdir(parents=True, exist_ok=True)
    (out / "package.json").write_text(json.dumps(pkg, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "key.json").write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding="utf-8")
    by = {c: sum(1 for e in key_err if e["category"] == c) for c in cats}
    print(f"{chapter_id}: пул {len(pool)}, пакет {len(items)} утверждений, ошибок {len(key_err)} {by}, «верных, но неожиданных» {len(key_tr)} → {out}")
    return pkg, key


# ====================== режим --mixed: все четыре типа элементов в пропорции контрольного пакета ======================

CONTROL_MIX = {"claim": 60, "analogy": 5, "script_line": 10, "scene_fact": 6}  # как в контрольном пакете организаторов


def corrupt_text(text: str, quote: str, cat: str):
    if cat == "measured_vs_inferred":
        return corrupt_measured(text, quote, "inference")
    return CORRUPTORS[cat](text, quote)


def corrupt_analogy(it: dict, k: int):
    return ANALOGY[k % len(ANALOGY)]


def corrupt_fact(it: dict):
    t = it["text"]
    for phrase, old, new in DEPTH:
        if phrase in t.lower():
            return t.replace(old, new, 1), "frame_error", "направление оси глубины перевёрнуто"
    for m in re.finditer(rf"(?<![\w.,])(\d+(?:[.,]\d+)?)(\s?{FACT_UNITS})(?![\w])", t):
        if float(m.group(1).replace(",", ".")) > 0:
            return t[:m.start()] + _num(m.group(1)) + m.group(2) + t[m.end():], "frame_error", f"число {m.group(1)} увеличено в 10 раз"
    return None


def translate_items(items: list[dict]) -> list[dict]:
    """Переводит тексты элементов на английский (tier bulk через glava/llm.py), чтобы получить пакет, где пересказ и цитаты на одном языке."""
    from glava.llm import ask_json
    from .llm_check import pmap

    def tr(it):
        out = dict(it)
        for f in ("text", "breaks_where"):
            if isinstance(it.get(f), str) and it[f].strip():
                r = ask_json('Translate the learning-material sentence into English faithfully (keep numbers, units, symbols, hedges and quantifiers exactly). Return JSON {"text": "..."}.', it[f], tier="bulk", tag="bench:translate")
                if isinstance(r, dict) and isinstance(r.get("text"), str) and r["text"].strip():
                    out[f] = r["text"].strip()
        return out
    return pmap(tr, items, 8, default=None)


def make_mixed(chapter_id: str, out: Path, seed: int = 7, share: float = 0.2, translate: bool = False):
    use_lang(chapter_lang(chapter_id))
    from .self_check import build_package
    run_dir = ROOT / "runs" / chapter_id
    full = build_package(run_dir)
    layer = json.loads((run_dir / "layer.json").read_text(encoding="utf-8"))
    node_pages = {n["id"]: n.get("pages") or [] for n in layer["nodes"]}
    ch = load_chapter(chapter_id)
    rnd = random.Random(seed)
    by = {t: [i for i in full["items"] if i["type"] == t and not i["id"].startswith("T")] for t in CONTROL_MIX}
    claims_by_id = {c["id"]: c for c in by["claim"]}
    good_claims = [c for c in by["claim"] if c.get("quote") and c.get("page_pdf") and check_claim(ch, c["quote"], c["page_pdf"])["ok"] and len(c["text"]) > 40]
    gc = {c["id"]: c for c in good_claims}
    # строки сценария с опорой на хорошее утверждение; факты и аналогии — по порядку случайно
    lines = [l for l in by["script_line"] if l.get("claim_ref") in gc]
    rnd.shuffle(lines)
    lines = lines[: CONTROL_MIX["script_line"]]
    facts = by["scene_fact"][:]
    rnd.shuffle(facts)
    facts = facts[: CONTROL_MIX["scene_fact"]]
    anas = [a for a in by["analogy"] if (a.get("breaks_where") or "").strip()]
    rnd.shuffle(anas)
    anas = anas[: CONTROL_MIX["analogy"]]
    need = {l["claim_ref"] for l in lines}
    rest = [c for c in good_claims if c["id"] not in need]
    rnd.shuffle(rest)
    chosen_claims = [gc[i] for i in sorted(need)] + rest[: max(0, CONTROL_MIX["claim"] - len(need))]

    if translate:  # английский пересказ над английским источником; цитаты не трогаем
        allsel = chosen_claims + anas + lines + facts
        tr = translate_items(allsel)
        trm = {t["id"]: t for t in tr if t}
        chosen_claims = [trm.get(c["id"], c) for c in chosen_claims]
        anas = [trm.get(c["id"], c) for c in anas]
        lines = [trm.get(c["id"], c) for c in lines]
        facts = [trm.get(c["id"], c) for c in facts]
        gc = {c["id"]: trm.get(c["id"], c) for c in good_claims}
    cats = ["sign_flip", "overgeneralization", "lost_condition", "wrong_scaling", "measured_vs_inferred"]
    errors: dict[str, dict] = {}
    items_out: dict[str, dict] = {}

    # утверждения: ≈20% (квота по кругу из категорий, где есть подходящие)
    n_claim_err = round(len(chosen_claims) * share)
    cand = {c_: [] for c_ in cats}
    for c in chosen_claims:
        if c["id"] in need:
            continue  # опорные для строк сценария не портим: порча только в самой строке
        for cat in cats:
            r = corrupt_text(c["text"], c["quote"], cat)
            if r and r[0] != c["text"]:
                cand[cat].append((c, cat, r))
    used = set()
    while len(errors) < n_claim_err and any(cand.values()):
        for cat in sorted(cats, key=lambda k: sum(1 for e in errors.values() if e["category"] == k)):
            while cand[cat] and cand[cat][0][0]["id"] in used:
                cand[cat].pop(0)
            if cand[cat] and len(errors) < n_claim_err:
                c, _, r = cand[cat].pop(0)
                used.add(c["id"])
                items_out[c["id"]] = dict(c, text=r[0], kind="observation" if cat == "measured_vs_inferred" else c.get("kind"))
                errors[c["id"]] = {"target": c["id"], "category": cat, "severity": SEVERITY[cat], "correct": c["text"], "pages": [c["page_pdf"]], "quote": c["quote"], "note": r[1]}
    # аналогии
    for k, a in enumerate(anas[: max(1, round(len(anas) * share))]):
        items_out[a["id"]] = dict(a, breaks_where=corrupt_analogy(a, k))
        errors[a["id"]] = {"target": a["id"], "category": "analogy_without_limit", "severity": "significant", "correct": a.get("breaks_where", ""),
                           "pages": node_pages.get(a["node_id"]) or [], "note": "граница аналогии заменена на «точная, для любых»"}
    # строки сценария
    n_line_err = max(1, round(len(lines) * share))
    done = 0
    for idx, l in enumerate(lines):
        if done >= n_line_err:
            break
        ref = gc[l["claim_ref"]]
        for cat in cats[idx % len(cats):] + cats[: idx % len(cats)]:
            r = corrupt_text(l["text"], ref["quote"], cat)
            if r and r[0] != l["text"]:
                items_out[l["id"]] = dict(l, text=r[0])
                errors[l["id"]] = {"target": l["id"], "category": cat, "severity": SEVERITY[cat], "correct": l["text"], "pages": [ref["page_pdf"]], "quote": ref["quote"], "note": r[1]}
                done += 1
                break
    # факты сцены
    n_fact_err = max(1, round(len(facts) * share))
    done = 0
    for f in facts:
        if done >= n_fact_err:
            break
        r = corrupt_fact(f)
        if r:
            items_out[f["id"]] = dict(f, text=r[0])
            errors[f["id"]] = {"target": f["id"], "category": r[1], "severity": "critical" if "вверх" in r[0] else "significant", "correct": f["text"],
                               "pages": node_pages.get(f.get("node_id")) or [], "note": r[2]}
            done += 1

    all_items = [items_out.get(i["id"], i) for i in chosen_claims + anas + lines + facts]
    order = {"claim": 0, "analogy": 1, "script_line": 2, "scene_fact": 3}
    all_items.sort(key=lambda i: (order[i["type"]], i["id"]))
    spec_ids = {f["video_id"] for f in facts + lines}
    pkg = {"package_id": f"bench_mixed_{chapter_id}", "chapter": chapter_id, "chapter_file": Path(ch.meta["md"]).name, "language": chapter_lang(chapter_id), "items": all_items,
           "scenes": [s_ for s_ in full["scenes"] if s_["video_id"] in spec_ids], "frames": []}
    key = {"package_id": pkg["package_id"], "errors": list(errors.values()), "tricky_true": []}
    out.mkdir(parents=True, exist_ok=True)
    (out / "package.json").write_text(json.dumps(pkg, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "key.json").write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding="utf-8")
    cnt = {t: sum(1 for i in all_items if i["type"] == t) for t in CONTROL_MIX}
    ecnt = {t: sum(1 for e in errors.values() if next(i for i in all_items if i["id"] == e["target"])["type"] == t) for t in CONTROL_MIX}
    print(f"{chapter_id} (mixed): элементов {cnt}, ошибок {ecnt} (всего {len(errors)}) → {out}")
    return pkg, key


def type_of(target: str) -> str:
    return {"C": "утверждения", "A": "аналогии", "L": "строки сценария", "S": "факты сцены", "F": "кадры"}.get(target[:1], "другое")


# ====================== режим --subtle: тонкую ошибку вносит модель, код проверяет ======================

SUBTLE_SYSTEM = """Ты вносишь в учебный пересказ ОДНУ тонкую, правдоподобную ошибку заданной категории, чтобы проверить рецензента. Даны точная цитата автора и пересказ.
Верни JSON: {"original_fragment": "...", "changed_fragment": "..."} — минимальная замена: original_fragment ДОСЛОВНО встречается в пересказе, changed_fragment его заменяет.
Требования:
- пересказ после замены должен ПРОТИВОРЕЧИТЬ цитате автора или выходить за неё именно в заданной категории, но звучать естественно и грамматично;
- не меняй больше одной мысли; не добавляй пометок об ошибке;
- НЕ используй слова-маркеры: всегда, любой, любая, любые, любого, никогда, все, во всех, только, лишь, целиком, полностью, однозначно, обязательно (для английского текста: always, any, every, never, all, only, entirely, completely, strictly, necessarily); пиши на языке пересказа; для переворота не используй простые антонимы выше/ниже, растёт/падает, больше/меньше — переверни зависимость иначе
  (причина и следствие, прямая и обратная связь, «не превышает» → «превышает», «убывает по мере» → «нарастает по мере» и т. п.);
- original_fragment должен присутствовать и в цитате автора (то место, которое ты искажаешь), changed_fragment — нет.
Категории: sign_flip — переворот направления или признака; overgeneralization — снять границы применимости, не используя слов-маркеров (например «независимо от условий», «в принципе для данного класса объектов»);
lost_condition — выбросить условие применимости (changed_fragment может быть пустым); wrong_scaling — неверная степень, пропорциональность или порядок величины в числе;
measured_vs_inferred — вывод выдать за прямое измерение или наоборот, без слов «прямым измерением» (например «экспериментально зафиксировано»)."""


def subtle_corrupt(text: str, quote: str, cat: str, kind: str | None):
    """Тонкая порча моделью + механическая проверка: фрагмент есть в пересказе и в цитате, замена нет в цитате, текст изменился."""
    from glava.chapter import norm
    from glava.llm import ask_json
    r = ask_json(SUBTLE_SYSTEM, f"Категория: {cat}\nЦитата автора: {quote}\nПересказ: {text}", tier="bulk", tag=f"bench:subtle:{cat}")
    if not isinstance(r, dict):
        return None
    o, c = r.get("original_fragment"), r.get("changed_fragment")
    if not isinstance(o, str) or not isinstance(c, str) or not o.strip() or o not in text or o == c:
        return None
    if norm(o) not in norm(quote) or (c.strip() and norm(c) in norm(quote)):
        return None
    new = text.replace(o, c, 1)
    return (new, f"[тонкая] «{o}» → «{c}»") if new != text and len(new) > 20 else None


def make_subtle(chapter_id: str, out: Path, n: int = 90, seed: int = 7, error_share: float = 0.2, n_tricky: int = 10):
    use_lang(chapter_lang(chapter_id))
    from .llm_check import pmap
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
    # по 3–4 попытки на утверждение-кандидата: берём первые, где модель дала валидную порчу
    plan = [(pool[i], cats[i % len(cats)]) for i in range(min(len(pool), n_err * 4))]
    results = pmap(lambda pc: subtle_corrupt(pc[0]["text"], pc[0]["quote"], pc[1], pc[0].get("kind")) if (pc[1] != "measured_vs_inferred" or pc[0].get("kind") == "inference") else None,
                   plan, 8, default=None)
    errors, used, per = {}, set(), {c: 0 for c in cats}
    quota = {c: n_err // len(cats) + (1 if i < n_err % len(cats) else 0) for i, c in enumerate(cats)}
    for (c, cat), r in zip(plan, results):
        if r and per[cat] < quota[cat] and c["id"] not in used:
            per[cat] += 1
            used.add(c["id"])
            errors[c["id"]] = (c, cat, r[0], r[1])
    # недобор квоты добираем из любых категорий
    for (c, cat), r in zip(plan, results):
        if len(errors) >= n_err:
            break
        if r and c["id"] not in used:
            used.add(c["id"])
            errors[c["id"]] = (c, cat, r[0], r[1])
    tricky = []
    for c in pool:
        if len(tricky) >= n_tricky:
            break
        if c["id"] in used:
            continue
        t, q = c["text"].lower(), c["quote"].lower()
        hit = [a_ for a_ in ABSOLUTES if a_ in t and a_ in q]
        if hit:
            tricky.append((c, hit[0].strip()))
            used.add(c["id"])
    normal = [c for c in pool if c["id"] not in used][: max(0, n - len(errors) - len(tricky))]
    entries = [("err", e[0], e) for e in errors.values()] + [("tricky", c, h) for c, h in tricky] + [("ok", c, None) for c in normal]
    order = {n_["id"]: i for i, n_ in enumerate(layer["nodes"])}
    entries.sort(key=lambda e: (order[e[1]["node"]], e[1]["id"]))
    items, key_err, key_tr = [], [], []
    for i, (kind, c, extra) in enumerate(entries, 1):
        iid = f"C{i:03d}"
        text, kk = c["text"], c.get("kind")
        if kind == "err":
            _, cat, text, note = extra
            kk = "observation" if cat == "measured_vs_inferred" else kk
            key_err.append({"target": iid, "category": cat, "severity": SEVERITY[cat], "correct": c["text"], "pages": [c["page_pdf"]], "quote": c["quote"], "note": note})
        elif kind == "tricky":
            key_tr.append({"target": iid, "why_tricky": f"формулировка «{extra}» есть и в тексте автора", "pages": [c["page_pdf"]]})
        items.append({"id": iid, "type": "claim", "node_id": c["node"], "text": text, "kind": kk, "page_pdf": c["page_pdf"], "quote": c["quote"], "orig_id": c["id"]})
    pkg = {"package_id": f"bench_subtle_{chapter_id}", "chapter": chapter_id, "chapter_file": Path(ch.meta["md"]).name, "language": chapter_lang(chapter_id), "items": items, "scenes": [], "frames": []}
    key = {"package_id": pkg["package_id"], "errors": key_err, "tricky_true": key_tr}
    out.mkdir(parents=True, exist_ok=True)
    (out / "package.json").write_text(json.dumps(pkg, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "key.json").write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding="utf-8")
    by = {c: sum(1 for e in key_err if e["category"] == c) for c in cats}
    print(f"{chapter_id} (subtle): пакет {len(items)} утверждений, ошибок {len(key_err)} {by}, «верных, но неожиданных» {len(key_tr)} → {out}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("chapter_id")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--n", type=int, default=90)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--mixed", action="store_true", help="все типы элементов (утверждения, аналогии, строки сценария, факты сцены) в пропорции контрольного пакета")
    ap.add_argument("--translate-en", action="store_true", help="для английской главы: перевести пересказ на английский моделью перед порчей")
    ap.add_argument("--subtle", action="store_true", help="тонкую ошибку вносит модель (tier bulk через glava/llm.py); код проверяет противоречие цитате")
    a = ap.parse_args(argv)
    if a.mixed:
        make_mixed(a.chapter_id, Path(a.out), a.seed, translate=a.translate_en)
    elif a.subtle:
        make_subtle(a.chapter_id, Path(a.out), a.n, a.seed)
    else:
        make(a.chapter_id, Path(a.out), a.n, a.seed)


if __name__ == "__main__":
    sys.exit(main())
