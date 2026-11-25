"""Мелкие помощники рисования на Pillow: перенос строк, подгонка шрифта, пунктир, плашки."""
from __future__ import annotations

from PIL import ImageDraw

from .style import font


def wrap(text: str, fnt, max_w: float) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for w in para.split():
            cand = (cur + " " + w).strip()
            if fnt.getlength(cand) <= max_w or not cur:
                cur = cand
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    return [ln for ln in lines if ln] or [""]


def fit(text: str, max_w: float, size: int, min_size: int, max_lines: int, bold=False, fnt_fn=None):
    """Шрифт и строки: уменьшаем кегль до min_size, пока текст не влезет в max_lines строк."""
    s = size
    while True:
        f = fnt_fn(s) if fnt_fn else font(s, bold)
        lines = wrap(text, f, max_w)
        if (len(lines) <= max_lines and all(f.getlength(ln) <= max_w for ln in lines)) or s <= min_size:
            return f, lines[:max_lines] if s <= min_size else lines
        s -= 2


def line_h(fnt) -> int:
    a, d = fnt.getmetrics()
    return int((a + d) * 1.08)


def text_block(d: ImageDraw.ImageDraw, xy, lines, fnt, fill, align="left", width=None, spacing=1.0):
    x, y = xy
    lh = int(line_h(fnt) * spacing)
    for ln in lines:
        if align == "center" and width:
            dx = (width - fnt.getlength(ln)) / 2
        elif align == "right" and width:
            dx = width - fnt.getlength(ln)
        else:
            dx = 0
        d.text((x + dx, y), ln, font=fnt, fill=fill)
        y += lh
    return y


def dashed_hline(d, x0, x1, y, fill, width=2, dash=14, gap=10):
    x = x0
    while x < x1:
        d.line([(x, y), (min(x + dash, x1), y)], fill=fill, width=width)
        x += dash + gap


def dashed_polyline(d, pts, fill, width=3, dash=16, gap=10):
    """Пунктир вдоль ломаной (для второй кривой в том же треке)."""
    import math
    on, left = True, dash
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        seg = math.hypot(x1 - x0, y1 - y0)
        pos = 0.0
        while pos < seg:
            step = min(left, seg - pos)
            if on:
                a = pos / seg if seg else 0
                b = (pos + step) / seg if seg else 0
                d.line([(x0 + (x1 - x0) * a, y0 + (y1 - y0) * a), (x0 + (x1 - x0) * b, y0 + (y1 - y0) * b)],
                       fill=fill, width=width)
            pos += step
            left -= step
            if left <= 1e-6:
                on = not on
                left = dash if on else gap


def rounded_box(d, box, fill, radius=18, outline=None, width=1):
    d.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


class SDraw:
    """ImageDraw с суперсэмплингом: координаты в логических пикселях, картинка в k раз больше.

    Pillow рисует линии и круги без сглаживания; рисуем в 2× и уменьшаем LANCZOS — края гладкие.
    Масштаб хранится в img.info["ssaa"] (переживает img.copy()).
    """

    def __init__(self, img):
        from PIL import ImageDraw as _D
        self.k = float(img.info.get("ssaa", 1))
        self.d = _D.Draw(img)

    def _p(self, xy):
        k = self.k
        if isinstance(xy, (list, tuple)) and xy and isinstance(xy[0], (list, tuple)):
            return [(x * k, y * k) for x, y in xy]
        return [v * k for v in xy]

    def _f(self, fnt):
        if self.k == 1 or not hasattr(fnt, "path"):
            return fnt
        from PIL import ImageFont
        return _scaled_font(fnt.path, int(round(fnt.size * self.k)), getattr(fnt, "_glava_var", None))

    def line(self, xy, fill=None, width=1, joint=None):
        self.d.line(self._p(xy), fill=fill, width=max(1, int(round(width * self.k))), joint=joint)

    def rectangle(self, xy, fill=None, outline=None, width=1):
        self.d.rectangle(self._p(xy), fill=fill, outline=outline, width=max(1, int(round(width * self.k))))

    def rounded_rectangle(self, xy, radius=0, fill=None, outline=None, width=1):
        self.d.rounded_rectangle(self._p(xy), radius=radius * self.k, fill=fill, outline=outline,
                                 width=max(1, int(round(width * self.k))))

    def ellipse(self, xy, fill=None, outline=None, width=1):
        self.d.ellipse(self._p(xy), fill=fill, outline=outline, width=max(1, int(round(width * self.k))))

    def polygon(self, xy, fill=None, outline=None):
        self.d.polygon(self._p(xy), fill=fill, outline=outline)

    def text(self, xy, text, font=None, fill=None, stroke_width=0, stroke_fill=None, anchor=None):
        self.d.text(tuple(self._p(xy)), text, font=self._f(font), fill=fill,
                    stroke_width=int(round(stroke_width * self.k)), stroke_fill=stroke_fill, anchor=anchor)


from functools import lru_cache as _lru


@_lru(maxsize=None)
def _scaled_font(path, size, var=None):
    from PIL import ImageFont
    f = ImageFont.truetype(path, size)
    if var:
        f.set_variation_by_name(var)
    return f
