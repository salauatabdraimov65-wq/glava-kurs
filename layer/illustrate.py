"""Иллюстрации шагов кодом (SVG) → runs/<chapter>/illustrations/*.svg + illustrations.json

    uv run python -m layer.illustrate <chapter_id>

Не меньше одной на шаг. Без генерации: схема строится из полей узла, поэтому ничего не «плывёт».
  decision_on_data → «признаки → правило → вывод» (+ контрпример)
  procedure        → пронумерованные шаги процедуры
  mechanism        → цепочка «причина → следствие» (по стрелкам или фразам)
Метка происхождения «схема» нарисована на самой картинке; цвета — из стайлгайда домена.
"""
from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

from glava.chapter import ROOT

W = 760
PAD = 24
FONT = "Segoe UI, -apple-system, Helvetica, Arial, sans-serif"


def wrap(text: str, width: int) -> list[str]:
    words, lines, cur = (text or "").split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur); cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines or [""]


def esc(s):
    return html.escape(s or "", quote=True)


class Svg:
    def __init__(self, style):
        self.parts, self.y, self.s = [], PAD + 44, style

    def text(self, x, y, s, size=16, weight=400, color=None, anchor="start"):
        self.parts.append(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{color or self.s["ink"]}" '
                          f'text-anchor="{anchor}" font-family="{FONT}">{esc(s)}</text>')

    def box(self, x, w, lines, fill, stroke, title=None, size=16):
        h = 16 + (22 if title else 0) + 22 * len(lines)
        self.parts.append(f'<rect x="{x}" y="{self.y}" width="{w}" height="{h}" rx="10" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>')
        ty = self.y + 26
        if title:
            self.text(x + 14, ty, title, size=13, weight=700, color=stroke); ty += 22
        for ln in lines:
            self.text(x + 14, ty, ln, size=size); ty += 22
        self.y += h
        return h

    def arrow(self, label=None):
        x = W / 2
        self.parts.append(f'<line x1="{x}" y1="{self.y + 4}" x2="{x}" y2="{self.y + 26}" stroke="{self.s["muted"]}" stroke-width="2"/>'
                          f'<polygon points="{x - 6},{self.y + 22} {x + 6},{self.y + 22} {x},{self.y + 32}" fill="{self.s["muted"]}"/>')
        if label:
            self.text(x + 14, self.y + 22, label, size=13, color=self.s["muted"])
        self.y += 38

    def render(self, title, badge, badge_color, foot):
        top = (f'<rect x="0" y="0" width="{W}" height="{{H}}" rx="14" fill="{self.s["bg"]}"/>'
               f'<rect x="{PAD}" y="{PAD}" width="{len(badge) * 10 + 20}" height="24" rx="6" fill="{badge_color}"/>')
        hdr = (f'<text x="{PAD + 10}" y="{PAD + 17}" font-size="13" font-weight="700" fill="#fff" font-family="{FONT}">{esc(badge.upper())}</text>'
               f'<text x="{PAD + len(badge) * 10 + 32}" y="{PAD + 17}" font-size="15" font-weight="700" fill="{self.s["ink"]}" font-family="{FONT}">{esc(title)}</text>')
        self.y += 10
        fl = wrap(foot, 92)
        for i, ln in enumerate(fl):
            self.text(PAD, self.y + 16 + i * 18, ln, size=12, color=self.s["muted"])
        H = int(self.y + 16 + 18 * len(fl) + PAD / 2)
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img">'
                + top.replace("{H}", str(H)) + hdr + "".join(self.parts) + "</svg>")


def palette(style_path: Path) -> dict:
    st = json.loads(style_path.read_text(encoding="utf-8")) if style_path.exists() else {}
    prov = st.get("provenance", {})
    return {"bg": "#FBFAF7", "ink": "#1C232B", "muted": "#5B6673", "box": "#FFFFFF", "edge": "#9AA6B2",
            "accent": st.get("measurement", {}).get("color", "#C9A227"), "good": "#2E8B57", "warn": "#B03A2E",
            "badge": prov.get("схема", "#8E44AD")}


def split_chain(text: str) -> list[str]:
    parts = [p.strip(" .;") for p in re.split(r"\s*(?:→|->|⇒)\s*", text or "") if p.strip(" .;")]
    if len(parts) >= 2:
        return parts[:6]
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip()]
    return sents[:5]


