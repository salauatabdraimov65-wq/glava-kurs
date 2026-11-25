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
from ..style import font, hex_rgb, mix, mono_font
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
    bore_sx = W * 0.15
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
    top_sharp = max(R.y_top - 20, ys(self.top) - 30)
    bot_sharp = min(R.y_bot + 20, ys(self.bottom) + 30)
    img = blur_bands(img, int(top_sharp * k), int(bot_sharp * k), 18 * k)
    img = motion_blur_y(img, min(18, vel / 30 * k * 0.5))
    if R.__dict__.get("_vig") is None:
        v = vignette(H * k // 4, W * k // 4, 0.65, 0.42, 0.52)
        R._vig = Image.fromarray((v * 255).astype(np.uint8)).resize((W * k, H * k), Image.BILINEAR).convert("RGB")
    img = ImageChops.multiply(img, R._vig)
    img.info["ssaa"] = k
    world = img.copy()
    self._cine_overlay(img, R, s, t, cam, ys)
    # данные живут только в «сцене» между заголовком и субтитрами: снаружи — порода в расфокусе
    if R.__dict__.get("_stage") is None:
        yy = np.arange(H * k, dtype=np.float32)[:, None] / k
        feather = 46
        m = np.clip((yy - (R.y_top - 40)) / feather, 0, 1) * np.clip(((R.y_bot + 70) - yy) / feather, 0, 1)
        R._stage = Image.fromarray((m * 255).astype(np.uint8).repeat(W * k, axis=1))
    out = Image.composite(img, world, R._stage)
    out.info["ssaa"] = k
    return out


def _qual(u: float) -> str:
    return "низкое" if u < 0.34 else ("среднее" if u < 0.67 else "высокое")


def _norm(self, c, v):
    lo, hi = float(c.get("min", 0)), float(c.get("max", 1))
    if c.get("scale") == "log":
        lo, hi, v = np.log10(max(lo, 1e-6)), np.log10(max(hi, 1e-6)), np.log10(max(v, 1e-6))
    return float(min(max((v - lo) / (hi - lo) if hi != lo else 0.5, 0), 1))


def _grains(size: int, fluid, rock, seed: int, k: int) -> Image.Image:
    """Лупа: зёрна породы и флюид в порах между ними (схема)."""
    rng = np.random.default_rng(seed)
    n = size * k
    img = Image.new("RGB", (n, n), tuple(int(c * 0.9) for c in fluid))
    img.info["ssaa"] = k
    d = SDraw(img)
    pts = []
    for _ in range(400):
        x, y = rng.uniform(0, size), rng.uniform(0, size)
        r = rng.uniform(size * 0.07, size * 0.12)
        if all((x - a) ** 2 + (y - b) ** 2 > (r + rr) ** 2 * 0.72 for a, b, rr in pts):
            pts.append((x, y, r))
    for x, y, r in pts:  # зерно со светотенью: тень, тело, блик
        d.ellipse([x - r + 3, y - r + 4, x + r + 3, y + r + 4], fill=tuple(int(c * 0.45) for c in fluid))
        d.ellipse([x - r, y - r, x + r, y + r], fill=mix(rock, (0, 0, 0), 0.12))
        d.ellipse([x - r * 0.85, y - r * 0.9, x + r * 0.6, y + r * 0.55], fill=rock)
        d.ellipse([x - r * 0.55, y - r * 0.65, x - r * 0.05, y - r * 0.2], fill=mix(rock, (255, 255, 255), 0.35))
    return img


def _cine_overlay(self, img, R, s, t, cam, ys):
    """Данные в кадре без «графика»: колонка «показания» со световыми лентами и шкалой словами,
    крупные плашки значений, лупа с порами, викторина, рамки, прибор."""
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
    i_seg = R.seg_at(t)
    seg = R.segs[i_seg]
    quiz = (not seg.beat.say) and seg.beat.pause
    hl = s.get("highlight")
    # фокус на слое: остальные слои гаснут
    if hl and 0 <= hl["idx"] < len(self.layers):
        a = _alpha(hl["born"], t, 0.4)
        L0 = self.layers[hl["idx"]]
        for j, Lr in enumerate(self.layers):
            if j == hl["idx"]:
                continue
            y0_, y1_ = max(0, ys(Lr["top"])), min(H, ys(Lr["bottom"]))
            if y1_ > y0_:
                box = (0, int(y0_ * k), W * k, int(y1_ * k))
                reg = img.crop(box)
                img.paste(Image.blend(reg, Image.new("RGB", reg.size, bg), 0.5 * a), box[:2])
    # колонка «показания»: тёмное стекло без рамки
    ya, yb = max(0, ys(self.top)), min(H, ys(self.bottom))
    box = (int(tx0 * k), int(max(0, ya) * k), int(tx1 * k), int(min(H, yb) * k))
    if box[3] > box[1]:
        reg = img.crop(box)
        img.paste(Image.blend(reg.filter(ImageFilter.GaussianBlur(12 * k)), Image.new("RGB", reg.size, bg), 0.62),
                  box[:2])
    cw = tx1 - tx0
    for frac in (1 / 3, 2 / 3):  # границы «низкое / среднее / высокое»
        x = tx0 + 16 + frac * (cw - 32)
        for yy in np.arange(ya, yb, 22):
            d.line([(x, yy), (x, min(yb, yy + 10))], fill=mix(bg, muted, 0.45), width=2)
    f_q = mono_font(28, True)
    yq = min(yb, R.y_bot) - 44
    for frac, word in ((1 / 6, "низко"), (0.5, "средне"), (5 / 6, "высоко")):
        x = tx0 + 16 + frac * (cw - 32)
        d.text((x - f_q.getlength(word) / 2, yq), word, font=f_q, fill=mix(muted, text, 0.45),
               stroke_width=4, stroke_fill=bg)
    # границы слоёв тонким пунктиром через колонку
    for Lr in self.layers[1:]:
        y = ys(Lr["top"])
        if 0 < y < H:
            dashed_hline(d, tx0, tx1, y, fill=mix(bg, muted, 0.6), width=2, dash=10, gap=10)
    # световые ленты: каждая кривая в своей шкале (качественно), цвет — из стайлгайда
    def xcol(c, v):
        return tx0 + 16 + _norm(self, c, v) * (cw - 32)
    for c in self.curves:
        cs = s["curves"][c["id"]]
        if not cs["visible"] or not cs["rec"]:
            continue
        z0, z1 = cs["rec"]
        m = (self.z >= z0) & (self.z <= z1)
        zz = np.concatenate([self.z[m], [z1]])
        vv = np.concatenate([self.values[c["id"]][m], [self.value_at(c["id"], z1)]])
        if len(zz) < 2:
            continue
        pts = [(xcol(c, v_), ys(z_)) for z_, v_ in zip(zz, vv)]
        col = self.curve_color(c)
        d.line(pts, fill=mix(bg, col, 0.25), width=26, joint="curve")
        d.line(pts, fill=mix(bg, col, 0.55), width=13, joint="curve")
        d.line(pts, fill=mix(col, (255, 255, 255), 0.25), width=5, joint="curve")
        if z0 - 1e-6 <= s["tool"] <= z1 + 1e-6:
            xv, yv = xcol(c, self.value_at(c["id"], s["tool"])), ys(s["tool"])
            d.ellipse([xv - 15, yv - 15, xv + 15, yv + 15], fill=col, outline=(255, 255, 255), width=3)
    # рамки сравнения — ПОД подписями слоёв (подпись с обводкой не зачёркивается линией рамки)
    lab_x = bcx + bhalf + 34
    f_m = font(40, True)
    for mk in s["marks"]:
        a = _alpha(mk.get("born"), t, R.fade)
        if a <= 0:
            continue
        col = mix(bg, meas, a)
        y0_, y1_ = ys(float(mk["top"])), ys(float(mk["bottom"]))
        d.rounded_rectangle([lab_x - 14, y0_ + 4, tx1 + 8, y1_ - 4], radius=18, outline=col, width=5)
        if mk.get("label") and a > 0.4:
            lab = visible(mk["label"], "подпись рамки").upper()
            fl = mono_font(30, True)
            tw_ = fl.getlength(lab)
            d.rounded_rectangle([tx1 - tw_ - 30, y0_ - 22, tx1 + 4, y0_ + 22], radius=12, fill=col)
            d.text((tx1 - tw_ - 14, y0_ - 18), lab, font=fl, fill=bg)
    # подписи слоёв — крупно, справа от ствола
    f_l = font(42, True)
    label_bottom = {}
    label_rects = []
    lab_x = bcx + bhalf + 34
    lab_w = tx0 - lab_x - 24
    for j, Lr in enumerate(self.layers):
        if not Lr.get("label"):
            continue
        y0_, y1_ = ys(Lr["top"]), ys(Lr["bottom"])
        top_ = max(y0_, R.y_top - 10) + 18
        if not (R.y_top - 40 < top_ < R.y_bot) or (y1_ - y0_) < 60:
            continue
        dim = hl and hl["idx"] != j
        lines_ = wrap(visible(Lr["label"], "подпись слоя"), f_l, lab_w)[:2]
        y_lab0, w_lab = top_, max(f_l.getlength(ln) for ln in lines_)
        for ln in lines_:
            d.text((lab_x, top_), ln, font=f_l, fill=mix(text, bg, 0.5) if dim else text, stroke_width=6, stroke_fill=bg)
            top_ += line_h(f_l)
        label_bottom[j] = top_
        label_rects.append((lab_x - 6, y_lab0 - 6, lab_x + w_lab + 6, top_ + 6))
        if quiz:  # викторина: на каждом пласте «?» — справа от подписи, а если не влезает — под ней
            qa = min(1.0, (t - seg.start) / 0.3)
            f_big = font(70, True)
            qx = lab_x + w_lab + 24
            if qx + 92 > tx0 - 8:
                qx, cy = lab_x, top_ + 56
            else:
                cy = (y_lab0 + top_) / 2
            if cy + 46 <= min(y1_, R.y_bot) + 10:
                d.ellipse([qx, cy - 46, qx + 92, cy + 46], fill=mix(bg, meas, qa))
                d.text((qx + 46 - f_big.getlength("?") / 2, cy - 44), "?", font=f_big, fill=bg)
    # прибор на кабеле
    ty = ys(s["tool"])
    th = max(80, R.ppm * zoom * 3.2)
    tw = max(18, bhalf * 0.85)
    d.line([(bcx, 0), (bcx, ty - th / 2)], fill=mix(text, muted, 0.3), width=4)
    if s.get("moving"):
        for r_, a_ in ((34, 0.14), (20, 0.28)):
            d.rounded_rectangle([bcx - tw / 2 - r_, ty - th / 2 - r_, bcx + tw / 2 + r_, ty + th / 2 + r_],
                                radius=int(tw / 2 + r_), fill=mix(bg, meas, a_))
    d.rounded_rectangle([bcx - tw / 2, ty - th / 2, bcx + tw / 2, ty + th / 2], radius=int(tw / 2),
                        fill=mix(meas, (0, 0, 0), 0.25))
    d.rounded_rectangle([bcx - tw / 2 + 3, ty - th / 2 + 4, bcx - tw / 2 + tw * 0.38, ty + th / 2 - 4],
                        radius=int(tw / 4), fill=mix(meas, (255, 255, 255), 0.35))
    dashed_hline(d, bcx + tw, tx0, ty, fill=mix(bg, meas, 0.5), width=2, dash=8, gap=8)
    # значения крупно: на подсветке слоя без флюида — обе кривые; с флюидом — лупа с порами
    if hl and 0 <= hl["idx"] < len(self.layers):
        a = _alpha(hl["born"] + 0.15, t, 0.35)
        Lr = self.layers[hl["idx"]]
        zc = (Lr["top"] + Lr["bottom"]) / 2
        yc = min(max(ys(zc), R.y_top + 120), R.y_bot - 120)
        fluid = Lr.get("fluid")
        if fluid and fluid in st.get("fluids", {}) and a > 0:
            size = 250
            key = ("lens", hl["idx"])
            if key not in R.__dict__.setdefault("_cache", {}):
                R._cache[key] = _grains(size, hex_rgb(st["fluids"][fluid]["color"]),
                                        mix(self.lith_color(Lr.get("lith")), (255, 255, 255), 0.15), 5 + hl["idx"], k)
            lens = R._cache[key]
            lb = label_bottom.get(hl["idx"], yc - 60)
            cx_ = lab_x + size / 2 + 10
            cy_ = min(max(lb + size / 2 + 16, R.y_top + size / 2), R.y_bot - size / 2 - 40)
            r_ = size / 2 * (0.6 + 0.4 * a)
            mask = Image.new("L", lens.size, 0)
            mask.info["ssaa"] = k
            SDraw_m = SDraw(mask)
            SDraw_m.ellipse([size / 2 - r_, size / 2 - r_, size / 2 + r_, size / 2 + r_], fill=int(255 * a))
            img.paste(lens, (int((cx_ - size / 2) * k), int((cy_ - size / 2) * k)), mask)
            d.ellipse([cx_ - r_, cy_ - r_, cx_ + r_, cy_ + r_], outline=mix(bg, text, a), width=5)
            d.line([(bcx + bhalf, ys(zc)), (cx_ - r_ * 0.7, cy_ - r_ * 0.7)], fill=mix(bg, text, a * 0.8), width=3)
            tag = "УВЕЛИЧЕНО · СХЕМА"
            fl = mono_font(20, True)
            tw_ = fl.getlength(tag)
            d.rounded_rectangle([cx_ - tw_ / 2 - 12, cy_ + r_ * 0.62, cx_ + tw_ / 2 + 12, cy_ + r_ * 0.62 + 32],
                                radius=10, fill=mix(bg, bg, a))
            d.text((cx_ - tw_ / 2, cy_ + r_ * 0.62 + 4), tag, font=fl, fill=mix(bg, text, a))
            fname = st["fluids"][fluid].get("ru", fluid)
            fb = font(36, True)
            d.text((cx_ - fb.getlength(fname) / 2, cy_ - 22), fname, font=fb, fill=mix(bg, text, a),
                   stroke_width=5, stroke_fill=bg)
        elif a > 0:
            nvis = sum(1 for c in self.curves if s["curves"][c["id"]]["visible"])
            lb = label_bottom.get(hl["idx"], yc - 60)
            y = min(max(lb + 14, R.y_top + 10), R.y_bot - 118 * nvis - 50)
            for c in self.curves:
                if not s["curves"][c["id"]]["visible"]:
                    continue
                v = self.value_at(c["id"], zc)
                vs = (f"{v:.0f}" if abs(v) >= 10 else f"{v:.1f}").replace("-", "−").replace(".", ",")
                word = _qual(_norm(self, c, v))
                col = self.curve_color(c)
                f1, f2 = mono_font(46, True), font(32, True)
                l1 = f"≈ {vs} {visible(c.get('unit', ''), 'единица кривой')}"
                l2 = f"{visible(c.get('label', ''), 'подпись кривой').lower()} · {word}"
                wpx = max(f1.getlength(l1), f2.getlength(l2)) + 44
                x0_ = lab_x
                d.rounded_rectangle([x0_, y, x0_ + wpx, y + 104], radius=20, fill=mix(bg, panel, a))
                d.rectangle([x0_, y + 14, x0_ + 7, y + 90], fill=mix(bg, col, a))
                d.text((x0_ + 22, y + 8), l1, font=f1, fill=mix(bg, text, a))
                d.text((x0_ + 22, y + 60), l2, font=f2, fill=mix(bg, col, a))
                y += 118
    # кадр в истинном масштабе — поверх колонки показаний
    if s.get("true_scale") is not None:
        self.draw_true_scale_inset(d, (tx0 - 10, max(R.y_top, ya), tx1 + 10, min(R.y_bot, yb)), s, t, R.fade)
    # живое показание у прибора во время спуска (первая кривая)
    if s.get("moving") and self.curves:
        c = self.curves[0]
        cs = s["curves"][c["id"]]
        if cs["visible"]:
            v = self.value_at(c["id"], s["tool"])
            vs = (f"{v:.0f}" if abs(v) >= 10 else f"{v:.1f}").replace(".", ",")
            lab = f"≈ {vs} {visible(c.get('unit', ''), 'единица кривой')}"
            f1 = mono_font(40, True)
            wpx = f1.getlength(lab) + 34
            y = min(max(ty - 30, R.y_top + 10), R.y_bot - 70)
            def _hit(yy):
                return any(not (lab_x + wpx < r[0] or lab_x > r[2] or yy + 64 < r[1] or yy > r[3]) for r in label_rects)
            if _hit(y):   # ищем ближайшее свободное место по вертикали
                for dy in range(16, 400, 16):
                    for yy in (y + dy, y - dy):
                        if R.y_top + 10 <= yy <= R.y_bot - 70 and not _hit(yy):
                            y = yy; break
                    else:
                        continue
                    break
            d.rounded_rectangle([lab_x, y, lab_x + wpx, y + 64], radius=32, fill=text)
            d.text((lab_x + 17, y + 6), lab, font=f1, fill=bg)
