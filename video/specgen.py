"""Узел учебного слоя → spec.json сцены (сценарий ролика). Модель пишет черновик, код проверяет и
возвращает ошибки модели на исправление. Вызов модели — только через glava/llm.py.

    uv run python -m video.specgen runs/<chapter>/layer.json <node_id>          # один узел
    uv run python -m video.specgen runs/<chapter>/layer.json --routed           # узлы с роликом по routing.json
    uv run python -m video.specgen samples/latyshova_mini/layer.json latyshova.mud_controls_logs --build

Результат: runs/<chapter>/video/<video_id>/spec.json (+ --build сразу собирает ролик).
Если ни один шаблон узлу не подходит, пишется no_video.json с причиной — ролик не рисуется.
Геологии здесь нет: литологии, флюиды, кривые и чек-лист берутся из доменного пакета.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .spec import CLASSES, LABELS
from .style import ROOT, domain_dir, domain_for_chapter, load_style
from .templates import catalog, get_template
from .textclean import problems

MAX_ROUNDS = 3

SYSTEM = """Ты сценарист учебных шортсов по главе книги. Ролик рисуется кодом по спецификации сцены (spec.json),
генерации видео нет. Пиши только JSON.

Правила (ТЗ, обязательны):
- Структура: крючок-вопрос в первой фразе (до 3 секунд) → механизм → вопрос на предсказание → пауза
  (фраза с "say": "" и "pause": 1.2–1.8) → ответ → вывод → последняя фраза — вопрос на перенос.
- 9–15 фраз, ролик 40–75 секунд (≈130 слов в минуту), в каждой фразе 6–15 слов, максимум 18.
- Каждая фраза "say" опирается на утверждение узла: "claim_id" — id из списка claims. Голос не говорит больше
  источника. Нет утверждения — нет фразы. Аналогия допустима только с "is_analogy": true.
- Сценарий для уха: числа и формулы словами (НЕ цифрами), без формул, греческих букв и знаков = / ^.
  Аббревиатура при первом упоминании раскрывается словами.
- "on_screen" — короткий заголовок кадра 2–6 слов, НЕ повторяет голос дословно, без формул и греческих букв.
  Ключевые 1–2 слова можно выделить *звёздочками*.
- "label" кадра: "симуляция" (кадр считается из модели), "схема" (условная картинка) или "реальные данные".
  "frame_class": "B" для процесса/механизма, "A" для данных.
- Все числа сцены, которых нет в главе (глубины, свойства, толщины), — условные: перечисли их в "assumptions".
- "exaggerations": что показано не в масштабе и во сколько раз; "plaque": ["не в масштабе:", "<кратко>"].
- Физика должна быть верной по главе: порядок слоёв, флюиды в порах, газ над нефтью над водой, глубина вниз.
- Не закрепляй заблуждение: если узел его опровергает, ролик показывает, почему оно неверно.
- Вопрос на перенос — про НОВУЮ ситуацию, которой нет в ролике (другие условия, другая скважина, данные для
  модели), а не повтор показанного. Вопрос на предсказание задаётся ДО ответа и отвечается кадром после паузы.
