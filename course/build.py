"""Build a self-contained offline course from a chapter package."""

from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path
from typing import Any


def read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    with path.open("r", encoding="utf-8") as source:
        return json.load(source)


def text_of(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("text", "")
    return str(value).strip() if value is not None else ""


def inline_text(value: str) -> str:
    """Escape text while rendering the small offline math subset used by chapters."""
    greek = {
        "\\alpha": "α", "\\beta": "β", "\\gamma": "γ", "\\delta": "δ",
        "\\epsilon": "ε", "\\theta": "θ", "\\lambda": "λ", "\\mu": "μ",
        "\\pi": "π", "\\rho": "ρ", "\\sigma": "σ", "\\tau": "τ",
        "\\phi": "φ", "\\omega": "ω", "\\Gamma": "Γ", "\\Delta": "Δ",
        "\\Sigma": "Σ", "\\Omega": "Ω",
    }

    def render_formula(match: re.Match[str]) -> str:
        formula = html.escape(match.group(1))
        formula = re.sub(r"_\{([^{}]+)\}", r"<sub>\1</sub>", formula)
        formula = re.sub(r"\^\{([^{}]+)\}", r"<sup>\1</sup>", formula)
        formula = re.sub(r"_([A-Za-z0-9])", r"<sub>\1</sub>", formula)
        formula = re.sub(r"\^([A-Za-z0-9])", r"<sup>\1</sup>", formula)
        for latex, symbol in greek.items():
            formula = formula.replace(latex, symbol)
        return f'<span class="formula">{formula}</span>'

    parts = re.split(r"(\$[^$\n]+\$)", value)
    return "".join(render_formula(re.match(r"\$(.+)\$", part)) if part.startswith("$") else html.escape(part) for part in parts if part)


def field_paragraph(value: Any, page: Any, *, label: str = "") -> str:
    """Render a text field and retain its source page and provenance flags."""
    text = text_of(value)
    if not text:
        return ""
    flags: list[str] = []
    if isinstance(value, dict) and value.get("by_model") is True:
        flags.append('<span class="badge">написано моделью</span>')
    if isinstance(value, dict) and value.get("hypothesis") is True:
        flags.append('<span class="badge hypothesis">гипотеза</span>')
    source = f'<span class="page">стр. {html.escape(str(page))}</span>' if page else ""
    title = f"<h3>{html.escape(label)}</h3>" if label else ""
    paragraphs = []
    for part in re.split(r"\n\s*\n", text):
        part = part.strip()
        if part:
            paragraphs.append(
                f'<p class="paragraph">{inline_text(part)} {source} {" ".join(flags)}</p>'
            )
    return title + "".join(paragraphs)


def render_node(node: dict[str, Any], tasks: list[dict[str, Any]], illustrations: list[dict[str, Any]], routes: Any, base: Path) -> str:
    pages = node.get("pages") or []
    page = pages[0] if pages else ""
    topic = text_of((node.get("layers") or {}).get("topic"))
    heading = topic or str(node.get("title", "Шаг курса"))
    heading_words = heading.split()
    if len(heading_words) > 7:
        heading = " ".join(heading_words[:7])
    book_title = str(node.get("title", ""))
    blocks = [f'<h1>{html.escape(heading)}</h1>']
    if book_title and book_title != heading:
        title_page = f'<span class="page">стр. {html.escape(str(page))}</span>' if page else ""
        blocks.append(f'<div class="book-title">Раздел книги: {html.escape(book_title)} {title_page}</div>')
    supported_task_types = {"single", "multiple", "order", "gap", "calc"}
    node_tasks = [
        task for task in tasks
        if task.get("step_id") == node.get("id")
        and task.get("type") in supported_task_types
        and task.get("id")
        and text_of(task.get("prompt"))
        and "answer" in task
    ]
    step_minutes = estimated_minutes(node, tasks)
    if step_minutes:
        blocks.append(f'<div class="step-time">≈ {step_minutes} мин · чтение и практика</div>')
    step_key = html.escape(str(node.get("id", "")), quote=True)
    if node_tasks:
        blocks.append(
            '<div class="step-mode-switcher" role="group" aria-label="Режим шага">'
            f'<button type="button" data-step-mode="material" aria-pressed="true" aria-controls="lesson-{step_key}">Материал</button>'
            f'<button type="button" data-step-mode="practice" aria-pressed="false" aria-controls="practice-{step_key}">Практика <span class="mode-count" data-mode-count>0 / {len(node_tasks)}</span></button></div>'
        )
    blocks.append(f'<div class="lesson-column" id="lesson-{step_key}">')

    layers = node.get("layers") or {}
    if layers.get("main"):
        blocks.append('<section class="content-block"><h2>Суть</h2>')
        blocks.append(field_paragraph(layers.get("main"), page))
        blocks.append("</section>")

    details: list[str] = []
    for key, label in (("mechanism", "Механизм"), ("practice", "Практика"), ("nuances", "Нюансы")):
        if layers.get(key):
            rendered = field_paragraph(layers.get(key), page, label=label)
            if rendered:
                details.append(rendered)
    for item in layers.get("deeper") or []:
        rendered = field_paragraph(item, page, label="Подробнее")
        if rendered:
            details.append(rendered)
    claims = [claim for claim in (node.get("claims") or []) if isinstance(claim, dict) and (text_of(claim.get("text")) or text_of(claim.get("quote")))]
    if details or claims:
        blocks.append('<section class="content-block"><h2>Как это работает</h2>')
        blocks.extend(details)
        for claim in claims:
            claim_page = claim.get("page_pdf") or page
            claim_id = str(claim.get("id", ""))
            claim_anchor = html.escape("claim-" + claim_id.replace("#", "-"), quote=True)
            blocks.append(f'<div id="{claim_anchor}" class="claim">{field_paragraph(claim.get("text"), claim_page, label="Утверждение из главы")}</div>')
            quote = text_of(claim.get("quote"))
            if quote:
                provenance = "написано моделью" if claim.get("by_model") is True else "цитата из главы"
                blocks.append(
                    f'<details class="quote"><summary>{provenance} · стр. {html.escape(str(claim_page))}</summary>'
                    f'<blockquote>{html.escape(quote)}</blockquote></details>'
                )
        blocks.append("</section>")

    decision = node.get("decision_on_data")
    if isinstance(decision, dict):
        values = [*decision.get("features", []), decision.get("rule"), decision.get("conclusion")]
        if decision.get("counterexample"):
            values.append(decision["counterexample"])
        values = [str(value) for value in values if value]
        if values:
            blocks.append('<section class="content-block"><h2>По шагам</h2><ol>')
            blocks.extend(f"<li>{html.escape(value)} {f'<span class=\"page\">стр. {html.escape(str(page))}</span>' if page else ''}</li>" for value in values)
            blocks.append("</ol></section>")

    for example in node.get("worked_examples") or []:
        if not isinstance(example, dict):
            continue
        example_page = example.get("page_pdf") or page
        steps = [str(item) for item in example.get("steps", []) if item]
        if not any((text_of(example.get("condition")), steps, text_of(example.get("result")), text_of(example.get("figure")))):
            continue
        blocks.append('<section class="content-block"><h2>Разбор примера</h2>')
        blocks.append(field_paragraph(example.get("condition"), example_page, label="Условие"))
        if steps:
            blocks.append("<ol>" + "".join(
                f'<li>{html.escape(item)} <span class="page">стр. {html.escape(str(example_page))}</span></li>' for item in steps
            ) + "</ol>")
        blocks.append(field_paragraph(example.get("result"), example_page, label="Итог"))
        if example.get("figure"):
            blocks.append(field_paragraph(example["figure"], example_page, label="Рисунок"))
        blocks.append("</section>")

    terms = [term for term in (node.get("terms_introduced") or []) if str(term).strip()]
    if terms:
        blocks.append('<section class="content-block"><h2>Понятия</h2><ul class="terms">')
        blocks.extend(f"<li>{html.escape(str(term))}</li>" for term in terms)
        blocks.append("</ul></section>")

    misconception = node.get("misconception")
    if isinstance(misconception, dict) and misconception.get("text"):
        blocks.append('<section class="content-block"><h2>Частая ошибка</h2>')
        blocks.append(field_paragraph(misconception, page))
        blocks.append("</section>")

    meaning = node.get("data_meaning") or {}
    meaning_values = [("Из главы", meaning.get("from_chapter")), ("Что ломается", meaning.get("breaks"))]
    meaning_values.extend(("Гипотеза", item) for item in meaning.get("hypotheses") or [])
    if any(text_of(value) for _, value in meaning_values):
        blocks.append('<section class="content-block"><h2>Что это значит для данных</h2>')
        blocks.extend(field_paragraph(value, page, label=label) for label, value in meaning_values)
        blocks.append("</section>")

    analogy = node.get("it_analogy")
    if isinstance(analogy, dict) and (analogy.get("text") or analogy.get("breaks_where")):
        blocks.append('<section class="content-block"><h2>Аналогия и её границы</h2>')
        blocks.append(field_paragraph(analogy.get("text"), page, label="Аналогия"))
        blocks.append(field_paragraph(analogy.get("breaks_where"), page, label="Где аналогия ломается"))
        blocks.append("</section>")

    book_tasks = [item for item in (node.get("book_tasks") or []) if isinstance(item, dict) and (text_of(item.get("text")) or text_of(item.get("figure")))]
    if book_tasks:
        blocks.append('<section class="content-block"><h2>Задачи из книги</h2>')
        for book_task in book_tasks:
            task_page = book_task.get("page_pdf") or page
            label = f"Задача {book_task.get('number', '')}".strip()
            blocks.append(field_paragraph(book_task.get("text"), task_page, label=label))
            if book_task.get("figure"):
                blocks.append(field_paragraph(book_task["figure"], task_page, label="Нужный рисунок"))
        blocks.append("</section>")

    for illustration in illustrations:
        if illustration.get("step_id") != node.get("id"):
            continue
        image_path = illustration.get("path") or illustration.get("file") or illustration.get("src")
        if image_path and (base / Path(str(image_path))).is_file():
            relative = Path("..") / Path(str(image_path))
            src = html.escape(relative.as_posix(), quote=True)
            alt = html.escape(str(illustration.get("alt") or illustration.get("caption") or "Иллюстрация"), quote=True)
            caption = html.escape(str(illustration.get("caption") or ""))
            provenance = {
                "code_from_data": "код по данным", "code_from_model": "написано моделью",
                "photo": "фотография", "redrawn_from_book": "перерисовано из книги",
                "ai_generated": "иллюстрация ИИ",
            }.get(str(illustration.get("origin", "")), "")
            mark = " · ".join(html.escape(str(value)) for value in (illustration.get("label"), provenance) if value)
            figure_page = f'<span class="page">стр. {html.escape(str(page))}</span>' if page else ""
            suffix = f' · {mark}' if mark else ""
            if Path(str(image_path)).suffix.lower() == ".mp4":
                blocks.append(f'<figure class="content-block"><video controls preload="metadata" src="{src}" aria-label="{alt}"></video><figcaption>{caption}{suffix} {figure_page}</figcaption></figure>')
            else:
                blocks.append(f'<figure class="content-block"><img loading="lazy" src="{src}" alt="{alt}"><figcaption>{caption}{suffix} {figure_page}</figcaption></figure>')

    route = route_for_node(routes, str(node.get("id", "")))
    video = route.get("video")
    video_path = route.get("video_path") or route.get("file") or route.get("path")
    if isinstance(video, str):
        video_path = video_path or video
    elif isinstance(video, dict):
        video_path = video_path or video.get("file") or video.get("path")
    video_id = route.get("video_id") or route.get("videoId")
    if not video_path and video_id:
        video_path = str(Path("video") / str(video_id) / "video.mp4")
    if video_path and (base / Path(str(video_path))).is_file():
        src = html.escape((Path("..") / str(video_path)).as_posix(), quote=True)
        blocks.append(f'<section class="content-block"><h2>Видео</h2><video controls preload="metadata" src="{src}">Ваш браузер не поддерживает видео.</video></section>')

    if not node_tasks:
        step_id = html.escape(str(node.get("id", "")), quote=True)
        blocks.append(f'<div class="step-complete"><button type="button" data-complete-step="{step_id}">Отметить шаг как изученный</button></div>')
    else:
        blocks.append('<div class="reading-actions"><button class="button-primary" type="button" data-open-practice>Перейти к практике →</button></div>')
    blocks.append("</div>")
    if node_tasks:
        blocks.append(f'<aside class="content-block practice practice-column" id="practice-{step_key}" hidden><div class="exercise-kicker">ПРАКТИКА</div><h2>Проверьте понимание</h2><div class="step-reward" data-step-reward></div>')
        claim_pages = {str(claim.get("id")): claim.get("page_pdf") for claim in claims if claim.get("id")}
        claim_texts = {str(claim.get("id")): claim for claim in claims if claim.get("id") and text_of(claim.get("text"))}
        for task in node_tasks:
            pages_for_task = sorted({claim_pages[claim_id] for claim_id in task.get("claim_ids", []) if claim_id in claim_pages and claim_pages[claim_id]})
            source = ", ".join(str(source_page) for source_page in pages_for_task) or page
            claim_links = []
            for claim_id in task.get("claim_ids", []):
                if claim_id in claim_pages:
                    anchor = html.escape("claim-" + str(claim_id).replace("#", "-"), quote=True)
                    short_id = html.escape(str(claim_id).rsplit("#", 1)[-1])
                    claim_links.append(f'<a href="#{anchor}">{short_id}</a>')
            claim_text = " · утверждение " + ", ".join(claim_links) if claim_links else ""
            source_markup = f'<span data-task-source class="page">стр. {html.escape(str(source))}{claim_text}</span>' if source else ""
            hint_claims = [claim_texts[claim_id] for claim_id in task.get("claim_ids", []) if claim_id in claim_texts]
            hint_markup = ""
            if hint_claims:
                hint_parts = []
                for claim in hint_claims:
                    model_badge = '<span class="badge">написано моделью</span>' if claim.get("by_model") is True else ""
                    hint_parts.append(
                        f'<p>{inline_text(text_of(claim.get("text")))} '
                        f'<span class="page">стр. {html.escape(str(claim.get("page_pdf") or page))}</span> {model_badge}</p>'
                    )
                hint_content = "".join(hint_parts)
                hint_markup = f'<div class="hint-box"><button type="button" class="hint-toggle" aria-expanded="false">Подсказка</button><div class="hint-content" hidden>{hint_content}</div></div>'
            blocks.append(f'<article class="task" data-task-id="{html.escape(str(task.get("id", "")), quote=True)}">{source_markup}{hint_markup}</article>')
        blocks.append("</aside>")
    blocks.append(
        '<details class="step-notes no-print"><summary>Мои заметки к шагу</summary><div class="notes-content">'
        f'<label for="notes-{step_key}">Запишите выводы, вопросы или то, что хотите повторить.</label>'
        f'<textarea id="notes-{step_key}" data-step-note maxlength="12000" rows="5"></textarea>'
        '<span class="notes-status" role="status">Заметки сохраняются только на этом устройстве.</span></div></details>'
    )
    return "".join(blocks)


def estimated_minutes(node: dict[str, Any], tasks: list[dict[str, Any]]) -> int:
    for key in ("estimated_minutes", "time_minutes", "duration_min"):
        value = node.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return max(1, round(value))
    text_parts: list[str] = []
    for value in (node.get("layers") or {}).values():
        if isinstance(value, list):
            text_parts.extend(text_of(item) for item in value)
        else:
            text_parts.append(text_of(value))
    text_parts.extend(text_of(claim.get("text")) for claim in node.get("claims", []) if isinstance(claim, dict))
    words = sum(len(part.split()) for part in text_parts)
    task_count = sum(1 for task in tasks if task.get("step_id") == node.get("id"))
    if not words and not task_count:
        return 0
    return max(1, (words + 179) // 180 + task_count)


def route_for_node(routes: Any, node_id: str) -> dict[str, Any]:
    if isinstance(routes, dict):
        items = routes.get("routes", routes.get("nodes", routes.get("routing", routes)))
        if isinstance(items, dict):
            candidate = items.get(node_id, {})
            return candidate if isinstance(candidate, dict) else {}
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and item.get("step_id", item.get("node_id", item.get("id"))) == node_id:
                    return item
    return {}


def render_cards(nodes: list[dict[str, Any]]) -> str:
    cards = []
    for index, node in enumerate(nodes):
        pages = node.get("pages") or []
        page = pages[0] if pages else ""
        layers = node.get("layers") or {}
        topic = text_of(layers.get("topic")) or str(node.get("title", "Шаг курса"))
        title = str(node.get("title", ""))
        title_markup = ""
        if title and title != topic:
            title_page = f'<span class="page">стр. {html.escape(str(page))}</span>' if page else ""
            title_markup = f'<div class="book-title">{html.escape(title)} {title_page}</div>'
        cards.append(
            f'<article class="review-card" data-review-id="{html.escape(str(node.get("id", "")), quote=True)}">'
            f'<h2>{html.escape(topic)}</h2>{title_markup}'
            f'{field_paragraph(layers.get("main"), page)}'
            f'<div class="card-actions"><button type="button" data-review="know" aria-pressed="false">Знаю</button>'
            f'<button type="button" data-review="repeat" aria-pressed="false">Повторить</button>'
            f'<button type="button" data-go="{index}">Открыть шаг</button></div></article>'
        )
    return "".join(cards)


def render_summary(nodes: list[dict[str, Any]]) -> str:
    summaries = []
    for node in nodes:
        pages = node.get("pages") or []
        page = pages[0] if pages else ""
        layers = node.get("layers") or {}
        topic = text_of(layers.get("topic")) or str(node.get("title", "Шаг курса"))
        section = [f'<article class="content-block summary-step"><h2>{html.escape(topic)}</h2>']
        if node.get("title") and str(node["title"]) != topic:
            title_page = f'<span class="page">стр. {html.escape(str(page))}</span>' if page else ""
            section.append(f'<div class="book-title">{html.escape(str(node["title"]))} {title_page}</div>')
        for key, label in (("main", "Суть"), ("mechanism", "Механизм"), ("practice", "Практика"), ("nuances", "Нюансы")):
            if text_of(layers.get(key)):
                section.append(field_paragraph(layers[key], page, label=label))
        for claim in node.get("claims") or []:
            if isinstance(claim, dict) and text_of(claim.get("text")):
                section.append(field_paragraph(claim.get("text"), claim.get("page_pdf") or page, label="Утверждение"))
        section.append("</article>")
        summaries.append("".join(section))
    return "".join(summaries)


def render_map(nodes: list[dict[str, Any]]) -> str:
    """Create a small dependency map in inline SVG without external libraries."""
    if not nodes:
        return ""
    step_ids = {str(node.get("id", "")): index for index, node in enumerate(nodes)}
    height = max(180, 100 + len(nodes) * 110)
    pieces = [
        f'<svg class="link-map" viewBox="0 0 800 {height}" role="img" aria-labelledby="map-title map-desc">',
        '<title id="map-title">Карта связей шагов</title>',
        '<desc id="map-desc">Шаги и их связи из учебного слоя</desc>',
        '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="currentColor"/></marker></defs>',
    ]
    relations = []
    for source_index, node in enumerate(nodes):
        source_id = str(node.get("id", ""))
        source_y = 45 + source_index * 110
        for relation in node.get("links") or []:
            if not isinstance(relation, dict) or not relation.get("target"):
                continue
            target_id = str(relation["target"])
            relation_type = str(relation.get("type", "связь"))
            label = {
                "prerequisite": "предпосылка", "example_of": "пример",
                "refutes_misconception": "опровергает", "to_data": "связано с данными",
            }.get(relation_type, relation_type)
            if target_id in step_ids:
                target_index = step_ids[target_id]
                target_y = 45 + target_index * 110
                if source_index < target_index:
                    path = f"M400 {source_y + 64} C400 {source_y + 82}, 400 {target_y - 18}, 400 {target_y}"
                elif source_index > target_index:
                    path = f"M180 {source_y + 32} C70 {source_y + 32}, 70 {target_y + 32}, 180 {target_y + 32}"
                else:
                    path = f"M620 {source_y + 24} C750 {source_y + 24}, 750 {source_y + 52}, 620 {source_y + 52}"
                pieces.append(f'<path class="map-edge" d="{path}" marker-end="url(#arrow)"/>')
                if source_index < target_index:
                    pieces.append(f'<text class="edge-label" x="410" y="{(source_y + target_y + 64) // 2}">{html.escape(label)}</text>')
            note = str(relation.get("note", ""))
            source_name = text_of((node.get("layers") or {}).get("topic")) or str(node.get("title", source_id))
            target_name = next((text_of((item.get("layers") or {}).get("topic")) or str(item.get("title", target_id)) for item in nodes if str(item.get("id", "")) == target_id), target_id)
            relations.append(f'<li><strong>{html.escape(source_name)}</strong> → {html.escape(target_name)} <span class="muted">({html.escape(label)})</span>{f": {html.escape(note)}" if note else ""}</li>')
    for index, node in enumerate(nodes):
        y = 45 + index * 110
        label = text_of((node.get("layers") or {}).get("topic")) or str(node.get("title", f"Шаг {index + 1}"))
        pieces.append(f'<rect class="map-node" x="180" y="{y}" width="440" height="64" rx="14"/>')
        pieces.append(f'<text class="map-number" x="205" y="{y + 39}">{index + 1}</text>')
        pieces.append(f'<text class="map-label" x="240" y="{y + 39}">{html.escape(label[:62])}</text>')
    pieces.append("</svg>")
    relations_markup = "<ul class=\"relation-list\">" + "".join(relations) + "</ul>" if relations else "<p class=\"muted\">В исходных данных нет связей между шагами.</p>"
    return "".join(pieces) + relations_markup


def geology_theme_vars() -> str:
    """Load the domain palette so course colors stay aligned with the geology package."""
    project_root = Path(__file__).resolve().parents[1]
    styleguide = read_json(project_root / "domain" / "geology" / "styleguide.json", {}) or {}
    lithology = styleguide.get("lithology", {})
    palette = {
        "background": styleguide.get("background"),
        "panel": styleguide.get("panel"),
        "text": styleguide.get("text"),
        "muted": styleguide.get("muted"),
        "sand": (lithology.get("sand") or {}).get("color"),
        "shale": (lithology.get("shale") or {}).get("color"),
        "limestone": (lithology.get("limestone") or {}).get("color"),
        "dolomite": (lithology.get("dolomite") or {}).get("color"),
        "anhydrite": (lithology.get("anhydrite") or {}).get("color"),
        "salt": (lithology.get("salt") or {}).get("color"),
        "dense": (lithology.get("dense") or {}).get("color"),
        "reef": (lithology.get("reef") or {}).get("color"),
    }
    safe_palette = {
        name: value for name, value in palette.items()
        if isinstance(value, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", value)
    }
    return " ".join(f"--geo-{name}:{value};" for name, value in safe_palette.items())


def ui_icon(name: str) -> str:
    """Simple line icons, kept inline for offline use."""
    paths = {
        "layers": "m12 3 10 5-10 5L2 8 12 3ZM2 12l10 5 10-5M2 16l10 5 10-5",
        "book": "M12 5c-3-2-6-2-10-1v15c4-1 7-1 10 1 3-2 6-2 10-1V4c-4-1-7-1-10 1Zm0 0v15",
        "check": "M9 12l2 2 4-4M9 3H5v19h14V3h-4M9 2h6v4H9V2Z",
        "map": "m3 5 6-2 6 2 6-2v16l-6 2-6-2-6 2V5Zm6-2v16m6-14v16",
        "cards": "M7 7h14v14H7V7ZM3 17V3h14",
        "scroll": "M5 3h14v18H5V3Zm4 5h6m-6 4h6m-6 4h4",
    }
    path = paths.get(name, paths["book"])
    return f'<svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="{path}"/></svg>'


def count_word(count: int, one: str, few: str, many: str) -> str:
    if 11 <= count % 100 <= 14:
        return many
    if count % 10 == 1:
        return one
    return few if 2 <= count % 10 <= 4 else many


def ordered_nodes(raw_nodes: list[Any]) -> list[dict[str, Any]]:
    nodes = [node for node in raw_nodes if isinstance(node, dict) and node.get("id")]
    nodes.sort(key=lambda node: (node.get("order", 10**9), str(node.get("id", ""))))
    by_id = {str(node["id"]): node for node in nodes}
    original_order = {str(node["id"]): index for index, node in enumerate(nodes)}
    prerequisites: dict[str, set[str]] = {node_id: set() for node_id in by_id}
    dependents: dict[str, set[str]] = {node_id: set() for node_id in by_id}
    for node in nodes:
        node_id = str(node["id"])
        for link in node.get("links") or []:
            if isinstance(link, dict) and link.get("type") == "prerequisite":
                target = str(link.get("target", ""))
                if target in by_id:
                    prerequisites[node_id].add(target)
                    dependents[target].add(node_id)
    ready = [node_id for node_id, needs in prerequisites.items() if not needs]
    key = lambda node_id: (by_id[node_id].get("order", 10**9), original_order[node_id], node_id)
    ready.sort(key=key)
    result: list[str] = []
    while ready:
        node_id = ready.pop(0)
        result.append(node_id)
        for dependent in sorted(dependents[node_id], key=key):
            prerequisites[dependent].discard(node_id)
            if not prerequisites[dependent] and dependent not in result and dependent not in ready:
                ready.append(dependent)
        ready.sort(key=key)
    if len(result) < len(nodes):
        result.extend(sorted((node_id for node_id in by_id if node_id not in result), key=key))
    return [by_id[node_id] for node_id in result]


def html_page(layer: dict[str, Any], tasks_data: dict[str, Any], routes: Any, illustrations_data: Any, base: Path) -> str:
    nodes = ordered_nodes(layer.get("nodes", []))
    task_items = tasks_data.get("tasks", []) if isinstance(tasks_data, dict) else []
    tasks = [task for task in task_items if isinstance(task, dict)]
    illustrations = illustrations_data.get("illustrations", []) if isinstance(illustrations_data, dict) else []
    if not isinstance(illustrations, list):
        illustrations = []
    ids = [str(node.get("id", "")) for node in nodes]
    task_map: dict[str, list[dict[str, Any]]] = {step_id: [] for step_id in ids}
    for task in tasks:
        if task.get("step_id") in task_map:
            task_map[task["step_id"]].append(task)

    step_markup = []
    nav_markup = []
    for index, node in enumerate(nodes):
        step_id = ids[index]
        step_tasks = task_map.get(step_id, [])
        step_markup.append(
            f'<section class="step" id="step-{index}" data-step-id="{html.escape(step_id, quote=True)}" data-mode="material" hidden>'
            f'{render_node(node, step_tasks, illustrations, routes, base)}</section>'
        )
        label = text_of((node.get("layers") or {}).get("topic")) or str(node.get("title", f"Шаг {index + 1}"))
        nav_markup.append(
            f'<button class="step-link" type="button" data-go="{index}" data-step-id="{html.escape(step_id, quote=True)}"><span class="step-number">{index + 1}</span>'
            f'<span class="step-name">{html.escape(label)}</span><span class="step-marker" aria-hidden="true">○</span></button>'
        )

    cards_markup = render_cards(nodes)
    summary_markup = render_summary(nodes)
    map_content = render_map(nodes)
    chapter_name = html.escape(str(layer.get("chapter", "Курс")))
    hero_title = text_of(layer.get("title"))
    if not hero_title and nodes:
        hero_title = text_of((nodes[0].get("layers") or {}).get("topic")) or str(nodes[0].get("title", ""))
    hero_title = html.escape(hero_title or str(layer.get("chapter", "Полевой маршрут")))
    step_count = len(nodes)
    task_count = len(tasks)
    step_word = count_word(step_count, "этап", "этапа", "этапов")
    task_word = count_word(task_count, "задание", "задания", "заданий")
    task_json = json.dumps(tasks, ensure_ascii=False, separators=(",", ":"))
    task_json = task_json.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    geology_vars = geology_theme_vars()
    theme_css = Path(__file__).with_name("theme.css").read_text(encoding="utf-8")
    strata_markup = (Path(__file__).parent / "assets" / "strata.svg").read_text(encoding="utf-8")
    terrain_markup = (Path(__file__).parent / "assets" / "terrain-background.svg").read_text(encoding="utf-8")
    return f'''<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>{chapter_name} — курс</title>
  <style>
    :root {{ {geology_vars} }}
    {theme_css}
  </style>
</head>
<body>
<div class="ambient-terrain" aria-hidden="true">{terrain_markup}</div>
<div class="shell">
  <div class="masthead"><div class="brand"><span class="brand-icon">{ui_icon('layers')}</span> ГЛАВА → КУРС</div><span class="offline-label"><span class="status-dot"></span> Доступно офлайн</span></div>
  <header class="hero">
    <div class="hero-copy">
      <div class="hero-kicker">УЧЕБНЫЙ МОДУЛЬ</div>
      <h1>{hero_title}</h1>
      <p class="hero-note">Изучите материал главы, разберите объяснения и проверьте понимание на практических заданиях.</p>
      <div class="hero-stats"><span>{ui_icon('book')} <b>{step_count}</b> {step_word}</span><span>{ui_icon('check')} <b>{task_count}</b> {task_word}</span><span>{ui_icon('scroll')} Ссылки на источник</span></div>
      <div class="hero-completion"><div><span>Пройдено этапов</span><b id="completed-steps">0 / {step_count}</b></div><div class="hero-track" role="progressbar" aria-label="Пройденные этапы" aria-valuemin="0" aria-valuemax="{step_count}" aria-valuenow="0"><span id="completion-fill"></span></div></div>
    </div>
    <div class="strata-scene" aria-hidden="true">{strata_markup}</div>
  </header>
  <div class="course-toolbar"><div class="learning-status">{ui_icon('check')}<span>Выполнено заданий: <b id="solved-tasks" aria-live="polite">0 / {task_count}</b></span></div><div class="theme-controls"><button class="button-primary" type="button" data-show-view="course" data-resume>Продолжить обучение →</button><button id="theme-toggle" type="button">Тёмная тема</button><button id="reset-progress" type="button" aria-label="Сбросить прогресс">Сбросить прогресс</button></div></div>
  <div class="layout">
    <aside id="course-nav" aria-label="Навигация курса"><div class="route-heading">{ui_icon('book')}<div><div class="nav-kicker">СОДЕРЖАНИЕ МОДУЛЯ</div><strong>Последовательность изучения</strong></div></div><div class="progress" role="progressbar" aria-label="Текущий шаг" aria-valuemin="1"><span id="progress-fill"></span></div><div id="progress-label" class="muted"></div><nav class="step-list">{''.join(nav_markup)}</nav><div class="nav-footnote">Читайте материал, сверяйтесь с источником и закрепляйте его заданиями. Прогресс сохраняется на этом устройстве.</div></aside>
    <main id="course-content">
      <nav class="view-switcher" aria-label="Раздел курса">
        <button type="button" data-show-view="course" aria-pressed="true">{ui_icon('book')} Курс</button>
        <button type="button" data-show-view="cards" aria-pressed="false">{ui_icon('cards')} Карточки</button>
        <button type="button" data-show-view="map" aria-pressed="false">{ui_icon('map')} Карта связей</button>
        <button type="button" data-show-view="summary" aria-pressed="false">{ui_icon('scroll')} Конспект</button>
      </nav>
      <section id="course-view" class="app-view">{''.join(step_markup)}<div class="footer-nav"><button id="previous" type="button">← Назад</button><button id="next" type="button">Дальше →</button></div></section>
      <section id="cards-view" class="app-view" hidden><h1>Карточки повторения</h1><div class="cards-grid">{cards_markup}</div></section>
      <section id="map-view" class="app-view" hidden><h1>Карта связей шагов</h1>{map_content}</section>
      <section id="summary-view" class="app-view" hidden><h1>Конспект</h1><p><button id="print-summary" class="no-print" type="button">Распечатать / сохранить в PDF</button></p>{summary_markup}</section>
    </main>
  </div>
  <footer class="site-footer">Материал главы · Практические задания · Повторение</footer>
</div>
<script>
const TASKS = {task_json};
const STEPS = [...document.querySelectorAll('.step')];
const CHAPTER = {json.dumps(str(layer.get('chapter', 'course')), ensure_ascii=False)};
const STORE_KEY = 'course-progress:' + CHAPTER;
const TASK_KEY = 'course-tasks:' + CHAPTER;
const COMPLETE_KEY = 'course-completed-steps:' + CHAPTER;
const CARD_KEY = 'course-cards:' + CHAPTER;
const VIEW_KEY = 'course-view:' + CHAPTER;
const THEME_KEY = 'course-theme-v2:' + CHAPTER;
const MODE_KEY = 'course-step-modes:' + CHAPTER;
const NOTES_KEY = 'course-notes:' + CHAPTER;
let saved = {{}};
let solved = {{}};
let reviewed = {{}};
let completedSteps = {{}};
function loadRecord(key) {{
  try {{ const value = JSON.parse(localStorage.getItem(key) || '{{}}'); return value && typeof value === 'object' && !Array.isArray(value) ? value : {{}}; }} catch (_) {{ return {{}}; }}
}}
const stepModes = loadRecord(MODE_KEY);
const notes = loadRecord(NOTES_KEY);
try {{ saved = JSON.parse(localStorage.getItem(STORE_KEY) || '{{}}'); }} catch (_) {{}}
try {{ solved = JSON.parse(localStorage.getItem(TASK_KEY) || '{{}}'); }} catch (_) {{}}
try {{ reviewed = JSON.parse(localStorage.getItem(CARD_KEY) || '{{}}'); }} catch (_) {{}}
try {{ completedSteps = JSON.parse(localStorage.getItem(COMPLETE_KEY) || '{{}}'); }} catch (_) {{}}
let current = Math.min(Math.max(Number(saved.step) || 0, 0), Math.max(STEPS.length - 1, 0));
const esc = (s) => String(s ?? '');
function persist(key, value) {{ try {{ localStorage.setItem(key, JSON.stringify(value)); return true; }} catch (_) {{ return false; }} }}
function removeStored(key) {{ try {{ localStorage.removeItem(key); }} catch (_) {{}} }}
function updateLearningProgress() {{
  const taskCards = [...document.querySelectorAll('[data-task-id]')];
  const solvedCount = taskCards.filter(card => solved[card.dataset.taskId]).length;
  document.getElementById('solved-tasks').textContent = solvedCount + ' / ' + taskCards.length;
}}
function syncCourseProgress() {{
  let completedCount = 0;
  let changed = false;
  const justCompleted = [];
  const validTypes = ['single', 'multiple', 'order', 'gap', 'calc'];
  STEPS.forEach(step => {{
    const stepId = step.dataset.stepId;
    const stepTasks = TASKS.filter(task => task.step_id === stepId && validTypes.includes(task.type) && task.id && task.prompt && Object.hasOwn(task, 'answer'));
    const done = stepTasks.length ? stepTasks.every(task => solved[task.id]) : Boolean(completedSteps[stepId]);
    step.dataset.complete = String(done);
    if (done) {{
      completedCount += 1;
      if (!completedSteps[stepId]) {{
        completedSteps[stepId] = true;
        changed = true;
        justCompleted.push(stepId);
      }}
    }}
    const nav = [...document.querySelectorAll('.step-link')].find(item => item.dataset.stepId === stepId);
    if (nav) {{
      nav.classList.toggle('is-complete', done);
      nav.querySelector('.step-marker').textContent = done ? '✓' : '○';
      nav.setAttribute('aria-label', nav.querySelector('.step-name').textContent + (done ? ', пройдено' : ', не пройдено'));
    }}
    const reward = step.querySelector('[data-step-reward]');
    if (reward) {{
      const solvedCount = stepTasks.filter(task => solved[task.id]).length;
      reward.textContent = done ? 'Все задания этапа выполнены' : 'Выполнено заданий: ' + solvedCount + ' из ' + stepTasks.length;
      reward.classList.toggle('is-earned', done);
      const count = step.querySelector('[data-mode-count]');
      if (count) count.textContent = solvedCount + ' / ' + stepTasks.length;
    }}
    const completionButton = step.querySelector('[data-complete-step]');
    if (completionButton) {{
      completionButton.disabled = done;
      completionButton.textContent = done ? 'Шаг изучен' : 'Отметить шаг как изученный';
    }}
  }});
  document.getElementById('completed-steps').textContent = completedCount + ' / ' + STEPS.length;
  document.getElementById('completion-fill').style.width = (STEPS.length ? Math.round(completedCount / STEPS.length * 100) : 0) + '%';
  document.querySelector('.hero-track').setAttribute('aria-valuenow', String(completedCount));
  const resumeButton = document.querySelector('[data-resume]');
  resumeButton.textContent = STEPS.length && completedCount === STEPS.length ? 'Повторить материал →' : 'Продолжить обучение →';
  resumeButton.disabled = !STEPS.length;
  if (changed) {{
    persist(COMPLETE_KEY, completedSteps);
  }}
  updateLearningProgress();
  return justCompleted;
}}
let theme = '';
try {{ theme = localStorage.getItem(THEME_KEY) || ''; }} catch (_) {{}}
if (!['light', 'dark'].includes(theme)) theme = 'light';
document.documentElement.dataset.theme = theme;
const themeToggle = document.getElementById('theme-toggle');
function updateThemeButton() {{ themeToggle.textContent = theme === 'dark' ? 'Светлая тема' : 'Тёмная тема'; themeToggle.setAttribute('aria-label', theme === 'dark' ? 'Включить светлую тему' : 'Включить тёмную тему'); }}
themeToggle.addEventListener('click', () => {{ theme = theme === 'dark' ? 'light' : 'dark'; document.documentElement.dataset.theme = theme; try {{ localStorage.setItem(THEME_KEY, theme); }} catch (_) {{}} updateThemeButton(); }});
updateThemeButton();
let activeView = 'course';
try {{ activeView = localStorage.getItem(VIEW_KEY) || 'course'; }} catch (_) {{}}
if (!['course', 'cards', 'map', 'summary'].includes(activeView)) activeView = 'course';
function setView(name) {{
  activeView = name;
  document.querySelectorAll('.app-view').forEach(view => view.hidden = view.id !== name + '-view');
  document.getElementById('course-nav').hidden = name !== 'course';
  document.querySelector('.layout').dataset.view = name;
  document.body.dataset.view = name;
  document.querySelectorAll('.view-switcher [data-show-view]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.showView === name)));
  try {{ localStorage.setItem(VIEW_KEY, name); }} catch (_) {{}}
}}
function scrollToLesson() {{
  const step = STEPS[current];
  if (!step) return;
  const heading = step.querySelector('h1');
  if (heading) {{ heading.tabIndex = -1; heading.focus({{preventScroll: true}}); }}
  step.scrollIntoView({{block: 'start', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'}});
}}
function setStepMode(step, mode, save = true) {{
  const practice = step.querySelector('.practice-column');
  const selected = mode === 'practice' && practice ? 'practice' : 'material';
  step.dataset.mode = selected;
  step.querySelector('.lesson-column').hidden = selected !== 'material';
  if (practice) practice.hidden = selected !== 'practice';
  step.querySelectorAll('[data-step-mode]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.stepMode === selected)));
  stepModes[step.dataset.stepId] = selected;
  if (save) persist(MODE_KEY, stepModes);
}}
STEPS.forEach(step => {{
  setStepMode(step, stepModes[step.dataset.stepId], false);
  step.querySelectorAll('[data-step-mode]').forEach(button => button.addEventListener('click', () => setStepMode(step, button.dataset.stepMode)));
  step.querySelector('[data-open-practice]')?.addEventListener('click', () => {{
    setStepMode(step, 'practice');
    step.querySelector('[data-step-mode="practice"]').focus({{preventScroll: true}});
    step.scrollIntoView({{block: 'start'}});
  }});
  const input = step.querySelector('[data-step-note]');
  const status = step.querySelector('.notes-status');
  input.value = typeof notes[step.dataset.stepId] === 'string' ? notes[step.dataset.stepId] : '';
  if (input.value) status.textContent = 'Сохранено на этом устройстве.';
  input.addEventListener('input', () => {{
    notes[step.dataset.stepId] = input.value;
    const message = persist(NOTES_KEY, notes) ? 'Сохранено на этом устройстве.' : 'Сохранение недоступно. Скопируйте заметку перед закрытием страницы.';
    if (status.textContent !== message) status.textContent = message;
  }});
}});
function resumeLearning() {{
  const nextIndex = STEPS.findIndex(step => step.dataset.complete !== 'true');
  setView('course');
  setStep(nextIndex < 0 ? 0 : nextIndex);
  if (nextIndex < 0 && STEPS[current]) setStepMode(STEPS[current], 'material');
  scrollToLesson();
}}
document.querySelectorAll('[data-show-view]').forEach(button => button.addEventListener('click', () => {{
  if (button.hasAttribute('data-resume')) resumeLearning();
  else setView(button.dataset.showView);
}}));
document.addEventListener('click', event => {{
  const link = event.target.closest('.task a[href^="#claim-"]');
  if (!link) return;
  const target = document.getElementById(link.getAttribute('href').slice(1));
  const step = target?.closest('.step');
  if (!step) return;
  event.preventDefault();
  setView('course');
  setStep(STEPS.indexOf(step));
  setStepMode(step, 'material');
  target.tabIndex = -1;
  target.focus({{preventScroll: true}});
  target.scrollIntoView({{block: 'start'}});
}});
function setStep(index) {{
  if (!STEPS.length) return;
  current = Math.min(Math.max(index, 0), STEPS.length - 1);
  STEPS.forEach((step, i) => step.hidden = i !== current);
  document.querySelectorAll('.step-link').forEach((button, i) => {{
    if (i === current) button.setAttribute('aria-current', 'step'); else button.removeAttribute('aria-current');
  }});
  const percent = Math.round(((current + 1) / STEPS.length) * 100);
  document.getElementById('progress-fill').style.width = percent + '%';
  document.querySelector('.progress').setAttribute('aria-valuenow', String(current + 1));
  document.querySelector('.progress').setAttribute('aria-valuemax', String(STEPS.length));
  document.getElementById('progress-label').textContent = `${{current + 1}} из ${{STEPS.length}} шагов`;
  document.getElementById('previous').disabled = current === 0;
  document.getElementById('next').disabled = current === STEPS.length - 1;
  persist(STORE_KEY, {{step: current}});
}}
document.querySelectorAll('[data-go]').forEach(button => button.addEventListener('click', () => {{ setView('course'); setStep(Number(button.dataset.go)); scrollToLesson(); }}));
document.getElementById('previous').addEventListener('click', () => {{ setStep(current - 1); scrollToLesson(); }});
document.getElementById('next').addEventListener('click', () => {{ setStep(current + 1); scrollToLesson(); }});
document.querySelectorAll('.review-card').forEach(card => {{
  const id = card.dataset.reviewId;
  card.querySelectorAll('[data-review]').forEach(button => {{
    button.setAttribute('aria-pressed', String(reviewed[id] === button.dataset.review));
    button.addEventListener('click', () => {{
      reviewed[id] = button.dataset.review;
      persist(CARD_KEY, reviewed);
      card.querySelectorAll('[data-review]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
    }});
  }});
}});
document.querySelectorAll('[data-complete-step]').forEach(button => button.addEventListener('click', () => {{
  const stepId = button.dataset.completeStep;
  if (!completedSteps[stepId]) {{
    completedSteps[stepId] = true;
    persist(COMPLETE_KEY, completedSteps);
    button.textContent = 'Шаг изучен';
    button.disabled = true;
    syncCourseProgress();
  }}
}}));
document.getElementById('print-summary').addEventListener('click', () => {{ setView('summary'); window.print(); }});
document.getElementById('reset-progress').addEventListener('click', () => {{ removeStored(STORE_KEY); removeStored(TASK_KEY); removeStored(CARD_KEY); removeStored(COMPLETE_KEY); removeStored(MODE_KEY); location.reload(); }});
function taskElement(task) {{
  const card = document.querySelector(`[data-task-id="${{CSS.escape(task.id)}}"]`);
  if (!card) return;
  const prompt = document.createElement('p'); prompt.className = 'task-prompt';
  if (task.type === 'gap') {{
    const parts = String(task.prompt || '').split(/(\\{{\\{{\\d+\\}}\\}})/g);
    parts.forEach(part => {{ if (/^\\{{\\{{\\d+\\}}\\}}$/.test(part)) {{ const input = document.createElement('input'); input.className = 'gap-input'; input.type = 'text'; input.dataset.gap = part.match(/\\d+/)[0]; input.setAttribute('aria-label', 'Ответ в пропуск'); prompt.append(input); }} else prompt.append(document.createTextNode(part)); }});
  }} else prompt.textContent = esc(task.prompt);
  if (task.by_model === true) {{ const mark = document.createElement('span'); mark.className = 'badge'; mark.textContent = 'написано моделью'; prompt.append(' ', mark); }}
  const taskSource = card.querySelector('[data-task-source]');
  if (taskSource) prompt.append(' ', taskSource);
  card.append(prompt);
  if (task.type === 'calc') {{
    const input = document.createElement('input'); input.type = 'number'; input.step = 'any'; input.className = 'gap-input'; input.dataset.calc = 'true'; input.setAttribute('aria-label', 'Числовой ответ'); card.append(input);
    if (task.unit) {{ const unit = document.createElement('span'); unit.textContent = ' ' + task.unit; card.append(unit); }}
  }} else if (task.type === 'order') {{
    const options = [...(task.options || [])];
    let seed = String(task.id || '').split('').reduce((n, c) => (n * 31 + c.charCodeAt(0)) >>> 0, 7);
    for (let i = options.length - 1; i > 0; i--) {{ seed = (seed * 1664525 + 1013904223) >>> 0; const j = seed % (i + 1); [options[i], options[j]] = [options[j], options[i]]; }}
    const list = document.createElement('div'); list.className = 'options order-list'; list.setAttribute('aria-label', 'Переставьте варианты в нужном порядке');
    options.forEach(option => {{ const row = document.createElement('div'); row.className = 'order-item'; row.dataset.id = option.id; row.draggable = true;
      const handle = document.createElement('button'); handle.className = 'drag-handle'; handle.type = 'button'; handle.textContent = '↕'; handle.setAttribute('aria-label', 'Перетаскивать вариант');
      const text = document.createElement('span'); text.className = 'order-text'; text.textContent = option.text;
      const up = document.createElement('button'); up.type = 'button'; up.textContent = '↑'; up.setAttribute('aria-label', 'Переместить вверх');
      const down = document.createElement('button'); down.type = 'button'; down.textContent = '↓'; down.setAttribute('aria-label', 'Переместить вниз');
      up.addEventListener('click', () => {{ if (row.previousElementSibling) list.insertBefore(row, row.previousElementSibling); }});
      down.addEventListener('click', () => {{ if (row.nextElementSibling) list.insertBefore(row.nextElementSibling, row); }});
      row.append(handle, text, up, down); list.append(row);
      row.addEventListener('dragstart', e => {{ e.dataTransfer.setData('text/plain', option.id); row.classList.add('dragging'); }});
      row.addEventListener('dragend', () => row.classList.remove('dragging'));
    }});
    list.addEventListener('dragover', e => {{ e.preventDefault(); const dragged = list.querySelector('.dragging'); const target = e.target.closest('.order-item'); if (dragged && target && dragged !== target) {{ const rect = target.getBoundingClientRect(); list.insertBefore(dragged, e.clientY < rect.top + rect.height / 2 ? target : target.nextSibling); }} }});
    let pointerRow = null;
    list.addEventListener('pointerdown', e => {{ const handle = e.target.closest('.drag-handle'); if (!handle || handle.disabled) return; pointerRow = handle.closest('.order-item'); pointerRow.classList.add('dragging'); handle.setPointerCapture(e.pointerId); e.preventDefault(); }});
    list.addEventListener('pointermove', e => {{ if (!pointerRow) return; const target = document.elementFromPoint(e.clientX, e.clientY)?.closest('.order-item'); if (target && target !== pointerRow) {{ const rect = target.getBoundingClientRect(); list.insertBefore(pointerRow, e.clientY < rect.top + rect.height / 2 ? target : target.nextSibling); }} }});
    list.addEventListener('pointerup', () => {{ if (pointerRow) pointerRow.classList.remove('dragging'); pointerRow = null; }});
    list.addEventListener('pointercancel', () => {{ if (pointerRow) pointerRow.classList.remove('dragging'); pointerRow = null; }});
    card.append(list);
  }} else if (task.type !== 'gap' && task.type !== 'calc') {{
    const options = document.createElement('div'); options.className = 'options';
    (task.options || []).forEach(option => {{ const label = document.createElement('label'); label.className = 'option'; const input = document.createElement('input'); input.type = task.type === 'multiple' ? 'checkbox' : 'radio'; input.name = 'answer-' + task.id; input.value = option.id; const text = document.createElement('span'); text.textContent = option.text; label.append(input, text); options.append(label); }});
    card.append(options);
  }}
  const controls = document.createElement('div'); controls.className = 'task-tools'; const check = document.createElement('button'); check.type = 'button'; check.textContent = 'Проверить'; controls.append(check); card.append(controls);
  const result = document.createElement('div'); result.className = 'task-result'; result.hidden = true; result.setAttribute('aria-live', 'polite'); card.append(result);
  card.querySelectorAll('.hint-toggle').forEach(button => button.addEventListener('click', () => {{
    const hint = button.nextElementSibling;
    hint.hidden = !hint.hidden;
    button.setAttribute('aria-expanded', String(!hint.hidden));
    button.textContent = hint.hidden ? 'Подсказка' : 'Скрыть подсказку';
  }}));
  function restoreAndLockAnswer() {{
    if (task.type === 'single' || task.type === 'multiple') {{
      const expected = (Array.isArray(task.answer) ? task.answer : [task.answer]).map(String);
      card.querySelectorAll('.option input').forEach(input => {{ input.checked = expected.includes(input.value); }});
    }} else if (task.type === 'gap') {{
      const expected = Array.isArray(task.answer) ? task.answer : [task.answer];
      [...card.querySelectorAll('[data-gap]')].sort((a, b) => Number(a.dataset.gap) - Number(b.dataset.gap)).forEach((input, index) => {{ input.value = String(expected[index] ?? ''); }});
    }} else if (task.type === 'calc') {{
      card.querySelector('[data-calc]').value = String(task.answer);
    }} else if (task.type === 'order') {{
      const list = card.querySelector('.order-list');
      const rows = [...list.querySelectorAll('.order-item')];
      (Array.isArray(task.answer) ? task.answer : []).forEach(id => {{
        const row = rows.find(item => item.dataset.id === String(id));
        if (row) list.append(row);
      }});
      rows.forEach(row => {{ row.draggable = false; }});
      list.querySelectorAll('button').forEach(button => {{ button.disabled = true; }});
    }}
    card.querySelectorAll('input').forEach(input => {{ input.disabled = true; }});
    check.disabled = true;
  }}
  if (solved[task.id]) {{ restoreAndLockAnswer(); showResult(true); }}
  function expectedAnswer() {{
    const optionText = id => (task.options || []).find(option => String(option.id) === String(id))?.text ?? String(id);
    if (task.type === 'single') return optionText(task.answer);
    if (task.type === 'multiple' || task.type === 'order') return (Array.isArray(task.answer) ? task.answer : []).map(optionText).join(task.type === 'order' ? ' → ' : ', ');
    if (task.type === 'gap') return (Array.isArray(task.answer) ? task.answer : [task.answer]).join(', ');
    if (task.type === 'calc') return String(task.answer) + (task.unit ? ' ' + task.unit : '');
    return String(task.answer ?? '');
  }}
  function showResult(correct) {{
    result.hidden = false;
    const explanation = task.explanation || '';
    const modelMark = task.explanation_by_model === true ? ' · написано моделью' : '';
    result.textContent = (correct ? 'Верно. ' : 'Пока неверно. Верный ответ: ' + expectedAnswer() + '. ') + explanation + modelMark;
    result.classList.toggle('correct', correct);
  }}
  check.addEventListener('click', () => {{
    let answer;
    if (task.type === 'order') answer = [...card.querySelectorAll('.order-item')].map(row => row.dataset.id);
    else if (task.type === 'gap') answer = [...card.querySelectorAll('[data-gap]')].sort((a,b) => Number(a.dataset.gap)-Number(b.dataset.gap)).map(input => input.value.trim());
    else if (task.type === 'calc') {{ const input = card.querySelector('[data-calc]'); answer = input.value.trim() === '' ? NaN : Number(input.value); }}
    else answer = [...card.querySelectorAll('input:checked')].map(input => input.value);
    let correct = false;
    if (task.type === 'single') correct = answer.length === 1 && answer[0] === String(task.answer);
    else if (task.type === 'multiple') correct = Array.isArray(task.answer) && answer.length === task.answer.length && [...answer].sort().every((v, i) => String(v) === String([...task.answer].map(String).sort()[i]));
    else if (task.type === 'order') correct = Array.isArray(task.answer) && answer.length === task.answer.length && answer.every((v, i) => String(v) === String(task.answer[i]));
    else if (task.type === 'gap') correct = Array.isArray(task.answer) && answer.length === task.answer.length && answer.every((v, i) => v.trim().replace(/\\s+/g, ' ').localeCompare(String(task.answer[i]).trim().replace(/\\s+/g, ' '), undefined, {{sensitivity:'base'}}) === 0);
    else if (task.type === 'calc') correct = Number.isFinite(answer) && Math.abs(answer - Number(task.answer)) <= Number(task.tolerance || 0);
    const firstSolve = correct && !solved[task.id];
    if (firstSolve) {{
      solved[task.id] = true;
      persist(TASK_KEY, solved);
    }}
    const newMilestones = syncCourseProgress();
    showResult(correct);
    if (firstSolve && newMilestones.includes(card.closest('.step').dataset.stepId)) result.textContent += ' Все задания этапа выполнены.';
    if (correct) restoreAndLockAnswer();
  }});
}}
TASKS.forEach(taskElement);
syncCourseProgress();
setStep(current);
setView(activeView);
</script>
</body>
</html>'''


def build(input_path: Path, output_dir: Path | None = None) -> Path:
    base = input_path if input_path.is_dir() else input_path.parent
    layer_path = input_path / "layer.json" if input_path.is_dir() else input_path
    layer = read_json(layer_path)
    if not isinstance(layer, dict) or not isinstance(layer.get("nodes"), list):
        raise ValueError(f"Не найден корректный layer.json: {layer_path}")
    tasks_data = read_json(base / "tasks.json", {"tasks": []})
    if not isinstance(tasks_data, dict):
        tasks_data = {"tasks": []}
    routes = read_json(base / "routing.json", {}) or {}
    illustrations = read_json(base / "illustrations.json", {}) or {}
    destination = output_dir or (base / "course")
    destination.mkdir(parents=True, exist_ok=True)
    page = destination / "index.html"
    page.write_text(html_page(layer, tasks_data, routes, illustrations, base), encoding="utf-8")
    return page


def main() -> None:
    parser = argparse.ArgumentParser(description="Собрать автономный HTML-курс из JSON главы")
    parser.add_argument("chapter_dir", type=Path, help="папка с layer.json и необязательным tasks.json")
    parser.add_argument("--output", type=Path, help="папка вывода; по умолчанию <глава>/course")
    args = parser.parse_args()
    print(build(args.chapter_dir, args.output))


if __name__ == "__main__":
    main()
