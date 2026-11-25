"""Design tokens taken from the chapter's domain package.

The course code knows nothing about a particular subject. Everything that makes
the course look like its domain (palette, legend names, pattern hints) is read
from ``domain/<name>/styleguide.json``. Missing pieces fall back to a neutral
palette, so a new domain recolours the course without code changes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HEX = re.compile(r"#[0-9A-Fa-f]{6}")

# Neutral fallback: used only when the domain package has no usable colours.
NEUTRAL_LIGHT = {"paper": "#F3E4C4", "paper_2": "#EAD6AE", "ink": "#2A1C10", "muted": "#6B5238"}
NEUTRAL_DARK = {"paper": "#11161B", "paper_2": "#1A2129", "ink": "#F1E6CF", "muted": "#A9B0B8"}
NEUTRAL_STRATA = ["#E9B949", "#4FA88B", "#5B8DEF", "#E0736A", "#9C7AD6", "#D9A26B"]
STATUS = {"good": "#1F8A4C", "bad": "#C2412D"}

# Generic map-legend patterns; a domain entry may name one via "pattern".
PATTERNS = ("dots", "dash", "brick", "diag", "cross", "wave", "ring", "zigzag")


def _rgb(value: str) -> tuple[float, float, float]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{round(max(0.0, min(1.0, c)) * 255):02X}" for c in rgb)


def _luminance(value: str) -> float:
    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in _rgb(value))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def mix(a: str, b: str, share_b: float) -> str:
    ra, rb = _rgb(a), _rgb(b)
    return _hex(tuple(x + (y - x) * share_b for x, y in zip(ra, rb)))  # type: ignore[arg-type]


def readable_on(color: str, background: str, target: float = 4.6) -> str:
    """Shift a colour toward black or white until it reads as text on background."""
    toward = "#000000" if _luminance(background) > 0.35 else "#FFFFFF"
    for step in range(0, 101, 4):
        candidate = mix(color, toward, step / 100)
        if contrast(candidate, background) >= target:
            return candidate
    return toward


def ink_on(fill: str, light: str, dark: str) -> str:
    return light if contrast(light, fill) >= contrast(dark, fill) else dark


def chroma(value: str) -> float:
    rgb = _rgb(value)
    return max(rgb) - min(rgb)


def hue(value: str) -> float:
    import colorsys
    return colorsys.rgb_to_hsv(*_rgb(value))[0] * 360


def hue_gap(a: str, b: str) -> float:
    gap = abs(hue(a) - hue(b)) % 360
    return min(gap, 360 - gap)


def chapter_domain(chapter_id: str, override: str | None = None) -> str:
    if override:
        return override
    config = PROJECT_ROOT / "config" / "chapters.json"
    try:
        with config.open("r", encoding="utf-8") as source:
            entry = (json.load(source).get("chapters") or {}).get(chapter_id) or {}
        return str(entry.get("domain") or "geology")
    except (OSError, ValueError, AttributeError):
        return "geology"


def _read(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as source:
            return json.load(source)
    except (OSError, ValueError):
        return default


def load_domain(domain: str) -> dict[str, Any]:
    """Domain by name (folder in domain/) or by path to a domain folder."""
    folder = Path(domain) if Path(domain).is_dir() and ("/" in domain or "\\" in domain) else PROJECT_ROOT / "domain" / domain
    styleguide = _read(folder / "styleguide.json", {})
    misconceptions = _read(folder / "misconceptions.json", [])
    return {
        "name": folder.name,
        "styleguide": styleguide if isinstance(styleguide, dict) else {},
        "misconceptions": {
            str(item["id"]): str(item.get("text", ""))
            for item in misconceptions if isinstance(item, dict) and item.get("id")
        } if isinstance(misconceptions, list) else {},
    }


def swatch_groups(styleguide: dict[str, Any]) -> list[tuple[str, list[dict[str, str]]]]:
    """Groups of named colours: {"key": {"color": "#…", "ru": "…"}} in file order."""
    groups = []
    for key, value in styleguide.items():
        if not isinstance(value, dict):
            continue
        items = []
        for item_key, item in value.items():
            if isinstance(item, dict) and HEX.fullmatch(str(item.get("color", ""))):
                items.append({"key": str(item_key), "color": str(item["color"]).upper(),
                              "label": str(item.get("ru") or item.get("label") or item_key),
                              "pattern": str(item.get("pattern", ""))})
        if len(items) >= 3:
            groups.append((str(key), items))
    return groups


def pattern_body(name: str, ink: str) -> str:
    """Shapes of a 16×16 legend pattern tile."""
    stroke = f'stroke="{ink}" stroke-opacity=".55" stroke-width="1.3" fill="none"'
    shapes = {
        "dots": f'<circle cx="4" cy="4" r="1.3" fill="{ink}" fill-opacity=".55"/><circle cx="12" cy="12" r="1.3" fill="{ink}" fill-opacity=".55"/>',
        "dash": f'<path d="M2 5h6M10 13h6" {stroke}/>',
        "brick": f'<path d="M0 .5h16M0 8.5h16M4 .5v8M12 8.5v8" {stroke}/>',
        "diag": f'<path d="M-2 18 18-2M6 18 18 6M-2 10 10-2" {stroke}/>',
        "cross": f'<path d="M4 2v4M2 4h4M12 10v4M10 12h4" {stroke}/>',
        "wave": f'<path d="M0 5q4-4 8 0t8 0M0 13q4-4 8 0t8 0" {stroke}/>',
        "ring": f'<circle cx="8" cy="8" r="3.2" {stroke}/>',
        "zigzag": f'<path d="M0 6l4-4 4 4 4-4 4 4M0 14l4-4 4 4 4-4 4 4" {stroke}/>',
    }
    return shapes.get(name, shapes["dots"])


def _pattern_svg(name: str, ink: str) -> str:
    body = pattern_body(name, ink)
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">{body}</svg>'
    return 'url("data:image/svg+xml,' + quote(svg, safe=" =:/,-.") + '")'


def theme(domain_data: dict[str, Any]) -> dict[str, Any]:
    """CSS custom properties for both themes plus the legend shown to the reader."""
    styleguide = domain_data.get("styleguide") or {}
    course = styleguide.get("course") if isinstance(styleguide.get("course"), dict) else {}
    groups = swatch_groups(styleguide)
    wanted = str(course.get("strata_group") or "")
    strata_group = next((items for key, items in groups if key == wanted), groups[0][1] if groups else [])
    strata = strata_group[:8] or [
        {"key": f"s{i}", "color": color, "label": "", "pattern": ""} for i, color in enumerate(NEUTRAL_STRATA)
    ]

    def pick(source: dict[str, Any], key: str, fallback: str) -> str:
        value = str(source.get(key, ""))
        return value.upper() if HEX.fullmatch(value) else fallback

    light_src = course.get("light") if isinstance(course.get("light"), dict) else {}
    light = {k: pick(light_src, k, v) for k, v in NEUTRAL_LIGHT.items()}
    dark_src = course.get("dark") if isinstance(course.get("dark"), dict) else {
        "paper": styleguide.get("background"), "paper_2": styleguide.get("panel"),
        "ink": styleguide.get("text"), "muted": styleguide.get("muted"),
    }
    dark = {k: pick(dark_src, k, v) for k, v in NEUTRAL_DARK.items()}
    dark["muted"] = readable_on(dark["muted"], dark["paper"])
    light["muted"] = readable_on(light["muted"], light["paper"])

    by_chroma = sorted(strata, key=lambda item: chroma(item["color"]), reverse=True)
    accent = pick(course, "accent", by_chroma[0]["color"])
    # Second accent: the most saturated colour that sits far from the first on the colour wheel.
    contrasting = [item["color"] for item in by_chroma[1:] if hue_gap(item["color"], accent) >= 90]
    accent_2 = pick(course, "accent_2", (contrasting or [item["color"] for item in by_chroma[1:]] or [accent])[0])

    common: list[str] = [f"--strata-count:{len(strata)}"]
    light_vars: list[str] = []
    dark_vars: list[str] = []
    band = []
    for index, item in enumerate(strata):
        color = item["color"]
        pattern = item["pattern"] if item["pattern"] in PATTERNS else PATTERNS[index % len(PATTERNS)]
        on = ink_on(color, "#FFFFFF", "#111111")
        common.append(f"--s{index}:{color}")
        common.append(f"--s{index}-on:{on}")
        common.append(f"--s{index}-pat:{_pattern_svg(pattern, on)}")
        light_vars.append(f"--s{index}-text:{readable_on(color, light['paper'])}")
        dark_vars.append(f"--s{index}-text:{readable_on(color, dark['paper'])}")
        start, end = index * 100 / len(strata), (index + 1) * 100 / len(strata)
        band.append(f"{color} {start:.2f}% {end:.2f}%")
    common.append(f"--strata-band:linear-gradient(90deg,{','.join(band)})")
    common.append(f"--accent:{accent}")
    common.append(f"--accent-on:{ink_on(accent, '#FFFFFF', '#111111')}")
    common.append(f"--accent-2:{accent_2}")
    common.append(f"--accent-2-on:{ink_on(accent_2, '#FFFFFF', '#111111')}")

    def scheme(base: dict[str, str]) -> list[str]:
        paper = base["paper"]
        return [
            f"--paper:{paper}", f"--paper-2:{base['paper_2']}", f"--ink:{base['ink']}", f"--muted:{base['muted']}",
            f"--rule:{mix(paper, base['ink'], .22)}", f"--rule-soft:{mix(paper, base['ink'], .1)}",
            f"--accent-text:{readable_on(accent, paper)}", f"--accent-2-text:{readable_on(accent_2, paper)}",
            f"--accent-wash:{mix(paper, accent, .18)}", f"--accent-2-wash:{mix(paper, accent_2, .16)}",
            f"--good:{readable_on(STATUS['good'], paper)}", f"--good-wash:{mix(paper, STATUS['good'], .14)}",
            f"--bad:{readable_on(STATUS['bad'], paper)}", f"--bad-wash:{mix(paper, STATUS['bad'], .12)}",
        ]

    light_vars = scheme(light) + light_vars
    dark_vars = scheme(dark) + dark_vars
    # The core-box navigation stays dark in both themes, like a map's legend panel.
    common += [
        f"--night:{dark['paper']}", f"--night-2:{dark['paper_2']}", f"--night-ink:{dark['ink']}",
        f"--night-muted:{dark['muted']}", f"--night-rule:{mix(dark['paper'], dark['ink'], .2)}",
        f"--night-accent:{readable_on(accent, dark['paper'])}",
    ]
    legend = [{"index": i, "label": item["label"], "color": item["color"]} for i, item in enumerate(strata) if item["label"]]
    return {
        "css": (":root{" + ";".join(common + light_vars) + "}"
                ":root[data-theme=\"dark\"]{color-scheme:dark;" + ";".join(dark_vars) + "}"),
        "count": len(strata),
        "legend": legend,
        "colors": [item["color"] for item in strata],
        "ons": [ink_on(item["color"], "#FFFFFF", "#111111") for item in strata],
        "patterns": [item["pattern"] if item["pattern"] in PATTERNS else PATTERNS[i % len(PATTERNS)] for i, item in enumerate(strata)],
    }


ART_TYPES = {".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml"}


def art_css(domain_name: str) -> tuple[str, list[str]]:
    """Illustrations of the domain as CSS variables (--art-<file name>: url(data:…)).

    Looked up in domain/<name>/art/ first, then in course/art-staging/<name>/ (until the domain
    package gets the files). No files = no variables: the course falls back to plain paper.
    """
    import base64

    folders = [PROJECT_ROOT / "domain" / domain_name / "art", Path(__file__).resolve().parent / "art-staging" / domain_name]
    found: dict[str, Path] = {}
    for folder in folders:
        if folder.is_dir():
            for item in sorted(folder.iterdir()):
                if item.suffix.lower() in ART_TYPES and item.is_file() and not item.name.startswith("."):
                    found.setdefault(item.stem.lower(), item)
    variables, names = [], []
    for stem, path in found.items():
        safe = re.sub(r"[^a-z0-9-]+", "-", stem).strip("-")
        if not safe:
            continue
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        variables.append(f'--art-{safe}:url("data:{ART_TYPES[path.suffix.lower()]};base64,{data}")')
        names.append(safe)
    return (":root{" + ";".join(variables) + "}" if variables else ""), names
