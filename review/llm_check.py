"""Проверка моделью: по каждой цели — элемент + страница источника ± 1 соседняя. Без предметных знаний главы."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from glava.chapter import Chapter
from glava.llm import ask_json

from .checks import best_page, cand

WORKERS = 8  # как в layer/build.py


def pmap(fn, seq, workers: int = WORKERS):
    """Порядок результатов тот же, что у seq (как list(ex.map(...)) в layer/build.py); workers<=1 — строго последовательно."""
    seq = list(seq)
    if workers <= 1 or len(seq) <= 1:
        return [fn(x) for x in seq]
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(fn, seq))

CATEGORIES = """overgeneralization — сверхобобщение: «всегда/любой/все/никогда/только», где автор даёт условия или оговорки.
sign_flip — переворот направления или признака: выше↔ниже, рост↔падение, больше↔меньше, положительный↔отрицательный.
measured_vs_inferred — измеренное выдано за выведенное или наоборот (прибор измеряет сигнал, свойство получают интерпретацией).
lost_condition — потеряно условие применимости (при каких условиях/для какого типа/района верно).
missing_caveat — пропущена оговорка автора («как правило», «в благоприятных условиях», «примерно»).
wrong_scaling — неверный закон масштаба или зависимость: степень, пропорциональность, число, единицы.
wrong_mechanism — неверный механизм прибора или процесса.
outdated_as_current — устаревшее выдано за текущую практику.
analogy_without_limit — ИТ-аналогия без границы или с неверной границей (например, «аналогия точная», «всегда»).
frame_error — ошибка кадра/сцены: ось глубины вверх, порядок слоёв/флюидов неверный, неверная метка происхождения, неподписанное преувеличение, число не совпадает с утверждением.
other — другое."""

SYSTEM = f"""Ты строгий научный рецензент. Тебе дают один элемент учебного черновика и страницы исходной главы (авторский текст).
Твоя задача: определить, ВНОСИТ ли элемент ошибку относительно текста автора. Язык главы может быть любым (русский, английский).

Важно:
- Ошибка = элемент утверждает то, что противоречит автору или превышает его: искажён знак/направление, число, степень, условие, механизм, масштаб, обобщение.
- Если элемент точно или осторожнее передаёт автора, ошибки НЕТ, даже если звучит неожиданно или парадоксально. Ложная тревога на верном элементе хуже пропуска.
- Перефразирование и сокращение без смены смысла — не ошибка. Условное «числа условные» в сцене — не ошибка.
- Сверяй числа, знаки (выше/ниже, растёт/падает), кванторы, условия, границы аналогии с текстом автора ПОСЛОВНО.
- Для аналогии: проверь, что `breaks_where` называет настоящую границу, не утверждает «аналогия точная/универсальна» и не противоречит главе.
- Для фразы закадра: сверь с текстом автора и с утверждением-опорой; обрати внимание на «всегда», «прямо измеряет», потерянные оговорки.
- Для факта сцены: сверь с текстом автора и со спецификацией (порядок слоёв, ось глубины, числа, плашка).

Категории ошибок:
{CATEGORIES}

Тяжесть (severity): critical — ошибка меняет вывод или физику (переворот знака/направления, неверный механизм, неверная метка происхождения кадра, перепутанные числа, меняющие вывод);
significant — потеряно условие или оговорка, сверхобобщение; minor — неточная формулировка без изменения смысла.

Ответ — JSON:
{{"has_error": bool, "category": одна из категорий или null, "severity": "critical|significant|minor" или null,
 "problem": "что не так, кратко", "fix": "как должно быть: готовая исправленная формулировка по тексту автора",
 "page_pdf": номер страницы (число) из меток <!-- page N --> , где автор это подтверждает,
 "quote": "дословная цитата автора со страницы page_pdf, подтверждающая исправление (короткая)", "confidence": число 0..1 — уверенность, что это именно ошибка}}
