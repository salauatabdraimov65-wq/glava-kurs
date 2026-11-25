"""Стиль ролика: цвета и правила берутся из доменного пакета (domain/<домен>/styleguide.json).

Код не знает, что такое нефть: имена литологий, флюидов и кривых приходят из стайлгайда.
"""
from __future__ import annotations

import json
import os
import sys
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

ROOT = Path(__file__).resolve().parent.parent
VIDEO_DIR = Path(__file__).resolve().parent


def hex_rgb(h: str, alpha: int | None = None) -> tuple:
    h = h.lstrip("#")
    rgb = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    return rgb + (alpha,) if alpha is not None else rgb


def mix(c1, c2, t: float) -> tuple:
    """Смешать два цвета (t=0 → c1, t=1 → c2)."""
    return tuple(int(round(a + (b - a) * t)) for a, b in zip(c1[:3], c2[:3]))


def domain_for_chapter(chapter_id: str | None) -> str:
    if os.environ.get("VIDEO_DOMAIN"):
        return os.environ["VIDEO_DOMAIN"]
    try:
        cfg = json.loads((ROOT / "config" / "chapters.json").read_text(encoding="utf-8"))["chapters"]
        return cfg.get(chapter_id or "", {}).get("domain", "geology")
    except (OSError, KeyError, ValueError):
        return "geology"


def domain_dir(domain: str) -> Path:
    return ROOT / "domain" / domain


@lru_cache(maxsize=None)
def load_style(domain: str) -> dict:
    return json.loads((domain_dir(domain) / "styleguide.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def load_settings() -> dict:
    """Настройки видео-станка (голос, fps и т.д.). Переменные окружения VIDEO_* перекрывают файл."""
    s = json.loads((VIDEO_DIR / "settings.json").read_text(encoding="utf-8"))
    env_map = {
        "VIDEO_VOICE_ENGINE": ("voice", "engine"),
        "VIDEO_EDGE_VOICE": ("voice", "edge_voice"),
        "VIDEO_EDGE_RATE": ("voice", "edge_rate"),
        "VIDEO_SILERO_SPEAKER": ("voice", "silero_speaker"),
        "VIDEO_FPS": ("render", "fps"),
    }
    for env, (sec, key) in env_map.items():
        if os.environ.get(env):
            val = os.environ[env]
            s[sec][key] = int(val) if key == "fps" else val
    return s


# ---------------------------------------------------------------- шрифты
def _font_candidates(name: str, bold: bool) -> list[Path]:
    win = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    mac_sup = Path("/System/Library/Fonts/Supplemental")
    linux = Path("/usr/share/fonts/truetype")
    mpl = None
    try:
        import matplotlib
        mpl = Path(matplotlib.__file__).parent / "mpl-data" / "fonts" / "ttf"
    except ImportError:
        pass
    cands: list[Path] = []
    if name.lower().replace(" ", "") == "segoeui":
        cands += [win / ("segoeuib.ttf" if bold else "segoeui.ttf")]
    cands += [
        win / ("arialbd.ttf" if bold else "arial.ttf"),
        mac_sup / ("Arial Bold.ttf" if bold else "Arial.ttf"),
        Path.home() / "Library/Fonts" / ("Arial Bold.ttf" if bold else "Arial.ttf"),
        linux / "dejavu" / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),
    ]
    if mpl:
        cands.append(mpl / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"))
    return cands


@lru_cache(maxsize=None)
def font_path(name: str, bold: bool = False) -> str:
    for p in _font_candidates(name, bold):
        if p.exists():
            return str(p)
    print("[video] нет TTF-шрифта с кириллицей, текст будет некрасивым", file=sys.stderr)
    return ""


FONTS = VIDEO_DIR / "fonts"


@lru_cache(maxsize=None)
def font(size: int, bold: bool = False, name: str = "PT Sans") -> ImageFont.FreeTypeFont:
    """Основной шрифт: PT Sans из video/fonts (одинаково на Mac и Windows), иначе системный."""
    own = FONTS / ("PTSans-Bold.ttf" if bold else "PTSans-Regular.ttf")
    if own.exists():
        return ImageFont.truetype(str(own), size)
    p = font_path("Segoe UI", bold)
    if not p:
        return ImageFont.load_default(size)
    return ImageFont.truetype(p, size)


@lru_cache(maxsize=None)
def display_font(size: int) -> ImageFont.FreeTypeFont:
    """Узкий жирный шрифт для заголовков: PT Sans Narrow Bold из video/fonts; иначе Bahnschrift / Arial Narrow."""
    own = FONTS / "PTSansNarrow-Bold.ttf"
    if own.exists():
        return ImageFont.truetype(str(own), size)
    win = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "bahnschrift.ttf"
    if win.exists():
        f = ImageFont.truetype(str(win), size)
        for name in (b"Bold Condensed", "Bold Condensed", b"Bold SemiCondensed", "Bold"):
            try:
                f.set_variation_by_name(name)
                f._glava_var = name
                break
            except (OSError, ValueError):
                continue
        return f
    for p in (Path("/System/Library/Fonts/Supplemental/Arial Narrow Bold.ttf"),
              Path.home() / "Library/Fonts/Arial Narrow Bold.ttf",
              Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "ARIALNB.TTF"):
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return font(size, True)


@lru_cache(maxsize=None)
def mono_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    """Цифры, теги, шкалы: JetBrains Mono из video/fonts."""
    own = FONTS / ("JetBrainsMono-Bold.ttf" if bold else "JetBrainsMono-Regular.ttf")
    return ImageFont.truetype(str(own), size) if own.exists() else font(size, bold)
