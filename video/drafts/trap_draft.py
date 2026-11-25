"""Черновик 12–14 с по образцу 05 (иллюстрация, а не схема): закат, вышка, волнистые пласты, ствол с прибором,
непрерывный спуск камеры, тёмные карточки с оранжевыми номерами, субтитры без плашки.

Правило вёрстки: слова и цифры живут только в «слотах» (карточка сверху, список под ней, субтитры снизу,
тег над субтитрами); на картинке — только кружки-номера пластов. Поэтому ничего не налезает.

    uv run python -m video.drafts.trap_draft -o runs/drafts/trap_draft.mp4 --w 540
"""
from __future__ import annotations

import argparse
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .. import sound, tts
from ..cine import fbm, rock_texture
from ..style import font, mono_font

# ---------------------------------------------------------------- содержание (факты: Латышова, табл. с. 93–94)
LINES = [  # голос и когда появляется
    "Максимум сопротивления. Значит, нефть?",
    "Глина — низкое. Песчаник с нефтью — среднее.",
    "Максимум — у плотного известняка. Это не нефть.",
]
LAYERS = [  # сверху вниз: подпись, значение словами, цвет, узор
    ("глина", "низкое", (92, 104, 86), "lines"),
    ("песчаник с нефтью", "среднее", (196, 160, 98), "dots"),
    ("плотный известняк", "максимум", (168, 172, 176), "brick"),
]
ORANGE = (247, 168, 50)
WHITE = (245, 245, 242)
GREY = (150, 150, 158)
INK = (16, 14, 20)


def ease(u):
    u = min(max(u, 0.0), 1.0)
    return u * u * (3 - 2 * u)


def ease_out(u):
    u = min(max(u, 0.0), 1.0)
    return 1 - (1 - u) ** 3


