"""Графики книги, перерисованные по формулам главы (origin = redrawn_from_book, метка «симуляция»).

    uv run python -m layer.redraw <chapter_id> [...]

Модель предлагает — код проверяет:
  1) для каждого рисунка-графика (описание распознавания FORMAT … PLOT/CHART) сильная модель решает, построен ли он
     по формуле из текста главы, и если да — даёт формулу как выражение, ось x, набор кривых и ожидаемую форму;
  2) код проверяет: формула (цитата) дословно есть в главе на этой или соседней странице, подпись «Рис. N» есть в тексте,
     выражение состоит только из разрешённых функций, значения конечны, форма кривых (растёт / убывает) совпадает
     с описанием рисунка, кривые различимы;
  3) код считает и рисует SVG (matplotlib), на картинке — метка «симуляция» и источник «перерисовано по рис. N, стр. M».
Рисунки с замеренными точками (данные эксперимента, каротаж, керн) не перерисовываются: точки на них — не формула.
Безразмерные оси подписываются как условные единицы, если в главе нет чисел.
"""
from __future__ import annotations

import ast
import json
import math
import re
import sys
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from glava.chapter import ROOT, check_claim, load_chapter, norm
from glava.llm import ask_json

SYS = """Ты инженер-методист. Дан рисунок из книги (его описание сделано программой распознавания, а не автором) и текст главы рядом с ним.
Реши: построен ли этот график ПО ФОРМУЛЕ, которая есть в тексте главы (расчётные кривые, теоретическая функция)?
Если на рисунке замеренные точки, каротажные диаграммы, керн, схема без функции, или формулы в тексте нет — {"redraw": false, "why": "…"}.
Если да — верни JSON:
{"redraw": true,
 "figure_number": "номер рисунка как в подписи, например 23.1",
 "title": "короткий заголовок по-русски, до 9 слов",
 "formula_quote": "ДОСЛОВНЫЙ фрагмент текста главы с этой формулой (как в тексте, с $…$), 10–200 символов",
 "formula_page": номер страницы, где формула,
 "expression": "выражение y через x и параметры на Python: + - * / ** ( ), функции exp, log, sqrt, erf, erfc, Ei, E1, abs; константы pi, euler_gamma",
 "x": {"name": "x", "label": "подпись оси x по-русски с обозначением", "from": число, "to": число, "log": false},
 "y_label": "подпись оси y по-русски с обозначением",
 "params": {"имя": число, ...}  — общие параметры (если в главе нет чисел — безразмерные, например pk=1, pg=0, kappa=1),
 "curves": [{"label": "подпись кривой как на рисунке, например t₁", "params": {"t": число}}] — 1–4 кривые, как на рисунке,
 "shape": "increasing" | "decreasing" | "other" — форма кривых по ОПИСАНИЮ рисунка,
 "dimensionless": true|false — оси в условных единицах (в главе нет конкретных чисел),
 "caption": "что показывает график и что из него следует, 1–2 фразы по-русски, по тексту главы"}
Имена параметров — латиницей (kappa, t, pk, pg, mu, k, h, Q). Ei(z) — интегральная показательная функция (Ei(-x) = -E1(x))."""

FUNCS = {"exp": np.exp, "log": np.log, "ln": np.log, "sqrt": np.sqrt, "abs": np.abs}
CONSTS = {"pi": math.pi, "euler_gamma": 0.5772156649}


def E1(x):
    """Интегральная показательная функция E1(x), x > 0: ряд при x ≤ 1, цепная дробь (Лентц) при x > 1."""
    x = np.asarray(x, dtype=float)
    out = np.full(x.shape, np.nan)
    s = (x > 0) & (x <= 1)
    if s.any():
        xs = x[s]; term = np.ones_like(xs); acc = np.zeros_like(xs)
        for n in range(1, 60):
            term = term * (-xs) / n
            acc += term / n
        out[s] = -CONSTS["euler_gamma"] - np.log(xs) - acc
    b = x > 1
    if b.any():
        xb = x[b]; tiny = 1e-300
        bb = xb + 1.0; c = np.full_like(xb, 1 / tiny); d = 1.0 / bb; h = d.copy()
        for i in range(1, 200):
            an = -i * i; bb = bb + 2.0
            d = 1.0 / (an * d + bb); c = bb + an / c; h = h * c * d
        out[b] = h * np.exp(-xb)
    return out


def Ei(z):
    z = np.asarray(z, dtype=float)
    return np.where(z < 0, -E1(np.where(z < 0, -z, 1.0)), np.nan)   # для z ≥ 0 в подземной гидромеханике не нужна


_erf = np.vectorize(math.erf)
FUNCS.update({"erf": lambda v: _erf(np.asarray(v, dtype=float)), "erfc": lambda v: 1 - _erf(np.asarray(v, dtype=float)),
              "Ei": Ei, "E1": E1})
OK_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Call, ast.Name, ast.Load, ast.Constant,
            ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.USub, ast.UAdd)