Если ошибки нет: has_error=false, confidence — уверенность в том, что ошибки нет, как 1-значение не нужна: поставь 0.0."""
TIERS = {"critical": 3, "significant": 2, "minor": 1}


def pages_ctx(ch: Chapter, page: int | None) -> str:
    if page is None or page not in ch.pages:
        return ""
    ps = [p for p in (page - 1, page, page + 1) if p in ch.pages]
    return "\n\n".join(f"<!-- page {p} -->\n{ch.pages[p][:6000]}" for p in ps)


def item_page(it: dict, items: dict, ch: Chapter) -> int | None:
    if it["type"] == "claim":
        return it.get("page_pdf")
    if it["type"] == "script_line":
        ref = items.get(it.get("claim_ref") or "")
        return ref.get("page_pdf") if ref else None
    return None


def build_user(it: dict, items: dict, pkg: dict, ch: Chapter) -> str:
    page = item_page(it, items, ch)
    parts = [f"Тип элемента: {it['type']}", f"Текст элемента: {it['text']}"]
    if it["type"] == "claim":
        parts += [f"Вид (claim kind): {it.get('kind')}", f"Заявленная страница: {it.get('page_pdf')}", f"Заявленная цитата автора: {it.get('quote')}"]
    elif it["type"] == "analogy":
        parts += [f"Где аналогия перестаёт работать (breaks_where): {it.get('breaks_where')}"]
        sibs = [c for c in pkg["items"] if c["type"] == "claim" and c.get("node_id") == it.get("node_id")]
        if sibs:
            parts.append("Утверждения того же узла:\n" + "\n".join(f"- [{c['id']}, стр. {c['page_pdf']}] {c['text']}" for c in sibs))
            page = sibs[0]["page_pdf"]
    elif it["type"] == "script_line":
        ref = items.get(it.get("claim_ref") or "")
        parts += [f"Подпись на экране: {it.get('on_screen')}"]
        if ref:
            parts += [f"Утверждение-опора {ref['id']}: {ref['text']}", f"Цитата автора для опоры: {ref.get('quote')}"]
    elif it["type"] == "scene_fact":
        spec = next((s["spec"] for s in pkg["scenes"] if s["video_id"] == it.get("video_id")), {})
        parts += [f"Место в спецификации: {it.get('spec_path')}", "Спецификация сцены:\n" + json.dumps(spec, ensure_ascii=False)[:6000]]
        node_cl = [c for c in pkg["items"] if c["type"] == "claim" and c.get("node_id") == it.get("node_id")]
        if node_cl:
            parts.append("Утверждения узла:\n" + "\n".join(f"- [{c['id']}, стр. {c['page_pdf']}] {c['text']}" for c in node_cl))
            page = node_cl[0]["page_pdf"]
    if page is None:
        page, _ = best_page(ch, it["text"])
    ctx = pages_ctx(ch, page)
    parts.append("Текст автора (страницы источника ± соседние):\n" + ctx)
    return "\n\n".join(parts)


def check_with_model(pkg: dict, ch: Chapter, limit: int | None, tier: str = "bulk", only: set | None = None, workers: int = WORKERS) -> list[dict]:
    items = {i["id"]: i for i in pkg["items"]}
    todo = pkg["items"][:limit] if limit else pkg["items"]
    if only is not None:
        todo = [i for i in todo if i["id"] in only]

    def one(it):
        try:
            r = ask_json(SYSTEM, build_user(it, items, pkg, ch), tier=tier, tag=f"review:{tier}:{it['id']}")
        except Exception as e:  # бюджет, сеть — не валим весь прогон
            print(f"  ! {it['id']}: {e}")
            return None
        if not r.get("has_error"):
            return None
        cat = r.get("category") or "other"
        sev = r.get("severity") if r.get("severity") in TIERS else "significant"
        page = r.get("page_pdf")
        page = int(page) if isinstance(page, (int, float, str)) and str(page).isdigit() else item_page(it, items, ch)
        return cand(it["id"], cat, r.get("problem", ""), r.get("fix", ""), page, r.get("quote"),
                    float(r.get("confidence") or 0), source=f"llm:{tier}", severity=sev)

    return [c for c in pmap(one, todo, workers) if c]


FRAME_SYSTEM = f"""Ты проверяешь кадр учебного ролика. Дан PNG-кадр, заявленное автором описание (`shows`), метка происхождения, класс кадра, плашка и инварианты спецификации сцены.
Найди расхождения между картинкой, описанием и спецификацией. Чек-лист: глубина растёт вниз; газ над нефтью над водой; метка происхождения («реальные данные»/«симуляция»/«схема») соответствует тому, как сделан кадр (рисованный кадр не может быть «реальными данными»);
преувеличение подписано числом или словами в плашке; подписи и числа на картинке совпадают с `shows` и спецификацией; кривая согласована с разрезом; положения меток (контактов, границ) совпадают со спецификацией.
Кадр — ОДИН момент ролика. В хронологии ниже указано, что к секунде кадра уже произошло: не считай ошибкой отсутствие того, что по хронологии появляется позже,
и наличие того, что уже должно быть. Ошибки самой спецификации/фраз закадра сюда не относятся (их проверяют отдельно): оценивай именно кадр — картинку, `shows`, метку, плашку, согласованность картинки и `shows` с хронологией и инвариантами.
Плашка у всех кадров ролика одна и та же (шаблон): её полноту НЕ оценивай, это проверяется отдельно через факты сцены. Мелкие визуальные неточности (короткий отрезок кривой, оттенок, подпись без слова «настоящий») — не ошибка.
Сообщай об ошибке кадра только при ОДНОЗНАЧНОМ противоречии одного из видов: (1) `shows` утверждает то, чего нет на картинке или что противоречит спецификации/инвариантам (глубина, порядок слоёв/флюидов, положение меток, направление «выше/ниже», числа);
(2) метка происхождения не соответствует тому, как построен кадр (на картинке видно «симуляция/схема», а метка «реальные данные», и наоборот); (3) неподписанное преувеличение. Если сомневаешься — ошибки нет.
Если расхождений нет, ошибки нет: ложная тревога хуже пропуска. Заметь только то, что видно на картинке или явно следует из текста.
Тяжесть: critical — метка происхождения неверна, неверный порядок слоёв/флюидов, глубина вверх, переворот направления; significant — число или положение метки не совпадает; minor — неточная подпись.
Категория ошибки всегда frame_error (если это не неверная метка — тоже frame_error).
Ответ — JSON: {{"has_error": bool, "category": "frame_error", "severity": "critical|significant|minor", "problem": "...", "fix": "что должно быть в кадре/метке",
"page_pdf": номер страницы главы, подтверждающей исправление (число) или null, "quote": "цитата автора" или null, "confidence": 0..1}}"""


def check_frames_with_model(pkg: dict, ch: Chapter, base: Path, tier: str = "strong", workers: int = WORKERS) -> list[dict]:
    def one(f):
        spec = next((s["spec"] for s in pkg["scenes"] if s["video_id"] == f["video_id"]), {})
        facts = [i for i in pkg["items"] if i["type"] in ("scene_fact",) and i.get("video_id") == f["video_id"]]
        claim_pages = sorted({c["page_pdf"] for c in pkg["items"] if c["type"] == "claim" and c.get("page_pdf") and c.get("node_id") == (facts[0].get("node_id") if facts else None)})
        lines = sorted((i for i in pkg["items"] if i["type"] == "script_line" and i.get("video_id") == f["video_id"]), key=lambda i: i.get("t_start") or 0)
        beats = spec.get("beats") or []
        tl = []
        for k, l in enumerate(lines):
            act = beats[k].get("action") if k < len(beats) else None
            tl.append(f"{l.get('t_start')}–{l.get('t_end')} с: «{l['text']}» действие сцены: {json.dumps(act, ensure_ascii=False)}" + ("  <- ДО кадра" if (l.get('t_start') or 0) <= f["t"] else "  <- ПОСЛЕ кадра"))
        user = ("Хронология ролика:\n" + "\n".join(tl) + "\n\n" + f"Кадр {f['id']}, секунда {f['t']}, класс {f['frame_class']}, метка: {f['label']}\nshows: {f['shows']}\nплашка: {f.get('plaque')}\n"
                f"Инварианты: {json.dumps(spec.get('invariants'), ensure_ascii=False)}\nПараметры: {json.dumps(spec.get('params'), ensure_ascii=False)[:2500]}\n"
                f"Допущения: {json.dumps(spec.get('assumptions'), ensure_ascii=False)}\nПреувеличения: {json.dumps(spec.get('exaggerations'), ensure_ascii=False)}\n"
                f"Факты сцены:\n" + "\n".join("- " + x["text"] for x in facts) + "\n\nТекст автора:\n" + "\n\n".join(pages_ctx(ch, p) for p in claim_pages[:2]))
        try:
            r = ask_json(FRAME_SYSTEM, user, tier=tier, images=[base / f["file"]], tag=f"review:{tier}:{f['id']}")
        except Exception as e:
            print(f"  ! {f['id']}: {e}")
            return None
        if not r.get("has_error"):
            return None
        page = r.get("page_pdf")
        page = int(page) if str(page).isdigit() else (claim_pages[0] if claim_pages else None)
        sev = r.get("severity") if r.get("severity") in TIERS else "significant"
        return cand(f["id"], "frame_error", r.get("problem", ""), r.get("fix", ""), page, r.get("quote"),
                    float(r.get("confidence") or 0), source=f"llm:{tier}", severity=sev)

    return [c for c in pmap(one, pkg["frames"], workers) if c]


REVERSE_SYSTEM = """Ты ищешь ПОДТВЕРЖДЕНИЕ. Дан элемент учебного черновика и текст автора (страницы главы). Найди в тексте автора фрагмент, который ДОСЛОВНО подтверждает элемент так, как он сформулирован, с теми же условиями, числами, знаками и кванторами.
Если такой фрагмент есть — верни его дословно (без изменений, из текста выше). Если автор говорит иное, уже, с оговорками, которых нет в элементе, или другое число/знак — подтверждения нет.
Не натягивай: перефразирование без смены смысла допустимо, но смена условия, числа, знака, механизма или добавленный абсолют — это НЕ подтверждение.
JSON: {"supported": bool, "quote": "дословный фрагмент автора или null", "page_pdf": число или null, "same_conditions": bool, "reason": "кратко"}"""


def reverse_check(pkg: dict, ch: Chapter, target: str, base: Path):
    """Спрашиваем наоборот: есть ли в главе дословное подтверждение элемента. -> (supported_by_text: bool, quote, page)"""
    from glava.chapter import find_quote
    items = {i["id"]: i for i in pkg["items"]}
    it = items.get(target)
    if not it:
        return False, None, None
    user = build_user(it, items, pkg, ch).replace("Заявленная цитата автора:", "Цитата, приложенная к элементу:")
    r = ask_json(REVERSE_SYSTEM, "Найди подтверждение для элемента.\n\n" + user, tier="strong", tag=f"reverse:{target}")
    q = r.get("quote")
    if r.get("supported") and r.get("same_conditions") and q:
        pages = find_quote(ch, q)  # цитата действительно дословно есть в главе (проверка кодом)
        if pages:
            return True, q, pages[0]
    return False, q, None


REVERSE_FRAME_SYSTEM = """Ты ищешь подтверждение, что кадр верен. Дан кадр (картинка), описание `shows`, метка, хронология ролика и спецификация.
Проверь по порядку: (1) `shows` соответствует картинке; (2) `shows` и картинка согласованы с хронологией к секунде кадра и со спецификацией (глубины, положение меток, порядок слоёв); (3) метка происхождения соответствует тому, как построен кадр.
Если всё это выполняется (мелкие визуальные огрехи не в счёт) — кадр верен. JSON: {"frame_ok": bool, "reason": "кратко"}"""


def reverse_check_frame(pkg: dict, ch: Chapter, frame: dict, base: Path) -> bool:
    spec = next((s["spec"] for s in pkg["scenes"] if s["video_id"] == frame["video_id"]), {})
    lines = sorted((i for i in pkg["items"] if i["type"] == "script_line" and i.get("video_id") == frame["video_id"]), key=lambda i: i.get("t_start") or 0)
    beats = spec.get("beats") or []
    tl = [f"{l.get('t_start')}–{l.get('t_end')} с: «{l['text']}» действие: {json.dumps(beats[k].get('action') if k < len(beats) else None, ensure_ascii=False)}" for k, l in enumerate(lines)]
    user = (f"Кадр {frame['id']}, секунда {frame['t']}, метка: {frame['label']}\nshows: {frame['shows']}\nИнварианты: {json.dumps(spec.get('invariants'), ensure_ascii=False)}\n"
            f"Параметры: {json.dumps(spec.get('params'), ensure_ascii=False)[:2500]}\nХронология:\n" + "\n".join(tl))
    r = ask_json(REVERSE_FRAME_SYSTEM, user, tier="strong", images=[base / frame["file"]], tag=f"reverse:{frame['id']}")
    return bool(r.get("frame_ok"))