class World:
    """Высокий «мир»: небо с вышкой сверху, под землёй волнистые пласты, по центру ствол."""

    def __init__(self, W):
        self.W = W
        self.s = W / 540                       # масштаб относительно 540 px
        s = self.s
        self.sky_h = int(520 * s)
        self.ground = self.sky_h
        self.layer_tops = [self.ground + int(v * s) for v in (0, 300, 620, 940)]  # верх каждого пласта + низ
        self.H = self.layer_tops[-1] + int(500 * s)
        self.cx = W * 0.5
        self.img = self._build()

    def wave(self, y0, x, k):
        s = self.s
        return y0 + 18 * s * math.sin(x / (110 * s) + k) + 9 * s * math.sin(x / (37 * s) + 2 * k)

    def _build(self):
        W, H, s = self.W, self.H, self.s
        img = Image.new("RGB", (W, H), INK)
        a = np.zeros((H, W, 3), np.float32)
        # небо: малиновый → оранжевый к горизонту
        y = np.linspace(0, 1, self.sky_h, dtype=np.float32)[:, None]
        top, mid, hor = np.array([42, 22, 48]), np.array([128, 58, 96]), np.array([232, 128, 82])
        sky = np.where(y < 0.6, top + (mid - top) * (y / 0.6), mid + (hor - mid) * ((y - 0.6) / 0.4))
        a[:self.sky_h] = sky[:, None, :] if sky.ndim == 2 else sky
        # пласты с фактурой и волнистыми границами
        xs = np.arange(W)
        tops = [np.array([self.wave(t, x, i * 1.7) for x in xs]) if i else np.full(W, float(t))
                for i, t in enumerate(self.layer_tops)]
        seq = LAYERS + [("", "", (58, 52, 50), "hatch")]
        for i in range(len(self.layer_tops)):
            col = np.array(seq[i][2] if i < len(seq) else (58, 52, 50), np.float32)
            y0 = int(tops[i].min()) - 2
            y1 = int(tops[i + 1].max()) + 2 if i + 1 < len(tops) else H
            tex = rock_texture(y1 - y0, W, col, seq[min(i, len(seq) - 1)][3], 60 * s, seed=3 + i)
            yy = np.arange(y0, y1)[:, None]
            inside = (yy >= tops[i][None, :]) & ((yy < tops[i + 1][None, :]) if i + 1 < len(tops) else True)
            reg = a[y0:y1]
            reg[inside] = tex[inside]
        # чуть темнее с глубиной
        depth = np.clip((np.arange(H) - self.ground) / (H - self.ground), 0, 1)[:, None, None]
        a[self.ground:] *= (1 - 0.35 * depth[self.ground:])
        img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
        d = ImageDraw.Draw(img)
        # горизонт: тёмная полоса земли и дальние огни
        d.rectangle([0, self.ground - int(10 * s), W, self.ground], fill=(30, 22, 26))
        rng = np.random.default_rng(4)
        for x in rng.uniform(0, W, 18):
            d.ellipse([x - 1.5 * s, self.ground - 22 * s, x + 1.5 * s, self.ground - 19 * s], fill=(255, 200, 140))
        # вышка: решётчатая, с тёплыми огнями
        cx, g = self.cx, self.ground - int(10 * s)
        hgt, base = 330 * s, 62 * s
        top_y = g - hgt
        for sgn in (-1, 1):
            d.line([(cx + sgn * base, g), (cx + sgn * 9 * s, top_y)], fill=(44, 28, 34), width=max(2, int(5 * s)))
        for j in range(1, 11):
            yy = g - hgt * j / 11
            half = base - (base - 9 * s) * j / 11
            d.line([(cx - half, yy), (cx + half, yy)], fill=(60, 36, 40), width=max(1, int(2 * s)))
            nh = base - (base - 9 * s) * (j + 1) / 11
            d.line([(cx - half, yy), (cx + nh, yy - hgt / 11)], fill=(60, 36, 40), width=max(1, int(2 * s)))
            d.line([(cx + half, yy), (cx - nh, yy - hgt / 11)], fill=(60, 36, 40), width=max(1, int(2 * s)))
        d.rectangle([cx - 14 * s, top_y - 16 * s, cx + 14 * s, top_y], fill=(60, 36, 40))
        d.rectangle([cx - 90 * s, g - 30 * s, cx + 90 * s, g], fill=(40, 26, 32))   # платформа
        glow = Image.new("RGB", (W, H), (0, 0, 0))
        gd = ImageDraw.Draw(glow)
        for j in range(2, 11, 2):
            yy = g - hgt * j / 11
            half = base - (base - 9 * s) * j / 11
            for sgn in (-1, 1):
                gd.ellipse([cx + sgn * half - 6 * s, yy - 6 * s, cx + sgn * half + 6 * s, yy + 6 * s], fill=(255, 150, 70))
        gd.rectangle([cx - 80 * s, g - 26 * s, cx + 80 * s, g - 20 * s], fill=(255, 140, 60))
        glow = glow.filter(ImageFilter.GaussianBlur(9 * s))
        img = Image.fromarray(np.clip(np.asarray(img, np.int32) + np.asarray(glow, np.int32), 0, 255).astype(np.uint8))
        d = ImageDraw.Draw(img)
        for j in range(2, 11, 2):
            yy = g - hgt * j / 11
            half = base - (base - 9 * s) * j / 11
            for sgn in (-1, 1):
                d.ellipse([cx + sgn * half - 2 * s, yy - 2 * s, cx + sgn * half + 2 * s, yy + 2 * s], fill=(255, 230, 190))
        # ствол: обсадная колонна и раствор
        bw = 22 * s
        d.rectangle([cx - bw, g, cx + bw, H], fill=(36, 34, 40))
        d.rectangle([cx - bw + 4 * s, g, cx + bw - 4 * s, H], fill=(70, 58, 50))
        d.line([(cx - bw + 7 * s, g), (cx - bw + 7 * s, H)], fill=(110, 96, 84), width=max(1, int(2 * s)))
        return img


