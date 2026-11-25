"""Кинематографичная отрисовка шаблона well_tool_curve для video/cine.py.

Мир (породы, ствол, корка, зона фильтрата) растеризуется один раз в метрах; камера берёт из него окно
(масштаб + центр по глубине). Всё, что несёт данные (кривые, шкала, рамки, прибор, подписи), рисуется
поверх в экранных координатах той же функцией глубина→экран — кривая всегда против своих слоёв.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageChops, ImageFilter

from ..cine import blur_bands, fbm, motion_blur_y, rock_texture, vignette
from ..draw import SDraw, dashed_hline, dashed_polyline, line_h, wrap
from ..style import font, hex_rgb, mix
from ..textclean import visible


def _alpha(born, t, fade):
    if born is None:
        return 0.0
    return min(1.0, max(0.0, (t - born) / max(fade, 1e-3)))


def cine_world(self, ppm_w: float, sec_w_world: float, H: int, ppm: float, R) -> dict:
    """Растры мира: base, +корка, +корка+фильтрат. Координаты мира — px при максимальном наезде."""
    st = self.style
    W = R.W
    zmax = R.zmax
    ext = (H / ppm) * 0.75
    z0, z1 = self.top - ext, self.bottom + ext
    wh = int((z1 - z0) * ppm_w)
    ww = int(W * zmax)
    bore_sx = W * 0.34
    bore_xw = bore_sx * zmax
    bw = float(self.p.get("bore_width", 0.18))
    bore_px = bw * ppm if bw > 1 else W * 0.6 * bw
    bore_px = max(20, min(W * 0.16, bore_px))
    half_w = bore_px * zmax / 2
    base = np.zeros((wh, ww, 3), np.float32)

    def yw(z):
        return int(round((z - z0) * ppm_w))

    layers = self.layers or [{"top": self.top, "bottom": self.bottom, "lith": None}]
    for n, Lr in enumerate(layers):
        a = max(0, yw(z0 if n == 0 else Lr["top"]))
        b = min(wh, yw(z1 if n == len(layers) - 1 else Lr["bottom"]))
        if b <= a:
            continue
        col = self.lith_color(Lr.get("lith"))
        fl = Lr.get("fluid")
        if fl and fl in st.get("fluids", {}):
            col = mix(col, hex_rgb(st["fluids"][fl]["color"]), 0.22)
        base[a:b] = rock_texture(b - a, ww, col, self._pattern_name(Lr), ppm_w, seed=17 + n)
        # граница слоёв — тонкий тёмный шов с неровностью
        if n > 0:
            edge = (fbm(1, ww, np.random.default_rng(n), base=40)[0] - 0.5) * ppm_w * 0.06
            for x in range(0, ww, 2):
                y = int(a + edge[x])
                base[max(0, y - 2):y + 2, x:x + 2] *= 0.55
    # общий свет: сверху-слева светлее
    yy = np.linspace(0, 1, wh, dtype=np.float32)[:, None]
    xx = np.linspace(0, 1, ww, dtype=np.float32)[None, :]
    base *= (1.05 - 0.18 * xx)[..., None]
    cx = bore_xw
    xs = np.arange(ww, dtype=np.float32)
    # ствол: колонна бурового раствора, цилиндрическая светотень
    mud = np.array(hex_rgb(st.get("fluids", {}).get("mud", {}).get("color", st["muted"])), np.float32)
    rel = (xs - cx) / half_w
    inb = np.abs(rel) <= 1
    shade = 0.55 + 0.55 * np.sqrt(np.clip(1 - rel ** 2, 0, 1)) + 0.25 * np.exp(-((rel + 0.35) / 0.18) ** 2)
    streak = fbm(wh, int(inb.sum()) or 1, np.random.default_rng(99), base=ppm_w * 0.5)
    mud_col = mud[None, None, :] * (shade[inb][None, :, None]) * (0.9 + 0.2 * streak[..., None])
    with_bore = base.copy()
    with_bore[:, inb] = mud_col
    # корка: тёмная шероховатая полоса по стенке против слоёв с mud_cake
    cake = with_bore.copy()
    cake_w = max(5.0, half_w * 0.22)
    rough = fbm(wh, 1, np.random.default_rng(7), base=ppm_w * 0.2)[:, 0] * cake_w * 0.6
    cake_col = mud * 0.45
    for Lr in layers:
        if not Lr.get("mud_cake"):
            continue
        a, b = max(0, yw(Lr["top"])), min(wh, yw(Lr["bottom"]))
        for side in (-1, 1):
            for y in range(a, b):
                w_ = cake_w + rough[y]
                lo = int(cx + side * half_w - (w_ if side > 0 else 0))
                hi = int(cx + side * half_w + (0 if side > 0 else w_))
                lo, hi = (lo, hi) if lo < hi else (hi, lo)
                if side < 0:
                    lo, hi = int(cx - half_w), int(cx - half_w + w_)
                else:
                    lo, hi = int(cx + half_w - w_), int(cx + half_w)
                cake[y, max(0, lo):max(0, hi)] = cake_col * (0.85 + 0.3 * rough[y] / max(cake_w, 1))
    # зона проникновения фильтрата: мягкий фронт с «языками» по зерну
    inv = cake.copy()
    filt = np.array(hex_rgb(st.get("fluids", {}).get("filtrate", {}).get("color", st["muted"])), np.float32)
    free = ww - (cx + half_w)
    for n, Lr in enumerate(layers):
        wdt = float(Lr.get("invasion_width", 0) or 0)
        if wdt <= 0:
            continue
        a, b = max(0, yw(Lr["top"])), min(wh, yw(Lr["bottom"]))
        reach = min(wdt, 1.0) * min(free, cx - half_w) * 0.9
        finger = fbm(b - a, ww, np.random.default_rng(31 + n), base=ppm_w * 0.25)
        dist = np.abs(xs[None, :] - cx) - half_w
        front = reach * (0.75 + 0.5 * finger)
        al = np.clip(1 - dist / np.maximum(front, 1), 0, 1) ** 0.8 * 0.78
        al[:, inb] = 0
        inv[a:b] = inv[a:b] * (1 - al[..., None]) + filt[None, None, :] * (0.8 + 0.3 * finger[..., None]) * al[..., None]
    def to_img(arr):
        return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    return {"base": to_img(with_bore), "cake": to_img(cake), "inv": to_img(inv), "z0": z0, "ppm_w": ppm_w,
            "bore_sx": bore_sx, "bore_xw": bore_xw, "bore_px": bore_px, "zmax": zmax,
            "vig": None}


def cine_scale_note(self, R) -> str:
    w = R.world
    k_b = w["bore_px"] / (float(self.p.get("bore_diameter_m") or 0.2) * R.ppm)
    notes = []
    if k_b >= 2:
        notes.append(f"ствол шире реального ≈ в {int(round(k_b)) if k_b < 20 else int(round(k_b, -1))} раз (общий план)")
    if any(L.get("mud_cake") for L in self.layers):
        cake_px = max(5.0, w["bore_px"] / 2 * 0.22)
        k_c = cake_px / (float(self.p.get("mud_cake_m") or 0.01) * R.ppm)
        notes.append(f"корка толще ≈ в {int(round(k_c)) if k_c < 20 else int(round(k_c, -1))} раз")
    return "; ".join(notes)


def cine_compose(self, R, s: dict, t: float, cam) -> Image.Image:
    st = self.style
    k = R.k
    W, H = R.W, R.H
    w = R.world
    zoom, cz, vel = cam
    zmax = w["zmax"]
    f = zoom / zmax                       # мир → экран
    mid_y = (R.y_top + R.y_bot) / 2
    # окно мира под экран
    yw_c = (cz - w["z0"]) * w["ppm_w"]
    x0w = w["bore_xw"] - w["bore_sx"] / f
    y0w = yw_c - mid_y / f
    box = (int(x0w), int(y0w), int(x0w + W / f), int(y0w + H / f))
    a_c, a_i = _alpha(s["cake"], t, R.fade), _alpha(s["invasion"], t, R.fade)
    crop = w["base"].crop(box)
    if a_c > 0:
        crop = Image.blend(crop, w["cake"].crop(box), a_c)
    if a_i > 0:
        crop = Image.blend(crop, w["inv"].crop(box), a_i)
    img = crop.resize((W * k, H * k), Image.BICUBIC)
    # оптика: размытие вне интервала/зоны интереса, смаз при быстром проезде, виньетка
    ys = lambda z: mid_y + (z - cz) * R.ppm * zoom  # noqa: E731
    top_sharp = max(260, ys(self.top) - 30) if zoom < 1.3 else 300
    bot_sharp = min(H - 300, ys(self.bottom) + 30) if zoom < 1.3 else H - 420
    img = blur_bands(img, int(top_sharp * k), int(bot_sharp * k), 18 * k)
    img = motion_blur_y(img, min(18, vel / 30 * k * 0.5))
    if R.__dict__.get("_vig") is None:
        v = vignette(H * k // 4, W * k // 4, 0.65, 0.42, 0.52)
        R._vig = Image.fromarray((v * 255).astype(np.uint8)).resize((W * k, H * k), Image.BILINEAR).convert("RGB")
    img = ImageChops.multiply(img, R._vig)
    img.info["ssaa"] = k
    self._cine_overlay(img, R, s, t, cam, ys)
    return img


def _cine_overlay(self, img, R, s, t, cam, ys):
    st = self.style
    k = R.k
    W, H = R.W, R.H
    zoom = cam[0]
    d = SDraw(img)
    bg, text, muted, panel = (hex_rgb(st[x]) for x in ("background", "text", "muted", "panel"))
    meas = hex_rgb(st["measurement"]["color"])
    tx0, tx1 = R.track
    w = R.world
    bcx = w["bore_sx"]
    bhalf = w["bore_px"] * zoom / 2
    ya, yb = max(0, ys(self.top)), min(H, ys(self.bottom))
    # трек: тёмное стекло поверх породы
    box = (int(tx0 * k), int(max(0, ya - 30) * k), int(tx1 * k), int(min(H, yb + 30) * k))
    if box[3] > box[1]:
        reg = img.crop(box)
        dark = Image.new("RGB", reg.size, mix(bg, panel, 0.5))
        img.paste(Image.blend(reg.filter(ImageFilter.GaussianBlur(10 * k)), dark, 0.72), box[:2])
        d.rectangle([tx0, max(0, ya - 30), tx1, min(H, yb + 30)], outline=mix(bg, muted, 0.6), width=2)
    # шкала глубин: шаг так, чтобы подписи не слипались
    f_t = font(34, True)
    step = next(sv for sv in (0.5, 1, 2, 5, 10, 20, 50, 100) if sv * R.ppm * zoom >= 95)
    v = np.ceil(self.top / step) * step
    while v <= self.bottom + 1e-9:
        y = ys(v)
        if 300 < y < H - 420:
            lab = self._fmt(v)
            d.line([(tx0, y), (tx1, y)], fill=mix(bg, muted, 0.35), width=1)
            d.text((tx1 - f_t.getlength(lab) - 10, y - line_h(f_t) - 2), lab, font=f_t, fill=mix(muted, text, 0.3),
                   stroke_width=3, stroke_fill=bg)
        v += step
    # границы слоёв продолжаются в треке — видно, что кривая отвечает слоям
    for Lr in self.layers[1:]:
        y = ys(Lr["top"])
        if 0 < y < H:
            dashed_hline(d, tx0, tx1, y, fill=mix(bg, muted, 0.75), width=2, dash=10, gap=8)
    # кривые
    for tr in self.tracks:
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
            pts = [(self.x_of(c, (tx0 + 10, tx1 - 10), v_), ys(z_)) for z_, v_ in zip(zz, vv)]
            col = self.curve_color(c)
            glow = mix(bg, col, 0.35)
            if c.get("dashed"):
                dashed_polyline(d, pts, fill=col, width=7)
            else:
                d.line(pts, fill=glow, width=16, joint="curve")
                d.line(pts, fill=col, width=7, joint="curve")
            if z0 - 1e-6 <= s["tool"] <= z1 + 1e-6:
                xv = self.x_of(c, (tx0 + 10, tx1 - 10), self.value_at(cid, s["tool"]))
                yv = ys(s["tool"])
                d.ellipse([xv - 13, yv - 13, xv + 13, yv + 13], fill=col, outline=bg, width=4)
    # шкала значений трека — внизу трека
    if self.curves:
        c = self.curves[0]
        f_s = font(30, True)
        y = min(H - 430, yb + 36)
        lo = self._fmt(float(c.get("min", 0))).replace("-", "−")
        hi = f"{self._fmt(float(c.get('max', 1))).replace('-', '−')} {visible(c.get('unit', ''), 'единица кривой')}"
        d.text((tx0 + 8, y), lo, font=f_s, fill=muted, stroke_width=3, stroke_fill=bg)
        d.text((tx1 - 8 - f_s.getlength(hi), y), hi, font=f_s, fill=muted, stroke_width=3, stroke_fill=bg)
    # подписи слоёв — мелко, с тенью, слева от ствола
    f_l = font(36, True)
    for Lr in self.layers:
        if not Lr.get("label"):
            continue
        y0_, y1_ = ys(Lr["top"]), ys(Lr["bottom"])
        yc = (max(y0_, 330) + min(y1_, H - 440)) / 2
        if not (330 < yc < H - 440) or (y1_ - y0_) < 50:
            continue
        lines = wrap(visible(Lr["label"], "подпись слоя"), f_l, bcx - bhalf - R.safe[0] - 30)
        yy = yc - len(lines) * line_h(f_l) / 2
        for ln in lines:
            d.text((R.safe[0], yy), ln, font=f_l, fill=text, stroke_width=5, stroke_fill=bg)
            yy += line_h(f_l)
    # прибор на кабеле: металл, блик, свечение в движении
    ty = ys(s["tool"])
    th = max(70, R.ppm * zoom * 1.3)
    tw = max(16, bhalf * 0.9)
    d.line([(bcx, 0), (bcx, ty - th / 2)], fill=mix(text, muted, 0.3), width=4)
    if s.get("moving"):
        for r_, a_ in ((tw * 2.6, 0.12), (tw * 1.7, 0.22)):
            d.rounded_rectangle([bcx - r_, ty - th / 2 - r_ / 2, bcx + r_, ty + th / 2 + r_ / 2], radius=int(r_),
                                fill=mix(bg, meas, a_))
    d.rounded_rectangle([bcx - tw / 2, ty - th / 2, bcx + tw / 2, ty + th / 2], radius=int(tw / 2), fill=mix(meas, (0, 0, 0), 0.25))
    d.rounded_rectangle([bcx - tw / 2 + 3, ty - th / 2 + 4, bcx - tw / 2 + tw * 0.38, ty + th / 2 - 4],
                        radius=int(tw / 4), fill=mix(meas, (255, 255, 255), 0.35))
    for j in range(1, 4):
        yy = ty - th / 2 + th * j / 4
        d.line([(bcx - tw / 2, yy), (bcx + tw / 2, yy)], fill=mix(meas, (0, 0, 0), 0.5), width=2)
    # луч синхрона: от прибора к точке кривой
    dashed_hline(d, bcx + tw, tx0, ty, fill=mix(bg, meas, 0.55), width=2, dash=8, gap=8)
    # рамки
    f_m = font(36, True)
    for mk in s["marks"]:
        a = _alpha(mk.get("born"), t, R.fade)
        if a <= 0:
            continue
        col = mix(bg, text, a)
        y0_, y1_ = ys(float(mk["top"])), ys(float(mk["bottom"]))
        rx = 24 + (tx1 - 24) * min(1, a * 1.2)
        d.rectangle([24, y0_, rx, y1_], outline=col, width=4)
        if mk.get("label") and a > 0.5:
            lab = visible(mk["label"], "подпись рамки")
            d.text((tx1 - f_m.getlength(lab) - 12, y0_ + 6), lab, font=f_m, fill=col, stroke_width=4, stroke_fill=bg)
    # подсветка слоя
    hl = s.get("highlight")
    if hl and 0 <= hl["idx"] < len(self.layers):
        a = _alpha(hl["born"], t, R.fade)
        Lr = self.layers[hl["idx"]]
        d.rectangle([20, ys(Lr["top"]), tx1 + 4, ys(Lr["bottom"])], outline=mix(bg, meas, a), width=6)
    # выноски и значение — пилюли с линией к объекту
    f_c = font(36, True)

    def pill(x, y, label, a, anchor, right=False):
        tw_ = f_c.getlength(label)
        hh = line_h(f_c) + 12
        bx = x - tw_ - 26 if right else x
        d.line([((bx + tw_ + 26) if not right else bx, y), anchor], fill=mix(bg, text, a), width=3)
        d.ellipse([anchor[0] - 7, anchor[1] - 7, anchor[0] + 7, anchor[1] + 7], fill=mix(bg, text, a))
        d.rounded_rectangle([bx, y - hh / 2, bx + tw_ + 26, y + hh / 2], radius=int(hh / 2), fill=mix(bg, text, a))
        d.text((bx + 13, y - hh / 2 + 4), label, font=f_c, fill=bg)

    placed = []
    for n, c in enumerate(s.get("callouts", [])):
        a = _alpha(c["born"] + 0.15 * n, t, 0.3)
        if a <= 0:
            continue
        tg = c.get("target", "tool")
        if tg == "tool":
            anc = (bcx, ty)
        elif tg in ("cake", "invasion", "bore"):
            key = {"cake": "mud_cake", "invasion": "invasion_width"}.get(tg)
            cand = [L for L in self.layers if (L.get(key) if key else True)] or self.layers
            Lr = min(cand, key=lambda q: abs((q["top"] + q["bottom"]) / 2 - s["tool"]))
            yz = ys((Lr["top"] + Lr["bottom"]) / 2)
            anc = (bcx - bhalf - (4 if tg == "cake" else bhalf * 1.6), yz)
        elif isinstance(tg, dict) and "depth" in tg:
            anc = (bcx * 0.5, ys(float(tg["depth"])))
        else:
            anc = (bcx, ty)
        y = min(max(anc[1] - 100, 340), H - 460)
        while any(abs(y - q) < 62 for q in placed):
            y += 64
        placed.append(y)
        pill(R.safe[0], y, visible(c["text"], "выноска"), a, anc)
    ro = s.get("readout")
    if ro:
        a = _alpha(ro["born"], t, 0.3)
        cid = ro["curve"] or next((c["id"] for c in self.curves if s["curves"][c["id"]]["visible"]), None)
        cs = s["curves"].get(cid) if cid else None
        if cs and cs["visible"] and cs["rec"] and a > 0:
            c = self._curve(cid)
            z = min(max(s["tool"], cs["rec"][0]), cs["rec"][1])
            v = self.value_at(cid, z)
            xv, yv = self.x_of(c, (tx0 + 10, tx1 - 10), v), ys(z)
            vs = (f"{v:.0f}" if abs(v) >= 10 else f"{v:.1f}").replace("-", "−").replace(".", ",")
            lab = f"≈ {vs} {visible(c.get('unit', ''), 'единица кривой')}".strip()
            y = yv + 80 if yv + 80 < H - 460 else yv - 80
            pill(min(xv + 30, tx1 - f_c.getlength(lab) - 30), y, lab, a, (xv, yv))
