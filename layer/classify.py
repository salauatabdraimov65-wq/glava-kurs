"""Тип трудности шага (М, В, И, Ф, П, Пр, Н, Р) — отдельный проход классификации по содержанию шага.

    uv run python -m layer.classify <chapter_id> [...]            # переписать difficulty в layer.json
    uv run python -m layer.classify --bench                        # сравнить модели (см. ниже)

Определения и примеры — из «Подачи доменов геологии» организаторов (config/difficulty.json + примеры ниже).
Тип отвечает на вопрос «что именно трудно понять», а не «о чём текст».
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from glava.chapter import ROOT, chapters_config
from glava.llm import ask_json

DIFF = json.loads((ROOT / "config" / "difficulty.json").read_text(encoding="utf-8"))

SYS = """Ты методист. Определи ТИП ТРУДНОСТИ шага учебного курса: что именно новичку трудно понять в этом шаге.
Тип — не тема и не раздел науки, а вид мысленной модели, которую человек должен построить.

Типы и признаки:
М — Механизм процесса: как физически/технически что-то происходит, что куда движется и почему (фильтрат проникает в пласт; газ выделяется; цемент заполняет затруб; пустоты зарастают цементом).
В — Геологическое время: последовательность событий формирования за тысячи–миллионы лет, этапы, развитие во времени (риф растёт и отмирает; дельта проградирует; пористость меняется в диагенезе; история бассейна).
И — Косвенное измерение: цепочка «прибор/сигнал → отклик на диаграмме → свойство пласта», и что один отклик может иметь несколько причин (сопротивление, ПС, ГК, нейтронный метод, их показания против пород и флюидов).
Ф — Формализм и модель: смысл уравнения, модели или расчётной схемы, их членов, допущений и решений (уравнение пьезопроводности, решения, безразмерные переменные, приближённые методы).
П — Пространство: геометрия тел в объёме, по картам и разрезам — форма, размеры, взаимное расположение, сечения (строение рифового массива, форма песчаного тела, положение ловушки, размещение скважин по площади).
Пр — Процедура: порядок действий специалиста, входные данные, точки выбора, ручной труд (как строят колонку, как выбирают комплекс методов, как проводят ВНК, как рассчитывают порог).
Н — Неопределённость и решение: диапазон вместо числа, пороги и их выбор, неоднозначность, цена ошибки, когда правило не работает (граничные значения, статистический выбор порога, погрешность метода).
Р — Распознавание: узнать объект по внешним/диагностическим признакам — тип породы, фации, обстановку, тип рифа или коллектора по облику, составу, строению, набору признаков.

Правила: выбери основной тип и (если есть) второй, отличный от основного.
- Р — только узнавание объекта по ОБЛИКУ, составу, строению (порода, фация, обстановка, тип рифа, тип коллектора по керну/обнажению/описанию).
- Признаки на каротажных диаграммах и показания приборов (сопротивление, ПС, ГК, нейтронные, микрозонды, «таблица признаков пород по методам ГИС») — это И (вторым может быть Р или Н), а не Р.
- Пороги, граничные значения, выбор критерия, статистика, неоднозначность — Н. Порядок действий — Пр.
- Формирование во времени — В. Формулы и расчётные модели — Ф.
Верни JSON {"primary":"…","secondary":"…" или null,"why":"одна фраза"}"""


def node_text(n: dict) -> str:
    claims = "\n".join(f"- {c['text']}" for c in n["claims"][:12])
    return (f"Шаг: {n['title']} (раздел книги: {n.get('book_title')})\nСуть: {n['layers']['main']['text']}\n"
            f"Как работает: {n['layers']['mechanism']['text']}\nУтверждения:\n{claims}")


def classify_nodes(nodes: list[dict], tier: str = "strong") -> list[dict]:
    def run(n):
        r = ask_json(SYS, node_text(n), tier=tier, tag="classify", effort="low")
        p = r.get("primary") if r.get("primary") in DIFF["types"] else None
        s = r.get("secondary") if r.get("secondary") in DIFF["types"] and r.get("secondary") != p else None
        return {"primary": p, "secondary": s, "why": r.get("why", "")}
    with ThreadPoolExecutor(8) as ex:
        return list(ex.map(run, nodes))


def apply(chapter: str):
    p = ROOT / "runs" / chapter / "layer.json"
    layer = json.loads(p.read_text(encoding="utf-8"))
    res = classify_nodes(layer["nodes"])
    for n, r in zip(layer["nodes"], res):
        if r["primary"]:
            n["difficulty"] = {"primary": r["primary"], "secondary": r["secondary"]}
    p.write_text(json.dumps(layer, ensure_ascii=False, indent=1), encoding="utf-8")
    print(chapter, dict(Counter(n["difficulty"]["primary"] for n in layer["nodes"])))


# Типы пар глав из ТЗ (таблица выборок) — только для СРАВНЕНИЯ моделей, в конвейер не входит.
EXPECTED = {"latyshova_gl5_par17-20": "ИПрН", "vendelshtein_gl1": "ИПрН", "basniev_gl23-24": "ФМ",
            "barenblatt_par4": "ФМ", "kuznetsov_gl10": "РВП", "baraboshkin_par1-2": "РВП"}
CANDIDATES = {"gpt-6-luna": ("bulk", "0.10,0.50"), "gpt-6.1-terra": ("strong", "1.0,5.0"),
              "gpt-5.5": ("strong", "5.0,30.0"), "gpt-6.2-sol": ("strong", "10.0,40.0")}


def bench(models=None):
    data = {c: json.loads((ROOT / "runs" / c / "layer.json").read_text(encoding="utf-8"))["nodes"] for c in EXPECTED}
    saved = {k: os.environ.get(k) for k in ("LLM_MODEL_BULK", "LLM_MODEL_STRONG", "LLM_PRICE_BULK", "LLM_PRICE_STRONG")}
    rows = {}
    for model, (tier, price) in CANDIDATES.items():
        if models and model not in models:
            continue
        os.environ[f"LLM_MODEL_{tier.upper()}"], os.environ[f"LLM_PRICE_{tier.upper()}"] = model, price
        hit = tot = 0
        per = {}
        try:
            for c, nodes in data.items():
                res = classify_nodes(nodes, tier=tier)
                h = sum(1 for r in res if r["primary"] and r["primary"] in _split(EXPECTED[c]))
                per[c] = (h, len(nodes), dict(Counter(r["primary"] for r in res)))
                hit += h; tot += len(nodes)
            rows[model] = (hit, tot, per)
            print(f"{model}: {hit}/{tot} = {100 * hit / tot:.0f}% шагов в типах своей главы")
            for c, (h, n, dist) in per.items():
                print(f"   {c}: {h}/{n} {dist}")
        except Exception as e:
            print(model, "ошибка:", str(e)[:150])
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v
    return rows


def _split(s):
    out, i = [], 0
    while i < len(s):
        if s[i:i + 2] == "Пр":
            out.append("Пр"); i += 2
        else:
            out.append(s[i]); i += 1
    return out


if __name__ == "__main__":
    if sys.argv[1:2] == ["--bench"]:
        bench(sys.argv[2:] or None)
    else:
        for c in sys.argv[1:]:
            apply(c)
