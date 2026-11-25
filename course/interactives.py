"""Render optional course blocks from the package's data and routing."""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from typing import Any


def page_label(pages: Any) -> str:
    values = pages if isinstance(pages, list) else [pages]
    return ", ".join(str(value) for value in values if value is not None and str(value).strip())


def render_decision(
    decision: dict[str, Any], node_id: str, pages: Any,
    inline: Callable[[str], str], paragraph: Callable[..., str],
) -> str:
    features = [str(value).strip() for value in (decision.get("features") or []) if value]
    conclusion = str(decision.get("conclusion") or "").strip()
    if not features or not conclusion:
        return ""
    source = page_label(pages)
    page = f'<span class="page">стр. {html.escape(source)}</span>' if source else ""
    key = html.escape(node_id, quote=True)
    checks = "".join(
        f'<label class="data-feature"><input type="checkbox" name="feature" value="{index}">'
        f'<span>{inline(feature)} {page}</span></label>'
        for index, feature in enumerate(features)
    )
    choices = [
        (conclusion, True),
        (f'Неверно, что: «{conclusion}»', False),
        (f'Для вывода «{conclusion}» достаточно только признака «{features[0]}»; остальные признаки учитывать не нужно.', False),
    ]
    offset = sum(ord(char) for char in node_id) % len(choices)
    choices = choices[offset:] + choices[:offset]
    options = "".join(
        f'<label class="option"><input type="radio" name="decision-{key}" value="{index}"'
        f' data-correct="{str(correct).lower()}"><span class="option-key" aria-hidden="true">{chr(65 + index)}</span>'
        f'<span>{inline(text)}</span></label>'
        for index, (text, correct) in enumerate(choices)
    )
    return (
        '<section class="content-block data-practicum">'
        f'<h2>Практикум на данных {page}</h2>'
        '<form data-observation-form><fieldset><legend>1. Что ты видишь в данных?</legend>'
        '<p class="muted">Отметьте замеченные признаки. Затем выберите вывод по их совокупности.</p>'
        f'<div class="data-features">{checks}</div></fieldset>'
        '<button type="submit" class="button-primary" disabled>Какой вывод? →</button></form>'
        '<form data-conclusion-form hidden><fieldset><legend tabindex="-1">2. Какой вывод следует из данных?</legend>'
        f'<div class="options">{options}</div></fieldset>'
        '<p class="muted">Учебные варианты ответов · написано моделью.</p>'
        '<div class="interactive-actions"><button type="button" data-back-to-features>← К признакам</button>'
        '<button type="submit" class="button-primary" disabled>Проверить вывод</button></div></form>'
        '<div class="decision-result" hidden><p data-decision-feedback role="status"></p>'
        f'{paragraph(decision.get("conclusion"), source, label="Вывод из учебного слоя")}'
        f'{paragraph(decision.get("rule"), source, label="Правило автора")}'
        f'{paragraph(decision.get("counterexample"), source, label="Когда правило ошибается")}'
        '</div></section>'
    )


def render_pressure(node_id: str, pages: Any) -> str:
    key = html.escape(node_id, quote=True)
    source = page_label(pages)
    page = f'<span class="page">стр. {html.escape(source)}</span>' if source else ""
    return (
        '<section class="content-block pressure-explorer">'
        f'<h2>Калькулятор давления {page}</h2>'
        '<p>Меняйте пьезопроводность и время: наблюдайте, как меняется давление с расстоянием от начальной точки.</p>'
        '<div class="pressure-controls">'
        f'<label for="kappa-{key}">Пьезопроводность κ <output data-kappa-output for="kappa-{key}"></output>'
        f'<input id="kappa-{key}" data-kappa type="range" min="0.1" max="5" step="0.1" value="1"></label>'
        f'<label for="time-{key}">Время t <output data-time-output for="time-{key}"></output>'
        f'<input id="time-{key}" data-pressure-time type="range" min="0" max="1000" step="1" value="726">'
        '<span class="range-limits"><span>10 с</span><span>30 сут · логарифмическая шкала</span></span></label>'
        '</div>'
        f'<svg class="pressure-chart" role="img" aria-labelledby="pressure-title-{key} pressure-desc-{key}" viewBox="0 0 640 340">'
        f'<title id="pressure-title-{key}">Давление от расстояния до начальной точки</title>'
        f'<desc id="pressure-desc-{key}"></desc><g data-pressure-drawing></g></svg>'
        '<p class="simulation-caption">симуляция по формуле главы, условные параметры '
        '<span class="badge hypothesis">гипотеза</span> <span class="badge">написано моделью</span></p>'
        '<p class="muted">Δp = A · E₁(r² / (4κt)), p = p₀ − Δp; p₀ = 20 МПа, A = 0,08 МПа. '
        'У точки r = 0 используется радиус 0,1 м, чтобы избежать особенности формулы.</p>'
        '</section>'
    )


def markdown_cells(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith(r"\|"):
        line = line[:-1]
    return [cell.strip().replace(r"\|", "|") for cell in re.split(r"(?<!\\)\|", line)]


def render_book_tables(tables: Any, pages: Any, inline: Callable[[str], str]) -> str:
    if not isinstance(tables, list):
        return ""
    blocks = []
    for table in tables:
        if not isinstance(table, dict) or not str(table.get("markdown") or "").strip():
            continue
        rows = [markdown_cells(line) for line in str(table["markdown"]).splitlines() if line.strip()]
        if not rows:
            continue
        has_header = len(rows) > 1 and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in rows[1])
        if has_header:
            del rows[1]
        number = table.get("number")
        title = "Таблица" + (f" {number}" if number is not None and str(number).strip() else "")
        source = page_label(table.get("pages") or pages)
        if source:
            title += f", стр. {source}"
        caption = str(table.get("caption") or "").strip()
        heading = html.escape(title)
        if caption:
            heading += " — " + inline(caption)
        row_html = []
        for index, cells in enumerate(rows):
            tag = "th" if index == 0 and has_header else "td"
            scope = ' scope="col"' if tag == "th" else ""
            row = "<tr>" + "".join(f"<{tag}{scope}>{inline(cell)}</{tag}>" for cell in cells) + "</tr>"
            if index == 0 and has_header:
                row = "<thead>" + row + "</thead><tbody>"
            row_html.append(row)
        content = "".join(row_html) + ("</tbody>" if has_header else "")
        note = str(table.get("ocr_note") or "").strip()
        note_html = f'<p class="table-note">{inline(note)}</p>' if note else ""
        model_mark = '<span class="badge">написано моделью</span>' if table.get("by_model") is True else ""
        blocks.append(
            f'<section class="content-block book-table-block"><h2>{heading} {model_mark}</h2>'
            f'<div class="book-table-scroll" tabindex="0" role="region" aria-label="{html.escape(title, quote=True)}">'
            f'<table class="book-table"><caption class="sr-only">{heading}</caption>{content}</table></div>{note_html}</section>'
        )
    return "".join(blocks)
