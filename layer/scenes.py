"""Спецификация сцены ролика из узла учебного слоя → runs/<chapter>/video/<video_id>/spec.json

    uv run python -m layer.scenes <chapter_id> <node_id> [--format short|long] [--template well_tool_curve]

Формат spec.json — как у организаторов (контрольный пакет → scenes[0].spec): template, params, beats, objects,
invariants, assumptions, exaggerations, plaque, prediction_question, transfer_question, data_it_bridge.
Каждая фраза закадра ссылается на claim_id узла; код проверяет ссылки и длину. Станок video/ рендерит spec → mp4.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from glava.chapter import ROOT
from glava.llm import ask_json

DIFF = json.loads((ROOT / "config" / "difficulty.json").read_text(encoding="utf-8"))

# Параметры шаблонов: что станок video/ умеет рисовать. Геологии здесь нет — только устройство сцены.
TEMPLATES = {
    "well_tool_curve": """Разрез по глубине + ствол скважины + прибор на кабеле + трек(и) кривой, вычисляемой из свойств слоёв.
params: {"depth_top": м, "depth_bottom": м, "depth_tick": м, "tool_start": м, "tool_resolution_m": окно усреднения прибора, "bore_width": условная ширина ствола, "bore_real_m": реальный диаметр ствола по главе (если в главе нет — 0.2),
 "layers": [{"top": м, "bottom": м, "lith": ключ литологии из стайлгайда, "label": подпись, "fluid": ключ флюида из стайлгайда или null,
             "props": {"<свойство>": число, ...}, "mud_cake": bool, "invasion_width": условная ширина зоны проникновения (0 — нет)}],
 "curves": [{"key": ключ кривой из стайлгайда, "prop": имя свойства из props, "label": подпись, "unit": единица, "min": число, "max": число, "log": bool}]}
action в beat: {"tool_to": глубина прибора к концу фразы} | {"mark": {"top": м, "bottom": м, "label": текст}} | {"show_mud_cake": true} | {"show_invasion": true} | {"highlight_layer": индекс} | {}""",
    "pressure_diffusion": """Давление вокруг скважины (radial) или у галереи (linear) во времени, считается по формуле главы.
params: {"geometry": "radial"|"linear", "solution": "ei"|"erfc"|"pssss", "p0_MPa": начальное давление, "kappa_m2_s": пьезопроводность,
 "q_m3_s": дебит (для radial), "mu_Pa_s": вязкость, "k_m2": проницаемость, "h_m": толщина, "r_w_m": радиус скважины,
 "x_max_m": размер области на графике, "times_s": [моменты времени для кривых], "views": ["map","profile","bottomhole_vs_log_t"]}
action в beat: {"time_to": секунды модели к концу фразы} | {"show_curve_at": t} | {"mark_x": м} | {"show_view": "..."} | {}""",
    "section_through_time": """Вертикальный разрез (x — расстояние, z — глубина/высота), где тела растут, смещаются или замещаются во времени:
рост рифа к свету, проградация дельты, заполнение ловушки, перемещение фронта флюида. Форма тел между ключевыми кадрами интерполируется.
params: {"x_max_m": ширина разреза, "z_top_m": верх (отметка, м; может быть выше уровня моря), "z_bottom_m": низ, "vertical_exaggeration": во сколько раз растянута вертикаль (1 — истинный масштаб),
 "time_unit": "тыс. лет"|"млн лет"|"сут"|"ч", "time_steps": [моменты времени ключевых кадров],
 "water_level": [{"t": момент, "z": отметка уровня воды}] или [],
 "bodies": [{"id": "reef_core", "lith": ключ литологии из стайлгайда, "fluid": ключ флюида или null, "label": подпись,
             "keyframes": [{"t": момент, "polygon": [[x, z], ...]}]}],
 "arrows": [{"t_from": момент, "t_to": момент, "from": [x, z], "to": [x, z], "label": "подпись процесса"}]}
action в beat: {"time_to": момент к концу фразы} | {"highlight_body": "id"} | {"mark_x": м} | {"show_true_scale": true} | {}""",
}

COMPUTED_TEMPLATES = {"well_tool_curve", "pressure_diffusion", "section_through_time"}  # кадр считается из модели

FORMATS = {
    "short": {"aspect": "9:16", "words": (90, 160), "seconds": "40–75", "focus": "один механизм"},
    "long": {"aspect": "16:9", "words": (200, 380), "seconds": "90–180",
             "focus": "решение на данных: признаки → правило → вывод, синхронные виды рядом, контрпример"},
}

SYS = """Ты сценарист учебных роликов. Пишешь спецификацию сцены по узлу учебного слоя — данными, а не прозой.
Ролик: {aspect}, {seconds} с, {focus}. Зритель — новичок-цифровик.
Шаблон сцены «{template}»: {template_doc}
Ключи стайлгайда: литология {lith}; флюиды {fluids}; кривые {curves}.