def draw(node: dict, pal: dict) -> tuple[str, str, str, str]:
    """→ (svg, вид, подпись, alt)"""
    s = Svg(pal)
    inner = W - 2 * PAD
    d = node.get("decision_on_data") or {}
    pages = node.get("pages") or []
    src = f"по утверждениям узла, стр. {pages[0]}–{pages[-1]}" if len(pages) > 1 else f"по утверждениям узла, стр. {pages[0] if pages else '?'}"
    if d.get("rule"):
        feats = d.get("features") or []
        s.box(PAD, inner, [f"• {ln}" if i == 0 else f"  {ln}" for f in feats[:5] for i, ln in enumerate(wrap(f, 70))] or [""],
              pal["box"], pal["edge"], "ПРИЗНАКИ В ДАННЫХ")
        s.arrow()
        s.box(PAD, inner, wrap(d["rule"], 72), "#FFF8E1", pal["accent"], "ПРАВИЛО АВТОРА")
        s.arrow()
        s.box(PAD, inner, wrap(d.get("conclusion", ""), 72), "#EAF5EE", pal["good"], "ВЫВОД")
        if d.get("counterexample"):
            s.y += 12
            s.box(PAD, inner, wrap(d["counterexample"], 72), "#FBEDEC", pal["warn"], "КОГДА ПРАВИЛО ОШИБАЕТСЯ")
        kind = "decision"
        cap = f"Решение на данных: признаки → правило → вывод. Схема {src}."
        alt = f"Схема: признаки {', '.join(feats[:4])}; правило: {d['rule']}; вывод: {d.get('conclusion', '')}"
    elif node.get("procedure"):
        steps = node["procedure"][:7]
        for i, st in enumerate(steps, 1):
            s.box(PAD, inner, wrap(st, 72), pal["box"], pal["edge"], f"ШАГ {i}")
            if i < len(steps):
                s.arrow()
        kind = "procedure"
        cap = f"Порядок действий специалиста. Схема {src}."
        alt = "Схема процедуры: " + "; ".join(f"{i}. {t}" for i, t in enumerate(steps, 1))
    else:
        chain = split_chain(node["layers"]["mechanism"]["text"]) or [node["layers"]["main"]["text"]]
        for i, st in enumerate(chain):
            s.box(PAD, inner, wrap(st, 72), pal["box"] if i < len(chain) - 1 else "#EAF5EE",
                  pal["edge"] if i < len(chain) - 1 else pal["good"], "ПРИЧИНА" if i == 0 else ("СЛЕДСТВИЕ" if i == len(chain) - 1 else None))
            if i < len(chain) - 1:
                s.arrow()
        kind = "mechanism"
        cap = f"Как это работает: цепочка «причина → следствие». Схема {src}."
        alt = "Схема механизма: " + " → ".join(chain)
    svg = s.render(node["title"], "схема", pal["badge"], "Схема построена кодом из учебного слоя, не рисунок книги. " + src.capitalize() + ".")
    return svg, kind, cap, alt


def illustrate(chapter: str):
    run = ROOT / "runs" / chapter
    layer = json.loads((run / "layer.json").read_text(encoding="utf-8"))
    pal = palette(ROOT / "domain" / layer.get("domain", "geology") / "styleguide.json")
    out = run / "illustrations"
    out.mkdir(exist_ok=True)
    items = []
    for n in layer["nodes"]:
        svg, kind, cap, alt = draw(n, pal)
        name = f"{n['id'].split('.', 1)[-1]}.svg"
        (out / name).write_text(svg, encoding="utf-8")
        items.append({"id": f"ill.{n['id'].split('.', 1)[-1]}", "step_id": n["id"], "node_id": n["id"],
                      "file": f"illustrations/{name}", "frame_class": "B", "origin": "code_from_model", "label": "схема",
                      "caption": cap, "alt": alt[:600], "source": "учебный слой, " + ", ".join(f"стр. {p}" for p in n["pages"][:3]),
                      "claim_ids": [c["id"] for c in n["claims"][:5]], "prompt": None, "kind": kind})
    doc = {"chapter": chapter, "illustrations": items}
    (run / "illustrations.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    from collections import Counter
    print(f"{chapter}: {len(items)} иллюстраций на {len(layer['nodes'])} шагов, виды {dict(Counter(i['kind'] for i in items))}")


if __name__ == "__main__":
    for c in sys.argv[1:]:
        illustrate(c)
