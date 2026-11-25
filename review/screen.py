"""Отсев: замечание остаётся, только если его подтверждают ≥2 независимых сигнала (код, bulk, strong).

Сигналы: `code` (бесплатные проверки), `llm:bulk` и `llm:strong` (одинаковый промпт, спрашиваются отдельно, без подсказок друг другу).
Согласие считается по семейству категорий: близкие категории («условие/оговорка/обобщение», «знак/масштаб») — одно семейство.
"""
from __future__ import annotations

from glava.chapter import Chapter, find_quote

FAMILY = {"overgeneralization": "scope", "lost_condition": "scope", "missing_caveat": "scope",
          "sign_flip": "direction", "wrong_scaling": "direction"}
SEV_W = {"critical": 3, "significant": 2, "minor": 1}
MIN_CODE_CONF = 0.4


def family(cat: str) -> str:
    return FAMILY.get(cat, cat)


def signal_of(c: dict) -> str | None:
    s = c.get("source", "")
    if s == "code":
        return "code" if c["confidence"] >= MIN_CODE_CONF else None
    return s if s in ("llm:bulk", "llm:strong") else None


def fix_severity(cat: str, text: str, model_sev: str) -> str:
    """critical — меняет вывод/физику; significant — потеряно условие/оговорка, сверхобобщение; minor — неточность."""
    if cat in ("sign_flip", "wrong_mechanism"):
        return "critical"
    if cat in ("lost_condition", "missing_caveat", "overgeneralization"):
        return "significant"
    if cat == "frame_error" and "метк" in text.lower() and "происхожд" in text.lower():
        return "critical"
    if cat == "analogy_without_limit":
        return "significant"
    return model_sev


def aggregate(cands: list[dict], ch: Chapter, min_signals: int = 2, agree: str = "family") -> list[dict]:
    """agree=\"family\" — согласие в одном семействе категорий (по умолчанию); \"target\" — по цели, категория не важна (больше находок и ложных тревог)."""
    fam_of = family if agree == "family" else (lambda cat: "any")
    by_t: dict[str, list[dict]] = {}
    for c in cands:
        if signal_of(c):
            by_t.setdefault(c["target"], []).append(c)
    out = []
    for tgt, cs in by_t.items():
        fams: dict[str, list[dict]] = {}
        for c in cs:
            fams.setdefault(fam_of(c["category"]), []).append(c)
        best = None
        for fam, group in fams.items():
            sigs = {signal_of(c) for c in group}
            key = (len(sigs), "llm:strong" in sigs)
            if best is None or key > best[0]:
                best = (key, fam, group, sigs)
        (_, _), fam, group, sigs = best[0], best[1], best[2], best[3]
        if len(sigs) < min_signals:
            continue
        # основа замечания: сначала strong, потом bulk, потом код; fix/страница только с подтверждённой цитатой
        order = {"llm:strong": 0, "llm:bulk": 1, "code": 2}
        group = sorted(group, key=lambda c: (order[signal_of(c)], -c["confidence"]))
        base = dict(group[0])
        for c in group:
            pages = find_quote(ch, c["quote"]) if c.get("quote") else []
            if pages and c.get("fix"):
                base.update(fix=c["fix"], quote=c["quote"], page_pdf=pages[0] if c.get("page_pdf") not in pages else c["page_pdf"],
                            problem=c["problem"], category=c["category"])
                break
        if base.get("page_pdf") is None:
            for c in group:
                if c.get("page_pdf") is not None:
                    base["page_pdf"] = c["page_pdf"]
                    break
        base["severity"] = fix_severity(base["category"], base["problem"] + " " + base["fix"], base["severity"])
        base["signals"] = sorted(sigs)
        base["confidence"] = round(sum(c["confidence"] for c in group) / len(group), 2)
        out.append(base)
    return out


def rank(cs: list[dict]) -> list[dict]:
    return sorted(cs, key=lambda c: (-len(c["signals"]), -SEV_W[c["severity"]], -c["confidence"]))
