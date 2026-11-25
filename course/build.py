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
                f'<p class="paragraph">{html.escape(part)} {source} {" ".join(flags)}</p>'
            )
    return title + "".join(paragraphs)


def render_node(node: dict[str, Any], tasks: list[dict[str, Any]], illustrations: list[dict[str, Any]], routes: Any, base: Path) -> str:
    pages = node.get("pages") or []
    page = pages[0] if pages else ""
    topic = text_of((node.get("layers") or {}).get("topic"))
    heading = topic or str(node.get("title", "Шаг курса"))
    book_title = str(node.get("title", ""))
    blocks = [f'<h1>{html.escape(heading)}</h1>']
    if book_title and book_title != heading:
        blocks.append(f'<p class="book-title">Раздел книги: {html.escape(book_title)}</p>')

    layers = node.get("layers") or {}
    if layers.get("main"):
        blocks.append('<section class="content-block"><h2>Суть</h2>')
        blocks.append(field_paragraph(layers.get("main"), page))
        blocks.append("</section>")

    details: list[str] = []
    for key, label in (("mechanism", "Механизм"), ("practice", "Практика"), ("nuances", "Нюансы")):
        if layers.get(key):
            details.append(field_paragraph(layers.get(key), page, label=label))
    for item in layers.get("deeper") or []:
        details.append(field_paragraph(item, page, label="Подробнее"))
    claims = node.get("claims") or []
    if details or claims:
        blocks.append('<section class="content-block"><h2>Как это работает</h2>')
        blocks.extend(details)
        for claim in claims:
            claim_page = claim.get("page_pdf") or page
            blocks.append(field_paragraph(claim.get("text"), claim_page, label="Утверждение из главы"))
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
        blocks.append('<section class="content-block"><h2>Разбор примера</h2>')
        blocks.append(field_paragraph(example.get("condition"), example_page, label="Условие"))
        steps = [str(item) for item in example.get("steps", []) if item]
        if steps:
            blocks.append("<ol>" + "".join(
                f'<li>{html.escape(item)} <span class="page">стр. {html.escape(str(example_page))}</span></li>' for item in steps
            ) + "</ol>")
        blocks.append(field_paragraph(example.get("result"), example_page, label="Итог"))
        if example.get("figure"):
            blocks.append(field_paragraph(example["figure"], example_page, label="Рисунок"))
        blocks.append("</section>")

    terms = node.get("terms_introduced") or []
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

    book_tasks = node.get("book_tasks") or []
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
        if image_path:
            relative = Path("..") / Path(str(image_path))
            src = html.escape(relative.as_posix(), quote=True)
            alt = html.escape(str(illustration.get("alt") or illustration.get("caption") or "Иллюстрация"), quote=True)
            caption = html.escape(str(illustration.get("caption") or ""))
            origin = "написано моделью" if illustration.get("by_model") is True else ""
            blocks.append(f'<figure class="content-block"><img loading="lazy" src="{src}" alt="{alt}"><figcaption>{caption} {origin}</figcaption></figure>')

    route = route_for_node(routes, str(node.get("id", "")))
    video_path = route.get("video") or route.get("video_path")
    if video_path:
        src = html.escape((Path("..") / str(video_path)).as_posix(), quote=True)
        blocks.append(f'<section class="content-block"><h2>Видео</h2><video controls preload="metadata" src="{src}">Ваш браузер не поддерживает видео.</video></section>')

    node_tasks = [task for task in tasks if task.get("step_id") == node.get("id")]
    if node_tasks:
        blocks.append('<section class="content-block practice"><h2>Проверьте себя</h2>')
        blocks.extend(f'<article class="task" data-task-id="{html.escape(str(task.get("id", "")), quote=True)}"></article>' for task in node_tasks)
        blocks.append("</section>")
    return "".join(blocks)


def route_for_node(routes: Any, node_id: str) -> dict[str, Any]:
    if isinstance(routes, dict):
        items = routes.get("nodes", routes.get("routing", routes))
        if isinstance(items, dict):
            candidate = items.get(node_id, {})
            return candidate if isinstance(candidate, dict) else {}
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and item.get("step_id", item.get("node_id", item.get("id"))) == node_id:
                    return item
    return {}


