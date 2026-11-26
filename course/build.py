"""Build a self-contained offline course from a chapter package."""

from __future__ import annotations

import argparse
import html
import json
import textwrap
import re
from pathlib import Path
from typing import Any

from course.domain_theme import art_css, chapter_domain, load_domain
from course.domain_theme import theme as domain_tokens
from course.interactives import render_book_tables, render_decision, render_pressure

# Page number → relative path of the page scan (filled per build from runs/<id>/scans/).
_SCANS: dict[str, str] = {}
SCAN_NAME = re.compile(r"p0*(\d+)\.(png|jpe?g|webp)", re.IGNORECASE)


def find_scans(base: Path) -> dict[str, str]:
    folder = base / "scans"
    found: dict[str, str] = {}
    if folder.is_dir():
        for item in sorted(folder.iterdir()):
            match = SCAN_NAME.fullmatch(item.name)
            if match and item.is_file():
                found.setdefault(match.group(1), (Path("..") / "scans" / item.name).as_posix())
    return found


def page_tag(page: Any) -> str:
    """Source page marker; becomes a «show scan» button when the scan exists."""
    if page is None or str(page).strip() == "":
        return ""
    label = html.escape(str(page))
    src = _SCANS.get(str(page).strip())
    if src:
        return (f'<button type="button" class="page scan-link" data-scan="{html.escape(src, quote=True)}" '
                f'aria-label="стр. {label}: показать скан страницы">стр. {label}</button>')
    return f'<span class="page">стр. {label}</span>'


def find_videos(base: Path) -> dict[str, list[dict[str, str]]]:
    """Rendered videos by step: video/<id>/video.mp4 plus node_id from spec.json or passport.json."""
    found: dict[str, list[dict[str, str]]] = {}
    folder = base / "video"
    if not folder.is_dir():
        return found
    for item in sorted(folder.iterdir()):
        if not (item / "video.mp4").is_file():
            continue
        meta: dict[str, Any] = {}
        for name in ("passport.json", "spec.json"):
            data = read_json(item / name, {})
            if isinstance(data, dict):
                meta = {**data, **meta}
        node_id = str(meta.get("node_id") or "")
        if not node_id:
            continue
        found.setdefault(node_id, []).append({
            "src": (Path("..") / "video" / item.name / "video.mp4").as_posix(),
            "title": text_of(meta.get("title")),
            "format": str(meta.get("format") or ""),
            "aspect": str(meta.get("aspect") or ""),
            "question": text_of(meta.get("prediction_question")),
            "transfer": text_of(meta.get("transfer_question")),
        })
    return found


def video_block(video: dict[str, str]) -> str:
    vertical = video.get("aspect") == "9:16" or video.get("format") == "short"
    kind = "Шортс" if vertical else "Видео"
    title = f'<h2>{html.escape(video["title"])}</h2>' if video.get("title") else "<h2>Ролик к шагу</h2>"
    question = (f'<p class="predict"><b>Сначала предскажите:</b> {inline_text(video["question"])}</p>'
                if video.get("question") else "")
    transfer = (f'<p class="transfer"><b>После просмотра:</b> {inline_text(video["transfer"])}</p>'
                if video.get("transfer") else "")
    return (f'<section class="content-block block-video{" is-vertical" if vertical else ""}">'
            f'<div class="block-kicker">{kind} · сделан по главе</div>{title}{question}'
            f'<div class="video-frame"><video controls preload="metadata" src="{html.escape(video["src"], quote=True)}">'
            f'Ваш браузер не поддерживает видео.</video></div>{transfer}</section>')


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
    return "".join(render_formula(match) if (match := re.fullmatch(r"\$([^$\n]+)\$", part)) else html.escape(part) for part in parts if part)


def field_paragraph(value: Any, page: Any, *, label: str = "") -> str:
    """Render a text field and retain its source page and provenance flags."""
    text = text_of(value)
    if not text:
        return ""
    flags: list[str] = []
    classes = ["paragraph"]
    if isinstance(value, dict) and value.get("by_model") is True:
        flags.append('<span class="badge">написано моделью</span>')
        classes.append("is-model")
    if isinstance(value, dict) and value.get("hypothesis") is True:
        flags.append('<span class="badge hypothesis">гипотеза</span>')
        classes.append("is-hypothesis")
    source = page_tag(page)
    title = f"<h3>{html.escape(label)}</h3>" if label else ""
    paragraphs = []
    for part in re.split(r"\n\s*\n", text):
        part = part.strip()
        if part:
            paragraphs.append(
                f'<p class="{" ".join(classes)}">{inline_text(part)} {source} {" ".join(flags)}</p>'
            )
    return title + "".join(paragraphs)


