"""Шаблон «pressure_diffusion» (тип Ф — формализм, модель; М — механизм).

Одна модель состояния — модельное время t — управляет всеми видами сразу:
  • вид сверху: воронка давления вокруг скважины (цвет — понижение давления);
  • профиль давления p(r) — кривые для зафиксированных моментов и текущая;
  • забойное давление от логарифма времени — кривая «пишется» до текущего t (как каротаж за прибором).
Давление не рисуется руками, оно считается по формуле главы:
  radial, ei   — Δp(r,t) = Q·μ / (4π·k·h) · E1(r² / 4κt)     (основная формула упругого режима)
  linear, erfc — Δp(x,t) = Δp0 · erfc(x / 2√(κt))            (галерея с постоянным давлением)

params:
  geometry: "radial"|"linear"; solution: "ei"|"erfc"; p0_MPa; kappa_m2_s; q_m3_s; mu_Pa_s; k_m2; h_m; r_w_m;
  dp_MPa (для linear); x_max_m; times_s [моменты для кривых]; views ["map","profile","bottomhole_vs_log_t"]
action (в beat): time_to: t (с) — модельное время идёт туда за фразу (по логарифму);
  show_curve_at: t — зафиксировать профиль момента t; mark_x: м — вертикальная отметка на профиле;
  show_view: имя вида — подсветить вид; clear_marks: true
"""
from __future__ import annotations

import copy
import math

import numpy as np

from ..draw import SDraw, dashed_polyline, fit, line_h, text_block
from ..style import font, hex_rgb, mix
from ..textclean import visible

NAME = "pressure_diffusion"
SUMMARY = ("Формализм и механизм упругого режима (типы Ф, М): давление вокруг скважины или у галереи во времени, "
           "посчитанное по формуле главы. Три синхронных вида: воронка сверху, профиль p(r), забойное давление от "
           "логарифма времени. Подходит, когда суть узла — как и с какой скоростью расходится возмущение давления.")
GUIDE = """params: geometry ("radial"|"linear"), solution ("ei"|"erfc"), p0_MPa (10–60), kappa_m2_s (0.1–5),
  q_m3_s, mu_Pa_s, k_m2, h_m, r_w_m (≈0.1), dp_MPa (для linear), x_max_m (10–500), times_s [3 момента, растущих в 3–10 раз],
  views ["map","profile","bottomhole_vs_log_t"].
action: time_to (с), show_curve_at (с), mark_x (м), show_view, clear_marks."""

EULER = 0.5772156649015329


def e1(x):
    """Интегральная показательная функция E1(x) = −Ei(−x), x > 0 (Абрамовиц–Стиган 5.1.11 и 5.1.56)."""
    x = np.asarray(x, dtype=float)
    out = np.empty_like(x)
    small = x <= 1.0
    xs = np.clip(x[small], 1e-300, None)
    s, term = np.zeros_like(xs), np.ones_like(xs)
    for n in range(1, 40):
        term = term * (-xs) / n
        s = s - term / n
    out[small] = -EULER - np.log(xs) + s
    xl = x[~small]
    a1, a2, b1, b2 = 2.334733, 0.250621, 3.330657, 1.681534
    out[~small] = (xl * xl + a1 * xl + a2) / (xl * xl + b1 * xl + b2) / (xl * np.exp(xl))
    return out


def ease(u):
    u = min(max(u, 0.0), 1.0)
    return u * u * (3 - 2 * u)


def fmt_num(v: float, nd: int = 2) -> str:
    if abs(v) >= 1000:
        s = f"{v:,.0f}".replace(",", " ")
    else:
        s = f"{v:.{nd}f}"
        if "." in s:
            s = s.rstrip("0").rstrip(".")
    return s.replace(".", ",").replace("-", "−")


def fmt_time(t: float) -> str:
    if t < 120:
        return f"{fmt_num(t, 0)} с"
    if t < 7200:
        return f"{fmt_num(t / 60, 0)} мин"
    if t < 2 * 86400:
        return f"{fmt_num(t / 3600, 1)} ч"
    return f"{fmt_num(t / 86400, 1)} сут"


