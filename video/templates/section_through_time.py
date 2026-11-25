"""Шаблон «section_through_time» (типы М — механизм, В — геологическое время, П — пространство).

Вертикальный разрез (x — расстояние, z — отметка, вверх положительно), где тела пород растут, смещаются,
размываются или замещаются во времени. Одна модель состояния — модельное время t — управляет формой всех тел,
уровнем воды, стрелками процессов и счётчиком времени. Форма тела между ключевыми кадрами интерполируется
(если число вершин разное — оба контура пересэмплируются по длине). Всё берётся из spec.json; цвета — из стайлгайда.

params:
  x_max_m, z_top_m, z_bottom_m, vertical_exaggeration (1 — истинный масштаб), time_unit, time_steps [..],
  water_level [{t, z}] или [], bodies [{id, lith, fluid, label, keyframes [{t, polygon [[x, z], ...]}]}],
  arrows [{t_from, t_to, from [x, z], to [x, z], label}]
action: time_to (момент к концу фразы), highlight_body (id), mark_x (м), show_true_scale (true), clear_marks
"""
from __future__ import annotations

import copy
import math

import numpy as np
from PIL import Image

from ..draw import SDraw, dashed_hline, fit, line_h, wrap
from ..style import font, hex_rgb, mix, mono_font
from ..textclean import visible

NAME = "section_through_time"
SUMMARY = ("Механизм во времени (типы М, В, П): разрез, где тела пород растут, смещаются, размываются или замещаются; "
           "уровень воды и стрелки процессов. Форма тел между ключевыми кадрами интерполируется.")
GUIDE = __doc__.split("params:", 1)[1]
N_RESAMPLE = 96


def ease(u):
    u = min(max(u, 0.0), 1.0)
    return u * u * (3 - 2 * u)


def resample(poly, n=N_RESAMPLE):
    """Замкнутый контур → n точек, равномерно по длине (для морфинга контуров с разным числом вершин)."""
    p = np.asarray(poly, float)
    if len(p) < 3:
        return np.repeat(p[:1], n, axis=0) if len(p) else np.zeros((n, 2))
    q = np.vstack([p, p[:1]])
    seg = np.hypot(*np.diff(q, axis=0).T)
    cum = np.concatenate([[0], np.cumsum(seg)])
    L = cum[-1] or 1.0
    s = np.linspace(0, L, n, endpoint=False)
    x = np.interp(s, cum, q[:, 0])
    z = np.interp(s, cum, q[:, 1])
    # начинаем с самой левой нижней точки — меньше «перекрута» при морфинге
    k = int(np.argmin(x * 1e3 - z))
    return np.roll(np.stack([x, z], 1), -k, axis=0)