def usable_tasks(tasks: list[Any]) -> list[dict[str, Any]]:
    return [task for task in tasks if isinstance(task, dict)
            and task.get("type") in {"single", "multiple", "order", "gap", "calc"}
            and task.get("id") and text_of(task.get("prompt")) and "answer" in task]


def prequestion_text(task: dict[str, Any]) -> str:
    return re.sub(r"\{\{\d+\}\}", "…", text_of(task.get("prompt")))


def render_node(node: dict[str, Any], tasks: list[dict[str, Any]], illustrations: list[dict[str, Any]], routes: Any, base: Path,
                *, index: int = 0, total: int = 1, videos: list[dict[str, str]] | None = None) -> str:
    pages = node.get("pages") or []
    page = pages[0] if pages else ""
    route = route_for_node(routes, str(node.get("id", "")))
    topic = text_of((node.get("layers") or {}).get("topic"))
    heading = topic or str(node.get("title", "Шаг курса"))
    heading_words = heading.split()
    if len(heading_words) > 7:
        heading = " ".join(heading_words[:7])
    book_title = str(node.get("title", ""))
    format_name = text_of(route.get("format"))
    node_tasks = [task for task in usable_tasks(tasks) if task.get("step_id") == node.get("id")]
    step_minutes = estimated_minutes(node, tasks)
    width = max(2, len(str(total)))
    meta = [f'<span class="plate-no">Шаг <b>{index + 1:0{width}d}</b> из {total}</span>']
    if format_name:
        meta.append(f'<span class="format-label">{html.escape(format_name[:1].upper() + format_name[1:])}</span>')
    if step_minutes:
        meta.append(f'<span class="step-time">≈ {step_minutes} мин · чтение и практика</span>')
    blocks = ['<header class="step-plate"><div class="plate-band" aria-hidden="true"><span></span></div>'
              f'<div class="plate-meta">{"".join(meta)}</div><h1>{html.escape(heading)}</h1>']
    if book_title and book_title != heading:
        title_page = page_tag(page)
        blocks.append(f'<div class="book-title"><span class="book-mark" aria-hidden="true">§</span> Раздел книги: {html.escape(book_title)} {title_page}</div>')
    blocks.append('</header>')
    step_key = html.escape(str(node.get("id", "")), quote=True)
    blocks.append('<div class="step-mode-switcher" role="group" aria-label="Этапы урока">')
    blocks.append(f'<button type="button" data-step-mode="material" aria-pressed="true" aria-controls="lesson-{step_key}"><span class="phase-number">1</span> Материал</button>')
    if node_tasks:
        blocks.append(f'<button type="button" data-step-mode="practice" aria-pressed="false" aria-controls="practice-{step_key}"><span class="phase-number">2</span> Практика <span class="mode-count" data-mode-count>0 / {len(node_tasks)}</span></button>')
    blocks.append(f'<button type="button" data-step-mode="result" aria-pressed="false" aria-controls="result-{step_key}"><span class="phase-number">{3 if node_tasks else 2}</span> Итог</button></div>')
    blocks.append(f'<div class="lesson-column" id="lesson-{step_key}">')
    if node_tasks and prequestion_text(node_tasks[0]):
        blocks.append(
            '<aside class="prequestion" aria-label="Вопрос до чтения"><div class="block-kicker">Вопрос до чтения</div>'
            f'<p class="prequestion-text">{inline_text(prequestion_text(node_tasks[0]))}</p>'
            '<p class="prequestion-note">Ответьте про себя и читайте дальше: проверите себя в практике.</p></aside>'
        )
    if text_of(node.get("why_here")):
        blocks.append('<section class="content-block lesson-purpose"><h2>В этом уроке</h2>')
        blocks.append(field_paragraph(node["why_here"], page))
        blocks.append('</section>')

    layers = node.get("layers") or {}
    if layers.get("main"):
        blocks.append('<section class="content-block block-main"><h2>Суть</h2>')
        blocks.append(field_paragraph(layers.get("main"), page))
        blocks.append("</section>")
    for video in videos or []:
        blocks.append(video_block(video))

    decision = node.get("decision_on_data")
    decision_interactive = ""
    if format_name.casefold() == "практикум на данных" and isinstance(decision, dict):
        decision_interactive = render_decision(decision, str(node.get("id", "")), pages, inline_text, field_paragraph)
        blocks.append(decision_interactive)
    if route.get("template") == "pressure_explorer":
        blocks.append(render_pressure(str(node.get("id", "")), pages))

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
        blocks.append('<section class="content-block block-mechanism"><h2>Как это работает</h2>')
        blocks.extend(details)
        for claim in claims:
            claim_page = claim.get("page_pdf") or page
            claim_id = str(claim.get("id", ""))
            claim_anchor = html.escape("claim-" + claim_id.replace("#", "-"), quote=True)
            quote = text_of(claim.get("quote"))
            quote_markup = ""
            if quote:
                provenance = "написано моделью" if claim.get("by_model") is True else "цитата из главы"
                quote_markup = (
                    f'<details class="quote"><summary>{provenance} · стр. {html.escape(str(claim_page))}</summary>'
                    f'<blockquote>{html.escape(quote)}<footer>{page_tag(claim_page)}</footer></blockquote></details>'
                )
            blocks.append(f'<div id="{claim_anchor}" class="claim"><div class="claim-label">Мысль автора</div>'
                          f'{field_paragraph(claim.get("text"), claim_page)}{quote_markup}</div>')
        blocks.append("</section>")

    if isinstance(decision, dict) and not decision_interactive:
        values = [*decision.get("features", []), decision.get("rule"), decision.get("conclusion")]
        if decision.get("counterexample"):
            values.append(decision["counterexample"])
        values = [str(value) for value in values if value]
        if values:
            blocks.append('<section class="content-block block-steps"><h2>По шагам</h2><ol>')
            blocks.extend(f"<li>{html.escape(value)} {page_tag(page)}</li>" for value in values)
            blocks.append("</ol></section>")

    for example in node.get("worked_examples") or []:
        if not isinstance(example, dict):
            continue
        example_page = example.get("page_pdf") or page
        steps = [str(item) for item in example.get("steps", []) if item]
        if not any((text_of(example.get("condition")), steps, text_of(example.get("result")), text_of(example.get("figure")))):
            continue
        blocks.append('<section class="content-block block-example"><h2>Разбор примера</h2>')
        blocks.append(field_paragraph(example.get("condition"), example_page, label="Условие"))
        if steps:
            blocks.append("<ol>" + "".join(
                f'<li>{html.escape(item)} {page_tag(example_page)}</li>' for item in steps
            ) + "</ol>")
        blocks.append(field_paragraph(example.get("result"), example_page, label="Итог"))
        if example.get("figure"):
            blocks.append(field_paragraph(example["figure"], example_page, label="Рисунок"))
        blocks.append("</section>")

    blocks.append(render_book_tables(node.get("book_tables"), pages, inline_text))

    terms = [term for term in (node.get("terms_introduced") or []) if str(term).strip()]
    if terms:
        blocks.append('<section class="content-block block-terms"><h2>Понятия</h2><ul class="terms">')
        blocks.extend(f"<li>{html.escape(str(term))}</li>" for term in terms)
        blocks.append("</ul></section>")

    misconception = node.get("misconception")
    if isinstance(misconception, dict) and misconception.get("text"):
        blocks.append('<section class="content-block block-misconception"><h2>Частая ошибка</h2>')
        blocks.append(field_paragraph(misconception, page))
        blocks.append("</section>")

    meaning = node.get("data_meaning") or {}
    meaning_values = [("Из главы", meaning.get("from_chapter")), ("Что ломается", meaning.get("breaks"))]
    meaning_values.extend(("Гипотеза", item) for item in meaning.get("hypotheses") or [])
    if any(text_of(value) for _, value in meaning_values):
        blocks.append('<section class="content-block block-data"><h2>Что это значит для данных</h2>')
        blocks.extend(field_paragraph(value, page, label=label) for label, value in meaning_values)
        blocks.append("</section>")

    analogy = node.get("it_analogy")
    if isinstance(analogy, dict) and (analogy.get("text") or analogy.get("breaks_where")):
        blocks.append('<section class="content-block block-analogy"><h2>Аналогия и её границы</h2>')
        blocks.append(field_paragraph(analogy.get("text"), page, label="Аналогия"))
        blocks.append(field_paragraph(analogy.get("breaks_where"), page, label="Где аналогия ломается"))
        blocks.append("</section>")

    book_tasks = [item for item in (node.get("book_tasks") or []) if isinstance(item, dict) and (text_of(item.get("text")) or text_of(item.get("figure")))]
    if book_tasks:
        blocks.append('<section class="content-block block-booktasks"><h2>Задачи из книги</h2>')
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
            figure_page = page_tag(page)
            suffix = f' · {mark}' if mark else ""
            if Path(str(image_path)).suffix.lower() == ".mp4":
                blocks.append(f'<figure class="content-block block-figure"><video controls preload="metadata" src="{src}" aria-label="{alt}"></video><figcaption>{caption}{suffix} {figure_page}</figcaption></figure>')
            else:
                blocks.append(f'<figure class="content-block block-figure"><img loading="lazy" src="{src}" alt="{alt}"><figcaption>{caption}{suffix} {figure_page}</figcaption></figure>')

    video = route.get("video")
    video_path = route.get("video_path") or route.get("file") or route.get("path")
    if isinstance(video, str):
        video_path = video_path or video
    elif isinstance(video, dict):
        video_path = video_path or video.get("file") or video.get("path")
    video_id = route.get("video_id") or route.get("videoId")
    if not video_path and video_id:
        video_path = str(Path("video") / str(video_id) / "video.mp4")
    shown = {item.get("src") for item in videos or []}
    if video_path and (base / Path(str(video_path))).is_file() and (Path("..") / str(video_path)).as_posix() not in shown:
        blocks.append(video_block({"src": (Path("..") / str(video_path)).as_posix(), "title": "", "format": text_of(route.get("format"))}))

    next_phase = "К практике →" if node_tasks else "Посмотреть итог →"
    blocks.append(f'<div class="reading-actions"><span class="reading-state" data-reading-state></span><button class="button-primary" type="button" data-read-step>Материал изучен · {next_phase}</button></div>')
    blocks.append("</div>")
    if node_tasks:
        blocks.append(f'<section class="content-block practice practice-column" id="practice-{step_key}" hidden><div class="exercise-kicker">ПРАКТИКА</div><h2>Проверьте понимание</h2><div class="quiz-topline"><div class="quiz-progress"><strong data-question-counter></strong><span class="step-reward" data-step-reward></span></div><nav class="quiz-jumps" aria-label="Выбор задания">')
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
                        f'{page_tag(claim.get("page_pdf") or page)} {model_badge}</p>'
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
            title_page = page_tag(page)
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
            title_page = page_tag(page)
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
    labels = [text_of((node.get("layers") or {}).get("topic")) or str(node.get("title", f"Шаг {i + 1}"))
              for i, node in enumerate(nodes)]
    wrapped = [textwrap.wrap(label, width=23) or [""] for label in labels]
    row_heights = [max(70, 24 + len(lines) * 22) for lines in wrapped]
    positions = []
    height = 28
    for row_height in row_heights:
        positions.append(height)
        height += row_height + 60
    pieces = [
        f'<svg class="link-map" viewBox="0 0 360 {height}" role="img" aria-labelledby="map-title map-desc">',
        '<title id="map-title">Карта связей шагов</title>',
        '<desc id="map-desc">Шаги и их связи из учебного слоя</desc>',
        '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="currentColor"/></marker></defs>',
    ]
    relations = []
    for source_index, node in enumerate(nodes):
        source_id = str(node.get("id", ""))
        source_y = positions[source_index]
        source_height = row_heights[source_index]
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
                target_y = positions[target_index]
                target_height = row_heights[target_index]
                if source_index < target_index:
                    path = f"M210 {source_y + source_height} V{target_y}"
                elif source_index > target_index:
                    path = f"M76 {source_y + source_height // 2} C16 {source_y + source_height // 2}, 16 {target_y + target_height // 2}, 76 {target_y + target_height // 2}"
                else:
                    path = f"M340 {source_y + 18} C360 {source_y + 18}, 360 {source_y + source_height - 18}, 340 {source_y + source_height - 18}"
                pieces.append(f'<path class="map-edge" d="{path}" marker-end="url(#arrow)"/>')
                if source_index < target_index:
                    pieces.append(f'<text class="edge-label" x="90" y="{source_y + source_height + 35}">{html.escape(label)}</text>')
            note = str(relation.get("note", ""))
            source_name = text_of((node.get("layers") or {}).get("topic")) or str(node.get("title", source_id))
            target_name = next((text_of((item.get("layers") or {}).get("topic")) or str(item.get("title", target_id)) for item in nodes if str(item.get("id", "")) == target_id), target_id)
            relations.append(f'<li><strong>{html.escape(source_name)}</strong> → {html.escape(target_name)} <span class="muted">({html.escape(label)})</span>{f": {html.escape(note)}" if note else ""}</li>')
    for index, lines in enumerate(wrapped):
        y, row_height = positions[index], row_heights[index]
        pieces.append(f'<rect class="map-node" x="76" y="{y}" width="264" height="{row_height}" rx="14"/>')
        pieces.append(f'<text class="map-number" x="88" y="{y + 30}">{index + 1}</text>')
        spans = "".join(f'<tspan x="122" dy="{0 if line_index == 0 else 22}">{html.escape(line)}</tspan>'
                        for line_index, line in enumerate(lines))
        pieces.append(f'<text class="map-label" x="122" y="{y + 30}">{spans}</text>')
    pieces.append("</svg>")
    relations_markup = "<ul class=\"relation-list\">" + "".join(relations) + "</ul>" if relations else "<p class=\"muted\">В исходных данных нет связей между шагами.</p>"
    return "".join(pieces) + relations_markup