def html_page(layer: dict[str, Any], tasks_data: dict[str, Any], routes: Any, illustrations_data: Any, base: Path) -> str:
    nodes = [node for node in layer.get("nodes", []) if isinstance(node, dict)]
    nodes.sort(key=lambda node: (node.get("order", 10**9), str(node.get("id", ""))))
    tasks = [task for task in (tasks_data or {}).get("tasks", []) if isinstance(task, dict)]
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
            f'<section class="step" id="step-{index}" data-step-id="{html.escape(step_id, quote=True)}" hidden>'
            f'{render_node(node, step_tasks, illustrations, routes, base)}</section>'
        )
        label = text_of((node.get("layers") or {}).get("topic")) or str(node.get("title", f"Шаг {index + 1}"))
        nav_markup.append(
            f'<button class="step-link" type="button" data-go="{index}"><span class="step-number">{index + 1}</span>'
            f'<span>{html.escape(label)}</span></button>'
        )

    chapter_name = html.escape(str(layer.get("chapter", "Курс")))
    task_json = json.dumps(tasks, ensure_ascii=False, separators=(",", ":"))
    task_json = task_json.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return f'''<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>{chapter_name} — курс</title>
  <style>
    :root {{ color-scheme: light dark; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; font-size: 17px; line-height: 1.6; --bg:#f4f6f8; --paper:#fff; --ink:#17212b; --muted:#536272; --line:#dce2e8; --accent:#155eef; --soft:#eaf1ff; }}
    @media (prefers-color-scheme: dark) {{ :root {{ --bg:#111820; --paper:#19232e; --ink:#ecf2f8; --muted:#b7c5d3; --line:#364553; --accent:#90b6ff; --soft:#263951; }} }}
    * {{ box-sizing:border-box; }} body {{ margin:0; background:var(--bg); color:var(--ink); }} button,input {{ font:inherit; }}
    .shell {{ max-width:1280px; margin:auto; padding:1rem; }} header {{ display:flex; align-items:center; justify-content:space-between; gap:1rem; }}
    h1 {{ font-size:clamp(1.6rem,4vw,2.35rem); line-height:1.2; margin:.3rem 0 .8rem; }} h2 {{ font-size:1.25rem; margin:0 0 .65rem; }} h3 {{ font-size:1rem; margin:.9rem 0 .2rem; color:var(--muted); }}
    button {{ color:inherit; border:1px solid var(--line); background:var(--paper); border-radius:.65rem; min-height:44px; padding:.55rem .8rem; cursor:pointer; }} button:hover {{ border-color:var(--accent); }} button:focus-visible,input:focus-visible,summary:focus-visible {{ outline:3px solid var(--accent); outline-offset:2px; }}
    .layout {{ display:grid; grid-template-columns:260px minmax(0,1fr); gap:1rem; margin-top:1rem; }} aside,.content-block,.task {{ background:var(--paper); border:1px solid var(--line); border-radius:1rem; padding:1rem; }} aside {{ align-self:start; position:sticky; top:1rem; }} .step-list {{ display:grid; gap:.4rem; margin-top:.7rem; }}
    .step-link {{ display:flex; gap:.65rem; align-items:center; text-align:left; width:100%; min-height:48px; }} .step-link[aria-current="step"] {{ border-color:var(--accent); background:var(--soft); }} .step-number {{ flex:none; display:grid; place-items:center; border-radius:99px; width:1.8rem; height:1.8rem; background:var(--soft); }}
    .progress {{ height:.55rem; border-radius:99px; overflow:hidden; background:var(--line); margin:.8rem 0 .25rem; }} .progress span {{ display:block; height:100%; background:var(--accent); width:0; transition:width .2s; }} .muted,.book-title {{ color:var(--muted); }} .book-title {{ margin-top:-.45rem; }} .paragraph {{ margin:.35rem 0 .75rem; }} .page {{ display:inline-block; white-space:nowrap; font-size:.78rem; color:var(--muted); margin-left:.25rem; }} .badge {{ font-size:.78rem; color:var(--muted); border:1px solid var(--line); border-radius:99px; padding:.08rem .45rem; }} .hypothesis {{ border-color:#a86a00; }}
    .content-block {{ margin:0 0 .85rem; overflow-wrap:anywhere; }} .content-block:last-child {{ margin-bottom:0; }} .terms {{ display:flex; flex-wrap:wrap; gap:.5rem; list-style:none; padding:0; }} .terms li {{ padding:.25rem .6rem; border-radius:99px; background:var(--soft); }}
    blockquote {{ margin:.5rem 0; padding:.6rem .85rem; border-left:3px solid var(--accent); background:var(--soft); }} details.quote {{ margin:.4rem 0 .8rem; }} summary {{ cursor:pointer; min-height:44px; display:flex; align-items:center; color:var(--muted); }} video,img {{ max-width:100%; border-radius:.6rem; }} .task {{ margin:.65rem 0; }} .task-prompt {{ font-weight:650; margin:0 0 .7rem; }} .options {{ display:grid; gap:.4rem; }} .option {{ display:flex; gap:.65rem; align-items:flex-start; border:1px solid var(--line); border-radius:.65rem; padding:.6rem; min-height:48px; }} .option input {{ margin-top:.35rem; width:1.15rem; height:1.15rem; }}
    .task-tools {{ display:flex; gap:.5rem; flex-wrap:wrap; margin-top:.7rem; }} .task-result {{ margin-top:.65rem; padding:.7rem; border-radius:.65rem; background:var(--soft); }} .task-result[hidden] {{ display:none; }} .gap-input {{ width:min(100%,16rem); border:1px solid var(--line); border-radius:.45rem; padding:.4rem; background:var(--paper); color:var(--ink); }} .order-item {{ display:flex; gap:.4rem; align-items:center; }} .order-item .drag-handle {{ touch-action:none; cursor:grab; min-width:44px; }} .order-item.dragging {{ opacity:.55; }} .order-item .order-text {{ flex:1; }}
    .footer-nav {{ display:flex; justify-content:space-between; gap:.75rem; margin:1rem 0 0; }} .footer-nav button {{ min-width:7rem; }}
    @media (max-width:700px) {{ .shell {{ padding:.65rem; }} header {{ align-items:flex-start; }} .layout {{ grid-template-columns:1fr; }} aside {{ position:static; }} .step-list {{ display:flex; overflow-x:auto; padding:.15rem .05rem .4rem; }} .step-link {{ width:auto; min-width:170px; }} .step-link span:last-child {{ white-space:normal; }} }}
    @media print {{ body {{ background:#fff; color:#000; }} aside,header,.footer-nav,.practice {{ display:none !important; }} .shell,.layout {{ max-width:none; display:block; margin:0; padding:0; }} .step {{ display:block !important; page-break-after:always; }} .content-block {{ border:0; padding:.3rem 0; break-inside:avoid; }} .quote > blockquote {{ display:block; }} }}
  </style>
</head>
<body>
<div class="shell">
  <header><div><div class="muted">Офлайн-курс</div><strong>{chapter_name}</strong></div><button id="reset-progress" type="button" aria-label="Сбросить прогресс">Сбросить прогресс</button></header>
  <div class="layout">
    <aside aria-label="Навигация курса"><strong>Шаги курса</strong><div class="progress" role="progressbar" aria-label="Прогресс по шагам"><span id="progress-fill"></span></div><div id="progress-label" class="muted"></div><nav class="step-list">{''.join(nav_markup)}</nav></aside>
    <main id="course-content">{''.join(step_markup)}<div class="footer-nav"><button id="previous" type="button">← Назад</button><button id="next" type="button">Дальше →</button></div></main>
  </div>
</div>
<script>
const TASKS = {task_json};
const STEPS = [...document.querySelectorAll('.step')];
const CHAPTER = {json.dumps(str(layer.get('chapter', 'course')), ensure_ascii=False)};
const STORE_KEY = 'course-progress:' + CHAPTER;
const TASK_KEY = 'course-tasks:' + CHAPTER;
let saved = {{}};
let solved = {{}};
try {{ saved = JSON.parse(localStorage.getItem(STORE_KEY) || '{{}}'); }} catch (_) {{}}
try {{ solved = JSON.parse(localStorage.getItem(TASK_KEY) || '{{}}'); }} catch (_) {{}}
let current = Math.min(Math.max(Number(saved.step) || 0, 0), Math.max(STEPS.length - 1, 0));
const esc = (s) => String(s ?? '');
function setStep(index) {{
  if (!STEPS.length) return;
  current = Math.min(Math.max(index, 0), STEPS.length - 1);
  STEPS.forEach((step, i) => step.hidden = i !== current);
  document.querySelectorAll('.step-link').forEach((button, i) => {{
    if (i === current) button.setAttribute('aria-current', 'step'); else button.removeAttribute('aria-current');
  }});
  const percent = Math.round(((current + 1) / STEPS.length) * 100);
  document.getElementById('progress-fill').style.width = percent + '%';
  document.getElementById('progress-label').textContent = `${{current + 1}} из ${{STEPS.length}} шагов`;
  document.getElementById('previous').disabled = current === 0;
  document.getElementById('next').disabled = current === STEPS.length - 1;
  localStorage.setItem(STORE_KEY, JSON.stringify({{step: current}}));
}}
document.querySelectorAll('.step-link').forEach(button => button.addEventListener('click', () => setStep(Number(button.dataset.go))));
document.getElementById('previous').addEventListener('click', () => setStep(current - 1));
document.getElementById('next').addEventListener('click', () => setStep(current + 1));
document.getElementById('reset-progress').addEventListener('click', () => {{ localStorage.removeItem(STORE_KEY); localStorage.removeItem(TASK_KEY); location.reload(); }});
function taskElement(task) {{
  const card = document.querySelector(`[data-task-id="${{CSS.escape(task.id)}}"]`);
  if (!card) return;
  const prompt = document.createElement('p'); prompt.className = 'task-prompt';
  if (task.type === 'gap') {{
    const parts = String(task.prompt || '').split(/(\\{{\\{{\\d+\\}}\\}})/g);
    parts.forEach(part => {{ if (/^\\{{\\{{\\d+\\}}\\}}$/.test(part)) {{ const input = document.createElement('input'); input.className = 'gap-input'; input.type = 'text'; input.dataset.gap = part.match(/\\d+/)[0]; input.setAttribute('aria-label', 'Ответ в пропуск'); prompt.append(input); }} else prompt.append(document.createTextNode(part)); }});
  }} else prompt.textContent = esc(task.prompt);
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
    list.addEventListener('pointerdown', e => {{ const handle = e.target.closest('.drag-handle'); if (!handle) return; pointerRow = handle.closest('.order-item'); pointerRow.classList.add('dragging'); handle.setPointerCapture(e.pointerId); e.preventDefault(); }});
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
  if (solved[task.id]) showResult(true);
  function showResult(correct) {{ result.hidden = false; result.textContent = (correct ? 'Верно. ' : 'Пока неверно. Верный ответ: ') + (task.explanation || ''); result.classList.toggle('correct', correct); }}
  check.addEventListener('click', () => {{
    let answer;
    if (task.type === 'order') answer = [...card.querySelectorAll('.order-item')].map(row => row.dataset.id);
    else if (task.type === 'gap') answer = [...card.querySelectorAll('[data-gap]')].sort((a,b) => Number(a.dataset.gap)-Number(b.dataset.gap)).map(input => input.value.trim());
    else if (task.type === 'calc') answer = Number(card.querySelector('[data-calc]').value);
    else answer = [...card.querySelectorAll('input:checked')].map(input => input.value);
    let correct = false;
    if (task.type === 'single') correct = answer.length === 1 && answer[0] === String(task.answer);
    else if (task.type === 'multiple' || task.type === 'order') correct = Array.isArray(task.answer) && answer.length === task.answer.length && answer.every((v, i) => String(v) === String(task.answer[i]));
    else if (task.type === 'gap') correct = Array.isArray(task.answer) && answer.length === task.answer.length && answer.every((v, i) => v.localeCompare(String(task.answer[i]), undefined, {{sensitivity:'accent'}}) === 0);
    else if (task.type === 'calc') correct = Number.isFinite(answer) && Math.abs(answer - Number(task.answer)) <= Number(task.tolerance || 0);
    if (correct) {{ solved[task.id] = true; localStorage.setItem(TASK_KEY, JSON.stringify(solved)); }}
    showResult(correct);
  }});
}}
TASKS.forEach(taskElement);
setStep(current);
</script>
</body>
</html>'''


def build(input_path: Path, output_dir: Path | None = None) -> Path:
    base = input_path if input_path.is_dir() else input_path.parent
    layer_path = input_path / "layer.json" if input_path.is_dir() else input_path
    layer = read_json(layer_path)
    if not isinstance(layer, dict) or not isinstance(layer.get("nodes"), list):
        raise ValueError(f"Не найден корректный layer.json: {layer_path}")
    tasks_data = read_json(base / "tasks.json", {"tasks": []}) or {"tasks": []}
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