def render(out: Path, W: int = 540, fps: int = 30):
    H = int(W * 16 / 9)
    s = W / 540
    # голос
    clips = [tts.synth(t, "geology") for t in LINES]
    gap = 0.35
    starts, t = [], 0.25
    for c in clips:
        starts.append(t)
        t += len(c["audio"]) / tts.SR + gap
    total = t + 1.0
    world = World(W)
    # раскладка слотов (в пикселях кадра) — тексты никогда не делят место
    f_title = font(int(34 * s), True)
    f_sub_title = font(int(19 * s), False)
    f_row = font(int(21 * s), True)
    f_row2 = font(int(19 * s), False)
    f_subs = font(int(25 * s), True)
    f_tag = mono_font(int(12 * s), True)
    slot_card = (int(28 * s), int(70 * s), W - int(28 * s))
    subs_y = H - int(200 * s)
    tag_y = subs_y - int(40 * s)

    # события: строки списка появляются, когда прибор входит в пласт
    t_desc0, t_desc1 = starts[1] - 0.2, starts[2] + 0.6
    tool_y0 = world.ground - 40 * s
    tool_y1 = (world.layer_tops[2] + world.layer_tops[3]) / 2

    def tool_y(tt):
        return tool_y0 + (tool_y1 - tool_y0) * ease((tt - t_desc0) / (t_desc1 - t_desc0))

    def cam(tt):
        """Центр кадра в координатах мира + масштаб: общий план → спуск за прибором → наезд."""
        wide_c = world.ground - 60 * s
        follow = tool_y(tt)
        u = ease((tt - (t_desc0 - 0.4)) / 1.2)
        c = wide_c + (follow + 80 * s - wide_c) * u
        z = 1.0 + 0.08 * ease(tt / max(t_desc0, 0.1)) + 0.3 * ease((tt - starts[2] - 0.3) / 1.6)
        return c, z

    card_bottom = [0.0]   # нижний край карточки в текущем кадре (номера на сцене его не пересекают)
    row_times = []
    for i, L in enumerate(world.layer_tops[:3]):
        mid = (world.layer_tops[i] + world.layer_tops[i + 1]) / 2
        # момент, когда прибор проходит середину пласта
        ts = np.linspace(t_desc0, t_desc1, 400)
        k = int(np.argmin([abs(tool_y(q) - mid) for q in ts]))
        row_times.append(float(ts[k]))

    subs = []
    for i, (st_, c) in enumerate(zip(starts, clips)):
        subs.append((st_, st_ + len(c["audio"]) / tts.SR + gap, LINES[i]))

    def frame(tt):
        cy, z = cam(tt)
        vh = H / z
        y0 = int(cy - vh * 0.45)
        y0 = max(0, min(world.H - int(vh), y0))
        vw = W / z
        x0 = int(world.cx - vw / 2)
        crop = world.img.crop((x0, y0, x0 + int(vw), y0 + int(vh))).resize((W, H), Image.BICUBIC)
        img = crop.convert("RGB")
        d = ImageDraw.Draw(img)

        def to_scr(wx, wy):
            return (wx - x0) * z, (wy - y0) * z
        # кабель и прибор
        tx, ty = to_scr(world.cx, tool_y(tt))
        gx, gy = to_scr(world.cx, world.ground - 330 * s)
        d.line([(tx, max(0, gy)), (tx, ty - 26 * s * z)], fill=(210, 210, 215), width=max(1, int(2 * s)))
        tw, th = 11 * s * z, 52 * s * z
        moving = t_desc0 < tt < t_desc1
        if moving:
            gl = Image.new("RGB", (W, H), (0, 0, 0))
            ImageDraw.Draw(gl).ellipse([tx - 40 * s, ty - 50 * s, tx + 40 * s, ty + 50 * s], fill=(120, 80, 10))
            img = Image.fromarray(np.clip(np.asarray(img, np.int32) + np.asarray(gl.filter(ImageFilter.GaussianBlur(18 * s)), np.int32), 0, 255).astype(np.uint8))
            d = ImageDraw.Draw(img)
        d.rounded_rectangle([tx - tw, ty - th / 2, tx + tw, ty + th / 2], radius=int(tw), fill=(232, 176, 40))
        d.rounded_rectangle([tx - tw + 2, ty - th / 2 + 3, tx - tw * 0.2, ty + th / 2 - 3], radius=int(tw / 2), fill=(255, 226, 140))
        card_bottom[0] = card_height(tt)
        # номера пластов на картинке (единственное, что стоит на сцене)
        f_b = font(int(18 * s), True)
        for i in range(3):
            if tt < row_times[i] - 0.15:
                continue
            a = ease_out((tt - row_times[i] + 0.15) / 0.35)
            mid = (world.layer_tops[i] + world.layer_tops[i + 1]) / 2
            bx, by = to_scr(world.cx - 120 * s, mid)
            r = 17 * s * (0.6 + 0.4 * a)
            if not (card_bottom[0] + r + 8 * s < by < tag_y - r - 8 * s):
                continue
            hot = i == 2 and tt > starts[2] + 0.4
            d.ellipse([bx - r, by - r, bx + r, by + r], fill=ORANGE if (i < 2 or hot) else ORANGE)
            num = str(i + 1)
            d.text((bx - f_b.getlength(num) / 2, by - f_b.size * 0.62), num, font=f_b, fill=INK)
        # затемнение сверху и снизу под тексты
        arr = np.asarray(img, np.float32)
        yy = np.arange(H, dtype=np.float32)[:, None] / H
        m = 1 - np.maximum(np.clip(1 - yy / 0.42, 0, 1) ** 1.6 * 0.72, np.clip((yy - 0.62) / 0.38, 0, 1) ** 1.3 * 0.82)
        arr = arr * m[..., None] + np.array(INK, np.float32) * (1 - m[..., None])
        img = Image.fromarray(arr.astype(np.uint8))
        img = card_layer(img, tt)
        d = ImageDraw.Draw(img)
        # тег над субтитрами
        tag = "РАЗРЕЗ СКВАЖИНЫ · МАСШТАБ УСЛОВНЫЙ · СХЕМА"
        d.text((int(28 * s), tag_y), tag, font=f_tag, fill=GREY)
        # субтитры: центр, без плашки, сказанные — белые
        for a0, a1, text in subs:
            if a0 <= tt < a1:
                draw_subs(d, text, f_subs, W, subs_y, (tt - a0) / max(0.1, a1 - gap - a0), s)
                break
        return img

    def card_height(tt):
        x0c, y0c, x1c = slot_card
        rows = [i for i in range(3) if tt >= row_times[i] - 0.1]
        title = "ВЫСОКОЕ СОПРОТИВЛЕНИЕ — НЕФТЬ?" if tt < starts[2] + 0.3 else "МАКСИМУМ — НЕ НЕФТЬ"
        tl = wrap(title, f_title, x1c - x0c - int(40 * s))
        h_title = len(tl) * int(f_title.size * 1.15) + int(f_sub_title.size * 1.4) + int(30 * s)
        return y0c + h_title + len(rows) * int(52 * s) + (int(10 * s) if rows else 0)

    def card_layer(img, tt):
        """Тёмная карточка сверху: заголовок + список строк «номер · порода — значение»."""
        x0c, y0c, x1c = slot_card
        rows = [i for i in range(3) if tt >= row_times[i] - 0.1]
        title = "ВЫСОКОЕ СОПРОТИВЛЕНИЕ — НЕФТЬ?" if tt < starts[2] + 0.3 else "МАКСИМУМ — НЕ НЕФТЬ"
        sub = "проверим по пластам" if tt < starts[2] + 0.3 else "нефть — там, где среднее"
        tl = wrap(title, f_title, x1c - x0c - int(40 * s))
        h_title = len(tl) * int(f_title.size * 1.15) + int(f_sub_title.size * 1.4) + int(30 * s)
        row_h = int(52 * s)
        hh = h_title + len(rows) * row_h + (int(10 * s) if rows else 0)
        ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(ov)
        a_card = ease_out(tt / 0.4)
        od.rounded_rectangle([x0c, y0c, x1c, y0c + hh], radius=int(14 * s), fill=(18, 16, 22, int(205 * a_card)),
                             outline=(255, 255, 255, int(28 * a_card)), width=1)
        od.rectangle([x0c + int(14 * s), y0c + int(16 * s), x0c + int(19 * s), y0c + h_title - int(14 * s)],
                     fill=ORANGE + (int(255 * a_card),))
        img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
        d = ImageDraw.Draw(img)
        y = y0c + int(14 * s)
        for ln in tl:
            d.text((x0c + int(32 * s), y), ln, font=f_title, fill=WHITE)
            y += int(f_title.size * 1.15)
        d.text((x0c + int(32 * s), y + int(2 * s)), sub, font=f_sub_title, fill=GREY)
        y = y0c + h_title
        for i in rows:
            a = ease_out((tt - row_times[i] + 0.1) / 0.3)
            name, val, col, _ = LAYERS[i]
            hot = i == 2 and tt > starts[2] + 0.4
            yy = y + (1 - a) * 10 * s
            bx = x0c + int(30 * s)
            d.rounded_rectangle([bx, yy + 8 * s, bx + 30 * s, yy + 38 * s], radius=int(6 * s), fill=ORANGE)
            num = str(i + 1)
            fb = font(int(19 * s), True)
            d.text((bx + 15 * s - fb.getlength(num) / 2, yy + 11 * s), num, font=fb, fill=INK)
            d.text((bx + 44 * s, yy + 6 * s), name, font=f_row, fill=WHITE)
            vcol = ORANGE if hot else (200, 200, 206)
            d.text((x1c - int(22 * s) - f_row2.getlength(val), yy + 9 * s), val, font=f_row2, fill=vcol)
            y += row_h
        return img

    # звук: музыка + эффекты на появлении строк
    voice = np.zeros(int(total * tts.SR))
    for st_, c in zip(starts, clips):
        i = int(st_ * tts.SR)
        voice[i:i + len(c["audio"])] += c["audio"][: len(voice) - i]
    fx = np.zeros_like(voice)
    for rt in row_times:
        p = sound.pop(int(rt * 10) % 3)
        i = int((rt + 1 / fps) * tts.SR)
        fx[i:i + len(p)] += p[: len(fx) - i]
    w0 = sound.whoosh(1, 0.6)
    i = int((t_desc0 - 0.3) * tts.SR)
    fx[i:i + len(w0)] += w0 * 0.8
    imp = sound.impact()
    i = int(starts[2] * tts.SR)
    fx[i:i + len(imp)] += imp[: len(fx) - i] * 0.8
    mix = sound.mix(voice, fx, sound.music(total), -18, -9)
    out.parent.mkdir(parents=True, exist_ok=True)
    raw, wav = out.with_suffix(".raw.wav"), out.with_suffix(".wav")
    tts.write_wav(raw, mix)
    tts.normalize(raw, wav, -16)
    raw.unlink(missing_ok=True)
    # кадры → ffmpeg
    n = int(total * fps)
    cmd = [tts.ffmpeg_exe(), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(fps), "-i", "-", "-i", str(wav), "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-shortest", str(out)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for k in range(n):
        p.stdin.write(frame(k / fps).tobytes())
    p.stdin.close()
    p.wait()
    wav.unlink(missing_ok=True)
    print(f"{out}  {total:.1f} с, {W}x{H}", file=sys.stderr)
    return frame, total


def wrap(text, f, w):
    out, cur = [], ""
    for word in text.split():
        cand = (cur + " " + word).strip()
        if f.getlength(cand) <= w or not cur:
            cur = cand
        else:
            out.append(cur)
            cur = word
    out.append(cur)
    return out


def draw_subs(d, text, f, W, y, progress, s):
    lines = wrap(text, f, W - 80 * s)
    total = sum(len(ln) for ln in lines) or 1
    done = 0
    for ln in lines:
        x = (W - f.getlength(ln)) / 2
        for word in ln.split():
            col = WHITE if done < progress * total else (130, 130, 138)
            d.text((x, y), word, font=f, fill=col, stroke_width=max(1, int(3 * s)), stroke_fill=INK)
            x += f.getlength(word + " ")
            done += len(word) + 1
        y += int(f.size * 1.25)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="runs/drafts/trap_draft.mp4")
    ap.add_argument("--w", type=int, default=540)
    ap.add_argument("--fps", type=int, default=30)
    a = ap.parse_args()
    render(Path(a.out), a.w, a.fps)


if __name__ == "__main__":
    main()