Верни JSON:
{{"title":"короткий заголовок-крючок","plaque":["не в масштабе:","что и во сколько раз преувеличено — числом"],
 "params":{{...по описанию шаблона...}},
 "beats":[{{"say":"фраза закадра","claim_id":"id утверждения узла или null для чистого перехода","on_screen":"подпись 2–5 слов",
           "label":"симуляция|схема|реальные данные","frame_class":"A|B","action":{{...}},"is_analogy":false,"pause_s":0}}],
 "objects":["что есть в кадре"],"invariants":["что не меняется между кадрами и проверяется"],
 "assumptions":["условные значения и упрощения — честно, со словом «условно»; не обобщай их на все случаи"],
 "exaggerations":["что преувеличено и во сколько раз"],
 "prediction_question":"вопрос на предсказание с паузой до ответа","transfer_question":"вопрос на перенос в конце",
 "data_it_bridge":"как это выглядит в данных и что там ломается","terms_to_check":["термины и аббревиатуры для словаря ударений"]}}
Правила подачи (зритель НИЧЕГО не знает о предмете и смотрит с телефона):
- История, а не лекция: «проблема → что делают → как это называется». Термин звучит ПОСЛЕ того, как зритель понял, зачем он нужен.
- Крючок в первые 3 секунды — ставки, а не определение: что пойдёт не так и чего это стоит («Скважину пробурили, а нефти нет. Почему прибор ошибся?»).
- Говори с зрителем на «ты», просто и живо, как объясняешь другу-программисту. Одна мысль на фразу, 6–14 слов.
- Одна бытовая аналогия на ролик (is_analogy: true) — и тут же, где она не работает.
- НИКАКИХ формул, символов, индексов и «квадрат расстояния не больше…» в голосе: только смысл («чем дальше от скважины, тем позже доходит перепад давления»). Числа — круглые, словами, не больше 2–3 на ролик.
- Структура: крючок → пауза на предсказание (beat без say с pause_s 2–3, on_screen — вопрос) → 3–5 шагов механизма, каждый с действием в кадре → ответ на крючок одной фразой → вопрос на перенос.
- Если узел (или соседний) опровергает заблуждение — строй сцену-КОНТРАСТ: рядом случай, где заблуждение кажется верным, и ловушка, где оно ломается; зритель сам видит разницу.
- В кадре не больше 2 кривых (well_tool_curve) и не больше 3–4 слоёв: экран телефона узкий.
- Не говори «слева/справа/вверху/в центре»: раскладка разная в 9:16 и 16:9. Называй вид по имени («на виде сверху», «на графике давления на забое») и подсвечивай его действием (show_view / highlight_layer).
- on_screen — 1–3 слова, ключевое слово можно выделить *звёздочками*; не дублирует голос дословно.
- Каждая фраза: до 15–18 слов, для уха: числа словами, аббревиатура раскрыта при первом упоминании.
- Каждая содержательная фраза опирается на утверждение узла (claim_id) и не говорит больше него. Сохраняй условия автора.
- Сумма слов всех say: {wmin}–{wmax}.
- Числа в params: если в главе есть числа — бери их; иначе условные значения, и это записано в assumptions. Глубина растёт вниз.
- Не закрепляй заблуждения из списка: {misc}
- Масштабы и скорости процессов правдоподобны (таблица ниже). Тела и зоны рисуй в масштабе разреза; отдельные поры/зёрна
  в разрезе километрового масштаба не рисуй — показывай зону. Любое увеличение — в exaggerations и plaque с числом «в N раз».
