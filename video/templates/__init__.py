"""Реестр шаблонов сцен. Новый шаблон = новый модуль с классом и запись в TEMPLATES."""
from .well_tool_curve import WellToolCurve

TEMPLATES = {
    WellToolCurve.name: WellToolCurve,
}


def get_template(name: str):
    if name not in TEMPLATES:
        raise ValueError(f"нет шаблона сцены «{name}»; есть: {', '.join(TEMPLATES)}")
    return TEMPLATES[name]