class SectionThroughTime:
    name = NAME
    scale_keyword = ("вертикал",)

    def __init__(self, params: dict, style: dict):
        self.p = params
        self.style = style
        self.xmax = float(params.get("x_max_m", 1000))
        self.ztop = float(params.get("z_top_m", 0))
        self.zbot = float(params.get("z_bottom_m", -100))
        if self.ztop <= self.zbot:
            raise ValueError("z_top_m должна быть выше z_bottom_m")
        self.ve = float(params.get("vertical_exaggeration", 1) or 1)
        self.unit = params.get("time_unit", "")
        self.steps = sorted(float(t) for t in params.get("time_steps", [0]))
        self.water = sorted(params.get("water_level") or [], key=lambda w: w["t"])
        self.bodies = []
        for b in params.get("bodies", []):
            kf = sorted(b.get("keyframes", []), key=lambda k: k["t"])
            if not kf:
                continue
            self.bodies.append({**b, "keyframes": kf, "_rs": [resample(k["polygon"]) for k in kf]})
        self.arrows = params.get("arrows") or []
        self.t0 = self.steps[0] if self.steps else 0.0
        self.t1 = self.steps[-1] if self.steps else 1.0

    # ------------------------------------------------------------ модель
    def body_poly(self, b, t):
        """Контур тела в момент t и его «проявленность» 0..1 (до первого кадра тела нет)."""
        kf, rs = b["keyframes"], b["_rs"]
        if t < kf[0]["t"]:
            span = max((self.t1 - self.t0) * 0.08, 1e-9)
            a = 1 - (kf[0]["t"] - t) / span
            if a <= 0:
                return None, 0.0
            # тело «прорастает» из центра своего первого контура
            c = rs[0].mean(0)
            return c + (rs[0] - c) * max(a, 0.05), a
        if t >= kf[-1]["t"] or len(kf) == 1:
            return rs[-1], 1.0
        for i in range(len(kf) - 1):
            a, b2 = kf[i]["t"], kf[i + 1]["t"]
            if a <= t <= b2:
                u = (t - a) / (b2 - a) if b2 > a else 1.0
                return rs[i] * (1 - u) + rs[i + 1] * u, 1.0
        return rs[-1], 1.0

    def water_at(self, t):
        if not self.water:
            return None
        ts = [w["t"] for w in self.water]
        zs = [w["z"] for w in self.water]
        return float(np.interp(t, ts, zs))

    def initial_state(self):
        return {"t": self.t0, "hl": None, "marks": [], "true": None}

    def animates(self, action: dict) -> bool:
        return "time_to" in action or bool(action.get("show_true_scale"))

    def begin_beat(self, state, action, t):
        s = copy.deepcopy(state)
        s["hl"] = {"id": action["highlight_body"], "born": t} if action.get("highlight_body") else None
        if action.get("clear_marks"):
            s["marks"] = []
        if action.get("mark_x") is not None:
            s["marks"].append({"x": float(action["mark_x"]), "born": t})
        if action.get("show_true_scale") and s["true"] is None:
            s["true"] = {"born": t, "u": 0.0}
        s["t_from"] = s["t"]
        return s

    def at(self, start, action, u):
        s = copy.deepcopy(start)
        if "time_to" in action:
            t1 = min(max(float(action["time_to"]), self.t0), self.t1)
            s["t"] = start["t"] + (t1 - start["t"]) * ease(u)
        if s.get("true") is not None and action.get("show_true_scale"):
            s["true"]["u"] = ease(u)
        elif s.get("true") is not None:
            s["true"]["u"] = 1.0
        return s

    def check(self, states):
        ok_poly = all(len(k["polygon"]) >= 3 for b in self.bodies for k in b["keyframes"])
        ok_kf = all(all(a["t"] <= c["t"] for a, c in zip(b["keyframes"], b["keyframes"][1:])) for b in self.bodies)
        ts = [s["t"] for s in states]
        ok_t = all(self.t0 - 1e-9 <= t <= self.t1 + 1e-9 for t in ts)
        inside = all(self.zbot - 1e-6 <= z <= self.ztop + 1e-6 and -1e-6 <= x <= self.xmax + 1e-6
                     for b in self.bodies for k in b["keyframes"] for x, z in k["polygon"])
        return [
            {"item": "у каждого тела контур из 3+ точек, ключевые кадры по времени", "ok": ok_poly and ok_kf},
            {"item": "время модели в пределах time_steps", "ok": ok_t},
            {"item": "тела внутри разреза (x, z)", "ok": inside},
            {"item": "форма тел между кадрами интерполируется, а не рисуется руками", "ok": True},
            {"item": f"вертикальное преувеличение ×{self.ve:g} подписано на плашке", "ok": True},
        ]

    # ------------------------------------------------------------ раскладка
    def layout(self, box, compact: bool):
        x0, y0, x1, y1 = box
        f_t = mono_font(40, True)
        top_pad = line_h(f_t) * 2 + 30          # счётчик времени и шкала времени
        bot_pad = 70                              # подпись оси x
        left_pad = 120                            # отметки z
        W, H = x1 - x0 - left_pad - 10, y1 - y0 - top_pad - bot_pad
        return {"box": box, "area": (x0 + left_pad, y0 + top_pad, x0 + left_pad + W, y0 + top_pad + H),
                "compact": compact, "timebar": (x0 + left_pad, y0 + line_h(f_t) + 16, x1 - 10)}

    def _ve(self, s):
        tr = s.get("true") if s else None
        return self.ve + (1 - self.ve) * (tr["u"] if tr else 0.0)

    def _map(self, L, s):
        ax0, ay0, ax1, ay1 = L["area"]
        ve = self._ve(s)
        sx = (ax1 - ax0) / self.xmax
        sz = sx * ve
        h_need = (self.ztop - self.zbot) * sz
        if h_need > ay1 - ay0:                    # не влезает по высоте — уменьшаем общий масштаб
            k = (ay1 - ay0) / h_need
            sx, sz = sx * k, sz * k
        w = self.xmax * sx
        h = (self.ztop - self.zbot) * sz
        ox = ax0 + (ax1 - ax0 - w) / 2
        oy = ay0 + (ay1 - ay0 - h) / 2
        return (lambda x: ox + x * sx), (lambda z: oy + (self.ztop - z) * sz), (ox, oy, ox + w, oy + h)

    def scale_note(self, L) -> str:
        if self.ve > 1.01:
            return f"вертикаль растянута ≈ в {self.ve:g} раза" if self.ve < 5 else f"вертикаль растянута ≈ в {self.ve:g} раз"
        return ""

    # ------------------------------------------------------------ рисование
    def draw_static(self, img, L):
        pass  # всё зависит от времени и масштаба — рисуется в draw_dynamic

    def _lith(self, lith):
        return hex_rgb(self.style.get("lithology", {}).get(lith, {}).get("color", self.style["muted"]))

    def draw_dynamic(self, img, L, s, t, fade):
        st = self.style
        k = img.info.get("ssaa", 1)
        d = SDraw(img)
        bg, text, muted, panel = (hex_rgb(st[x]) for x in ("background", "text", "muted", "panel"))
        meas = hex_rgb(st["measurement"]["color"])
        X, Z, rect = self._map(L, s)
        rx0, ry0, rx1, ry1 = rect
        # фон разреза: дно бассейна/вмещающая среда — тёмная панель; вода ниже уровня — цвет воды
        d.rectangle(rect, fill=mix(panel, bg, 0.2))
        wl = self.water_at(s["t"])
        water = hex_rgb(st.get("fluids", {}).get("water", {}).get("color", st["muted"]))
        if wl is not None and self.zbot < wl:
            yw = Z(min(wl, self.ztop))
            d.rectangle([rx0, yw, rx1, ry1], fill=mix(bg, water, 0.32))
            for j in range(6):  # лёгкая рябь у поверхности
                yy = yw + 6 + j * 9
                d.line([(rx0, yy), (rx1, yy)], fill=mix(bg, water, 0.42 - j * 0.05), width=2)
        # тела
        hl = s.get("hl")
        seen = set()
        for b in self.bodies:
            poly, a = self.body_poly(b, s["t"])
            zone = b.get("lith") in seen   # та же порода, что у тела раньше — это зона изменения внутри него
            seen.add(b.get("lith"))
            if poly is None:
                continue
            pts = [(X(x), Z(z)) for x, z in poly]
            col = self._lith(b.get("lith"))
            if zone:
                col = mix(col, (255, 255, 255), 0.45)
            fl = b.get("fluid")
            if fl and fl in st.get("fluids", {}):
                col = mix(col, hex_rgb(st["fluids"][fl]["color"]), 0.35)
            dim = hl and hl["id"] != b["id"]
            fill = mix(bg, col, a * (0.45 if dim else 1.0))
            d.polygon(pts, fill=fill)
            # фактура: штриховка внутри контура (по маске)
            self._texture(img, pts, mix(fill, (0, 0, 0), 0.18), k)
            edge = mix(fill, (255, 255, 255), 0.35) if not dim else mix(bg, col, 0.5)
            if zone:
                from ..draw import dashed_polyline
                dashed_polyline(d, pts + [pts[0]], fill=mix(bg, text, a), width=4, dash=12, gap=8)
            else:
                d.line(pts + [pts[0]], fill=edge, width=3)
        # уровень воды
        if wl is not None and self.zbot < wl <= self.ztop:
            yw = Z(wl)
            d.line([(rx0, yw), (rx1, yw)], fill=mix(bg, water, 0.95), width=4)
            fw = font(32, True)
            lab = "уровень воды"
            d.text((rx1 - fw.getlength(lab) - 8, yw - line_h(fw) - 4), lab, font=fw, fill=water,
                   stroke_width=4, stroke_fill=bg)
        # подсветка тела
        if hl:
            b = next((q for q in self.bodies if q["id"] == hl["id"]), None)
            if b:
                poly, a = self.body_poly(b, s["t"])
                if poly is not None:
                    aa = min(1.0, max(0.0, (t - hl["born"]) / max(fade, 1e-3)))
                    pts = [(X(x), Z(z)) for x, z in poly]
                    d.line(pts + [pts[0]], fill=mix(bg, meas, aa), width=7)
        placed = []

        def free_pos(x, y, w, h):
            """Сдвигаем подпись вниз/вверх, пока не перестанет пересекаться с уже поставленными."""
            for k_ in range(14):
                dy = ((k_ + 1) // 2) * (h + 6) * (1 if k_ % 2 else -1) if k_ else 0
                r = (x, y + dy, x + w, y + dy + h)
                if all(r[2] < q[0] or r[0] > q[2] or r[3] < q[1] or r[1] > q[3] for q in placed):
                    placed.append(r)
                    return r
            placed.append((x, y, x + w, y + h))
            return placed[-1]

        # подписи тел — по убыванию площади, у центра тела
        fl_ = font(38, True)
        order = []
        for b in self.bodies:
            poly, a = self.body_poly(b, s["t"])
            if poly is None or a < 0.6 or not b.get("label"):
                continue
            area = abs(np.sum(poly[:, 0] * np.roll(poly[:, 1], 1) - np.roll(poly[:, 0], 1) * poly[:, 1])) / 2
            order.append((area, b, poly))
        for area, b, poly in sorted(order, key=lambda q: -q[0]):
            cx, cz = poly[:, 0].mean(), poly[:, 1].mean()
            lab = visible(b["label"], "подпись тела")
            ls = wrap(lab, fl_, max(160, (rx1 - rx0) * 0.45))
            w = max(fl_.getlength(ln) for ln in ls)
            h = len(ls) * line_h(fl_)
            r = free_pos(X(cx) - w / 2, Z(cz) - h / 2, w, h)
            dim = hl and hl["id"] != b["id"]
            yy = r[1]
            for ln in ls:
                d.text((r[0] + (w - fl_.getlength(ln)) / 2, yy), ln, font=fl_,
                       fill=mix(text, bg, 0.45) if dim else text, stroke_width=6, stroke_fill=bg)
                yy += line_h(fl_)
        # стрелки процессов: бегущий пунктир от «from» к «to», подпись у начала стрелки
        fa = font(32, True)
        for ar in self.arrows:
            t_from, t_to = float(ar.get("t_from", self.t0)), float(ar.get("t_to", self.t1))
            if not (t_from - 1e-9 <= s["t"] <= t_to + 1e-9):
                continue
            span = max(t_to - t_from, 1e-9)
            a = min(1.0, (s["t"] - t_from) / (span * 0.25) + 0.15)
            (xa, za), (xb, zb) = ar["from"], ar["to"]
            pa, pb = (X(xa), Z(za)), (X(xb), Z(zb))
            L_ = math.hypot(pb[0] - pa[0], pb[1] - pa[1]) or 1
            ux, uy = (pb[0] - pa[0]) / L_, (pb[1] - pa[1]) / L_
            col = mix(bg, meas, a)
            phase = (t * 60) % 26
            pos = -phase
            while pos < L_ - 26:
                s0, s1 = max(pos, 0), min(pos + 14, L_ - 26)
                if s1 > s0:
                    d.line([(pa[0] + ux * s0, pa[1] + uy * s0), (pa[0] + ux * s1, pa[1] + uy * s1)], fill=col, width=6)
                pos += 26
            hx, hy = pb
            d.polygon([(hx, hy), (hx - ux * 30 - uy * 14, hy - uy * 30 + ux * 14),
                       (hx - ux * 30 + uy * 14, hy - uy * 30 - ux * 14)], fill=col)
            if ar.get("label"):
                lab = visible(ar["label"], "подпись стрелки")
                w, h = fa.getlength(lab), line_h(fa)
                r = free_pos(pa[0] - w / 2, pa[1] - h - 18, w, h)
                d.rounded_rectangle([r[0] - 10, r[1] - 2, r[2] + 10, r[3] + 4], radius=10, fill=mix(bg, panel, a))
                d.text((r[0], r[1]), lab, font=fa, fill=mix(bg, meas, a))
        # отметки x
        fm = mono_font(30, True)
        for mk in s["marks"]:
            x = X(min(max(mk["x"], 0), self.xmax))
            for yy in np.arange(ry0, ry1, 22):
                d.line([(x, yy), (x, min(ry1, yy + 12))], fill=text, width=3)
            lab = f"{mk['x']:g} м".replace(".", ",")
            d.text((x + 8, ry0 + 6), lab, font=fm, fill=text, stroke_width=4, stroke_fill=bg)
        # оси: отметки z слева, x снизу
        fz = mono_font(28, True)
        span = self.ztop - self.zbot
        step = next(sv for sv in (1, 2, 5, 10, 20, 25, 50, 100, 200, 500, 1000) if span / sv <= 6)
        z = math.ceil(self.zbot / step) * step
        while z <= self.ztop + 1e-9:
            y = Z(z)
            lab = f"{z:g}".replace("-", "−")
            d.text((rx0 - 14 - fz.getlength(lab), y - line_h(fz) / 2), lab, font=fz, fill=muted)
            d.line([(rx0 - 8, y), (rx0, y)], fill=muted, width=2)
            z += step
        d.text((L["box"][0], ry0 - line_h(fz) - 6), "отметка, м", font=font(28, True), fill=muted)
        xl = f"0 … {self.xmax:g} м по горизонтали"
        d.text((rx0, ry1 + 10), xl, font=font(28, True), fill=muted)
        # время: крупный счётчик + шкала с метками ключевых моментов
        f_big = mono_font(44, True)
        tl = f"{s['t']:.0f} {self.unit}".strip() if abs(s["t"]) >= 1 or s["t"] == 0 else f"{s['t']:.1f} {self.unit}"
        tx0, ty, tx1 = L["timebar"]
        d.text((tx0, L["box"][1]), tl, font=f_big, fill=meas)
        d.line([(tx0, ty + 18), (tx1, ty + 18)], fill=mix(bg, muted, 0.6), width=4)
        u = (s["t"] - self.t0) / max(self.t1 - self.t0, 1e-9)
        d.line([(tx0, ty + 18), (tx0 + (tx1 - tx0) * u, ty + 18)], fill=meas, width=6)
        for ts_ in self.steps:
            x = tx0 + (tx1 - tx0) * (ts_ - self.t0) / max(self.t1 - self.t0, 1e-9)
            d.ellipse([x - 7, ty + 11, x + 7, ty + 25], fill=meas if ts_ <= s["t"] + 1e-9 else mix(bg, muted, 0.7))

    def _texture(self, img, pts, ink, k):
        """Штриховка внутри контура тела (маской), чтобы тело читалось как порода, а не заливка."""
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        x0, y0, x1, y1 = int(min(xs)) - 2, int(min(ys)) - 2, int(max(xs)) + 3, int(max(ys)) + 3
        w, h = max(1, x1 - x0), max(1, y1 - y0)
        mask = Image.new("L", (w * k, h * k), 0)
        mask.info["ssaa"] = k
        SDraw(mask).polygon([(x - x0, y - y0) for x, y in pts], fill=255)
        tex = Image.new("RGB", (w * k, h * k), ink)
        cover = Image.new("L", (w * k, h * k), 0)
        cover.info["ssaa"] = k
        dc = SDraw(cover)
        for yy in range(0, h, 16):
            off = (yy // 16 % 2) * 12
            for xx in range(off, w, 24):
                dc.line([(xx, yy + 8), (xx + 10, yy + 8)], fill=160, width=2)
        from PIL import ImageChops
        m = ImageChops.multiply(mask, cover)
        img.paste(tex, (x0 * k, y0 * k), m)

    def focus(self, L, s):
        b = L["area"]
        return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
