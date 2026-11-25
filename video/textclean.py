"""Видимый текст ролика по ТЗ: подписи и субтитры без формул и греческих букв.

Любая надпись кадра (заголовок, плашка, подписи слоёв, кривых, рамок, выносок, финальная карточка, субтитры)
проходит через visible(). Что заменено — копится в REPORT и попадает в паспорт (audio_check не трогаем,
отдельное поле visual_text_check), чтобы человек увидел и поправил спецификацию.
"""
from __future__ import annotations

import re

GREEK = {"α": "альфа", "β": "бета", "γ": "гамма", "δ": "дельта", "Δ": "дельта", "ε": "эпсилон",
         "η": "эта", "θ": "тета", "κ": "каппа", "ϰ": "каппа", "λ": "лямбда", "μ": "мю", "ν": "ню",
         "π": "пи", "ρ": "ро", "σ": "сигма", "τ": "тау", "φ": "фи", "χ": "хи", "ψ": "пси", "ω": "омега",
         "Ω": "омега", "Σ": "сумма", "Φ": "фи", "Ψ": "пси", "Λ": "лямбда", "Θ": "тета", "Γ": "гамма"}
GREEK_RE = re.compile("[" + "".join(GREEK) + "]")
# знаки формул; «·» в единицах (Ом·м) и «≈» перед значением формулами не считаем
FORMULA_RE = re.compile(r"[=^_\\{}$√∑∫∂±×÷]|\b[a-zA-Z]\s*/\s*[a-zA-Z]\b|\b\w+\s*[<>]\s*\w+\b")

REPORT: list[dict] = []


def problems(s: str) -> list[str]:
    out = []
    if GREEK_RE.search(s or ""):
        out.append("греческая буква")
    if FORMULA_RE.search(s or ""):
        out.append("знак формулы")
    return out


def visible(s: str, where: str = "") -> str:
    """Текст для кадра: греческие буквы — словами, знаки формул убраны."""
    if not s:
        return s
    p = problems(s)
    if not p:
        return s
    out = GREEK_RE.sub(lambda m: GREEK[m.group(0)], s)
    out = re.sub(r"[=^_\\{}$√∑∫∂±×÷]", " ", out)
    out = re.sub(r"\s+", " ", out).strip()
    rec = {"where": where, "was": s, "now": out, "why": p}
    if rec not in REPORT:
        REPORT.append(rec)
    return out