class PressureDiffusion:
    name = NAME

    def __init__(self, params: dict, style: dict):
        p = self.p = params
        self.style = style
        self.geom = p.get("geometry", "radial")
        self.p0 = float(p.get("p0_MPa", 20))
        self.kappa = float(p.get("kappa_m2_s", 1.0))
        self.rw = float(p.get("r_w_m", 0.1)) if self.geom == "radial" else 0.0
        self.xmax = float(p.get("x_max_m", 50))
        self.times = sorted(float(t) for t in (p.get("times_s") or [1000, 4000, 16000]))
        if self.geom == "radial":
            self.A = float(p.get("q_m3_s", 0.001)) * float(p.get("mu_Pa_s", 0.001)) / (
                4 * math.pi * float(p.get("k_m2", 1e-13)) * float(p.get("h_m", 10))) / 1e6  # МПа
        else:
            self.A = float(p.get("dp_MPa", 1.0))
        self.t_min = self.times[0] / 10
        self.t_max = self.times[-1] * 3
        # радиальный приток: воронка давления — у самой скважины; на линейной оси её не видно → логарифм радиуса
        self.rlog = bool(p.get("r_log", self.geom == "radial" and self.xmax / max(self.rw, 1e-6) >= 100))
        self.r = np.geomspace(max(self.rw, 1e-3), self.xmax, 400) if self.rlog else np.linspace(self.rw, self.xmax, 400)
        self.views = list(p.get("views") or ["map", "profile", "bottomhole_vs_log_t"])
        dp_max = float(self.dp(np.array([max(self.rw, 1e-3)]), self.t_max)[0])
        self.p_lo = self.p0 - dp_max * 1.08
        self.lt = np.linspace(math.log10(self.t_min), math.log10(self.t_max), 300)
        self.pw = self.p0 - np.array([self.dp(np.array([max(self.rw, 1e-3)]), 10 ** v)[0] for v in self.lt])

    # ------------------------------------------------------------ модель
    def dp(self, r, t):
        r = np.asarray(r, dtype=float)
        if self.geom == "radial":
            return self.A * e1(np.maximum(r, 1e-6) ** 2 / (4 * self.kappa * t))
        return self.A * np.array([math.erfc(x / (2 * math.sqrt(self.kappa * t))) for x in r])

    def pressure(self, r, t):
        return self.p0 - self.dp(r, t)

    def initial_state(self):
        return {"t": self.t_min, "frozen": [], "marks": [], "view": None}

    def animates(self, action: dict) -> bool:
        return "time_to" in action

    def begin_beat(self, state, action, t):
        s = copy.deepcopy(state)
        if action.get("clear_marks"):
            s["marks"] = []
        if "show_curve_at" in action:
            tc = float(action["show_curve_at"])
            if tc not in s["frozen"]:
                s["frozen"].append(tc)
            s["t"] = max(s["t"], tc)
        if "mark_x" in action:
            s["marks"].append({"x": float(action["mark_x"]), "born": t})
        if "show_view" in action:
            s["view"] = {"name": action["show_view"], "born": t}
        return s

    def at(self, start, action, u):
        if "time_to" not in action:
            return start
        s = copy.deepcopy(start)
        t0, t1 = math.log10(max(start["t"], self.t_min)), math.log10(max(float(action["time_to"]), self.t_min))
        s["t"] = 10 ** (t0 + (t1 - t0) * ease(u))
        if u >= 1.0:  # момент, до которого дошли, остаётся на профиле
            tf = float(action["time_to"])
            if tf not in s["frozen"]:
                s["frozen"].append(tf)
        return s

    def check(self, states):
        res = []
        tt = sorted(set(self.times))
        prof = [self.pressure(self.r, t) for t in tt]
        res.append({"item": "давление растёт от скважины к границе (поток к скважине)",
                    "ok": all(np.all(np.diff(p) >= -1e-9) for p in prof)})
        res.append({"item": "со временем давление в каждой точке не растёт (воронка углубляется)",
                    "ok": all(np.all(b <= a + 1e-9) for a, b in zip(prof, prof[1:]))})
        res.append({"item": "забойное давление падает со временем при постоянном дебите",
                    "ok": bool(np.all(np.diff(self.pw) <= 1e-9))})
        res.append({"item": "давление вдали от скважины ≈ начальному (возмущение не дошло)",
                    "ok": float(self.dp(np.array([self.xmax * 10]), self.times[0])[0]) < 0.01 * self.A + 1e-9})
        res.append({"item": "кривые посчитаны по формуле главы (" + ("E1 — интегральная показательная функция" if self.geom == "radial" else "erfc") + ")", "ok": True})
        return res

    # ------------------------------------------------------------ раскладка
    def layout(self, box, compact: bool):
        x0, y0, x1, y1 = box
        names = [v for v in ["map", "profile", "bottomhole_vs_log_t"] if v in self.views]
        n = len(names)
        gap = 34
        panels = {}
        if compact:  # 9:16 — друг под другом; карта — сплюснутая полоса, графикам больше места
            share = {"map": 0.8, "profile": 1.1, "bottomhole_vs_log_t": 1.1}
            tot = sum(share[v] for v in names)
            hh = y1 - y0 - gap * (n - 1)
            y = y0
            for v in names:
                h = hh * share[v] / tot
                panels[v] = (x0, y, x1, y + h)
                y += h + gap
        elif n == 3 and "map" in names:  # 16:9 — карта слева, два графика справа (синхронные виды)
            mw = min((x1 - x0) * 0.4, y1 - y0)
            panels["map"] = (x0, y0, x0 + mw, y1)
            rest = [v for v in names if v != "map"]
            h = (y1 - y0 - gap) / 2
            for i, v in enumerate(rest):
                panels[v] = (x0 + mw + gap, y0 + i * (h + gap), x1, y0 + i * (h + gap) + h)
        else:
            w = (x1 - x0 - gap * (n - 1)) / n
            for i, v in enumerate(names):
                panels[v] = (x0 + i * (w + gap), y0, x0 + i * (w + gap) + w, y1)
        return {"box": box, "panels": panels, "compact": compact}

    def _plot_rect(self, pb):
        x0, y0, x1, y1 = pb
        f = font(34)
        return (x0 + 128, y0 + line_h(font(40, True)) + line_h(f) + 30, x1 - 28, y1 - line_h(f) * 2 - 22)

    def _xy_profile(self, pr, r, p):
        X0, Y0, X1, Y1 = pr
        if self.rlog:
            a, b = math.log10(max(self.rw, 1e-3)), math.log10(self.xmax)
            u = (math.log10(max(float(r), 1e-3)) - a) / (b - a)
        else:
            u = (r - self.rw) / (self.xmax - self.rw)
        return X0 + u * (X1 - X0), Y1 - (p - self.p_lo) / (self.p0 - self.p_lo) * (Y1 - Y0)

    def _xy_bh(self, pr, lt, p):
        X0, Y0, X1, Y1 = pr
        return X0 + (lt - self.lt[0]) / (self.lt[-1] - self.lt[0]) * (X1 - X0), Y1 - (p - self.p_lo) / (self.p0 - self.p_lo) * (Y1 - Y0)

    def _axes(self, d, pr, xlabel, ylabel, xticks, yticks, muted, grid):
        X0, Y0, X1, Y1 = pr
        f = font(34)
        d.rectangle([X0, Y0, X1, Y1], outline=muted, width=2)
        for x, lab in xticks:
            d.line([(x, Y0), (x, Y1)], fill=grid, width=1)
            d.text((x - f.getlength(lab) / 2, Y1 + 8), lab, font=f, fill=muted)
        for y, lab in yticks:
            d.line([(X0, y), (X1, y)], fill=grid, width=1)
            d.text((X0 - 12 - f.getlength(lab), y - line_h(f) / 2), lab, font=f, fill=muted)
        fl = font(34)
        d.text((X1 - fl.getlength(xlabel), Y1 + 10 + line_h(f)), xlabel, font=fl, fill=muted)
        d.text((X0 - 118, Y0 - line_h(fl) - 10), ylabel, font=fl, fill=muted)

    def _yticks(self, ymap, pr):
        lo, hi = self.p_lo, self.p0
        step = next(s for s in (0.05, 0.1, 0.2, 0.25, 0.5, 1, 2, 5, 10) if (hi - lo) / s <= 5)
        v = math.ceil(lo / step) * step
        out = []
        while v <= hi + 1e-9:
            out.append((ymap(v), fmt_num(v)))
            v += step
        return out

    def draw_static(self, img, L):
        st = self.style
        d = SDraw(img)
        muted, text, panel, bg = (hex_rgb(st[k]) for k in ("muted", "text", "panel", "background"))
        grid = mix(panel, muted, 0.25)
        fh = font(42, True)
        titles = {"map": "Вид сверху", "profile": "Давление по радиусу",
                  "bottomhole_vs_log_t": "Давление на забое"}
        for v, pb in L["panels"].items():
            d.rounded_rectangle(pb, radius=18, fill=mix(panel, bg, 0.35))
            fs, ls = fit(titles[v], pb[2] - pb[0] - 30, 40, 30, 1, True)
            d.text((pb[0] + 18, pb[1] + 12), ls[0], font=fs, fill=text)
            pr = self._plot_rect(pb)
            if v == "profile":
                if self.rlog:
                    lo = math.ceil(math.log10(max(self.rw, 1e-3)) - 1e-9)
                    xs = [10.0 ** e for e in range(lo, int(math.floor(math.log10(self.xmax))) + 1)]
                    if pr[2] - pr[0] < 400 and len(xs) > 4:
                        xs = xs[::2]
                    xt = [(self._xy_profile(pr, x, self.p0)[0], fmt_num(x, 1)) for x in xs]
                    xlab = "м от скважины, лог. шкала"
                else:
                    nt = 5 if pr[2] - pr[0] > 500 else 3
                    xt = [(self._xy_profile(pr, x, self.p0)[0], fmt_num(x, 1)) for x in np.linspace(0, self.xmax, nt)]
                    xlab = "м от скважины"
                yt = self._yticks(lambda p: self._xy_profile(pr, self.rw, p)[1], pr)
                self._axes(d, pr, xlab, "МПа", xt, yt, muted, grid)
            elif v == "bottomhole_vs_log_t":
                xt = []
                es = list(range(math.ceil(self.lt[0]), math.floor(self.lt[-1]) + 1))
                if pr[2] - pr[0] < 500 and len(es) > 3:
                    es = es[::2]
                for e in es:
                    xt.append((self._xy_bh(pr, e, self.p0)[0], fmt_time(10 ** e)))
                yt = self._yticks(lambda p: self._xy_bh(pr, self.lt[0], p)[1], pr)
                self._axes(d, pr, "время, лог. шкала", "МПа", xt, yt, muted, grid)
            elif v == "map":
                cx, cy, R = self._map_geom(pb)
                d.ellipse([cx - R, cy - R, cx + R, cy + R], outline=muted, width=2)
                fl = font(36)
                lab = f"радиус {fmt_num(self.xmax, 0)} м"
                d.line([(cx, cy + R + 20), (cx + R, cy + R + 20)], fill=muted, width=3)
                d.text((cx + R / 2 - fl.getlength(lab) / 2, cy + R + 26), lab, font=fl, fill=muted)

    def _map_geom(self, pb):
        x0, y0, x1, y1 = pb
        top = y0 + line_h(font(40, True)) + line_h(font(36, True)) + 34
        R = max(30.0, min((x1 - x0) / 2 - 40, (y1 - top) / 2 - 40))
        return (x0 + x1) / 2, top + R + 6, R

    def _tcolor(self, i, n):
        base = hex_rgb(self.style.get("curves", {}).get("PRESSURE", {}).get("color", "#00CEC9"))
        muted = hex_rgb(self.style["muted"])
        return mix(muted, base, 0.35 + 0.65 * (i + 1) / max(1, n))

    def draw_dynamic(self, img, L, s, t, fade):
        st = self.style
        d = SDraw(img)
        text, muted, bg = hex_rgb(st["text"]), hex_rgb(st["muted"]), hex_rgb(st["background"])
        accent = hex_rgb(st.get("curves", {}).get("PRESSURE", {}).get("color", "#00CEC9"))
        meas = hex_rgb(st["measurement"]["color"])
        tm = s["t"]
        f = font(40, True)
        for v, pb in L["panels"].items():
            vw = s.get("view")
            if vw and vw["name"] == v:
                a = min(1.0, max(0.0, (t - vw["born"]) / max(fade, 1e-3)))
                d.rounded_rectangle(pb, radius=18, outline=mix(bg, meas, a), width=5)
            pr = self._plot_rect(pb)
            if v == "profile":
                frozen = sorted(s["frozen"])
                for i, tf in enumerate(frozen):
                    pts = [self._xy_profile(pr, r, p) for r, p in zip(self.r, self.pressure(self.r, tf))]
                    col = self._tcolor(i, len(frozen))
                    dashed_polyline(d, pts, fill=col, width=4, dash=14, gap=9)
                    lx, ly = pts[min(len(pts) - 1, int(len(pts) * (0.18 + 0.24 * i)))]   # подписи моментов — вразбежку
                    lab = fmt_time(tf)
                    fl_ = font(32, True)
                    d.text((lx + 6, ly + 12 + i * (line_h(fl_) - 6)), lab, font=fl_, fill=col, stroke_width=5, stroke_fill=bg)
                pts = [self._xy_profile(pr, r, p) for r, p in zip(self.r, self.pressure(self.r, tm))]
                d.line(pts, fill=accent, width=7, joint="curve")
                for m in s["marks"]:
                    a = min(1.0, max(0.0, (t - m["born"]) / max(fade, 1e-3)))
                    x, _ = self._xy_profile(pr, m["x"], self.p0)
                    col = mix(bg, meas, a)
                    dashed_polyline(d, [(x, pr[1]), (x, pr[3])], fill=col, width=4, dash=12, gap=8)
                    lab = f"{fmt_num(m['x'], 1)} м"
                    lx = x + 8 if x + 8 + f.getlength(lab) <= pr[2] - 4 else x - 8 - f.getlength(lab)   # не за край графика
                    d.text((lx, pr[1] + 6), lab, font=f, fill=col, stroke_width=5, stroke_fill=bg)
            elif v == "bottomhole_vs_log_t":
                lt_now = math.log10(max(tm, self.t_min))
                k = max(2, int(np.searchsorted(self.lt, lt_now)))
                pts = [self._xy_bh(pr, a, p) for a, p in zip(self.lt[:k], self.pw[:k])]
                d.line(pts, fill=accent, width=7, joint="curve")
                x, y = self._xy_bh(pr, lt_now, float(np.interp(lt_now, self.lt, self.pw)))
                d.ellipse([x - 11, y - 11, x + 11, y + 11], fill=meas, outline=bg, width=3)
                for tf in s["frozen"]:
                    lf = math.log10(tf)
                    if lf <= lt_now + 1e-9:
                        xx, yy = self._xy_bh(pr, lf, float(np.interp(lf, self.lt, self.pw)))
                        d.ellipse([xx - 7, yy - 7, xx + 7, yy + 7], outline=accent, width=3)
            elif v == "map":
                cx, cy, R = self._map_geom(pb)
                dpw = max(float(self.dp(np.array([max(self.rw, 1e-3)]), self.t_max)[0]), 1e-9)
                rings = 48
                for i in range(rings, 0, -1):
                    rr = self.xmax * i / rings
                    q = float(self.dp(np.array([rr]), tm)[0]) / dpw
                    q = min(1.0, max(0.0, q)) ** 0.6
                    col = mix(mix(bg, muted, 0.15), accent, q)
                    rp = R * i / rings
                    d.ellipse([cx - rp, cy - rp, cx + rp, cy + rp], fill=col)
                d.ellipse([cx - 9, cy - 9, cx + 9, cy + 9], fill=meas, outline=bg, width=3)
            # часы модели — отдельной строкой под заголовком панели
            if v in ("map", "bottomhole_vs_log_t"):
                lab = f"t = {fmt_time(tm)}"
                fc = font(36, True)
                if v == "map":
                    d.text((pb[0] + 18, pb[1] + 14 + line_h(font(40, True))), lab, font=fc, fill=meas)
                else:  # на графике забойного давления — справа в строке заголовка
                    d.text((pb[2] - 24 - fc.getlength(lab), pb[1] + 16), lab, font=fc, fill=meas)
