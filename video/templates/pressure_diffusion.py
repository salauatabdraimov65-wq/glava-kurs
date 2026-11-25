"""Шаблон «pressure_diffusion» (тип Ф — формализм, модель): давление вокруг скважины (radial) или у галереи
(linear) во времени. Давление не рисуется руками — считается по формуле из params, одна модель состояния
(время модели) управляет всеми видами сразу: карта, профиль давления, забойное давление от логарифма времени.

params:
  geometry: "radial" | "linear";  solution: "ei" | "erfc" | "pssss"
  p0_MPa, kappa_m2_s, q_m3_s (radial), mu_Pa_s, k_m2, h_m, r_w_m, x_max_m, times_s [..], views [..]
  dp_MPa (linear erfc: перепад на галерее), r_axis: "log" | "linear" (по умолчанию log для radial)
  pssss_R: "2sqrt" (R = 2√(κt), по умолчанию) | "sqrt" (R = √(κt))
action: time_to (с модели к концу фразы), show_curve_at (с — мгновенно), mark_x (м), show_view (имя или список)

Формулы (radial, постоянный дебит q, пласт толщиной h):
  ei:    Δp(r,t) = q·μ/(4π·k·h) · E1(r²/(4κt))          — интегральная показательная функция
  pssss: Δp(r,t) = q·μ/(2π·k·h) · ln(R(t)/r) при r < R(t), иначе 0
  linear erfc: Δp(x,t) = dp · erfc(x / (2√(κt)))        — постоянный перепад на галерее
"""
from __future__ import annotations

import copy
import math

import numpy as np
from PIL import Image

from ..draw import SDraw, dashed_hline, fit, line_h, text_block
from ..style import font, hex_rgb, mix
from ..textclean import visible

NAME = "pressure_diffusion"
SUMMARY = ("Формализм (тип Ф): давление вокруг скважины или у галереи во времени по формуле главы "
           "(интегральная показательная функция, erfc или ПССС). Три синхронных вида: карта давления, профиль "
           "давления по расстоянию, забойное давление от логарифма времени.")
GUIDE = __doc__.split("params:", 1)[1].split("Формулы", 1)[0]

EULER = 0.5772156649015329