- Не утверждай, что что-то показано «в масштабе», если шаблон это не гарантирует.
"""


def _node(layer: dict, node_id: str) -> dict:
    for n in layer.get("nodes", []):
        if n.get("id") == node_id:
            return n
    raise SystemExit(f"узел {node_id} не найден в слое")


def _domain_brief(domain: str) -> dict:
    st = load_style(domain)
    brief = {
        "lithology": {k: v.get("ru") for k, v in st.get("lithology", {}).items()},
        "fluids": {k: v.get("ru") for k, v in st.get("fluids", {}).items()},
        "curves": {k: v.get("ru") for k, v in st.get("curves", {}).items()},
        "conventions": st.get("conventions", {}),
    }
    f = domain_dir(domain) / "physics_checklist.json"
    if f.exists():
        brief["physics_checklist"] = {x["id"]: x["item"] for x in json.loads(f.read_text(encoding="utf-8"))}
    f = domain_dir(domain) / "misconceptions.json"
    if f.exists():
        try:
            brief["misconceptions"] = json.loads(f.read_text(encoding="utf-8"))[:12]
        except (ValueError, TypeError):
            pass
    return brief


def _node_brief(node: dict) -> dict:
    keep = ("id", "title", "difficulty", "layers", "claims", "misconception", "data_meaning", "it_analogy",
            "terms_introduced", "terms_used", "worked_examples", "needs_scan")
    nb = {k: node.get(k) for k in keep if node.get(k) not in (None, [], {})}
    nb["claims"] = [{k: c.get(k) for k in ("id", "text", "kind", "quote", "page_pdf")} for c in node.get("claims", [])]
    return nb


def build_prompt(node: dict, domain: str, template_hint: str | None, errors: list[str] | None,
                 draft: dict | None) -> str:
    cat = catalog()
    example = json.loads((Path(__file__).parent / "examples" / "demo_spec.json").read_text(encoding="utf-8"))
    for k in ("objects", "physics_checklist"):
        example.pop(k, None)
    parts = [
        "УЗЕЛ УЧЕБНОГО СЛОЯ:\n" + json.dumps(_node_brief(node), ensure_ascii=False, indent=1),
        "ДОМЕННЫЙ ПАКЕТ (ключи для lith/fluid/curves и чек-лист физики):\n"
        + json.dumps(_domain_brief(domain), ensure_ascii=False, indent=1),
        "ШАБЛОНЫ СЦЕН:\n" + "\n\n".join(f"### {n}\n{c['summary']}\n{c['guide']}" for n, c in cat.items()),
        ("Маршрутизация назначила шаблон: " + template_hint) if template_hint else
        "Выбери шаблон сам. Если ни один не показывает суть узла честно — верни {\"no_video\": true, \"reason\": \"...\"}.",
        "ПРИМЕР ФОРМАТА (другой узел; бери формат, не содержание):\n" + json.dumps(example, ensure_ascii=False),
        "Верни JSON спецификации с полями: video_id, chapter, node_id, template, title, plaque, params, beats, "
        "objects, invariants, assumptions, exaggerations, prediction_question, transfer_question, data_it_bridge, "
        "listen_check (термины, где важно ударение), physics_checklist ({id: {answer: да|нет|н/п, note}}).",
    ]
    if errors:
        parts.append("ПРЕДЫДУЩИЙ ЧЕРНОВИК:\n" + json.dumps(draft, ensure_ascii=False)
                     + "\n\nИСПРАВЬ ОШИБКИ (верни спецификацию целиком):\n- " + "\n- ".join(errors))
    return "\n\n".join(parts)


# ---------------------------------------------------------------- проверка без модели
def validate(spec: dict, node: dict, domain: str) -> list[str]:
    errs: list[str] = []
    st = load_style(domain)
    claim_ids = {c["id"] for c in node.get("claims", [])}
    if spec.get("template") not in catalog():
        return [f"template «{spec.get('template')}» нет в каталоге: {list(catalog())}"]
    p = spec.get("params") or {}
    try:
        tpl = get_template(spec["template"])(p, st)
    except Exception as e:  # noqa: BLE001
        return [f"params не принимаются шаблоном: {e}"]
    # слои
    layers = sorted(p.get("layers", []), key=lambda L: L.get("top", 0))
    if not 2 <= len(layers) <= 8:
        errs.append("слоёв должно быть 2–8")
    for a, b in zip(layers, layers[1:]):
        if abs(a["bottom"] - b["top"]) > 1e-6:
            errs.append(f"слои не встык: {a['bottom']} ≠ {b['top']}")
    if layers and (abs(layers[0]["top"] - p["depth_top"]) > 1e-6 or abs(layers[-1]["bottom"] - p["depth_bottom"]) > 1e-6):
        errs.append("слои должны покрывать интервал depth_top…depth_bottom")
    for L in layers:
        if L.get("lith") not in st.get("lithology", {}):
            errs.append(f"lith «{L.get('lith')}» нет в стайлгайде: {list(st.get('lithology', {}))}")
        if L.get("fluid") and L["fluid"] not in st.get("fluids", {}):
            errs.append(f"fluid «{L['fluid']}» нет в стайлгайде")
    curve_ids = set()
    for c in p.get("curves", []):
        cid = c.get("id", c.get("key"))
        curve_ids.add(cid)
        if c.get("key") not in st.get("curves", {}):
            errs.append(f"curve key «{c.get('key')}» нет в стайлгайде: {list(st.get('curves', {}))}")
        miss = [L.get("label", "?") for L in layers if c.get("prop") not in (L.get("props") or {})]
        if miss:
            errs.append(f"у кривой {cid} нет свойства «{c.get('prop')}» в слоях: {miss}")
        if not float(c.get("max", 1)) > float(c.get("min", 0)):
            errs.append(f"у кривой {cid} max должен быть больше min")
    if not p.get("curves"):
        errs.append("нужна хотя бы одна кривая")
    top, bottom = float(p.get("depth_top", 0)), float(p.get("depth_bottom", 0))
    # фразы
    beats = spec.get("beats") or []
    if not 7 <= len(beats) <= 18:
        errs.append(f"фраз {len(beats)}, нужно 9–15")
    words = 0
    has_pause = False
    for i, b in enumerate(beats, 1):
        say = (b.get("say") or "").strip()
        n = len(say.split())
        words += n
        if not say:
            if b.get("pause"):
                has_pause = True
            else:
                errs.append(f"фраза {i}: пустая без pause")
        else:
            if n > 18:
                errs.append(f"фраза {i}: {n} слов, максимум 18")
            if b.get("claim_id") not in claim_ids and not b.get("is_analogy"):
                errs.append(f"фраза {i}: claim_id «{b.get('claim_id')}» нет в узле {sorted(claim_ids)}")
            if re.search(r"\d", say):
                errs.append(f"фраза {i}: числа пиши словами: «{say}»")
            for pr in problems(say):
                errs.append(f"фраза {i}: {pr} в голосе: «{say}»")
        for pr in problems(b.get("on_screen") or ""):
            errs.append(f"фраза {i}: {pr} в on_screen")
        if b.get("label") not in LABELS:
            errs.append(f"фраза {i}: label из {LABELS}")
        if b.get("frame_class") not in CLASSES:
            errs.append(f"фраза {i}: frame_class из {CLASSES}")
        a = b.get("action") or {}
        for key in ("tool_to", "tool_jump"):
            if key in a and not top <= float(a[key]) <= bottom:
                errs.append(f"фраза {i}: {key} вне интервала {top}–{bottom}")
        for m in (a.get("mark") if isinstance(a.get("mark"), list) else [a.get("mark")] if a.get("mark") else []):
            if not (top <= float(m["top"]) < float(m["bottom"]) <= bottom):
                errs.append(f"фраза {i}: рамка вне интервала или top ≥ bottom")
        for cid in (a.get("show_curves") or []) + ([a["readout"]] if isinstance(a.get("readout"), str) else []):
            if cid not in curve_ids:
                errs.append(f"фраза {i}: кривой «{cid}» нет в params.curves")
    if beats:
        first, last = beats[0], beats[-1]
        if "?" not in (first.get("say", "") + first.get("on_screen", "")):
            errs.append("первая фраза — крючок-вопрос (нужен «?»)")
        if "?" not in (last.get("say", "") + last.get("on_screen", "")):
            errs.append("последняя фраза — вопрос на перенос (нужен «?»)")
    if not has_pause:
        errs.append("нет паузы на предсказание (фраза с say \"\" и pause 1.2–1.8)")
    secs = words / 130 * 60
    if not 35 <= secs <= 80:
        errs.append(f"длина ≈{secs:.0f} с при 130 сл/мин, нужно 40–75 с")
    if not any(b.get("action", {}).get("tool_to") is not None for b in beats):
        errs.append("прибор ни разу не едет (нет tool_to) — механизм не показан")
    for key in ("prediction_question", "transfer_question", "plaque", "exaggerations", "assumptions", "data_it_bridge"):
        if not spec.get(key):
            errs.append(f"нет поля {key}")
    for pr in problems(" ".join(spec.get("plaque") or [])):
        errs.append(f"{pr} в plaque")
    if re.search(r"(?<!не )в масштабе", " ".join(spec.get("plaque") or []), re.I):
        errs.append("plaque утверждает «в масштабе» — в кадре преувеличения, так писать нельзя")
    return errs


def generate(layer_path: Path, node_id: str, template_hint: str | None = None, tier: str = "strong",
             out_root: Path | None = None) -> Path:
    from glava.llm import ask_json

    layer = json.loads(layer_path.read_text(encoding="utf-8"))
    node = _node(layer, node_id)
    chapter = layer.get("chapter") or node_id.split(".")[0]
    domain = domain_for_chapter(chapter)
    vid = re.sub(r"[^\w]+", "_", node_id.split(".")[-1])[:40] + "_v1"
    out_root = out_root or (ROOT / "runs" / chapter / "video")
    out_dir = out_root / vid
    out_dir.mkdir(parents=True, exist_ok=True)

    draft, errors, history = None, None, []
    for rnd in range(1, MAX_ROUNDS + 1):
        draft = ask_json(SYSTEM, build_prompt(node, domain, template_hint, errors, draft), tier=tier,
                         tag=f"video_specgen:{node_id}:{rnd}")
        if draft.get("no_video"):
            f = out_dir / "no_video.json"
            f.write_text(json.dumps({"node_id": node_id, "reason": draft.get("reason"), "by_model": True},
                                    ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[specgen] {node_id}: шаблона нет — {draft.get('reason')}", file=sys.stderr)
            return f
        draft.update({"video_id": vid, "chapter": chapter, "node_id": node_id,
                      "layer": str(layer_path.relative_to(ROOT)) if layer_path.is_relative_to(ROOT) else str(layer_path)})
        errors = validate(draft, node, domain)
        history.append({"round": rnd, "errors": errors})
        print(f"[specgen] {node_id}: круг {rnd}, ошибок {len(errors)}", file=sys.stderr)
        if not errors:
            break
    draft["generated_by"] = {"by_model": True, "tier": tier, "rounds": history,
                             "hypothesis_note": "числа сцены условные, см. assumptions"}
    if errors:
        draft["generated_by"]["unresolved_errors"] = errors
        print(f"[specgen] {node_id}: остались ошибки — смотри generated_by.unresolved_errors", file=sys.stderr)
    f = out_dir / "spec.json"
    f.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    return f


def main(argv=None):
    ap = argparse.ArgumentParser(description="Узел учебного слоя → spec.json ролика")
    ap.add_argument("layer", help="путь к layer.json")
    ap.add_argument("node_id", nargs="?", help="id узла (или --routed)")
    ap.add_argument("--routed", action="store_true", help="все узлы с роликом по routing.json рядом со слоем")
    ap.add_argument("--template", default=None, help="назначить шаблон")
    ap.add_argument("--tier", default="strong", choices=["bulk", "strong"])
    ap.add_argument("-o", "--out", default=None, help="папка для <video_id>/ (по умолчанию runs/<глава>/video)")
    ap.add_argument("--build", action="store_true", help="сразу собрать ролик")
    a = ap.parse_args(argv)
    layer_path = Path(a.layer).resolve()
    jobs: list[tuple[str, str | None]] = []
    if a.routed:
        routing = json.loads((layer_path.parent / "routing.json").read_text(encoding="utf-8"))
        for r in routing.get("routes", []):
            if r.get("format") in ("шортс", "длинное видео") or r.get("secondary_format") in ("шортс", "длинное видео"):
                jobs.append((r["node_id"], r.get("template")))
    elif a.node_id:
        jobs.append((a.node_id, a.template))
    else:
        ap.error("нужен node_id или --routed")
    out_root = Path(a.out).resolve() if a.out else None
    for node_id, hint in jobs:
        if hint and hint not in catalog():
            print(f"[specgen] {node_id}: шаблон «{hint}» ещё не сделан — пропуск", file=sys.stderr)
            continue
        f = generate(layer_path, node_id, hint, a.tier, out_root)
        print(f)
        if a.build and f.name == "spec.json":
            from .build import build
            res = build(f)
            for k, v in res["videos"].items():
                print(f"{k}: {v}")


if __name__ == "__main__":
    main()
