"""Шаблон «well_tool_curve» (тип И — косвенное измерение).

Одна модель состояния управляет всеми видами: глубина прибора задаёт и его положение в разрезе,
и конец записанной кривой. Кривая не рисуется руками — она вычисляется из свойств тех же слоёв,
что нарисованы в разрезе, с окном усреднения прибора (params.tool_resolution_m).

params:
  depth_top, depth_bottom, depth_tick       — интервал и шаг подписей, м (глубина растёт вниз)
  tool_start                                — глубина прибора в начале
  tool_resolution_m                         — окно усреднения прибора, м
  bore_width                                — ширина ствола, доля ширины колонки разреза (0..1)
  layers[]: top, bottom, lith, label, props{}, mud_cake, invasion_width, fluid?
  curves[]: key (цвет из стайлгайда curves.<key>), prop, label, unit, min, max,
            id?, scale? ("linear"|"log"), dashed?, track? (номер трека, по умолчанию свой)

action (в beat): tool_to, tool_jump, mark {top,bottom,label?,color?} или список, clear_marks,
  show_mud_cake, show_invasion, show_curves [id...], hide_curves [id...]
"""
from __future__ import annotations

import copy
import math

import numpy as np
from PIL import Image

from ..draw import SDraw, dashed_hline, dashed_polyline, fit, line_h, text_block, wrap
from ..style import font, hex_rgb, mix
from ..textclean import visible

NAME = "well_tool_curve"

# Для генератора сценариев (video/specgen.py): когда шаблон подходит и как заполнять params/action.
SUMMARY = ("Косвенное измерение (тип И): разрез из слоёв, ствол скважины, прибор на кабеле и трек кривой. "
           "Кривая вычисляется из свойств слоёв с окном прибора и пишется до глубины прибора. Подходит, когда "
           "суть узла — как прибор/кривая отвечает на свойства пород, флюидов, раствора, проникновения, "
           "и где показания обманывают.")
GUIDE = """params:
  depth_top, depth_bottom (м, правдоподобные глубины, интервал 8–30 м), depth_tick (шаг подписей, м),
  tool_start (м, обычно чуть ниже depth_top), tool_resolution_m (окно прибора, м, 0.3–1.0),
  bore_width (доля колонки разреза, 0.12–0.22), bore_diameter_m (реальный диаметр ствола, около 0.2),
  layers[]: {top, bottom, lith (ключ из lithology стайлгайда), label (подпись по-русски, коротко),
             props {имя_свойства: число для КАЖДОЙ кривой}, mud_cake (bool), invasion_width (0–1), fluid? (ключ из fluids)}
             — слои подряд без зазоров и перекрытий, от depth_top до depth_bottom, 3–6 слоёв;
  curves[]: {id, key (ключ из curves стайлгайда — определяет цвет), prop (имя свойства в props слоёв), label,
             unit, min, max, track (номер трека; кривые одного трека сравниваются), dashed?, scale? ("log")}
             — 1–2 кривые; если сравниваются два условия — две кривые в одном треке, вторая dashed.
action (в beat, всё необязательно):
  tool_to: глубина — прибор едет туда за фразу и пишет кривую;  tool_jump: глубина — мгновенно (перезапись);
  mark: {top, bottom, label} или список — рамка интервала (контакт, коллектор);  clear_marks: true;
  show_mud_cake: true; show_invasion: true;  show_curves: [id...] — какие кривые видны и пишутся;
  callout: [{target: "tool"|"cake"|"invasion"|"bore"|"curve:<id>"|{"depth": м}|{"layer": индекс}, text}] — выноска 1–3 слова;
  readout: "<id кривой>" — плашка «≈ значение» на конце кривой (значение считает код).
Числа свойств условные — перечисли их в assumptions; важно соотношение, которое следует из главы.
Ствол в кадре всегда шире реального: не пиши «в масштабе»; во сколько раз — код допишет на плашку сам."""


