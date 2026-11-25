"""Реестр шаблонов сцен. Новый шаблон = новый модуль с классом и запись в TEMPLATES."""
from . import well_tool_curve
from .well_tool_curve import WellToolCurve

TEMPLATES = {
    WellToolCurve.name: WellToolCurve,
}


def get_template(name: str):
    if name not in TEMPLATES:
        raise ValueError(f"нет шаблона сцены «{name}»; есть: {', '.join(TEMPLATES)}")
    return TEMPLATES[name]


def catalog() -> dict[str, dict]:
    """Описание шаблонов для генератора сценариев."""
    mods = {WellToolCurve.name: well_tool_curve}
    return {n: {"summary": m.SUMMARY, "guide": m.GUIDE} for n, m in mods.items()}