def ui_icon(name: str) -> str:
    """Simple line icons, kept inline for offline use."""
    paths = {
        "layers": "m12 3 10 5-10 5L2 8 12 3ZM2 12l10 5 10-5M2 16l10 5 10-5",
        "book": "M12 5c-3-2-6-2-10-1v15c4-1 7-1 10 1 3-2 6-2 10-1V4c-4-1-7-1-10 1Zm0 0v15",
        "check": "M9 12l2 2 4-4M9 3H5v19h14V3h-4M9 2h6v4H9V2Z",
        "map": "m3 5 6-2 6 2 6-2v16l-6 2-6-2-6 2V5Zm6-2v16m6-14v16",
        "cards": "M7 7h14v14H7V7ZM3 17V3h14",
        "scroll": "M5 3h14v18H5V3Zm4 5h6m-6 4h6m-6 4h4",
        "menu": "M3 6h18M3 12h18M3 18h18",
        "close": "m6 6 12 12M6 18 18 6",
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


def step_kinds(nodes: list[dict[str, Any]], routes: Any) -> tuple[list[int], dict[str, int]]:
    """Each step format gets its own legend swatch, in order of first appearance."""
    order: dict[str, int] = {}
    kinds = []
    for node in nodes:
        name = text_of(route_for_node(routes, str(node.get("id", ""))).get("format")) or "шаг"
        name = name[:1].upper() + name[1:]
        order.setdefault(name, len(order))
        kinds.append(order[name])
    return kinds, order


def seg_style(kind: int, count: int) -> str:
    k = kind % max(1, count)
    return f"--seg:var(--s{k});--seg-on:var(--s{k}-on);--seg-pat:var(--s{k}-pat);--seg-text:var(--s{k}-text)"


def html_page(layer: dict[str, Any], tasks_data: dict[str, Any], routes: Any, illustrations_data: Any, base: Path,
              domain: str | None = None) -> str:
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

    domain_data = load_domain(chapter_domain(str(layer.get("chapter", "")), domain))
    tokens = domain_tokens(domain_data)
    art_style, art_names = art_css(domain_data["folder"], slots=("cover", "hills"))
    overview_art = ('<div class="hero-illustration" aria-hidden="true"></div>'
                    if "cover" in art_names else "")
    _SCANS.clear()
    _SCANS.update(find_scans(base))
    videos = find_videos(base)
    kinds, kind_order = step_kinds(nodes, routes)
    strata_count = tokens["count"]

    step_markup = []
    nav_markup = []
    core_markup = []
    step_count = len(nodes)
    for index, node in enumerate(nodes):
        step_id = ids[index]
        step_tasks = task_map.get(step_id, [])
        style = seg_style(kinds[index], strata_count)
        safe_id = html.escape(step_id, quote=True)
        step_markup.append(
            f'<section class="step" id="step-{index}" data-step-id="{safe_id}" data-mode="material" style="{style}" hidden>'
            f'{render_node(node, step_tasks, illustrations, routes, base, index=index, total=step_count, videos=videos.get(step_id))}</section>'
        )
        label = text_of((node.get("layers") or {}).get("topic")) or str(node.get("title", f"Шаг {index + 1}"))
        minutes = estimated_minutes(node, step_tasks)
        lesson_task_count = len(step_tasks)
        task_word = count_word(lesson_task_count, "задание", "задания", "заданий")
        kind_name = next(name for name, value in kind_order.items() if value == kinds[index])
        meta = " · ".join(item for item in (kind_name, f"≈ {minutes} мин" if minutes else "", f"{lesson_task_count} {task_word}" if lesson_task_count else "") if item)
        nav_markup.append(
            f'<button class="step-link" type="button" data-go="{index}" data-step-id="{safe_id}" style="{style}">'
            f'<span class="core-seg" aria-hidden="true"></span><span class="step-number">{index + 1}</span>'
            f'<span class="lesson-info"><span class="step-name">{html.escape(label)}</span><span class="lesson-meta">{html.escape(meta)}</span></span>'
            '<span class="lesson-status">Не начат</span><span class="step-marker" aria-hidden="true">○</span></button>'
        )
        core_markup.append(f'<li data-step-id="{safe_id}" style="{style}" title="{index + 1}. {html.escape(label, quote=True)}"></li>')

    cards_markup = render_cards(nodes)
    summary_markup = render_summary(nodes)
    map_content = render_map(nodes)
    chapter_name = html.escape(str(layer.get("chapter", "Курс")))
    hero_title = html.escape(text_of(layer.get("title")) or "Учебный курс по главе")
    task_count = len(tasks)
    step_word = count_word(step_count, "урок", "урока", "уроков")
    total_minutes = sum(estimated_minutes(node, tasks) for node in nodes)
    video_count = sum(len(items) for key, items in videos.items() if key in ids)
    task_word = count_word(task_count, "задание", "задания", "заданий")
    stats = [(step_count, step_word), (task_count, task_word)]
    if total_minutes:
        stats.append((total_minutes, "мин"))
    if video_count:
        stats.append((video_count, count_word(video_count, "ролик", "ролика", "роликов")))
    stat_icons = [ui_icon("book"), ui_icon("check"), ui_icon("scroll"), ui_icon("cards")]
    stats_markup = "".join(
        f'<div class="stat">{stat_icons[i % len(stat_icons)]}<b>{value}</b><span>{html.escape(word)}</span></div>'
        for i, (value, word) in enumerate(stats)
    )
    kind_counts = {value: kinds.count(value) for value in kind_order.values()}
    kinds_legend = "".join(
        f'<li style="{seg_style(value, strata_count)}"><span class="swatch" aria-hidden="true"></span>'
        f'{html.escape(name)} <span class="muted">· {kind_counts[value]}</span></li>'
        for name, value in kind_order.items()
    )
    domain_legend = "".join(
        f'<li style="{seg_style(item["index"], strata_count)}"><span class="swatch" aria-hidden="true"></span>{html.escape(item["label"])}</li>'
        for item in tokens["legend"]
    )
    domain_legend_markup = (
        f'<div class="legend-domain"><span class="legend-caption">Цвета и знаки — из легенды домена</span><ul class="legend-list small">{domain_legend}</ul></div>'
        if domain_legend else ""
    )
    task_json = json.dumps(tasks, ensure_ascii=False, separators=(",", ":"))
    task_json = task_json.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    used_misconceptions = {
        str(option.get("misconception")) for task in tasks for option in task.get("options") or []
        if isinstance(option, dict) and option.get("misconception")
    }
    misconceptions = {key: value for key, value in domain_data["misconceptions"].items() if key in used_misconceptions}
    misconception_json = json.dumps(misconceptions, ensure_ascii=False, separators=(",", ":"))
    misconception_json = misconception_json.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    chapter_json = json.dumps(str(layer.get("chapter", "course")), ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    player_js = Path(__file__).with_name("player.js").read_text(encoding="utf-8")
    interactive_js = Path(__file__).with_name("interactives.js").read_text(encoding="utf-8")
    theme_css = Path(__file__).with_name("theme.css").read_text(encoding="utf-8")
    core_track = f'<ol class="core-track" aria-hidden="true">{"".join(core_markup)}</ol>'
    return f'''<!doctype html>
<html lang="ru" data-domain="{html.escape(domain_data["name"], quote=True)}" data-art="{" ".join(art_names)}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>{chapter_name} — курс</title>
  <style>
    {tokens["css"]}
    {art_style}
    {theme_css}
  </style>
</head>
<body>
<a class="skip-link" href="#course-content">К содержанию</a>
<div class="shell">
  <header class="masthead"><div class="header-brand"><button id="sidebar-toggle" class="sidebar-toggle" type="button" aria-label="Открыть меню курса" aria-controls="course-sidebar" aria-expanded="false">{ui_icon('menu')}</button><div class="brand"><span class="brand-icon" aria-hidden="true"></span><span class="brand-text">Глава <span aria-hidden="true">→</span> курс</span></div></div><div class="header-core">{core_track}</div><div class="header-tools"><span id="header-progress"></span><span class="offline-label"><span class="status-dot"></span> Офлайн</span></div></header>
  <aside id="course-sidebar" class="course-sidebar" aria-label="Навигация курса">
    <div class="sidebar-heading"><div><span class="nav-kicker">Маршрут главы</span><strong>Навигация</strong></div><button id="sidebar-close" class="sidebar-close" type="button" aria-label="Закрыть меню курса">{ui_icon('close')}</button></div>
    <div class="sidebar-progress"><div><span>Пройдено уроков</span><b id="sidebar-completed">0 / {step_count}</b></div><div class="sidebar-track" role="progressbar" aria-label="Прогресс курса" aria-valuemin="0" aria-valuemax="{step_count}" aria-valuenow="0"><span id="sidebar-progress-fill"></span></div></div>
    <nav class="sidebar-links" aria-label="Раздел курса"><button type="button" data-show-view="overview" aria-pressed="true">{ui_icon('book')} Обзор курса</button><button type="button" data-show-view="cards" aria-pressed="false">{ui_icon('cards')} Повторение</button><button type="button" data-show-view="map" aria-pressed="false">{ui_icon('map')} Карта связей</button><button type="button" data-show-view="summary" aria-pressed="false">{ui_icon('scroll')} Конспект</button></nav>
    <div class="sidebar-section-label">Программа · {step_count} {step_word}</div><nav class="sidebar-lessons core-box" aria-label="Уроки курса">{''.join(nav_markup)}</nav>
    <div class="sidebar-settings"><button id="theme-toggle" type="button">Тёмная тема</button><button id="reset-progress" type="button" aria-label="Сбросить прогресс">Сбросить прогресс</button></div>
  </aside>
  <div id="sidebar-backdrop" class="sidebar-backdrop" hidden aria-hidden="true"></div>
  <main id="course-content" class="layout">
    <section id="overview-view" class="app-view">
    <header class="hero">
      {overview_art}
      <div class="hero-copy">
        <div class="hero-kicker"><span class="kicker-rule" aria-hidden="true"></span>Курс по главе книги</div>
        <h1>{hero_title}</h1>
        <p class="hero-note">Короткие шаги по одной идее: вопрос до чтения, суть, мысли автора со страницами книги, затем практика с мгновенной проверкой. Прогресс сохраняется на этом устройстве.</p>
        <div class="hero-stats">{stats_markup}</div>
      </div>
      <div class="hero-completion"><div><span>Пройдено уроков</span><b id="completed-steps">0 / {step_count}</b></div><div class="hero-track" role="progressbar" aria-label="Пройденные уроки" aria-valuemin="0" aria-valuemax="{step_count}" aria-valuenow="0"><span id="completion-fill"></span></div></div>
    </header>
    <div class="resume-card"><div><div class="nav-kicker" id="resume-label">СЛЕДУЮЩИЙ УРОК</div><h2 id="resume-title"></h2><p id="resume-description" class="muted"></p></div><button class="button-primary" type="button" data-show-view="course" data-resume>Начать обучение →</button></div>
    <section class="legend-panel" aria-labelledby="legend-title"><h2 id="legend-title">Условные обозначения</h2><p class="muted">Тип шага подписан рядом с названием. Маршрут помогает найти текущий урок и увидеть пройденное.</p><ul class="legend-list">{kinds_legend}</ul>{domain_legend_markup}</section>
    <div class="course-toolbar"><div class="learning-status">{ui_icon('check')}<span>Выполнено заданий: <b id="solved-tasks" aria-live="polite">0 / {task_count}</b></span></div><span class="muted">Материал → Практика → Итог урока</span></div>
    <section id="course-nav" aria-label="Программа курса"><div class="route-heading"><div><div class="nav-kicker">Программа курса</div><h2>Уроки в порядке изучения</h2></div></div><nav class="step-list core-log">{''.join(nav_markup)}</nav><div class="nav-footnote">Прогресс и личные заметки сохраняются на этом устройстве.</div></section>
    </section>
    <section id="course-view" class="app-view" hidden><div class="reader-topbar"><button type="button" data-show-view="overview" aria-label="Вернуться к обзору курса">← Обзор курса</button><span aria-hidden="true">/</span><span id="reader-title"></span></div><div class="reader-progress"><div id="progress-label"></div><div class="progress" role="progressbar" aria-label="Текущий урок" aria-valuemin="1"><span id="progress-fill"></span></div></div>{''.join(step_markup)}<div class="footer-nav"><button id="previous" type="button">← Предыдущий урок</button><button id="next" type="button">Следующий урок →</button></div></section>
    <section id="cards-view" class="app-view" hidden><div class="view-kicker">Повторение</div><h1>Карточки повторения</h1><p class="muted">Отметьте, что уже знаете, и вернитесь к тому, что нужно повторить.</p><div class="cards-grid">{cards_markup}</div></section>
    <section id="map-view" class="app-view" hidden><div class="view-kicker">Связи</div><h1>Карта связей шагов</h1>{map_content}</section>
    <section id="summary-view" class="app-view" hidden><div class="view-kicker">Конспект</div><h1>Конспект</h1><p><button id="print-summary" class="no-print" type="button">Распечатать / сохранить в PDF</button></p>{summary_markup}</section>
  </main>
  <footer class="site-footer"><span>Тексты шагов — по главе книги; у каждого абзаца — страница источника.</span><span class="footer-marks"><span class="badge">написано моделью</span> пересказ или пояснение модели · <span class="badge hypothesis">гипотеза</span> вывода нет в главе</span></footer>
</div>
<dialog id="scan-dialog" class="scan-dialog" aria-labelledby="scan-title"><div class="scan-head"><strong id="scan-title">Скан страницы</strong><button type="button" data-close-scan aria-label="Закрыть скан">{ui_icon('close')}</button></div><img alt="" src=""></dialog>
<script>
const TASKS = {task_json};
const MISCONCEPTIONS = {misconception_json};
const CHAPTER = {chapter_json};
{interactive_js}
{player_js}
</script>
</body>
</html>'''



def build(input_path: Path, output_dir: Path | None = None, domain: str | None = None) -> Path:
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
    page.write_text(html_page(layer, tasks_data, routes, illustrations, base, domain), encoding="utf-8")
    return page


def main() -> None:
    parser = argparse.ArgumentParser(description="Собрать автономный HTML-курс из JSON главы")
    parser.add_argument("chapter_dir", type=Path, help="папка с layer.json и необязательным tasks.json")
    parser.add_argument("--output", type=Path, help="папка вывода; по умолчанию <глава>/course")
    parser.add_argument("--domain", help="доменный пакет (папка в domain/); по умолчанию из config/chapters.json")
    args = parser.parse_args()
    print(build(args.chapter_dir, args.output, args.domain))


if __name__ == "__main__":
    main()
