"""Кинематографичная подача шортса 9:16 (по образцу 06): кадр без панелей, процедурные текстуры пород со светом,
камера по глубине с глубиной резкости и смазом, полёт по стволу в крючке, типографика тег → заголовок → легенда.

Всё рисуется кодом из той же модели состояния шаблона: камера меняет только масштаб и центр по глубине,
трек кривой использует ту же функцию глубина→экран, поэтому кривая всегда стоит против своих слоёв (ТЗ, класс B).
Геологии здесь нет: цвета и узоры приходят из стайлгайда и params шаблона.
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageFilter

from .draw import SDraw, fit, line_h, text_block, wrap
from .style import display_font, font, hex_rgb, load_settings, mix, mono_font
from .textclean import visible


# ---------------------------------------------------------------- шум и текстуры
def value_noise(h: int, w: int, cell: float, rng) -> np.ndarray:
    gh, gw = int(h / cell) + 3, int(w / cell) + 3
    g = rng.random((gh, gw)).astype(np.float32)
    img = Image.fromarray((g * 255).astype(np.uint8)).resize((int(gw * cell), int(gh * cell)), Image.BICUBIC)
    return np.asarray(img, dtype=np.float32)[:h, :w] / 255.0


def fbm(h: int, w: int, rng, base: float = 64, octaves: int = 5) -> np.ndarray:
    out = np.zeros((h, w), np.float32)
    amp, tot, cell = 1.0, 0.0, base
    for _ in range(octaves):
        out += amp * value_noise(h, w, max(cell, 1.5), rng)
        tot += amp
        amp *= 0.5
        cell /= 2
    return out / tot


def rock_texture(h: int, w: int, color, pattern: str, ppm: float, seed: int) -> np.ndarray:
    """Порода: зерно, слоистость, пятна — по имени узора (из стайлгайда/слоя), размеры в метрах → px."""
    rng = np.random.default_rng(seed)
    c = np.array(color, np.float32)
    n1 = fbm(h, w, rng, base=max(8, ppm * 0.6))
    n2 = fbm(h, w, rng, base=max(3, ppm * 0.08), octaves=3)
    shade = 0.82 + 0.3 * (n1 - 0.5) + 0.16 * (n2 - 0.5)
    yy = np.arange(h, dtype=np.float32)[:, None]
    if pattern in ("lines", "shale"):        # тонкая слоистость
        lam = np.sin(2 * np.pi * (yy / max(4, ppm * 0.05) + 3 * n1)) * 0.07
        shade = shade + lam
    elif pattern in ("dots", "sand", "grain"):  # зернистость
        speck = (rng.random((h, w)) > 0.985).astype(np.float32)
        speck = np.asarray(Image.fromarray((speck * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(3)),
                           np.float32) / 255
        shade = shade - 0.18 * speck + 0.05 * (n2 > 0.62)
    elif pattern in ("brick", "limestone", "rhomb", "dolomite"):
        blk = np.abs(np.sin(np.pi * yy / max(6, ppm * 0.3))) < 0.06
        shade = shade - 0.12 * blk
    elif pattern in ("hatch", "dense"):
        shade = shade + 0.05 * np.sin((yy + np.arange(w)[None, :]) / max(3, ppm * 0.07))
    rgb = np.clip(c[None, None, :] * shade[..., None], 0, 255)
    return rgb


def vignette(h: int, w: int, strength: float = 0.55, cx=0.5, cy=0.5) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.hypot((xx / w - cx) / 0.75, (yy / h - cy) / 1.0)
    return np.clip(1 - strength * d ** 2, 0, 1)


def blur_bands(img: Image.Image, top: int, bottom: int, radius: float) -> Image.Image:
    """Глубина резкости: верх и низ кадра мягко уходят из фокуса (как в макро-кадрах 06)."""
    w, h = img.size
    small = img.resize((max(1, w // 4), max(1, h // 4)), Image.BILINEAR).filter(ImageFilter.GaussianBlur(radius / 4))
    blurred = small.resize((w, h), Image.BILINEAR)
    m = np.zeros((h, 1), np.float32)
    yy = np.arange(h)[:, None]
    m = np.clip((top - yy) / max(1, top * 0.9), 0, 1) + np.clip((yy - bottom) / max(1, (h - bottom) * 0.9), 0, 1)
    mask = Image.fromarray((np.clip(m, 0, 1) * 255).astype(np.uint8).repeat(w, axis=1))
    return Image.composite(blurred, img, mask)


def motion_blur_y(img: Image.Image, px: float) -> Image.Image:
    if px < 2:
        return img
    w, h = img.size
    n = max(2, int(px))
    acc = np.zeros((h, w, 3), np.float32)
    arr = np.asarray(img, np.float32)
    for k in range(n):
        acc += np.roll(arr, k - n // 2, axis=0)
    return Image.fromarray((acc / n).astype(np.uint8))


# ---------------------------------------------------------------- полёт по стволу (крючок)
def tunnel_frame(w: int, h: int, travel: float, colors: list, mud, seed: int = 5, cy_frac: float = 0.56) -> np.ndarray:
    """Вид вдоль ствола вниз: стенки — породы сцены, ближе к центру темнее; travel — пройденный путь (усл.)."""
    rng = np.random.default_rng(seed)
    tex_h, tex_w = 256, 512
    tex = 0.6 * fbm(tex_h, tex_w, rng, base=22) + 0.4 * fbm(tex_h, tex_w, rng, base=6, octaves=3)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cx, cy = w / 2, h * cy_frac
    dx, dy = xx - cx, yy - cy
    r = np.hypot(dx, dy) + 1e-3
    ang = (np.arctan2(dy, dx) / (2 * np.pi) + 0.5)
    out = np.zeros((h, w, 3), np.float32)
    samples = 4
    cols = np.array(colors, np.float32)
    for s in range(samples):  # смаз движения вдоль оси ствола
        v = (w * 0.9) / r + travel - s * 0.012 * (1 + travel * 0.02)
        ti = ((v * 40) % tex_h).astype(np.int32)
        tj = ((ang * tex_w) % tex_w).astype(np.int32)
        n = tex[ti, tj]
        band = (np.floor(v * 0.35) % len(cols)).astype(np.int32)
        base = cols[band]
        ring = 0.78 + 0.3 * np.sin(v * 14 + n * 7) + 0.12 * np.sin(ang * 2 * np.pi * 3 + v)
        out += base * (0.45 + 0.75 * n)[..., None] * ring[..., None]
    out /= samples
    light = np.clip((r / (w * 0.55)) ** 1.1, 0.04, 1.15)
    out *= light[..., None]
    # колонна бурового раствора по оси не видна, но кабель прибора — да
    out = np.clip(out, 0, 255)
    return out


# ---------------------------------------------------------------- музыка
def music_bed(total_s: float, sr: int, accents: list[float], seed: int = 3) -> np.ndarray:
    """Спокойная подложка: аккорды (ля минор — фа — до — соль) мягкими синусами + тихий пульс + акценты-подъёмы.
    Без внешних файлов. Громкость выставляется при сведении с голосом."""
    n = int(total_s * sr)
    t = np.arange(n) / sr
    out = np.zeros(n, np.float32)
    chords = [(57, 60, 64), (53, 57, 60), (48, 52, 55), (55, 59, 62)]  # MIDI
    bar = 4.0
    for i in range(int(total_s / bar) + 1):
        a, b = int(i * bar * sr), min(n, int((i + 1) * bar * sr + 0.5 * sr))
        if a >= n:
            break
        tt = t[a:b] - i * bar
        env = np.minimum(1, tt / 1.2) * np.exp(-np.maximum(0, tt - bar) * 3)
        for m in chords[i % 4]:
            f = 440 * 2 ** ((m - 69) / 12)
            out[a:b] += (np.sin(2 * np.pi * f * tt) * 0.5 + np.sin(2 * np.pi * f / 2 * tt) * 0.3) * env
    # тихий пульс на каждую долю (75 уд/мин)
    beat = 60 / 75
    for k in np.arange(0, total_s, beat):
        i = int(k * sr)
        L = int(0.25 * sr)
        tt = np.arange(min(L, n - i)) / sr
        out[i:i + len(tt)] += np.sin(2 * np.pi * (70 - 40 * tt) * tt) * np.exp(-tt * 18) * 0.9
    # подъём к акцентам (смена сцены)
    for ac in accents:
        a = int(max(0, ac - 1.0) * sr)
        b = min(n, int(ac * sr))
        if b - a < sr // 10:
            continue
        tt = np.arange(b - a) / sr
        noise = np.convolve(np.random.default_rng(seed).standard_normal(b - a), np.ones(30) / 30, "same")
        out[a:b] += noise * (tt / max(tt[-1], 1e-6)) ** 2 * 1.2
    # мягкий фильтр
    k = np.ones(8) / 8
    out = np.convolve(out, k, "same")
    return out / (np.max(np.abs(out)) + 1e-9)


def tracked(d, xy, text, fnt, fill, track=4):
    """Текст с разрядкой (теги, как в референсах)."""
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=fnt, fill=fill)
        x += fnt.getlength(ch) + track
    return x


# ---------------------------------------------------------------- рендер шортса
class CineRenderer:
    """Шортс 9:16 для шаблонов с поддержкой cine_* (сейчас well_tool_curve)."""

    def __init__(self, spec, template, style: dict, segs, base_renderer_cls):
        from .render import Layout, plain, subtitle_chunks, caption_tokens  # noqa: F401
        self.spec, self.tpl, self.style, self.segs = spec, template, style, segs
        self.kind = "9x16"
        self.W, self.H = 1080, 1920
        sz = style.get("safe_zone", {"top": 220, "bottom": 380, "side": 70})
        self.safe = (sz["side"], sz["top"], self.W - sz["side"], self.H - sz["bottom"])
        rs = load_settings()["render"]
        self.k = int(rs.get("ssaa", 2))
        self.fade = rs["fade_s"]
        # геометрия мира: интервал глубин занимает середину кадра в общем плане
        self.y_top, self.y_bot = 610, 1250
        self.ppm = (self.y_bot - self.y_top) / (template.bottom - template.top)
        self.zmax = float(rs.get("cine_follow_zoom", 1.5))
        self.sec_w = int(self.W * 0.6)
        self.track = (int(self.W * 0.62), self.W - self.safe[0] + 20)
        self.world = template.cine_world(self.ppm * self.zmax, self.sec_w * self.zmax, self.H, self.ppm, self)
        # состояния
        self.starts = []
        s = template.initial_state()
        for seg in segs:
            s0 = template.begin_beat(s, seg.beat.action, seg.start)
            self.starts.append(s0)
            s = template.at(s0, seg.beat.action, 1.0)
        f_sub = font(46, True)
        self.f_sub = f_sub
        self.subs = [subtitle_chunks(seg, f_sub, self.W - 2 * self.safe[0], max_lines=2) for seg in segs]
        self.intro_dur = min(float(rs.get("intro_s", 2.6)) + 0.8, segs[0].dur * 0.75)
        self.cam_keys = self._camera_plan()
        self.L = type("L", (), {"W": self.W, "H": self.H})()
        self.scale_note = template.cine_scale_note(self)

    # ------------------------------------------------------------ время и состояние
    def seg_at(self, t):
        for i, s in enumerate(self.segs):
            if t < s.start + s.dur:
                return i
        return len(self.segs) - 1

    def state_at(self, i, t):
        seg = self.segs[i]
        if "tool_to" not in seg.beat.action:
            return self.starts[i]
        a = seg.start + 0.1 + (self.intro_dur if i == 0 else 0)
        b = seg.start + max(a - seg.start + 0.3, seg.dur - 0.25)
        u = (t - a) / (b - a)
        return self.tpl.at(self.starts[i], seg.beat.action, min(max(u, 0.0), 1.0))

    def total(self):
        return self.segs[-1].start + self.segs[-1].dur

    def key_times(self):
        return [s.start + max(0.3, s.dur - 0.3) for s in self.segs]

    def states_for_check(self):
        return [self.state_at(i, seg.start + u * seg.dur) for i, seg in enumerate(self.segs) for u in (0, .5, 1)]

    # ------------------------------------------------------------ камера
    def _camera_plan(self):
        """Ключи камеры по фразам: (zoom, режим). Прибор едет → наезд и сопровождение; рамки/ответ → общий план;
        пауза → медленный наезд на трек; финал → общий план."""
        keys = []
        for i, seg in enumerate(self.segs):
            a = seg.beat.action
            if i == len(self.segs) - 1:
                keys.append((1.0, "wide"))
            elif not seg.beat.say and seg.beat.pause:
                keys.append((1.12, "hold"))
            elif "tool_to" in a:
                keys.append((self.zmax, "follow"))
            elif a.get("mark") or a.get("readout") or a.get("show_curves"):
                keys.append((1.0, "wide"))
            else:
                keys.append((1.06, "drift"))
        return keys

    def camera(self, t):
        """(zoom, центр по глубине, скорость центра в px/с) — сглаженно между фразами."""
        def raw(tt):
            i = self.seg_at(tt)
            z, mode = self.cam_keys[i]
            s = self.state_at(i, tt)
            mid = (self.tpl.top + self.tpl.bottom) / 2
            if mode == "follow":
                cz = s["tool"]
            elif mode in ("hold", "drift"):
                cz = mid * 0.6 + s["tool"] * 0.4
            else:
                cz = mid
            return z, cz
        # сглаживание: среднее по окну 0,8 с (плавные разгоны/торможения без рывков на стыках)
        win = [raw(max(0.0, min(self.total() - 1e-3, t + d))) for d in np.linspace(-0.4, 0.4, 9)]
        z = float(np.mean([w[0] for w in win]))
        cz = float(np.mean([w[1] for w in win]))
        z2, cz2 = raw(max(0.0, min(self.total() - 1e-3, t + 0.05)))
        vel = abs(cz2 - cz) * self.ppm * z / 0.05
        # не уводим интервал за пределы кадра
        half_m = (self.H / 2) / (self.ppm * z)
        lo, hi = self.tpl.top - 4, self.tpl.bottom + 4
        cz = min(max(cz, lo + half_m * 0.35), hi - half_m * 0.35)
        return z, cz, vel

    def ys(self, z, cam):
        zoom, cz, _ = cam
        mid_y = (self.y_top + self.y_bot) / 2
        return mid_y + (z - cz) * self.ppm * zoom

    # ------------------------------------------------------------ кадр
    def frame(self, t):
        i = self.seg_at(t)
        seg = self.segs[i]
        st = self.style
        k = self.k
        state = self.state_at(i, t)
        cam = self.camera(t)
        img = self.tpl.cine_compose(self, state, t, cam)          # мир + динамика в экранных координатах (×k)
        img.info["ssaa"] = k
        # крючок: полёт по стволу и переход в разрез
        if i == 0 and t < self.intro_dur + 0.5:
            img = self._intro(img, t)
            img.info["ssaa"] = k
        # пауза и финал — сцена приглушена и размыта
        if not seg.beat.say and seg.beat.pause:
            img = Image.blend(img, img.filter(ImageFilter.GaussianBlur(6 * k)), min(1, (t - seg.start) / 0.4))
            img.info["ssaa"] = k
        final = i == len(self.segs) - 1
        if final:
            u = min(1, (t - seg.start) / 0.6)
            img = Image.blend(img, img.filter(ImageFilter.GaussianBlur(10 * k)), u)
            img = Image.blend(img, Image.new("RGB", img.size, hex_rgb(st["background"])), 0.35 * u)
            img.info["ssaa"] = k
        self._scrims(img)
        d = SDraw(img)
        self._type(d, i, seg, t, final)
        for (a0, a1, lines) in self.subs[i]:
            if a0 <= t < a1:
                self._subtitle(d, lines, a0, a1, seg, t)
                break
        return img.resize((self.W, self.H), Image.LANCZOS)

    def _intro(self, scene: Image.Image, t: float) -> Image.Image:
        st = self.style
        k = self.k
        dur = self.intro_dur
        u = min(1.0, t / dur)
        e = 1 - (1 - u) ** 2.2
        w, h = self.W // 2, self.H // 2
        cols = [mix(self.tpl.lith_color(L.get("lith")), (40, 36, 32), 0.25) for L in self.tpl.layers] or [(120, 100, 80)]
        cols = cols[::-1] + cols
        mud = hex_rgb(st.get("fluids", {}).get("mud", {}).get("color", st["muted"]))
        arr = tunnel_frame(w, h, travel=e * 22, colors=cols, mud=mud)
        tun = Image.fromarray(arr.astype(np.uint8)).resize((self.W * k, self.H * k), Image.BILINEAR)
        tun.info["ssaa"] = k
        d = SDraw(tun)
        cx, cy = self.W / 2, self.H * 0.56
        d.line([(cx, 0), (cx, cy - 30)], fill=hex_rgb(st["text"]), width=3)
        meas = hex_rgb(st["measurement"]["color"])
        d.rounded_rectangle([cx - 9, cy - 40, cx + 9, cy - 4], radius=8, fill=meas)
        # счётчик глубины
        depth = int(round(self.tpl.top * e))
        groups = f"{depth:,}".split(",")
        f_big = mono_font(150, True)
        x0, y0 = self.safe[0], 1010
        x = x0
        for gi, g in enumerate(groups):  # разряды с узким зазором (моно-пробел слишком широкий)
            d.text((x, y0), g, font=f_big, fill=hex_rgb(st["text"]))
            x += f_big.getlength(g) + (22 if gi < len(groups) - 1 else 0)
        d.text((x + 14, y0 + 88), "м", font=mono_font(56, False), fill=hex_rgb(st["muted"]))
        tracked(d, (x0 + 4, y0 - 34), "ГЛУБИНА · СХЕМА", mono_font(26, True), hex_rgb(st["muted"]), 3)
        lith_now = self.tpl.layers[0].get("label", "") if u > 0.85 and self.tpl.layers else "ствол скважины"
        d.ellipse([x0 + 6, y0 + 212, x0 + 20, y0 + 226], fill=meas if u > 0.85 else hex_rgb(st["muted"]))
        d.text((x0 + 32, y0 + 198), visible(lith_now, "интро"), font=font(36, True), fill=hex_rgb(st["text"]))
        if t <= dur:
            return tun
        a = min(1.0, (t - dur) / 0.5)
        return Image.blend(tun, scene, a)

    def _scrims(self, img):
        """Затемнение сверху и снизу под текст (как в 06), без плашек."""
        k = self.k
        w, h = img.size
        arr = np.asarray(img, np.float32)
        yy = np.arange(h, dtype=np.float32)[:, None] / k
        top = np.clip(1 - yy / 760, 0, 1) ** 1.4 * 0.78
        bot = np.clip((yy - 1260) / 660, 0, 1) ** 1.2 * 0.85
        m = 1 - np.maximum(top, bot)
        bg = np.array(hex_rgb(self.style["background"]), np.float32)
        arr = arr * m[..., None] + bg * (1 - m[..., None])
        img.paste(Image.fromarray(arr.astype(np.uint8)))

    def _type(self, d, i, seg, t, final):
        from .render import caption_tokens, plain
        st = self.style
        text, muted = hex_rgb(st["text"]), hex_rgb(st["muted"])
        accent = hex_rgb(st["measurement"]["color"])
        x0, y0, x1, _ = self.safe
        # тег: происхождение кадра · масштаб
        lab = seg.beat.label.upper()
        pc = hex_rgb(st.get("provenance", {}).get(seg.beat.label, st["muted"]))
        f_tag = mono_font(26, True)
        d.ellipse([x0, y0 + 9, x0 + 14, y0 + 23], fill=pc)
        tracked(d, (x0 + 26, y0), f"{lab} · НЕ В МАСШТАБЕ", f_tag, mix(muted, text, 0.4), 3)
        cap = visible(plain(seg.beat.on_screen), "заголовок") if not final else ""
        if final:
            self._final(d, seg, t)
        elif cap:
            same = i > 0 and plain(self.segs[i - 1].beat.on_screen) == plain(seg.beat.on_screen)
            toks = [(w.upper(), h) for w, h in caption_tokens(visible(seg.beat.on_screen, "заголовок"))]
            f, lines = fit(" ".join(w for w, _ in toks), x1 - x0, 118, 64, 3, fnt_fn=display_font)
            lh = int(line_h(f) * 0.9)
            y, n, ti = y0 + 46, 0, 0
            bg = hex_rgb(st["background"])
            prev = self.segs[i - 1].beat.on_screen if i > 0 else ""
            for ln in lines:
                x = x0
                for w in ln.split(" "):
                    hl = toks[ti][1] if ti < len(toks) else False
                    ti += 1
                    u = 1.0 if same else min(1.0, max(0.0, (t - seg.start - 0.08 * n) / 0.14))
                    e = 1 - (1 - u) ** 3
                    target = accent if hl else text
                    col = mix(mix(bg, muted, 0.55), target, e)   # из серого в цвет, без движения
                    d.text((x, y), w, font=f, fill=col)
                    x += f.getlength(w + " ")
                    n += 1
                y += lh
            # легенда кривых точками (как «• нефть • вода»)
            self._legend(d, x0, y + 14)
        # плашка «не в масштабе» внизу, мелко
        pl = visible(" ".join(self.spec.get("plaque") or []), "плашка")
        if self.scale_note:
            kws = getattr(self.tpl, "scale_keyword", ())
            items = [x for x in (self.spec.get("plaque") or []) if not any(kw in x.lower() for kw in kws)]
            pl = visible(" ".join(items).rstrip(" .;:") + ("; " if items else "") + self.scale_note, "плашка")
        fpl, pls = fit(pl, x1 - x0, 30, 26, 2)
        text_block(d, (x0, 1290), pls, fpl, mix(muted, text, 0.15))

    def _legend(self, d, x, y):
        st = self.style
        f = font(30, True)
        vis = [c for c in self.tpl.curves]
        for c in vis:
            col = self.tpl.curve_color(c)
            lab = visible(c.get("label", c["id"]), "подпись кривой")
            if c.get("dashed"):
                for j in range(3):
                    d.line([(x + j * 9, y + 20), (x + j * 9 + 5, y + 20)], fill=col, width=5)
            else:
                d.line([(x, y + 20), (x + 23, y + 20)], fill=col, width=5)
            d.text((x + 34, y), lab, font=f, fill=hex_rgb(st["text"]))
            x += 34 + f.getlength(lab) + 40

    def _final(self, d, seg, t):
        from .render import plain
        st = self.style
        x0, y0, x1, _ = self.safe
        u = min(1.0, max(0.0, (t - seg.start) / 0.5))
        e = 1 - (1 - u) ** 3
        bg, text = hex_rgb(st["background"]), hex_rgb(st["text"])
        accent = hex_rgb(st["measurement"]["color"])
        cap = visible(plain(seg.beat.on_screen) or plain(self.spec.get("transfer_question", "")), "вопрос на перенос")
        f, ls = fit(cap.upper(), x1 - x0, 124, 64, 5, fnt_fn=display_font)
        lh = int(line_h(f) * 0.92)
        h = len(ls) * lh
        y = 860 - h / 2 + (1 - e) * 50
        tracked(d, (x0, y - 56), "ВОПРОС НА ПЕРЕНОС", mono_font(28, True), mix(bg, accent, e), 3)
        for ln in ls:
            d.text((x0, y), ln, font=f, fill=mix(bg, text, e))
            y += lh

    def _subtitle(self, d, lines, a0, a1, seg, t):
        st = self.style
        f = self.f_sub
        lh = line_h(f)
        x0, _, x1, y1 = self.safe
        y = y1 - len(lines) * lh
        speech_end = min(a1, seg.speech_start + seg.speech_dur)
        total = sum(len(ln) for ln in lines) or 1
        spoken = (t - a0) / max(0.1, speech_end - a0) * total
        done = 0
        bg = hex_rgb(st["background"])
        for ln in lines:
            x = x0
            for w in ln.split(" "):
                col = (255, 255, 255) if done < spoken else (165, 172, 182)
                d.text((x, y), w, font=f, fill=col, stroke_width=4, stroke_fill=bg)
                x += f.getlength(w + " ")
                done += len(w) + 1
            y += lh
