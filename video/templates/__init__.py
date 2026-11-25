"""Реестр шаблонов сцен. Новый шаблон = новый модуль с классом и запись в TEMPLATES."""
from . import pressure_diffusion, well_tool_curve
from .pressure_diffusion import PressureDiffusion
from .well_tool_curve import WellToolCurve

TEMPLATES = {
    WellToolCurve.name: WellToolCurve,
    PressureDiffusion.name: PressureDiffusion,
}


def get_template(name: str):
    if name not in TEMPLATES:
        raise ValueError(f"нет шаблона сцены «{name}»; есть: {', '.join(TEMPLATES)}")
    return TEMPLATES[name]


def catalog() -> dict[str, dict]:
    """Описание шаблонов для генератора сценариев."""
    mods = {WellToolCurve.name: well_tool_curve, PressureDiffusion.name: pressure_diffusion}
    return {n: {"summary": m.SUMMARY, "guide": m.GUIDE} for n, m in mods.items()}