def safe_eval(expr: str, env: dict):
    tree = ast.parse(expr.replace("^", "**"), mode="eval")
    for nd in ast.walk(tree):
        if not isinstance(nd, OK_NODES):
            raise ValueError(f"запрещённая конструкция {type(nd).__name__}")
        if isinstance(nd, ast.Call) and not (isinstance(nd.func, ast.Name) and nd.func.id in FUNCS):
            raise ValueError("неизвестная функция")
        if isinstance(nd, ast.Name) and nd.id not in env and nd.id not in FUNCS and nd.id not in CONSTS:
            raise ValueError(f"неизвестное имя {nd.id}")
    with np.errstate(all="ignore"):
        return eval(compile(tree, "<expr>", "eval"), {"__builtins__": {}}, {**FUNCS, **CONSTS, **env})


def compute(spec: dict) -> tuple[np.ndarray, list[np.ndarray]]:
    xs = spec["x"]
    a, b = float(xs["from"]), float(xs["to"])
    if xs.get("log"):
        grid = np.logspace(math.log10(max(a, 1e-12)), math.log10(b), 300)
    else:
        grid = np.linspace(a, b, 300)
        grid[grid == 0] = 1e-9 * (b - a)   # точка 0 для ln/Ei — чуть сдвинута
    ys = []
    for c in spec["curves"]:
        env = {**{k: float(v) for k, v in (spec.get("params") or {}).items()},
               **{k: float(v) for k, v in (c.get("params") or {}).items()}, xs.get("name", "x"): grid}
        ys.append(np.broadcast_to(np.asarray(safe_eval(spec["expression"], env), dtype=float), grid.shape).copy())
    return grid, ys


def verify(ch, spec: dict, fig_page: int) -> list[str]:
    errs = []
    q = (spec.get("formula_quote") or "").strip()
    fp = int(spec.get("formula_page") or fig_page)
    if not q or not any(check_claim(ch, q, p)["ok"] for p in {fp, fig_page, fig_page - 1, fig_page + 1} if p in ch.pages):
        errs.append("формулы нет в тексте главы дословно")
    num = str(spec.get("figure_number") or "").strip()
    near = " ".join(ch.pages.get(p, "") for p in (fig_page - 1, fig_page, fig_page + 1))
    if not num or not re.search(r"Рис\.?\s*" + re.escape(num) + r"(?!\d)", near, re.I):
        errs.append(f"подписи «Рис. {num}» нет рядом со страницей рисунка")
    if not spec.get("curves") or len(spec["curves"]) > 4:
        errs.append("нужно 1–4 кривые")
        return errs
    try:
        grid, ys = compute(spec)
    except Exception as e:
        return errs + [f"выражение не считается: {e}"]
    for c, y in zip(spec["curves"], ys):
        fin = np.isfinite(y)
        if fin.mean() < 0.9:
            errs.append(f"кривая {c.get('label')}: конечных значений {fin.mean():.0%}")
            continue
        d = np.diff(y[fin])
        shape = spec.get("shape")
        if shape == "increasing" and (d < -1e-9 * (np.ptp(y[fin]) + 1)).mean() > 0.05:
            errs.append(f"кривая {c.get('label')} не возрастает, а рисунок описан как возрастающий")
        if shape == "decreasing" and (d > 1e-9 * (np.ptp(y[fin]) + 1)).mean() > 0.05:
            errs.append(f"кривая {c.get('label')} не убывает, а рисунок описан как убывающий")
    if len(ys) > 1 and all(np.nanmax(np.abs(ys[i] - ys[0])) < 1e-3 * (np.nanmax(np.abs(ys[0])) + 1e-9) for i in range(1, len(ys))):
        errs.append("кривые совпадают — параметр кривых не влияет")
    return errs


def render(spec: dict, path, source: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    grid, ys = compute(spec)
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 12, "svg.fonttype": "path"})
    fig, ax = plt.subplots(figsize=(7.6, 4.6), dpi=100)
    colors = ["#1f5f8b", "#c8553d", "#2a7f62", "#8a5a9e"]
    import textwrap
    for i, (c, y) in enumerate(zip(spec["curves"], ys)):
        ax.plot(grid, y, lw=2.4, color=colors[i % 4], label=curve_label(c) or None)
    if spec["x"].get("log"):
        ax.set_xscale("log")
    ax.set_xlabel(spec["x"].get("label", "x") + (" (усл. ед.)" if spec.get("dimensionless") else ""))
    ax.set_ylabel("\n".join(textwrap.wrap(spec.get("y_label", "y") + (" (усл. ед.)" if spec.get("dimensionless") else ""), 34)))
    fig.suptitle("\n".join(textwrap.wrap(spec.get("title", ""), 58)), fontsize=14, x=0.01, ha="left", y=0.985)
    ax.grid(alpha=0.25)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    if any(c.get("label") for c in spec["curves"]):
        ax.legend(frameon=False)
    fig.text(0.99, 0.985, "СИМУЛЯЦИЯ", ha="right", va="top", fontsize=10, color="#555",
             bbox={"boxstyle": "round,pad=0.3", "fc": "#f1efe8", "ec": "#999"})
    fig.text(0.01, 0.01, source, ha="left", va="bottom", fontsize=9, color="#555")
    fig.tight_layout(rect=(0, 0.04, 1, 0.92))
    fig.savefig(path, format="svg")
    plt.close(fig)