Типичные масштабы предметной области:
{scales}"""


def make_spec(chapter: str, node_id: str, fmt: str, template: str | None, video_id: str | None = None,
              with_nodes: list[str] | None = None, brief: str | None = None) -> Path:
    run = ROOT / "runs" / chapter
    layer = json.loads((run / "layer.json").read_text(encoding="utf-8"))
    node = next(n for n in layer["nodes"] if n["id"] == node_id)
    template = template or DIFF["scene_template"].get(node["difficulty"]["primary"]) or "well_tool_curve"
    style = json.loads((ROOT / "domain" / layer.get("domain", "geology") / "styleguide.json").read_text(encoding="utf-8"))
    misc = json.loads((ROOT / "domain" / layer.get("domain", "geology") / "misconceptions.json").read_text(encoding="utf-8"))
    sc = ROOT / "domain" / layer.get("domain", "geology") / "scales.md"
    scales = sc.read_text(encoding="utf-8") if sc.exists() else "(нет)"
    f = FORMATS[fmt]
    sys_p = SYS.format(aspect=f["aspect"], seconds=f["seconds"], focus=f["focus"], template=template,
                       template_doc=TEMPLATES[template], lith=list(style["lithology"]), fluids=list(style["fluids"]),
                       curves=list(style["curves"]), wmin=f["words"][0], wmax=f["words"][1],
                       misc="; ".join(m["text"] for m in misc), scales=scales)
    user = (f"Узел: {node['title']} ({node_id}), тип {node['difficulty']}\nСуть: {node['layers']['main']['text']}\n"
            f"Механизм: {node['layers']['mechanism']['text']}\nНюансы: {node['layers']['nuances']['text']}\n"
            f"Частая ошибка: {(node.get('misconception') or {}).get('text')}\n"
            f"Данные: {node['data_meaning']['from_chapter']['text']} / ломается: {node['data_meaning']['breaks']['text']}\n"
            f"Решение на данных: {json.dumps(node.get('decision_on_data'), ensure_ascii=False)}\n"
            f"Числа: {node.get('numbers')}\n\nУтверждения (id | стр. | текст | цитата):\n" +
            "\n".join(f'{c["id"]} | {c["page_pdf"]} | {c["text"]} | «{c["quote"]}»' for c in node["claims"]))
    extra = [n for n in layer["nodes"] if n["id"] in (with_nodes or [])]
    for n in extra:  # соседний узел, на который опирается та же история
        user += f"\n\nУтверждения соседнего узла «{n['title']}» (на них тоже можно опираться):\n" + "\n".join(
            f'{c["id"]} | {c["page_pdf"]} | {c["text"]} | «{c["quote"]}»' for c in n["claims"])
    if brief:
        user += f"\n\nБРИФ ОТ РЕДАКТОРА (главное — следуй ему, но не выходи за утверждения):\n{brief}"
    spec = ask_json(sys_p, user, tier="strong", tag=f"scene:{chapter}")

    # проверки кодом
    ids = {c["id"] for c in node["claims"]} | {c["id"] for n in extra for c in n["claims"]}
    problems = []
    for b in spec.get("beats", []):
        if b.get("claim_id") and b["claim_id"] not in ids:
            problems.append(f"неизвестный claim_id {b['claim_id']}")
            b["claim_id"] = None
        # метка происхождения определяется тем, КАК сделан кадр, а не текстом фразы:
        # расчётные шаблоны рисуют каждый кадр из модели → «симуляция», класс B
        if template in COMPUTED_TEMPLATES:
            b["label"], b["frame_class"] = "симуляция", "B"
        elif b.get("label") not in ("симуляция", "схема", "реальные данные"):
            b["label"] = "схема"
        if b.get("frame_class") not in ("A", "B", "C", "D"):
            b["frame_class"] = "B"
        if b.get("pause_s") and not b.get("pause"):
            b["pause"] = b["pause_s"]          # станок video/ читает поле pause
        if len((b.get("say") or "").split()) > 20:
            problems.append(f"длинная фраза: {b['say'][:50]}…")
    # честность масштаба: условная ширина ствола всегда подписана числом (типичный диаметр ~0,2 м)
    p = spec.get("params") or {}
    if template == "well_tool_curve" and float(p.get("bore_width") or 0) > 0.3:
        real = float(p.get("bore_real_m") or 0.2)   # реальный диаметр: из параметров сцены, иначе типичный ~0,2 м
        k = round(float(p["bore_width"]) / real)
        note = f"ствол показан примерно в {k} раз шире реального (≈{real:g} м)".replace(".", ",")
        # одна строка о стволе — от кода; строки модели про ствол убираются, чтобы числа не спорили
        spec["exaggerations"] = [x for x in (spec.get("exaggerations") or []) if "ствол" not in x.lower()] + [note]
        pl = [x for x in (spec.get("plaque") or ["не в масштабе:"]) if "ствол" not in x]
        spec["plaque"] = pl + [note]
    for c in (p.get("curves") or []):   # широкий диапазон (сопротивление) — логарифмическая шкала, как на планшете
        try:
            if float(c.get("min", 0)) > 0 and float(c["max"]) / float(c["min"]) >= 50:
                c["scale"] = "log"
        except (KeyError, ValueError, ZeroDivisionError):
            pass
    words = sum(len((b.get("say") or "").split()) for b in spec.get("beats", []))
    if not f["words"][0] <= words <= f["words"][1]:
        problems.append(f"слов {words}, нужно {f['words']}")
    with_claim = sum(1 for b in spec.get("beats", []) if b.get("claim_id"))
    vid = video_id or f"{chapter.split('_')[0]}_{fmt}_{re.sub(r'[^a-z0-9]+', '_', node_id.split('.')[-1])[:24]}"
    spec.update({"video_id": vid, "chapter": chapter, "node_id": node_id, "template": template, "format": fmt,
                 "aspect": f["aspect"], "styleguide": f"domain/{layer.get('domain', 'geology')}/styleguide.json",
                 "spec_check": {"words": words, "beats": len(spec.get("beats", [])), "beats_with_claim": with_claim,
                                "problems": problems}})
    out = run / "video" / vid / "spec.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{out}  слов {words}, фраз {len(spec.get('beats', []))}, с опорой {with_claim}; замечания: {problems or 'нет'}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("chapter"); ap.add_argument("node_id")
    ap.add_argument("--format", default="short", choices=list(FORMATS))
    ap.add_argument("--template"); ap.add_argument("--video-id")
    ap.add_argument("--brief", help="необязательный бриф редактора: какую историю рассказать")
    ap.add_argument("--with", dest="with_nodes", nargs="*", help="id соседних узлов, чьи утверждения тоже можно использовать")
    a = ap.parse_args()
    make_spec(a.chapter, a.node_id, a.format, a.template, a.video_id, a.with_nodes, a.brief)
