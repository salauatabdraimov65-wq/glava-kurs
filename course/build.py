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


def usable_tasks(tasks: list[Any]) -> list[dict[str, Any]]:
    return [task for task in tasks if isinstance(task, dict)
            and task.get("type") in {"single", "multiple", "order", "gap", "calc"}
            and task.get("id") and text_of(task.get("prompt")) and "answer" in task]


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
    node_tasks = [task for task in usable_tasks(tasks) if task.get("step_id") == node.get("id")]
    step_minutes = estimated_minutes(node, tasks)
    if step_minutes:
        blocks.append(f'<div class="step-time">≈ {step_minutes} мин · чтение и практика</div>')
    step_key = html.escape(str(node.get("id", "")), quote=True)
    blocks.append('<div class="step-mode-switcher" role="group" aria-label="Этапы урока">')
    blocks.append(f'<button type="button" data-step-mode="material" aria-pressed="true" aria-controls="lesson-{step_key}"><span class="phase-number">1</span> Материал</button>')
    if node_tasks:
        blocks.append(f'<button type="button" data-step-mode="practice" aria-pressed="false" aria-controls="practice-{step_key}"><span class="phase-number">2</span> Практика <span class="mode-count" data-mode-count>0 / {len(node_tasks)}</span></button>')
    blocks.append(f'<button type="button" data-step-mode="result" aria-pressed="false" aria-controls="result-{step_key}"><span class="phase-number">{3 if node_tasks else 2}</span> Итог</button></div>')
    blocks.append(f'<div class="lesson-column" id="lesson-{step_key}">')
    if text_of(node.get("why_here")):
        blocks.append('<section class="content-block lesson-purpose"><h2>В этом уроке</h2>')
        blocks.append(field_paragraph(node["why_here"], page))
        blocks.append('</section>')

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

    next_phase = "К практике →" if node_tasks else "Посмотреть итог →"
    blocks.append(f'<div class="reading-actions"><span class="reading-state" data-reading-state></span><button class="button-primary" type="button" data-read-step>Материал изучен · {next_phase}</button></div>')
    blocks.append("</div>")
    if node_tasks:
        blocks.append(f'<section class="content-block practice practice-column" id="practice-{step_key}" hidden><div class="exercise-kicker">ПРАКТИКА</div><h2>Проверьте понимание</h2><div class="step-reward" data-step-reward></div><div class="quiz-topline"><strong data-question-counter></strong><nav class="quiz-jumps" aria-label="Выбор задания">')
        blocks.extend(f'<button type="button" data-question="{index}" aria-label="Задание {index + 1}">{index + 1}</button>' for index in range(len(node_tasks)))
        blocks.append('</nav></div>')
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
        blocks.append('<div class="quiz-navigation"><button type="button" data-question-previous>← Предыдущее задание</button><button class="button-primary" type="button" data-question-next>Следующее задание →</button></div></section>')
    practice_state = '<li data-practice-state></li>' if node_tasks else ''
    blocks.append(
        f'<section class="lesson-results content-block" id="result-{step_key}" hidden>'
        '<div class="exercise-kicker">ИТОГ УРОКА</div><h2 data-result-title>Ваш результат</h2>'
        '<p data-result-description></p><ul class="completion-checklist"><li data-theory-state></li>'
        f'{practice_state}</ul><div class="result-actions"><button class="button-primary" type="button" data-result-continue>Продолжить урок →</button>'
        '<button type="button" data-show-view="overview">К программе курса</button></div></section>'
    )
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
    task_items = (tasks_data.get("tasks") or []) if isinstance(tasks_data, dict) else []
    tasks = usable_tasks(task_items)
    illustrations = illustrations_data.get("illustrations", []) if isinstance(illustrations_data, dict) else []
    if not isinstance(illustrations, list):
        illustrations = []
    ids = [str(node.get("id", "")) for node in nodes]
    tasks = [task for task in tasks if task.get("step_id") in ids]
    task_map: dict[str, list[dict[str, Any]]] = {step_id: [] for step_id in ids}
    for task in tasks:
        if task.get("step_id") in task_map:
            task_map[task["step_id"]].append(task)

    step_markup = []
    nav_markup = []
    select_options = []
    for index, node in enumerate(nodes):
        step_id = ids[index]
        step_tasks = task_map.get(step_id, [])
        step_markup.append(
            f'<section class="step" id="step-{index}" data-step-id="{html.escape(step_id, quote=True)}" data-mode="material" hidden>'
            f'{render_node(node, step_tasks, illustrations, routes, base)}</section>'
        )
        label = text_of((node.get("layers") or {}).get("topic")) or str(node.get("title", f"Шаг {index + 1}"))
        minutes = estimated_minutes(node, step_tasks)
        lesson_task_count = len(step_tasks)
        task_word = count_word(lesson_task_count, "задание", "задания", "заданий")
        meta = " · ".join(item for item in (f"≈ {minutes} мин" if minutes else "", f"{lesson_task_count} {task_word}" if lesson_task_count else "", "Материал и практика" if lesson_task_count else "Материал") if item)
        select_options.append(f'<option value="{index}">Урок {index + 1}. {html.escape(label)}</option>')
        nav_markup.append(
            f'<button class="step-link" type="button" data-go="{index}" data-step-id="{html.escape(step_id, quote=True)}"><span class="step-number">{index + 1}</span>'
            f'<span class="lesson-info"><span class="step-name">{html.escape(label)}</span><span class="lesson-meta">{html.escape(meta)}</span></span>'
            '<span class="lesson-status">Не начат</span><span class="step-marker" aria-hidden="true">○</span></button>'
        )

    cards_markup = render_cards(nodes)
    summary_markup = render_summary(nodes)
    map_content = render_map(nodes)
    chapter_name = html.escape(str(layer.get("chapter", "Курс")))
    hero_title = html.escape(text_of(layer.get("title")) or "Учебный курс по главе")
    step_count = len(nodes)
    task_count = len(tasks)
    step_word = count_word(step_count, "урок", "урока", "уроков")
    total_minutes = sum(estimated_minutes(node, tasks) for node in nodes)
    duration_markup = f'<span>{ui_icon("scroll")} ≈ {total_minutes} мин</span>' if total_minutes else ''
    task_word = count_word(task_count, "задание", "задания", "заданий")
    task_json = json.dumps(tasks, ensure_ascii=False, separators=(",", ":"))
    task_json = task_json.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    chapter_json = json.dumps(str(layer.get("chapter", "course")), ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    player_js = Path(__file__).with_name("player.js").read_text(encoding="utf-8")
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
  <div class="masthead"><div class="brand"><span class="brand-icon">{ui_icon('layers')}</span> ГЛАВА → КУРС</div><div class="header-tools"><span class="offline-label"><span class="status-dot"></span> Доступно офлайн</span><div class="theme-controls"><button id="theme-toggle" type="button">Тёмная тема</button><button id="reset-progress" type="button" aria-label="Сбросить прогресс">Сбросить прогресс</button></div></div></div>
  <main id="course-content" class="layout">
    <nav class="view-switcher" aria-label="Раздел курса">
      <button type="button" data-show-view="overview" aria-pressed="true">{ui_icon('book')} Обзор курса</button>
      <button type="button" data-show-view="cards" aria-pressed="false">{ui_icon('cards')} Повторение</button>
      <button type="button" data-show-view="map" aria-pressed="false">{ui_icon('map')} Карта связей</button>
      <button type="button" data-show-view="summary" aria-pressed="false">{ui_icon('scroll')} Конспект</button>
    </nav>
    <section id="overview-view" class="app-view">
    <header class="hero">
    <div class="hero-copy">
      <div class="hero-kicker">САМОСТОЯТЕЛЬНОЕ ОБУЧЕНИЕ</div>
      <h1>{hero_title}</h1>
      <p class="hero-note">Проходите уроки последовательно: изучите материал, выполните задания и посмотрите итог. Возвращайтесь к сложным темам в любое время.</p>
      <div class="hero-stats"><span>{ui_icon('book')} <b>{step_count}</b> {step_word}</span><span>{ui_icon('check')} <b>{task_count}</b> {task_word}</span>{duration_markup}</div>
      <div class="hero-completion"><div><span>Пройдено уроков</span><b id="completed-steps">0 / {step_count}</b></div><div class="hero-track" role="progressbar" aria-label="Пройденные уроки" aria-valuemin="0" aria-valuemax="{step_count}" aria-valuenow="0"><span id="completion-fill"></span></div></div>
    </div>
    <div class="strata-scene" aria-hidden="true">{strata_markup}</div>
    </header>
    <div class="resume-card"><div><div class="nav-kicker" id="resume-label">СЛЕДУЮЩИЙ УРОК</div><h2 id="resume-title"></h2><p id="resume-description" class="muted"></p></div><button class="button-primary" type="button" data-show-view="course" data-resume>Начать обучение →</button></div>
    <div class="course-toolbar"><div class="learning-status">{ui_icon('check')}<span>Выполнено заданий: <b id="solved-tasks" aria-live="polite">0 / {task_count}</b></span></div><span class="muted">Материал → Практика → Итог урока</span></div>
    <section id="course-nav" aria-label="Программа курса"><div class="route-heading">{ui_icon('book')}<div><div class="nav-kicker">ПРОГРАММА КУРСА</div><strong>Уроки в порядке изучения</strong></div></div><nav class="step-list">{''.join(nav_markup)}</nav><div class="nav-footnote">Прогресс и личные заметки сохраняются на этом устройстве.</div></section>
    </section>
      <section id="course-view" class="app-view" hidden><div class="reader-topbar"><button type="button" data-show-view="overview" aria-label="Вернуться к обзору курса"><span aria-hidden="true">←</span> <span class="reader-back-label">Обзор курса</span></button><label><span class="reader-select-label">Перейти к уроку</span><select id="lesson-select" aria-label="Перейти к уроку">{''.join(select_options)}</select></label></div><div class="reader-progress"><div id="progress-label" class="muted"></div><div class="progress" role="progressbar" aria-label="Текущий урок" aria-valuemin="1"><span id="progress-fill"></span></div></div>{''.join(step_markup)}<div class="footer-nav"><button id="previous" type="button">← Предыдущий урок</button><button id="next" type="button">Следующий урок →</button></div></section>
      <section id="cards-view" class="app-view" hidden><h1>Карточки повторения</h1><div class="cards-grid">{cards_markup}</div></section>
      <section id="map-view" class="app-view" hidden><h1>Карта связей шагов</h1>{map_content}</section>
      <section id="summary-view" class="app-view" hidden><h1>Конспект</h1><p><button id="print-summary" class="no-print" type="button">Распечатать / сохранить в PDF</button></p>{summary_markup}</section>
    </main>
  <footer class="site-footer">Материал главы · Практические задания · Повторение</footer>
</div>
<script>
const TASKS = {task_json};
const CHAPTER = {chapter_json};
{player_js}
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