def curve_label(c: dict) -> str:
    """Подпись кривой + значения параметров: одинаковые подписи с рисунка (опечатки распознавания) остаются различимыми."""
    vals = ", ".join(f"{k} = {v:g}" if isinstance(v, (int, float)) else f"{k} = {v}" for k, v in (c.get("params") or {}).items())
    lab = (c.get("label") or "").strip()
    return f"{lab} ({vals})" if lab and vals and "=" not in lab else lab or vals


PLOT_RE = re.compile(r"PLOT|CHART|GRAPH|ГРАФИК", re.I)


def owner_node(layer: dict, page: int, formula_page: int) -> dict:
    """Шаг, где лежит формула (по страницам утверждений), иначе — где рисунок."""
    def score(n, p):
        return sum(1 for c in n["claims"] if c.get("page_pdf") == p)
    for p in (formula_page, page):
        best = max(layer["nodes"], key=lambda n: score(n, p))
        if score(best, p):
            return best
    return min(layer["nodes"], key=lambda n: min(abs(x - page) for x in (n.get("pages") or [10**6])))


def redraw(chapter: str) -> int:
    ch = load_chapter(chapter)
    run = ROOT / "runs" / chapter
    layer = json.loads((run / "layer.json").read_text(encoding="utf-8"))
    pages = set(ch.pages)
    figs = [(p, note) for p, notes in sorted(ch.figure_notes.items()) if p in pages for note in notes if PLOT_RE.search(note[:200])]

    def ask(item):
        p, note = item
        ctx = "\n\n".join(f"[страница {q}]\n{ch.pages[q]}" for q in (p - 1, p) if q in ch.pages)
        user = f"ОПИСАНИЕ РИСУНКА (распознавание, стр. {p}):\n{note[:2500]}\n\nТЕКСТ ГЛАВЫ:\n{ctx[:9000]}"
        return p, ask_json(SYS, user, tier="strong", tag=f"redraw:{chapter}", effort="low")

    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(ask, figs))
    outdir = run / "illustrations"
    outdir.mkdir(exist_ok=True)
    ill_p = run / "illustrations.json"
    ill = json.loads(ill_p.read_text(encoding="utf-8")) if ill_p.exists() else {"chapter": chapter, "illustrations": []}
    ill["illustrations"] = [i for i in ill["illustrations"] if i.get("origin") != "redrawn_from_book"]
    log, done, seen = [], 0, set()
    for p, spec in res:
        if not spec.get("redraw"):
            log.append({"page": p, "redraw": False, "why": spec.get("why")}); continue
        num = str(spec.get("figure_number") or "").strip()
        if num in seen:
            continue
        errs = verify(ch, spec, p)
        log.append({"page": p, "figure": num, "ok": not errs, "errors": errs})
        if errs:
            continue
        seen.add(num)
        fpage = int(spec.get("formula_page") or p)
        src = f"Перерисовано по рис. {num}, стр. {p}; рассчитано кодом по формуле главы, стр. {fpage}"
        fname = f"redrawn_{re.sub(r'[^0-9A-Za-z]+', '_', num)}.svg"
        render(spec, outdir / fname, src)
        node = owner_node(layer, p, fpage)
        q = norm(spec["formula_quote"])
        cids = [c["id"] for c in node["claims"] if q[:40] in norm(c.get("quote", "")) or c.get("page_pdf") == fpage][:4]
        curves = "; ".join(curve_label(c) for c in spec["curves"])
        ill["illustrations"].append({
            "id": f"fig.{fname[:-4]}", "step_id": node["id"], "node_id": node["id"], "file": f"illustrations/{fname}",
            "frame_class": "B", "origin": "redrawn_from_book", "label": "симуляция",
            "caption": f"{spec.get('caption', '')} Рис. {num} книги, перерисованный расчётом: {spec['expression']}"
                       + (f"; {curves}" if curves else "") + (". Оси в условных единицах." if spec.get("dimensionless") else "."),
            "alt": f"График: {spec.get('title', '')}. По оси x — {spec['x'].get('label')}, по оси y — {spec.get('y_label')}. "
                   f"Кривые: {'; '.join(curve_label(c) for c in spec['curves'])}.",
            "source": src, "claim_ids": cids, "prompt": None,
            "formula_quote": spec["formula_quote"], "expression": spec["expression"], "by_model": True})
        done += 1
    ill_p.write_text(json.dumps(ill, ensure_ascii=False, indent=1), encoding="utf-8")
    (run / "redraw_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{chapter}: графиков-кандидатов {len(figs)}, перерисовано по формулам {done}")
    for x in log:
        print("  ", x)
    return done


if __name__ == "__main__":
    for c in sys.argv[1:]:
        redraw(c)