def ease(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return u * u * (3 - 2 * u)


class WellToolCurve:
    name = NAME

    def __init__(self, params: dict, style: dict):
        self.p = params
        self.style = style
        self.top = float(params["depth_top"])
        self.bottom = float(params["depth_bottom"])
        if self.bottom <= self.top:
            raise ValueError("depth_bottom должна быть больше depth_top (глубина растёт вниз)")
        self.tick = float(params.get("depth_tick") or self._auto_tick())
        self.layers = sorted(params.get("layers", []), key=lambda L: L["top"])
        self.window = float(params.get("tool_resolution_m", 0.0) or 0.0)
        self.curves = []
        used = set()
        for i, c in enumerate(params.get("curves", [])):
            c = dict(c)
            cid = c.get("id") or c.get("key") or f"c{i}"
            if cid in used:
                cid = f"{cid}_{i}"
            used.add(cid)
            c["id"] = cid
            if c.get("log") and not c.get("scale"):
                c["scale"] = "log"
            self.curves.append(c)
        # треки: кривые с одинаковым track рисуются в одном треке
        tracks: dict = {}
        for i, c in enumerate(self.curves):
            tracks.setdefault(c.get("track", i), []).append(c["id"])
        self.tracks = list(tracks.values())
        self.z = np.linspace(self.top, self.bottom, 1601)
        self.values = {c["id"]: self.compute_curve(c) for c in self.curves}

    # ------------------------------------------------------------ модель
    def _auto_tick(self) -> float:
        span = self.bottom - self.top
        for t in (0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500):
            if span / t <= 10:
                return t
        return 1000

    def layer_at(self, z: float) -> dict | None:
        for L in self.layers:
            if L["top"] <= z < L["bottom"]:
                return L
        if self.layers and z >= self.layers[-1]["bottom"]:
            return self.layers[-1]
        return self.layers[0] if self.layers else None

    def compute_curve(self, c: dict) -> np.ndarray:
        """Отклик прибора: свойство слоя на глубине, усреднённое окном прибора."""
        prop = c["prop"]
        raw = np.array([float((self.layer_at(z) or {}).get("props", {}).get(prop, np.nan)) for z in self.z])
        if np.isnan(raw).all():
            raw = np.zeros_like(raw)
        raw = np.where(np.isnan(raw), np.nanmean(raw), raw)
        log = c.get("scale") == "log"
        work = np.log10(np.clip(raw, 1e-6, None)) if log else raw
        dz = self.z[1] - self.z[0]
        n = int(round(self.window / dz))
        if n >= 2:
            k = np.ones(n) / n
            pad = np.pad(work, (n, n), mode="edge")
            work = np.convolve(pad, k, mode="same")[n:-n]
        return 10 ** work if log else work

    def value_at(self, cid: str, z: float) -> float:
        return float(np.interp(z, self.z, self.values[cid]))

    def initial_state(self) -> dict:
        tool = float(self.p.get("tool_start", self.top))
        return {
            "tool": tool,
            "cake": None, "invasion": None,          # время появления (с), None — скрыто
            "marks": [],
            "curves": {c["id"]: {"visible": True, "rec": None} for c in self.curves},
            "callouts": [], "readout": None, "highlight": None,
        }

    def begin_beat(self, state: dict, action: dict, t: float) -> dict:
        """Мгновенные действия в начале фразы."""
        s = copy.deepcopy(state)
        if action.get("clear_marks"):
            s["marks"] = []
        marks = action.get("mark")
        if marks:
            for m in (marks if isinstance(marks, list) else [marks]):
                s["marks"].append({**m, "born": t})
        if "show_mud_cake" in action:
            s["cake"] = t if action["show_mud_cake"] else None
        if "show_invasion" in action:
            s["invasion"] = t if action["show_invasion"] else None
        if "show_curves" in action:
            ids = set(action["show_curves"])
            for cid, cs in s["curves"].items():
                cs["visible"] = cid in ids
        for cid in action.get("hide_curves", []) or []:
            if cid in s["curves"]:
                s["curves"][cid]["visible"] = False
        co = action.get("callout")
        s["callouts"] = [{**c, "born": t} for c in (co if isinstance(co, list) else [co])] if co else []
        hl = action.get("highlight_layer")
        s["highlight"] = {"idx": int(hl), "born": t} if hl is not None and hl is not False else None
        ro = action.get("readout")
        s["readout"] = ({"curve": ro if isinstance(ro, str) else None, "born": t} if ro else None)
        if "tool_jump" in action:
            s["tool"] = self._clamp(float(action["tool_jump"]))
        self._record(s, s["tool"], s["tool"])
        return s

    def _clamp(self, z: float) -> float:
        return min(max(z, self.top), self.bottom)

    def _record(self, s: dict, z0: float, z1: float):
        lo, hi = min(z0, z1), max(z0, z1)
        for cs in s["curves"].values():
            if not cs["visible"]:
                continue
            if cs["rec"] is None:
                cs["rec"] = [lo, hi]
            else:
                cs["rec"] = [min(cs["rec"][0], lo), max(cs["rec"][1], hi)]

    def at(self, start: dict, action: dict, u: float) -> dict:
        """Состояние внутри фразы, u ∈ [0, 1] — доля её длительности."""
        if "tool_to" not in action:
            return start
        s = copy.deepcopy(start)
        z0, z1 = start["tool"], self._clamp(float(action["tool_to"]))
        z = z0 + (z1 - z0) * ease(u)
        s["tool"] = z
        s["moving"] = 0.0 < u < 1.0 and abs(z1 - z0) > 1e-6
        self._record(s, z0, z)
        return s

    # ------------------------------------------------------------ проверка инвариантов
    def check(self, states: list[dict]) -> list[dict]:
        res = []
        ok_depth = self.bottom > self.top
        res.append({"item": "глубина растёт вниз", "ok": ok_depth})
        ok_layers = all(L["bottom"] > L["top"] for L in self.layers) and all(
            a["bottom"] <= b["top"] + 1e-9 for a, b in zip(self.layers, self.layers[1:]))
        res.append({"item": "слои не перекрываются, порядок сверху вниз", "ok": ok_layers})
        ok_end = True
        for s in states:
            for cid, cs in s["curves"].items():
                if cs["visible"] and cs["rec"] and abs(cs["rec"][1] - s["tool"]) > 1e-6 and s["tool"] >= cs["rec"][0]:
                    # конец кривой может быть ниже прибора только если прибор подняли (tool_jump вверх)
                    if s["tool"] > cs["rec"][1]:
                        ok_end = False
        res.append({"item": "конец кривой не ниже прибора (кривая записывается прибором)", "ok": ok_end})
        ok_range = all(self.top <= s["tool"] <= self.bottom for s in states)
        res.append({"item": "прибор внутри интервала разреза", "ok": ok_range})
        res.append({"item": "кривая вычислена из свойств слоёв окном прибора "
                            f"{self.window:g} м", "ok": all(c["prop"] for c in self.curves)})
        return res

    # ------------------------------------------------------------ раскладка
    def layout(self, box, compact: bool):
        x0, y0, x1, y1 = box
        st = self.style
        f_hdr = font(44, True)
        f_tick = font(40)
        axis_w = int(max(f_tick.getlength(self._fmt(v)) for v in self._ticks())) + 26
        n_tr = max(1, len(self.tracks))
        W = x1 - x0
        avail = W - axis_w - 16
        sec_w = int(avail * (0.48 if n_tr == 1 else 0.42))
        if not compact:
            sec_w = int(min(sec_w, avail * 0.5))
        gap = 28
        tr_w = (avail - sec_w - gap - (n_tr - 1) * 18) / n_tr
        # шапка трека как у планшета: строка на каждую кривую (образец линии + подпись) + шкала
        f_row, f_sc = font(42, True), font(36)
        rows = max(len(tr) for tr in self.tracks) if self.tracks else 1
        hdr_h = line_h(f_row) * rows + line_h(f_sc) + 34
        hdr_h = max(hdr_h, int(line_h(font(40)) * 2.2) + 8)  # «глубина, м ↓» слева
        plot_top = y0 + hdr_h
        L = {
            "box": box, "axis": (x0, x0 + axis_w), "plot": (plot_top, y1 - 6),
            "sec": (x0 + axis_w + 12, x0 + axis_w + 12 + sec_w),
            "tracks": [], "hdr_top": y0,
        }
        L["camera"] = (x0, plot_top - 28, x1, y1 + 26)
        tx = L["sec"][1] + gap
        for _ in self.tracks:
            L["tracks"].append((int(tx), int(tx + tr_w)))
            tx += tr_w + 18
        sx0, sx1 = L["sec"]
        bw = float(self.p.get("bore_width", 0.18))
        if bw > 1:  # метры в масштабе глубин
            px_per_m = (y1 - 6 - plot_top) / (self.bottom - self.top)
            bore_px = max(18, min(sec_w * 0.32, bw * px_per_m))
        else:
            bore_px = max(18, min(sec_w * 0.32, sec_w * bw))
        cx = sx0 + sec_w * 0.74
        L["bore"] = (cx - bore_px / 2, cx + bore_px / 2)
        return L

    def _curve(self, cid):
        return next(c for c in self.curves if c["id"] == cid)

    def _ticks(self):
        t0 = math.ceil(self.top / self.tick - 1e-9) * self.tick
        v, out = t0, []
        while v <= self.bottom + 1e-9:
            out.append(round(v, 6))
            v += self.tick
        return out

    @staticmethod
    def _fmt(v: float) -> str:
        return (f"{v:g}" if abs(v - round(v)) > 1e-9 else str(int(round(v)))).replace(".", ",")

    def y_of(self, L, z):
        p0, p1 = L["plot"]
        return p0 + (z - self.top) / (self.bottom - self.top) * (p1 - p0)

    def x_of(self, c, xr, v):
        lo, hi = float(c.get("min", 0)), float(c.get("max", 1))
        if c.get("scale") == "log":
            lo, hi, v = math.log10(max(lo, 1e-6)), math.log10(max(hi, 1e-6)), math.log10(max(v, 1e-6))
        u = (v - lo) / (hi - lo) if hi != lo else 0.5
        u = min(max(u, -0.02), 1.02)
        pad = 10
        return xr[0] + pad + u * (xr[1] - xr[0] - 2 * pad)

    def curve_color(self, c):
        return hex_rgb(self.style.get("curves", {}).get(c.get("key", ""), {}).get("color", self.style["measurement"]["color"]))

    def lith_color(self, lith):
        return hex_rgb(self.style.get("lithology", {}).get(lith, {}).get("color", self.style["panel"]))

    def _cake_px(self, L):
        return max(5, (L["bore"][1] - L["bore"][0]) * 0.12)

    def scale_note(self, L) -> str:
        """Преувеличения числом, по нарисованному кадру: ствол (params.bore_diameter_m, по умолчанию 0,2 м)
        и глинистая корка (params.mud_cake_m, по умолчанию 0,01 м), если она есть в сцене."""
        px_per_m = (L["plot"][1] - L["plot"][0]) / (self.bottom - self.top)

        def times(drawn, real_m):
            k = drawn / max(real_m * px_per_m, 1e-9)
            return int(round(k)) if k < 20 else int(round(k, -1))
        notes = []
        kb = times(L["bore"][1] - L["bore"][0], float(self.p.get("bore_diameter_m") or 0.2))
        if kb >= 2:
            notes.append(f"ствол шире реального ≈ в {kb} раз")
        if any(Lr.get("mud_cake") for Lr in self.layers):
            kc = times(self._cake_px(L), float(self.p.get("mud_cake_m") or 0.01))
            if kc >= 2:
                notes.append(f"корка толще ≈ в {kc} раз")
        return "; ".join(notes)

    scale_keyword = ("ствол", "корк")

    def focus(self, L, s) -> tuple[float, float]:
        """Точка интереса для камеры: прибор."""
        return ((L["bore"][0] + L["bore"][1]) / 2, self.y_of(L, s["tool"]))

    def _pattern_name(self, Lr) -> str:
        """Узор породы: из слоя (params.layers[].pattern) или стайлгайда (lithology.<lith>.pattern)."""
        return (Lr.get("pattern") or self.style.get("lithology", {}).get(Lr.get("lith"), {}).get("pattern")
                or "grain")

    def _pattern(self, img, rect, col, name):
        """Условный узор литологии (как на геологических колонках): точки, штрих, кирпич, линии…"""
        x0, y0, x1, y1 = rect
        k = img.info.get("ssaa", 1)
        w, h = int((x1 - x0) * k), int((y1 - y0) * k)
        if w < 4 or h < 4 or name == "none":
            return
        tile = Image.new("RGB", (w, h), col)
        tile.info["ssaa"] = k
        d = SDraw(tile)
        ink = mix(col, (0, 0, 0), 0.22)
        W, H = x1 - x0, y1 - y0
        rng = np.random.default_rng(int(x0 * 7 + y0 * 13) % 2**32)
        if name in ("dots", "sand"):
            step = 18
            for j, yy in enumerate(np.arange(6, H, step)):
                for xx in np.arange(6 + (j % 2) * step / 2, W, step):
                    d.ellipse([xx - 2.4, yy - 2.4, xx + 2.4, yy + 2.4], fill=ink)
        elif name in ("lines", "shale"):
            for yy in np.arange(8, H, 14):
                xx = 0
                while xx < W:
                    ln = rng.uniform(26, 60)
                    d.line([(xx, yy), (min(W, xx + ln), yy)], fill=ink, width=2)
                    xx += ln + rng.uniform(8, 16)
        elif name in ("brick", "limestone"):
            for j, yy in enumerate(np.arange(0, H, 22)):
                d.line([(0, yy), (W, yy)], fill=ink, width=2)
                for xx in np.arange((j % 2) * 22, W, 44):
                    d.line([(xx, yy), (xx, yy + 22)], fill=ink, width=2)
        elif name in ("rhomb", "dolomite"):
            for j, yy in enumerate(np.arange(0, H, 22)):
                d.line([(0, yy), (W, yy)], fill=ink, width=2)
                for xx in np.arange((j % 2) * 22, W, 44):
                    d.line([(xx, yy), (xx + 11, yy + 22)], fill=ink, width=2)
        elif name in ("hatch", "dense"):
            for xx in np.arange(-H, W, 16):
                d.line([(xx, H), (xx + H, 0)], fill=ink, width=2)
        elif name == "cross":
            for yy in np.arange(10, H, 22):
                for xx in np.arange(10, W, 22):
                    d.line([(xx - 4, yy), (xx + 4, yy)], fill=ink, width=2)
                    d.line([(xx, yy - 4), (xx, yy + 4)], fill=ink, width=2)
        else:  # grain: мелкое зерно, без смысловой нагрузки
            n = int(W * H / 260)
            for xx, yy in zip(rng.uniform(0, W, n), rng.uniform(0, H, n)):
                r = rng.uniform(1.0, 2.2)
                d.ellipse([xx - r, yy - r, xx + r, yy + r], fill=mix(col, (0, 0, 0), rng.uniform(0.08, 0.2)))
        img.paste(tile, (int(round(x0 * k)), int(round(y0 * k))))

    def anchor(self, L, s, target) -> tuple[float, float]:
        """Точка объекта для выноски: tool, bore, cake, invasion, curve:<id>, {depth}, {layer}."""
        bx0, bx1 = L["bore"]
        cx = (bx0 + bx1) / 2
        if isinstance(target, dict):
            if "depth" in target:
                z = float(target["depth"])
                return ((L["sec"][0] + bx0) / 2, self.y_of(L, z))
            if "layer" in target:
                Lr = self.layers[int(target["layer"])]
                return ((L["sec"][0] + bx0) / 2, self.y_of(L, (Lr["top"] + Lr["bottom"]) / 2))
        if target == "tool":
            return (cx, self.y_of(L, s["tool"]))
        if target in ("cake", "invasion"):
            key = "mud_cake" if target == "cake" else "invasion_width"
            cand = [Lr for Lr in self.layers if Lr.get(key)]
            if cand:
                Lr = min(cand, key=lambda q: abs((q["top"] + q["bottom"]) / 2 - s["tool"]))
                y = self.y_of(L, (Lr["top"] + Lr["bottom"]) / 2)
                if target == "cake":
                    return (bx0 + 3, y)
                ext = min(float(Lr.get("invasion_width", 0.3)), 1.0) * (L["sec"][1] - bx1) * 0.95
                return (bx0 - ext * 0.6, y)
        if isinstance(target, str) and target.startswith("curve"):
            cid = target.split(":", 1)[1] if ":" in target else self.curves[0]["id"]
            return self._tip(L, s, cid) or (cx, self.y_of(L, s["tool"]))
        return (cx, (L["plot"][0] + L["plot"][1]) / 2)

    def _tip(self, L, s, cid):
        cs = s["curves"].get(cid)
        if not cs or not cs["visible"] or not cs["rec"]:
            return None
        for tr, xr in zip(self.tracks, L["tracks"]):
            if cid in tr:
                z = min(max(s["tool"], cs["rec"][0]), cs["rec"][1])
                return (self.x_of(self._curve(cid), xr, self.value_at(cid, z)), self.y_of(L, z))
        return None

    def draw_overlays(self, img, L, s, t, fade):
        """Выноски-«таблетки» с линией к объекту и значение на конце кривой (число — из модели)."""
        st = self.style
        d = SDraw(img)
        bg, text = hex_rgb(st["background"]), hex_rgb(st["text"])
        f = font(40, True)
        p0, p1 = L["plot"]

        def pill(x, y, label, a, anchor_pt, right_align=False):
            tw = f.getlength(label)
            h = line_h(f) + 14
            bx = x - tw - 28 if right_align else x
            box = (bx, y - h / 2, bx + tw + 28, y + h / 2)
            ax, ay = anchor_pt
            near_x = box[2] if ax > box[2] else (box[0] if ax < box[0] else ax)
            d.line([(near_x, y), (ax, ay)], fill=mix(bg, text, a), width=3)
            d.ellipse([ax - 7, ay - 7, ax + 7, ay + 7], fill=mix(bg, text, a), outline=bg, width=2)
            d.rounded_rectangle(box, radius=int(h / 2), fill=mix(bg, text, a))
            d.text((box[0] + 14, y - h / 2 + 5), label, font=f, fill=bg)

        def al(born):
            return min(1.0, max(0.0, (t - born) / 0.3)) if born is not None else 0.0

        placed = []
        ph = line_h(f) + 22
        for n, c in enumerate(s.get("callouts", [])):
            a = al(c["born"] + 0.15 * n)
            if a <= 0:
                continue
            ax, ay = self.anchor(L, s, c.get("target", "tool"))
            y = min(max(ay - 90, p0 + 30), p1 - 30)
            for _ in range(8):
                clash = [q for q in placed if abs(q - y) < ph]
                if not clash:
                    break
                y = max(clash) + ph if max(clash) + ph < p1 - 30 else min(clash) - ph
            placed.append(y)
            if ax > (L["sec"][0] + L["sec"][1]) / 2 or c.get("target") == "tool":
                x = L["sec"][0] + 8
                pill(x, y, visible(c["text"], "выноска"), a, (ax, ay))
            else:
                pill(L["sec"][1] - 8, y, visible(c["text"], "выноска"), a, (ax, ay), right_align=True)
        ro = s.get("readout")
        if ro:
            a = al(ro["born"])
            cid = ro["curve"] or next((c["id"] for c in self.curves if s["curves"][c["id"]]["visible"]), None)
            tip = self._tip(L, s, cid) if cid else None
            if tip and a > 0:
                c = self._curve(cid)
                v = self.value_at(cid, min(max(s["tool"], s["curves"][cid]["rec"][0]), s["curves"][cid]["rec"][1]))
                vs = (f"{v:.0f}" if abs(v) >= 10 else f"{v:.1f}").replace("-", "−").replace(".", ",")
                lab = f"≈ {vs} {c.get('unit', '')}".strip()
                xr = next(x for tr, x in zip(self.tracks, L["tracks"]) if cid in tr)
                left = tip[0] > (xr[0] + xr[1]) / 2
                y = tip[1] + 70 if tip[1] + 70 < p1 - 30 else max(tip[1] - 70, p0 + 30)
                if left:
                    pill(tip[0] - 40, y, lab, a, tip, right_align=True)
                else:
                    pill(tip[0] + 40, y, lab, a, tip)

    def draw_intro(self, img, L, u: float):
        """Спуск с поверхности к интервалу (скачок масштаба): поверхность с вышкой уходит вверх,
        мимо пролетают пласты, счётчик глубины растёт до кровли интервала. u ∈ [0, 1]."""
        st = self.style
        k = img.info.get("ssaa", 1)
        x0, y0, x1, y1 = L["box"]
        y1 += 26
        W, H = x1 - x0, y1 - y0
        tile = Image.new("RGB", (int(W * k), int(H * k)), hex_rgb(st["background"]))
        tile.info["ssaa"] = k
        d = SDraw(tile)
        bg, text, muted = hex_rgb(st["background"]), hex_rgb(st["text"]), hex_rgb(st["muted"])
        e = ease(u)
        depth_label = f"{int(round(self.top * e)):,} м".replace(",", " ")
        world_h = H * 4.0
        off = e * (world_h - H)                      # камера едет вниз по «миру»
        ground = H * 0.42 - off
        cx = (L["bore"][0] + L["bore"][1]) / 2 - x0
        # пласты: цвета литологий этой сцены, повторяются, становятся темнее с глубиной
        cols = [self.lith_color(Lr.get("lith")) for Lr in self.layers] or [hex_rgb(st["panel"])]
        y, i = ground, 0
        rng = np.random.default_rng(3)
        while y < H:
            hh = rng.uniform(H * 0.18, H * 0.42)
            if y + hh > 0:
                col = mix(cols[i % len(cols)], bg, min(0.55, 0.15 + 0.4 * (y - ground) / world_h))
                d.rectangle([0, max(0, y), W, min(H, y + hh)], fill=col)
            y += hh
            i += 1
        # ствол и кабель
        bw = (L["bore"][1] - L["bore"][0])
        mud = hex_rgb(st.get("fluids", {}).get("mud", {}).get("color", st["muted"]))
        d.rectangle([cx - bw / 2, max(0, ground), cx + bw / 2, H], fill=mud)
        d.line([(cx, max(0, ground - 140)), (cx, H)], fill=text, width=3)
        # поверхность: вышка и каротажный подъёмник силуэтами
        if ground > -10:
            gy = ground
            d.rectangle([0, gy - 4, W, gy + 4], fill=muted)
            top = gy - min(330, H * 0.5)
            for sx in (-1, 1):
                d.line([(cx + sx * 70, gy), (cx + sx * 14, top)], fill=text, width=5)
            for j in range(1, 6):
                yy = gy - (gy - top) * j / 6
                half = 70 - (70 - 14) * j / 6
                d.line([(cx - half, yy), (cx + half, yy)], fill=muted, width=3)
                d.line([(cx - half, yy), (cx + half * 0.8, yy - (gy - top) / 6)], fill=muted, width=2)
            d.rectangle([cx - 26, top - 16, cx + 26, top], fill=text)
            tx = max(30, cx - 330)
            d.rounded_rectangle([tx, gy - 92, tx + 170, gy - 14], radius=10, fill=muted)
            d.rounded_rectangle([tx + 170, gy - 70, tx + 230, gy - 14], radius=8, fill=text)
            for wx in (tx + 34, tx + 130, tx + 200):
                d.ellipse([wx - 18, gy - 34, wx + 18, gy + 2], fill=bg, outline=text, width=4)
            d.line([(tx + 170, gy - 80), (cx, top + 8)], fill=text, width=2)
        # счётчик глубины
        f_big = font(110, True)
        d.text((W / 2, H * 0.5), depth_label, font=f_big, fill=text, anchor="mm", stroke_width=6, stroke_fill=bg)
        img.paste(tile, (int(round(x0 * k)), int(round(y0 * k))))

    # ------------------------------------------------------------ рисование
    def draw_static(self, img: Image.Image, L: dict):
        """Неподвижная часть: шкала глубин, слои, рамки треков, сетка. Рисуется один раз."""
        st = self.style
        d = SDraw(img)
        bg, text, muted, panel = (hex_rgb(st[k]) for k in ("background", "text", "muted", "panel"))
        p0, p1 = L["plot"]
        sx0, sx1 = L["sec"]
        bx0, bx1 = L["bore"]
        f_tick, f_hdr = font(40), font(44, True)
        # заголовок шкалы глубин
        f_ax = font(40)
        d.text((L["axis"][0], L["plot"][0] - line_h(f_ax) * 1.7), "глубина, м ↓", font=f_ax, fill=muted)
        # слои
        for Lr in self.layers:
            ya, yb = self.y_of(L, max(Lr["top"], self.top)), self.y_of(L, min(Lr["bottom"], self.bottom))
            if yb <= ya:
                continue
            col = self.lith_color(Lr.get("lith"))
            fl = Lr.get("fluid")
            if fl and fl in st.get("fluids", {}):
                col = mix(col, hex_rgb(st["fluids"][fl]["color"]), 0.28)
            d.rectangle([sx0, ya, sx1, yb], fill=col)
            self._pattern(img, (sx0, ya, sx1, yb), col, self._pattern_name(Lr))
            d.line([(sx0, ya), (sx1, ya)], fill=mix(col, bg, 0.5), width=2)
        d.rectangle([sx0, p0, sx1, p1], outline=muted, width=2)
        # подписи слоёв слева от ствола
        lab_w = bx0 - sx0 - 22
        for Lr in self.layers:
            if not Lr.get("label"):
                continue
            ya, yb = self.y_of(L, max(Lr["top"], self.top)), self.y_of(L, min(Lr["bottom"], self.bottom))
            h = yb - ya - 8
            for size in (44, 40, 36, 32, 28, 26):
                f = font(size, True)
                ls = wrap(visible(Lr["label"], "подпись слоя"), f, lab_w)
                if len(ls) * line_h(f) <= h and all(f.getlength(x) <= lab_w for x in ls):
                    break
            else:
                continue
            th = len(ls) * line_h(f)
            ty = ya + (yb - ya - th) / 2
            tw = max(f.getlength(x) for x in ls)
            d.rounded_rectangle([sx0 + 6, ty - 4, sx0 + 18 + tw, ty + th + 2], radius=10, fill=(*bg,))
            text_block(d, (sx0 + 12, ty - 2), ls, f, text)
        # шкала глубин
        for v in self._ticks():
            y = self.y_of(L, v)
            s = self._fmt(v)
            fh = f_tick.getbbox(s)
            d.text((L["axis"][1] - 16 - f_tick.getlength(s), y - (fh[1] + fh[3]) / 2), s, font=f_tick, fill=muted)
            d.line([(L["axis"][1] - 10, y), (sx0, y)], fill=muted, width=2)
        # треки
        for tr, xr in zip(self.tracks, L["tracks"]):
            d.rectangle([xr[0], p0, xr[1], p1], fill=mix(panel, bg, 0.3), outline=muted, width=2)
            for v in self._ticks():
                y = self.y_of(L, v)
                d.line([(xr[0], y), (xr[1], y)], fill=mix(panel, muted, 0.25), width=1)
            for Lr in self.layers[1:]:
                y = self.y_of(L, Lr["top"])
                if p0 < y < p1:
                    dashed_hline(d, xr[0], xr[1], y, fill=mix(panel, muted, 0.6), width=2, dash=10, gap=8)
            # шапка трека: на каждую кривую образец линии + подпись, ниже шкала
            y = L["hdr_top"]
            tw_ = xr[1] - xr[0]
            for cid in tr:
                c = self._curve(cid)
                col = self.curve_color(c)
                sample = 36
                fh_, ls = fit(visible(c.get("label", cid), "подпись кривой"), tw_ - sample - 12, 42, 34, 1, True)
                ly = y + line_h(fh_) / 2
                if c.get("dashed"):
                    dashed_polyline(d, [(xr[0], ly), (xr[0] + sample, ly)], fill=col, width=6, dash=12, gap=8)
                else:
                    d.line([(xr[0], ly), (xr[0] + sample, ly)], fill=col, width=6)
                d.text((xr[0] + sample + 12, y), ls[0], font=fh_, fill=col)
                y += line_h(font(42, True))
            c = self._curve(tr[0])
            sc = f"{self._fmt(float(c.get('min', 0)))} … {self._fmt(float(c.get('max', 1)))} {c.get('unit', '')}".strip()
            fs, ls = fit(sc.replace("-", "−"), tw_, 36, 28, 1)
            yb_ = L["plot"][0] - line_h(fs) - 30
            d.text((xr[0] + 4, yb_), self._fmt(float(c.get('min', 0))).replace("-", "−"), font=fs, fill=muted)
            mx = f"{self._fmt(float(c.get('max', 1))).replace('-', '−')} {visible(c.get('unit', ''), 'единица кривой')}".strip()
            d.text((xr[1] - 4 - fs.getlength(mx), yb_), mx, font=fs, fill=muted)
        # ствол (буровой раствор)
        mud = hex_rgb(st.get("fluids", {}).get("mud", {}).get("color", st["muted"]))
        d.rectangle([bx0, p0, bx1, p1], fill=mud)

    def draw_dynamic(self, img: Image.Image, L: dict, s: dict, t: float, fade: float):
        st = self.style
        d = SDraw(img)
        bg, text = hex_rgb(st["background"]), hex_rgb(st["text"])
        p0, p1 = L["plot"]
        sx0, sx1 = L["sec"]
        bx0, bx1 = L["bore"]
        mud = hex_rgb(st.get("fluids", {}).get("mud", {}).get("color", st["muted"]))

        def alpha(born):
            return 0.0 if born is None else min(1.0, max(0.0, (t - born) / fade)) if fade > 0 else 1.0

        # зона проникновения фильтрата
        a_inv = alpha(s["invasion"])
        if a_inv > 0:
            filt = hex_rgb(st.get("fluids", {}).get("filtrate", {}).get("color", st["muted"]))
            half_free = (sx1 - bx1)
            for Lr in self.layers:
                w = float(Lr.get("invasion_width", 0) or 0)
                if w <= 0:
                    continue
                ya, yb = self.y_of(L, max(Lr["top"], self.top)), self.y_of(L, min(Lr["bottom"], self.bottom))
                ext = min(w, 1.0) * half_free * 0.95 + 6
                col = mix(self.lith_color(Lr.get("lith")), filt, 0.85 * a_inv)
                d.rectangle([max(sx0, bx0 - ext), ya + 1, bx0, yb - 1], fill=col)
                d.rectangle([bx1, ya + 1, min(sx1 - 2, bx1 + ext), yb - 1], fill=col)
        # глинистая корка
        a_cake = alpha(s["cake"])
        if a_cake > 0:
            cake = mix(mud, (20, 16, 12), 0.45)
            cw = self._cake_px(L)
            for Lr in self.layers:
                if not Lr.get("mud_cake"):
                    continue
                ya, yb = self.y_of(L, max(Lr["top"], self.top)), self.y_of(L, min(Lr["bottom"], self.bottom))
                col = mix(mud, cake, a_cake)
                d.rectangle([bx0, ya, bx0 + cw, yb], fill=col)
                d.rectangle([bx1 - cw, ya, bx1, yb], fill=col)
        # прибор на кабеле
        meas = hex_rgb(st["measurement"]["color"])
        ty = self.y_of(L, s["tool"])
        cx = (bx0 + bx1) / 2
        tw = max(10, (bx1 - bx0) * 0.42)
        th = max(46, (p1 - p0) / (self.bottom - self.top) * 1.2)
        d.line([(cx, p0 - 24), (cx, ty - th / 2)], fill=text, width=3)
        if s.get("moving"):  # мягкое свечение прибора, пока он едет и пишет кривую
            for r, a_ in ((tw * 1.6, 0.18), (tw * 1.15, 0.3)):
                d.rounded_rectangle([cx - r, ty - th / 2 - r / 2, cx + r, ty + th / 2 + r / 2], radius=int(r),
                                    fill=mix(mud, meas, a_))
        d.rounded_rectangle([cx - tw / 2, ty - th / 2, cx + tw / 2, ty + th / 2], radius=int(tw / 2.5), fill=meas)
        d.ellipse([cx - 6, ty - 6, cx + 6, ty + 6], fill=bg)
        # линия синхрона: прибор → точка кривой
        if L["tracks"]:
            dashed_hline(d, bx1 + 4, L["tracks"][-1][1], ty, fill=mix(meas, bg, 0.35), width=2, dash=8, gap=8)
        # кривые
        for tr, xr in zip(self.tracks, L["tracks"]):
            for cid in tr:
                cs = s["curves"][cid]
                if not cs["visible"] or not cs["rec"]:
                    continue
                c = self._curve(cid)
                z0, z1 = cs["rec"]
                m = (self.z >= z0) & (self.z <= z1)
                zz = np.concatenate([self.z[m], [z1]])
                vv = np.concatenate([self.values[cid][m], [self.value_at(cid, z1)]])
                if len(zz) < 2:
                    continue
                pts = [(self.x_of(c, xr, v), self.y_of(L, z)) for z, v in zip(zz, vv)]
                col = self.curve_color(c)
                if c.get("dashed"):
                    dashed_polyline(d, pts, fill=col, width=6)
                else:
                    d.line(pts, fill=col, width=6, joint="curve")
                if z0 - 1e-6 <= s["tool"] <= z1 + 1e-6:
                    xv = self.x_of(c, xr, self.value_at(cid, s["tool"]))
                    d.ellipse([xv - 10, ty - 10, xv + 10, ty + 10], fill=col, outline=bg, width=3)
        # подсветка слоя: рамка вокруг слоя через разрез и треки
        hlt = s.get("highlight")
        if hlt and 0 <= hlt["idx"] < len(self.layers):
            a = alpha(hlt["born"])
            Lr = self.layers[hlt["idx"]]
            ya, yb = self.y_of(L, max(Lr["top"], self.top)), self.y_of(L, min(Lr["bottom"], self.bottom))
            right_ = L["tracks"][-1][1] if L["tracks"] else sx1
            col = mix(bg, hex_rgb(st["measurement"]["color"]), a)
            d.rectangle([sx0 - 8, ya - 2, right_ + 8, yb + 2], outline=col, width=5)
        # рамки (контакты, интервалы)
        f_m = font(40, True)
        right = L["tracks"][-1][1] if L["tracks"] else sx1
        for mk in s["marks"]:
            a = alpha(mk.get("born"))
            if a <= 0:
                continue
            colname = mk.get("color")
            base = hex_rgb(st[colname]["color"]) if colname in st and isinstance(st[colname], dict) else text
            col = mix(bg, base, a)
            ya, yb = self.y_of(L, float(mk["top"])), self.y_of(L, float(mk["bottom"]))
            if yb - ya < 10:
                ya, yb = (ya + yb) / 2 - 5, (ya + yb) / 2 + 5
            # рамка «прорисовывается» слева направо через разрез и треки
            rx = sx0 - 6 + (right + 12 - sx0) * ease(a)
            d.rectangle([sx0 - 6, ya, rx, yb], outline=col, width=4)
            if mk.get("label"):
                lab = visible(mk["label"], "подпись рамки")
                lw = f_m.getlength(lab)
                lx = right - lw - 14
                ly = ya + 4 if yb - ya > line_h(f_m) + 8 else ya - line_h(f_m) - 2
                if ly < p0:
                    ly = yb + 4
                d.text((lx, ly), lab, font=f_m, fill=col, stroke_width=4, stroke_fill=bg)


# кинематографичная отрисовка для шортса (video/cine.py)
from . import wtc_cine as _cine  # noqa: E402

WellToolCurve.cine_world = _cine.cine_world
WellToolCurve.cine_compose = _cine.cine_compose
WellToolCurve.cine_scale_note = _cine.cine_scale_note
WellToolCurve._cine_overlay = _cine._cine_overlay