def ease(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return u * u * (3 - 2 * u)


def e1(x: np.ndarray) -> np.ndarray:
    """Интегральная показательная функция E1(x), x > 0 (ряд при x ≤ 1, Абрамовиц–Стиган 5.1.56 при x > 1)."""
    x = np.asarray(x, dtype=float)
    out = np.empty_like(x)
    small = x <= 1
    xs = np.clip(x[small], 1e-300, None)
    s = np.zeros_like(xs)
    term = np.ones_like(xs)
    for n in range(1, 40):
        term = term * (-xs) / n
        s = s - term / n
    out[small] = -EULER - np.log(xs) + s
    xl = x[~small]
    out[~small] = (np.exp(-xl) / xl) * (xl ** 2 + 2.334733 * xl + 0.250621) / (xl ** 2 + 3.330657 * xl + 1.681534)
    return out


class PressureDiffusion:
    name = NAME
    scale_keyword = ("маркер",)

    def __init__(self, params: dict, style: dict):
        self.p = params
        self.style = style
        self.geom = params.get("geometry", "radial")
        self.sol = params.get("solution", "ei" if self.geom == "radial" else "erfc")
        self.p0 = float(params.get("p0_MPa", 10.0))
        self.kappa = float(params.get("kappa_m2_s", 0.1))
        self.q = float(params.get("q_m3_s", 0.001))
        self.mu = float(params.get("mu_Pa_s", 0.001))
        self.k = float(params.get("k_m2", 1e-13))
        self.h = float(params.get("h_m", 10.0))
        self.rw = float(params.get("r_w_m", 0.1))
        self.xmax = float(params.get("x_max_m", 50.0))
        self.dp = float(params.get("dp_MPa", 1.0))
        self.times = sorted(float(t) for t in params.get("times_s", [1000, 4000, 16000]))
        self.views = list(params.get("views") or ["map", "profile", "bottomhole_vs_log_t"])
        self.r_log = params.get("r_axis", "log" if self.geom == "radial" else "linear") == "log"
        self.t_lo = self.times[0] / 10
        self.t_hi = self.times[-1] * 2
        self.r = (np.geomspace(self.rw, self.xmax, 500) if self.r_log else np.linspace(self.rw, self.xmax, 500))
        # пределы оси давления: самое низкое забойное давление за всё время
        pw_min = float(self.pressure(np.array([self.rw]), self.t_hi)[0])
        span = max(self.p0 - pw_min, 1e-6)
        self.p_lo, self.p_hi = pw_min - 0.08 * span, self.p0 + 0.08 * span

    # ------------------------------------------------------------ физика
    def drawdown(self, r: np.ndarray, t: float) -> np.ndarray:
        """Δp, МПа."""
        r = np.asarray(r, dtype=float)
        if t <= 0:
            return np.zeros_like(r)
        if self.geom == "linear":
            from math import erfc
            return self.dp * np.array([erfc(x / (2 * math.sqrt(self.kappa * t))) for x in r])
        coef = self.q * self.mu / (self.k * self.h) / 1e6  # Па → МПа
        if self.sol == "pssss":
            R = (2 if self.p.get("pssss_R", "2sqrt") == "2sqrt" else 1) * math.sqrt(self.kappa * t)
            return np.where(r < R, coef / (2 * math.pi) * np.log(np.clip(R / r, 1, None)), 0.0)
        return coef / (4 * math.pi) * e1(r ** 2 / (4 * self.kappa * t))

    def pressure(self, r, t):
        return self.p0 - self.drawdown(r, t)

    def p_w(self, t: float) -> float:
        return float(self.pressure(np.array([self.rw]), t)[0])

    # ------------------------------------------------------------ состояние
    def initial_state(self) -> dict:
        return {"t": 0.0, "marks": [], "shown": {}, "frozen": [], "t_from": 0.0}

    def animates(self, action: dict) -> bool:
        return "time_to" in action

    def begin_beat(self, state: dict, action: dict, t: float) -> dict:
        s = copy.deepcopy(state)
        if "show_view" in action:
            v = action["show_view"]
            for name in (v if isinstance(v, list) else [v]):
                s["shown"].setdefault(name, t)
        if "show_curve_at" in action:
            s["t"] = float(action["show_curve_at"])
            if s["t"] not in s["frozen"]:
                s["frozen"].append(s["t"])
        if "mark_x" in action:
            s["marks"].append({"x": float(action["mark_x"]), "born": t})
        s["t_from"] = s["t"]
        self._freeze(s)
        return s

    def _freeze(self, s):
        for tt in self.times:
            if tt <= s["t"] + 1e-9 and tt not in s["frozen"]:
                s["frozen"].append(tt)
        s["frozen"].sort()

    def at(self, start: dict, action: dict, u: float) -> dict:
        if "time_to" not in action:
            return start
        s = copy.deepcopy(start)
        t0 = max(start["t"], self.t_lo)
        t1 = float(action["time_to"])
        e = ease(u)
        s["t"] = math.exp(math.log(t0) + (math.log(max(t1, 1e-9)) - math.log(t0)) * e) if t1 > 0 else 0.0
        self._freeze(s)
        return s

    def check(self, states: list[dict]) -> list[dict]:
        ok_p0 = all(float(self.pressure(self.r, s["t"]).max()) <= self.p0 + 1e-9 for s in states if s["t"] > 0)
        ts = [s["t"] for s in states]
        mono = all(b >= a - 1e-9 for a, b in zip(ts, ts[1:]))
        pw = [self.p_w(t) for t in sorted(set(self.times))]
        ok_pw = all(b <= a + 1e-12 for a, b in zip(pw, pw[1:]))
        order = all(np.all(self.pressure(self.r, b) <= self.pressure(self.r, a) + 1e-9)
                    for a, b in zip(self.times, self.times[1:]))
        return [
            {"item": "давление не выше начального p0 (скважина отбирает)", "ok": ok_p0},
            {"item": "время модели идёт вперёд", "ok": mono},
            {"item": "забойное давление при постоянном дебите не растёт со временем", "ok": ok_pw},
            {"item": "профиль более позднего момента лежит ниже раннего", "ok": order},
            {"item": f"давление считается по формуле ({self.geom}, {self.sol}), а не рисуется", "ok": True},
        ]

    # ------------------------------------------------------------ раскладка
    def layout(self, box, compact: bool):
        x0, y0, x1, y1 = box
        f_t = font(40, True)
        th = line_h(f_t) + 10
        n = len(self.views)
        L = {"box": box, "views": {}, "compact": compact}
        gap = 34
        if compact:  # 9:16 — карта сверху слева, под ней два графика друг под другом
            h_each = (y1 - y0 - gap * (n - 1)) / n
            y = y0
            for v in self.views:
                L["views"][v] = (x0, y, x1, y + h_each)
                y += h_each + gap
        else:
            w_each = (x1 - x0 - gap * (n - 1)) / n
            x = x0
            for v in self.views:
                L["views"][v] = (x, y0, x + w_each, y1)
                x += w_each + gap
        L["title_h"] = th
        return L

    def _plot_rect(self, vb):
        x0, y0, x1, y1 = vb
        return (x0 + 110, y0 + line_h(font(40, True)) + 40, x1 - 16, y1 - 92)

    def _x_r(self, pr, r):
        a, b = pr[0], pr[2]
        if self.r_log:
            u = (math.log10(max(r, self.rw)) - math.log10(self.rw)) / (math.log10(self.xmax) - math.log10(self.rw))
        else:
            u = (r - self.rw) / (self.xmax - self.rw)
        return a + min(max(u, 0), 1) * (b - a)

    def _x_t(self, pr, t):
        u = (math.log10(max(t, self.t_lo)) - math.log10(self.t_lo)) / (math.log10(self.t_hi) - math.log10(self.t_lo))
        return pr[0] + min(max(u, 0), 1) * (pr[2] - pr[0])

    def _y_p(self, pr, p):
        u = (p - self.p_lo) / (self.p_hi - self.p_lo)
        return pr[3] - min(max(u, -0.02), 1.02) * (pr[3] - pr[1])

    @staticmethod
    def _num(v: float) -> str:
        if v >= 1000:
            return f"{v:,.0f}".replace(",", " ")
        if v >= 10 or abs(v - round(v)) < 1e-9:
            return f"{v:.0f}"
        return f"{v:g}".replace(".", ",")

    def _titles(self):
        return {"map": "Карта давления", "profile": "Профиль давления",
                "bottomhole_vs_log_t": "Давление на забое"}

    # ------------------------------------------------------------ статика
    def draw_static(self, img: Image.Image, L: dict):
        st = self.style
        d = SDraw(img)
        bg, text, muted, panel = (hex_rgb(st[k]) for k in ("background", "text", "muted", "panel"))
        f_t, f_ax = font(40, True), font(32)
        for v, vb in L["views"].items():
            d.text((vb[0], vb[1]), self._titles().get(v, v), font=f_t, fill=text)
            if v == "map":
                continue
            pr = self._plot_rect(vb)
            d.rectangle(pr, fill=mix(panel, bg, 0.3), outline=muted, width=2)
            # ось давления
            for pv in self._p_ticks():
                y = self._y_p(pr, pv)
                d.line([(pr[0], y), (pr[2], y)], fill=mix(panel, muted, 0.25), width=1)
                s = f"{pv:g}".replace(".", ",")
                d.text((pr[0] - 12 - f_ax.getlength(s), y - line_h(f_ax) / 2), s, font=f_ax, fill=muted)
            d.text((vb[0], pr[1] - line_h(f_ax) - 4), "МПа", font=f_ax, fill=muted)
            if v == "profile":
                ticks = [x for x in (0.1, 0.3, 1, 3, 10, 30, 100, 300, 1000) if self.rw <= x <= self.xmax * 1.001] \
                    if self.r_log else list(np.linspace(0, self.xmax, 5))
                for xv in ticks:
                    x = self._x_r(pr, xv)
                    d.line([(x, pr[1]), (x, pr[3])], fill=mix(panel, muted, 0.25), width=1)
                    s = self._num(xv)
                    d.text((x - f_ax.getlength(s) / 2, pr[3] + 6), s, font=f_ax, fill=muted)
                lab = "расстояние от скважины, м" + (" (лог. шкала)" if self.r_log else "")
            else:
                dec = range(math.ceil(math.log10(self.t_lo)), math.floor(math.log10(self.t_hi)) + 1)
                for e_ in dec:
                    x = self._x_t(pr, 10 ** e_)
                    d.line([(x, pr[1]), (x, pr[3])], fill=mix(panel, muted, 0.25), width=1)
                    s = self._num(10 ** e_)
                    d.text((x - f_ax.getlength(s) / 2, pr[3] + 6), s, font=f_ax, fill=muted)
                lab = "время, с (лог. шкала)"
            fl, ls = fit(lab, pr[2] - pr[0], 32, 26, 1)
            d.text((pr[0], pr[3] + 6 + line_h(f_ax)), ls[0], font=fl, fill=muted)

    def _p_ticks(self):
        span = self.p_hi - self.p_lo
        step = next(s for s in (0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50) if span / s <= 6)
        v = math.ceil(self.p_lo / step) * step
        out = []
        while v <= self.p_hi:
            out.append(round(v, 6))
            v += step
        return out

    # ------------------------------------------------------------ динамика
    def _alpha_view(self, s, v, t, fade):
        if v not in s["shown"]:
            # вид, который ни разу не «показывают», виден сразу; остальные — бледные до show_view
            return 1.0 if not getattr(self, "_reveal", set()) or v not in self._reveal else 0.25
        return 0.25 + 0.75 * min(1.0, max(0.0, (t - s["shown"][v]) / max(fade, 1e-3)))

    def prepare(self, actions: list[dict]):
        """Виды, которые сценарий «показывает» (show_view), до этого бледные; остальные видны сразу."""
        rv = set()
        for a in actions:
            v = a.get("show_view")
            rv |= set(v if isinstance(v, list) else [v] if v else [])
        self._reveal = rv

    def _curve_col(self, i, n, active):
        st = self.style
        base = hex_rgb(st.get("curves", {}).get("PRESSURE", {}).get("color", st["measurement"]["color"]))
        if active:
            return base
        return mix(base, hex_rgb(st["muted"]), 0.55 * (n - 1 - i) / max(1, n - 1))

    def draw_dynamic(self, img: Image.Image, L: dict, s: dict, t: float, fade: float):
        st = self.style
        bg, text, muted, panel = (hex_rgb(st[k]) for k in ("background", "text", "muted", "panel"))
        meas = hex_rgb(st["measurement"]["color"])
        for v, vb in L["views"].items():
            a = self._alpha_view(s, v, t, fade)
            if v == "map":
                self._draw_map(img, vb, s, t, a)
            elif v == "profile":
                self._draw_profile(img, vb, s, t, a)
            else:
                self._draw_bh(img, vb, s, t, a)
        # время модели — общее для всех видов
        d = SDraw(img)
        f = font(40, True)
        lab = f"время после пуска: {self._num(s['t'])} с" if s["t"] > 0 else "скважина ещё не пущена"
        x0, y0, x1, y1 = L["box"]
        tw = f.getlength(lab)
        d.rounded_rectangle((x1 - tw - 30, y1 - line_h(f) - 14, x1, y1 + 4), radius=12, fill=panel)
        d.text((x1 - tw - 15, y1 - line_h(f) - 8), lab, font=f, fill=meas)

    def _draw_map(self, img, vb, s, t, a):
        st = self.style
        k = img.info.get("ssaa", 1)
        bg, text, muted, panel = (hex_rgb(st[k_]) for k_ in ("background", "text", "muted", "panel"))
        pcol = hex_rgb(st.get("curves", {}).get("PRESSURE", {}).get("color", st["measurement"]["color"]))
        x0, y0, x1, y1 = vb
        top = y0 + line_h(font(40, True)) + 24
        size = min(x1 - x0, y1 - top - 60)
        cx, cy = x0 + (x1 - x0) / 2, top + size / 2
        R = size / 2
        n = int(size * k)
        yy, xx = np.mgrid[0:n, 0:n]
        rr_px = np.hypot(xx - n / 2, yy - n / 2) / (n / 2)          # 0..1 → 0..x_max
        if self.geom == "radial":
            r_m = np.clip(rr_px * self.xmax, self.rw, None)
        else:
            r_m = np.clip(xx / n * self.xmax, 0, None)
        dd = self.drawdown(r_m.ravel(), s["t"]).reshape(r_m.shape) if s["t"] > 0 else np.zeros_like(r_m)
        dmax = max(self.p0 - self.p_lo, 1e-9)
        u = np.clip(dd / dmax, 0, 1) ** 0.5
        base = np.array(mix(panel, bg, 0.2), dtype=float)
        hi = np.array(pcol, dtype=float)
        rgb = base[None, None, :] * (1 - u[..., None]) + hi[None, None, :] * u[..., None]
        bgv = np.array(bg, dtype=float)
        rgb = bgv + (rgb - bgv) * a
        mask = rr_px <= 1.0 if self.geom == "radial" else np.ones_like(rr_px, bool)
        canvas = np.array(img.crop((int((cx - R) * k), int((cy - R) * k), int((cx - R) * k) + n, int((cy - R) * k) + n)),
                          dtype=float)
        canvas[mask] = rgb[mask]
        img.paste(Image.fromarray(canvas.astype(np.uint8)), (int((cx - R) * k), int((cy - R) * k)))
        d = SDraw(img)
        if self.geom == "radial":
            d.ellipse([cx - R, cy - R, cx + R, cy + R], outline=mix(bg, muted, a), width=2)
            for mk in s["marks"]:
                rp = min(mk["x"], self.xmax) / self.xmax * R
                d.ellipse([cx - rp, cy - rp, cx + rp, cy + rp], outline=mix(bg, text, a), width=3)
            mr = self._marker_px()
            d.ellipse([cx - mr, cy - mr, cx + mr, cy + mr], fill=mix(bg, text, a), outline=bg, width=2)
        # масштабная линейка
        f = font(32)
        bar_m = next(b for b in (1, 2, 5, 10, 20, 50, 100, 200, 500) if b >= self.xmax / 5)
        bl = bar_m / self.xmax * R
        by = cy + R + 26
        d.line([(cx - R, by), (cx - R + bl, by)], fill=mix(bg, text, a), width=4)
        d.text((cx - R + bl + 10, by - line_h(f) / 2), f"{bar_m} м", font=f, fill=mix(bg, muted, a))
        lab = "ярче — давление ниже"
        d.text((x1 - f.getlength(lab), by - line_h(f) / 2), lab, font=f, fill=mix(bg, muted, a))

    def _marker_px(self):
        return 9

    def scale_note(self, L) -> str:
        vb = L["views"].get("map")
        if not vb or self.geom != "radial":
            return ""
        x0, y0, x1, y1 = vb
        top = y0 + line_h(font(40, True)) + 24
        R = min(x1 - x0, y1 - top - 60) / 2
        real = self.rw / self.xmax * R
        k = self._marker_px() / max(real, 1e-9)
        n = int(round(k)) if k < 20 else int(round(k, -1))
        return f"маркер скважины на карте крупнее реального ≈ в {n} раз"

    def _draw_profile(self, img, vb, s, t, a):
        st = self.style
        d = SDraw(img)
        bg, text, muted = hex_rgb(st["background"]), hex_rgb(st["text"]), hex_rgb(st["muted"])
        pr = self._plot_rect(vb)
        # начальное давление
        y0p = self._y_p(pr, self.p0)
        dashed_hline(d, pr[0], pr[2], y0p, fill=mix(bg, muted, a), width=2)
        f = font(32, True)
        d.text((pr[2] - f.getlength("начальное") - 6, y0p - line_h(f) - 2), "начальное", font=f, fill=mix(bg, muted, a))
        frozen = s["frozen"]
        curves = [(tt, False) for tt in frozen if abs(tt - s["t"]) > 1e-6] + ([(s["t"], True)] if s["t"] > 0 else [])
        n = len(frozen) + 1
        for i, (tt, active) in enumerate(curves):
            pv = self.pressure(self.r, tt)
            pts = [(self._x_r(pr, r), self._y_p(pr, p)) for r, p in zip(self.r, pv)]
            col = mix(bg, self._curve_col(i, n, active), a)
            d.line(pts, fill=col, width=6 if active else 4, joint="curve")
            lab = f"{self._num(tt)} с"
            lx, ly = pts[-1][0] - f.getlength(lab) - 8, pts[-1][1] + 6
            if not active or tt in frozen:
                d.text((lx, ly), lab, font=f, fill=col, stroke_width=3, stroke_fill=bg)
        # отметки расстояния и касательные к профилям на них
        for j, mk in enumerate(s["marks"]):
            newest = j == len(s["marks"]) - 1
            x = self._x_r(pr, max(mk["x"], self.rw))
            col = mix(bg, text if newest else muted, a)
            d.line([(x, pr[1]), (x, pr[3])], fill=col, width=3 if newest else 2)
            lab = f"{self._num(mk['x'])} м"
            d.text((x + 8, pr[1] + 6), lab, font=f, fill=col, stroke_width=3, stroke_fill=bg)
            if newest:
                for i, (tt, active) in enumerate(curves):
                    self._tangent(d, pr, mk["x"], tt, mix(bg, text, a * 0.9))

    def _tangent(self, d, pr, x, tt, col):
        """Касательная к профилю в точке x (в координатах графика — по численной производной)."""
        if tt <= 0:
            return
        x = max(x, self.rw)
        e = 1.04
        xa, xb = x / e if self.r_log else x - 0.02 * self.xmax, x * e if self.r_log else x + 0.02 * self.xmax
        pa, pb = self.pressure(np.array([xa, xb]), tt)
        X1, Y1 = self._x_r(pr, xa), self._y_p(pr, pa)
        X2, Y2 = self._x_r(pr, xb), self._y_p(pr, pb)
        if abs(X2 - X1) < 1e-6:
            return
        slope = (Y2 - Y1) / (X2 - X1)
        cx, cy = self._x_r(pr, x), self._y_p(pr, float(self.pressure(np.array([x]), tt)[0]))
        half = (pr[2] - pr[0]) * 0.14
        d.line([(cx - half, cy - slope * half), (cx + half, cy + slope * half)], fill=col, width=2)
        d.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], fill=col)

    def _draw_bh(self, img, vb, s, t, a):
        st = self.style
        d = SDraw(img)
        bg, muted = hex_rgb(st["background"]), hex_rgb(st["muted"])
        pr = self._plot_rect(vb)
        y0p = self._y_p(pr, self.p0)
        dashed_hline(d, pr[0], pr[2], y0p, fill=mix(bg, muted, a), width=2)
        if s["t"] <= self.t_lo:
            return
        ts = np.geomspace(self.t_lo, min(s["t"], self.t_hi), 200)
        pts = [(self._x_t(pr, tt), self._y_p(pr, self.p_w(tt))) for tt in ts]
        col = mix(bg, self._curve_col(0, 1, True), a)
        d.line(pts, fill=col, width=6, joint="curve")
        f = font(32, True)
        for tt in s["frozen"]:
            if tt <= s["t"]:
                x, y = self._x_t(pr, tt), self._y_p(pr, self.p_w(tt))
                d.ellipse([x - 8, y - 8, x + 8, y + 8], fill=col, outline=bg, width=2)
        x, y = pts[-1]
        d.ellipse([x - 11, y - 11, x + 11, y + 11], fill=col, outline=bg, width=3)
        lab = f"≈ {self.p_w(s['t']):.2f} МПа".replace(".", ",")
        d.text((min(x + 14, pr[2] - f.getlength(lab)), y - line_h(f) - 8), lab, font=f, fill=col,
               stroke_width=3, stroke_fill=bg)

    def focus(self, L, s):
        x0, y0, x1, y1 = L["box"]
        return ((x0 + x1) / 2, (y0 + y1) / 2)
