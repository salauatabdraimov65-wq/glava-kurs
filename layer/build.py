"""Сборка учебного слоя главы: глава → runs/<chapter>/{layer,coverage,routing,tasks}.json

    uv run python -m layer.build <chapter_id>
    uv run python -m layer.build test1 --md путь/к/главе.md --pages 12-30 [--domain drilling] [--lang en]

Проходы (модель предлагает — код проверяет):
  A. извлечение утверждений по кускам главы (bulk) → цитата сверяется с текстом, страница берётся из md
  B. группировка утверждений в шаги-узлы (strong) → код чинит пропуски, дубли, слишком мелкие шаги
  C. наполнение каждого узла (bulk): суть, механизм, ошибка, данные, аналогия, термины
  D. задания (bulk) → код проверяет ключи
  E. маршрутизация и таблица покрытия — кодом
Геологии в этом файле нет: предметное берётся из domain/<domain>/ и config/difficulty.json.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from glava.chapter import ROOT, Chapter, chapters_config, check_claim, find_quote, load_chapter, norm
from glava.llm import ask_json, spent_usd
from layer.units import COVERAGE_KIND, Unit, split_units

WORKERS = 8
CHUNK_WORDS = 1400
MIN_CLAIMS_PER_NODE = 4
MIN_TASKS = 18          # ТЗ: не меньше 15 заданий на главу — берём с запасом
DIFF = json.loads((ROOT / "config" / "difficulty.json").read_text(encoding="utf-8"))


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def domain_pack(domain: str) -> dict:
    d = ROOT / "domain" / domain
    pack = {"misconceptions": [], "audience": ""}
    if (d / "misconceptions.json").exists():
        pack["misconceptions"] = json.loads((d / "misconceptions.json").read_text(encoding="utf-8"))
    if (d / "audience.md").exists():
        pack["audience"] = (d / "audience.md").read_text(encoding="utf-8")
    pack["notation"] = (d / "notation.md").read_text(encoding="utf-8") if (d / "notation.md").exists() else ""
    return pack


# ───────────────────────── A. извлечение ─────────────────────────

SYS_A = """Ты извлекаешь из главы учебника ВСЕ мысли автора для учебного курса. Точность важнее красоты.
Тебе даны единицы главы в формате [id] (вид) текст.
Верни JSON:
{"claims":[{"unit_id":"p091.03","text":"мысль своими словами по-русски, до 30 слов","kind":"observation|inference|assumption",
            "quote":"ДОСЛОВНЫЙ непрерывный фрагмент этой единицы, 8–220 символов","source_kind":"text|formula|table|figure_caption|task"}],
 "worked_examples":[{"unit_ids":["p097.02","p097.03"],"condition":"условие примера","steps":["шаг 1","шаг 2"],"result":"итог с числами автора","figure":"рис. N, стр. M или null"}],
 "book_tasks":[{"unit_id":"p095.08","number":"91","text":"формулировка задачи","figure":"рис. N или null"}],
 "skip":[{"unit_id":"p092.01","reason":"почему в этой единице нет мысли для курса"}]}
Правила:
- Каждая единица вида paragraph/formula/table/figure/task — либо даёт хотя бы одно утверждение, либо попадает в skip с честной причиной. «Непоказуемо» — не причина.
- Одно утверждение = одна мысль. Извлекай всё: механизмы, условия применимости, числа, диапазоны, пороги, оговорки, исключения, примеры.
- НЕ обобщай: сохраняй условия автора («при…», «если…», «как правило», «примерно», «в большинстве случаев»). Не меняй знак и направление («выше/ниже», «растёт/падает»).
- quote копируй символ в символ из текста ЭТОЙ единицы, не исправляй опечатки распознавания, не склеивай куски.
- kind: observation — факт/наблюдение; inference — вывод автора; assumption — допущение, принятое автором.
- Формула: text объясняет смысл формулы словами; quote — фрагмент формулы или фразы рядом.
- Таблица: по утверждению на каждую содержательную строку.
- Если глава на другом языке — text всё равно по-русски, quote на языке оригинала.
- worked_examples — только разобранные автором примеры с решением; book_tasks — задачи для самостоятельной работы."""


def chunk_units(units: list[Unit]) -> list[list[Unit]]:
    chunks, cur, words = [], [], 0
    for u in units:
        w = len(u.text.split())
        if cur and words + w > CHUNK_WORDS and u.kind not in ("formula",):
            chunks.append(cur); cur, words = [], 0
        cur.append(u); words += w
    if cur:
        chunks.append(cur)
    return chunks


def fmt_units(us: list[Unit]) -> str:
    return "\n\n".join(f"[{u.unit_id}] ({u.kind}) {u.text}" for u in us)


def locate_quote(ch: Chapter, by_id: dict[str, Unit], unit_id: str, quote: str) -> tuple[str | None, int | None]:
    """Возвращает (unit_id, page) где цитата реально есть, или (None, None).
    Окончательное решение — check_claim, той же функцией потом проверяется слой."""
    uid, page = _locate(ch, by_id, unit_id, quote)
    if uid and not check_claim(ch, quote, page)["ok"]:
        return None, None
    return uid, page


def _locate(ch, by_id, unit_id, quote):
    q = norm(quote or "")
    if len(q) < 8:
        return None, None
    u = by_id.get(unit_id)
    if u and q in norm(u.text):
        return u.unit_id, u.page_pdf
    for v in by_id.values():                       # модель перепутала единицу
        if v.in_scope and q in norm(v.text):
            return v.unit_id, v.page_pdf
    pages = find_quote(ch, quote)                  # цитата на стыке единиц/страниц
    if pages:
        cand = [v for v in by_id.values() if v.page_pdf == pages[0] and v.in_scope]
        return (cand[0].unit_id if cand else unit_id), pages[0]
    return None, None


def pass_a(ch: Chapter, units: list[Unit]):
    by_id = {u.unit_id: u for u in units}
    content = [u for u in units if u.in_scope and u.kind not in ("running_header", "heading")]
    chunks = chunk_units(content)
    title = ch.meta.get("title", ch.id)
    log(f"A: {len(content)} единиц, {len(chunks)} кусков")

    def run(chunk):
        user = f"Глава: {title}\n\n{fmt_units(chunk)}"
        r = ask_json(SYS_A, user, tier="bulk", tag=f"A:{ch.id}")
        bad = []
        good = []
        for c in r.get("claims", []):
            c["quote"] = (c.get("quote") or "").strip().rstrip("-‐‑ ").strip()
            uid, page = locate_quote(ch, by_id, c.get("unit_id", ""), c.get("quote", ""))
            if uid:
                c["unit_id"], c["page_pdf"] = uid, page
                good.append(c)
            else:
                bad.append(c)
        if bad:  # один проход ремонта цитат
            fix = ask_json(
                "Исправь цитаты: каждая quote должна быть ДОСЛОВНЫМ непрерывным фрагментом текста указанной единицы "
                "(символ в символ). Верни JSON {\"claims\":[...]} с теми же полями; утверждение, для которого нет "
                "дословной опоры в тексте, удали.",
                f"Единицы:\n{fmt_units(chunk)}\n\nУтверждения с неверными цитатами:\n{json.dumps(bad, ensure_ascii=False)}",
                tier="bulk", tag=f"A-fix:{ch.id}")
            for c in fix.get("claims", []):
                c["quote"] = (c.get("quote") or "").strip().rstrip("-‐‑ ").strip()
                uid, page = locate_quote(ch, by_id, c.get("unit_id", ""), c.get("quote", ""))
                if uid:
                    c["unit_id"], c["page_pdf"] = uid, page
                    good.append(c)
        return good, len(bad), r

    with ThreadPoolExecutor(WORKERS) as ex:
        results = list(ex.map(run, chunks))
    claims, worked, tasks, skips, n_bad = [], [], [], {}, 0
    seen = set()
    for good, nb, r in results:
        n_bad += nb
        for c in good:
            k = norm(c["quote"]) + c["unit_id"]
            if k in seen:
                continue
            seen.add(k)
            c.setdefault("kind", "observation")
            if c["kind"] not in ("observation", "inference", "assumption"):
                c["kind"] = "observation"
            if c.get("source_kind") not in ("text", "formula", "table", "task", "figure_caption", "figure_description_model"):
                c["source_kind"] = "text"
            claims.append(c)
        worked += [w for w in r.get("worked_examples", []) if w.get("unit_ids")]
        tasks += [t for t in r.get("book_tasks", []) if t.get("unit_id") in by_id]
        for s in r.get("skip", []):
            if s.get("unit_id") in by_id:
                skips[s["unit_id"]] = s.get("reason") or "без мысли для курса"
    order = {u.unit_id: i for i, u in enumerate(units)}
    claims.sort(key=lambda c: order.get(c["unit_id"], 0))
    for i, c in enumerate(claims, 1):
        c["tmp_id"] = f"c{i}"
    log(f"A: {len(claims)} утверждений; неверных цитат до ремонта {n_bad}")
    return claims, worked, tasks, skips


# ───────────────────────── B. группировка ─────────────────────────

SYS_B = """Ты методист курса для новичков-цифровиков (знают данные и программирование, не знают предмета).
Разложи утверждения главы по шагам курса. Верни JSON:
{"steps":[{"slug":"latin_snake_case_2_4_слова","title":"заголовок для новичка 3–7 слов","book_title":"название раздела книги, к которому относится шаг",
           "claim_ids":["c1","c2"],"difficulty":"И","secondary":"Пр","threshold":false,"prerequisites":[1,2]}]}
Правила:
- Шаг = одна идея на 3–7 минут, обычно 5–12 утверждений. Не делай шагов из 1–3 утверждений: присоединяй их к соседнему по смыслу.
- Строки одной таблицы признаков — в один-два шага, а не по шагу на строку.
- Каждое утверждение — ровно в одном шаге. Порядок шагов — без скачков: то, на что опирается шаг, идёт раньше; в целом следуй порядку главы.
- prerequisites — номера шагов (с 1), без которых этот шаг не понять.
- threshold=true — пороговая идея, без которой дальше всё непонятно (1–3 на главу).
- Тип трудности (основной и второй) — что именно трудно понять:
{types}"""


def slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")
    return s[:40] or "step"


def pass_b(ch: Chapter, claims: list[dict], prefix: str) -> list[dict]:
    types = "\n".join(f"  {k} — {v}" for k, v in DIFF["types"].items())
    user = f"Глава: {ch.meta.get('title', ch.id)}\n\n" + "\n".join(
        f'{c["tmp_id"]} | стр.{c["page_pdf"]} | {c["text"]}' for c in claims)
    r = ask_json(SYS_B.replace("{types}", types), user, tier="strong", tag=f"B:{ch.id}")
    steps = [s for s in r.get("steps", []) if s.get("claim_ids")]
    valid = {c["tmp_id"] for c in claims}
    # дубли: утверждение остаётся в первом шаге
    used = set()
    for s in steps:
        s["claim_ids"] = [i for i in s["claim_ids"] if i in valid and not (i in used or used.add(i))]
    steps = [s for s in steps if s["claim_ids"]]
    # пропуски: в шаг соседнего утверждения
    pos = {c["tmp_id"]: i for i, c in enumerate(claims)}
    owner = {i: k for k, s in enumerate(steps) for i in s["claim_ids"]}
    for c in claims:
        if c["tmp_id"] not in owner:
            j = pos[c["tmp_id"]]
            near = next((owner[claims[x]["tmp_id"]] for d in range(1, len(claims))
                         for x in (j - d, j + d) if 0 <= x < len(claims) and claims[x]["tmp_id"] in owner), 0)
            steps[near]["claim_ids"].append(c["tmp_id"]); owner[c["tmp_id"]] = near
    # слишком мелкие шаги — слить с соседом (предпочтительно предыдущим из того же раздела)
    k = 0
    while len(steps) > 1 and k < len(steps):
        s = steps[k]
        if len(s["claim_ids"]) < MIN_CLAIMS_PER_NODE:
            t = k - 1 if k > 0 and (steps[k - 1].get("book_title") == s.get("book_title") or k == len(steps) - 1) else k + 1
            if t >= len(steps):
                t = k - 1
            steps[t]["claim_ids"] += s["claim_ids"]
            for x in steps:  # перенумерация предпосылок
                x["prerequisites"] = [p - (p > k + 1) if p != k + 1 else t + 1 - (t > k)
                                      for p in x.get("prerequisites", []) if isinstance(p, int)]
            steps.pop(k)
            continue
        k += 1
    for s in steps:
        s["claim_ids"].sort(key=lambda i: pos[i])
    # id узлов
    ids, nodes = set(), []
    for n, s in enumerate(steps, 1):
        base = f"{prefix}.{slugify(s.get('slug') or s.get('title'))}"
        nid, j = base, 2
        while nid in ids:
            nid, j = f"{base}_{j}", j + 1
        ids.add(nid)
        prim = s.get("difficulty") if s.get("difficulty") in DIFF["types"] else "М"
        sec = s.get("secondary") if s.get("secondary") in DIFF["types"] and s.get("secondary") != prim else None
        nodes.append({"id": nid, "order": n, "title": s.get("title") or nid, "book_title": s.get("book_title"),
                      "difficulty": {"primary": prim, "secondary": sec}, "threshold": bool(s.get("threshold")),
                      "_claims": s["claim_ids"], "_prereq": [p for p in s.get("prerequisites", []) if isinstance(p, int) and 1 <= p < n]})
    log(f"B: {len(nodes)} узлов")
    return nodes


# ───────────────────────── C. наполнение узла ─────────────────────────

SYS_C = """Ты пишешь шаг учебного курса для новичка-цифровика. Используй ТОЛЬКО мысли автора из списка утверждений и текста главы.
Всё, чего нет в главе, помечай как гипотезу. Пиши по-русски, фразы до 20 слов, термины объясняй.
Зритель: {audience}
Верни JSON:
{{"why_here":"зачем этот шаг здесь, 1 фраза",
 "topic":"тема, 2–5 слов",
 "main":"суть: 2–3 фразы, первая — главное",
 "mechanism":"как это работает: причина → следствие, по мыслям автора",
 "practice":"что делает специалист на практике по главе",
 "nuances":"условия применимости, исключения, оговорки автора",
 "deeper":["ветка «глубже» для любопытных (формулы, выводы) — 0–3 пункта"],
 "procedure":["шаги процедуры, если автор описывает порядок действий, иначе []"],
 "numbers":["пороги, диапазоны, условия с числами автора, иначе []"],
 "glossary":[{{"term":"термин","definition":"определение простыми словами","en":"английский эквивалент или null"}}],
 "terms_introduced":["термины, которые вводит шаг"], "terms_used":["термины, которые шаг использует"],
 "misconception":{{"ref":"id из списка заблуждений или null","text":"частая ошибка новичка, которую шаг исправляет"}},
 "data_from_chapter":"как это знание выглядит в данных (вход, выход) — только по главе",
 "data_breaks":"где правило ломается в данных",
 "hypotheses":["выводы для данных/ML, которых нет в главе — это гипотезы"],
 "it_analogy":"ИТ-аналогия", "it_breaks_where":"где аналогия перестаёт работать (обязательно)",
 "now_practice":"как это делается сейчас (если глава описывает устаревшую технику) или null",
 "inefficiency":{{"inputs":"","manual_steps":"","judgment_points":"","time":"","typical_errors":""}} или null (только для процедур),
 "decision_on_data":{{"features":["признаки"],"rule":"правило автора","conclusion":"вывод","counterexample":"когда правило ошибается"}} или null (если автор даёт правило «признаки → вывод»),
 "video_scores":{{"time":0,"invisible":0,"scale":0,"counterintuitive":0,"cause_effect":0}}}}
video_scores: 0–2 каждый — есть ли изменение во времени, невидимое становится видимым, скачок масштаба, контринтуитивность, цепочка причина→следствие.
Заблуждения (используй ref только если подходит точно):
{misc}

Обозначения предметной области и ловушки распознавания (в пересказе и глоссарии пиши правильное обозначение):
{notation}"""


def pass_c(ch: Chapter, nodes, claims_by_tmp, units_by_id, pack):
    misc = "\n".join(f'{m["id"]}: {m["text"]}' for m in pack["misconceptions"]) or "(нет)"
    sys_c = SYS_C.format(audience=pack["audience"][:1500] or "сотрудник цифровой трансформации без профильного образования", misc=misc, notation=pack.get("notation") or "(нет)")

    def run(node):
        cl = [claims_by_tmp[i] for i in node["_claims"]]
        uids = sorted({c["unit_id"] for c in cl})
        text = "\n\n".join(f"[стр. {units_by_id[u].page_pdf}] {units_by_id[u].text}" for u in uids)
        user = (f"Шаг: {node['title']} (раздел: {node.get('book_title')})\nТип трудности: {node['difficulty']}\n\n"
                "Утверждения:\n" + "\n".join(f'- (стр.{c["page_pdf"]}) {c["text"]}' for c in cl) +
                f"\n\nТекст главы для этого шага:\n{text[:12000]}")
        return ask_json(sys_c, user, tier="bulk", tag=f"C:{ch.id}")

    with ThreadPoolExecutor(WORKERS) as ex:
        res = list(ex.map(run, nodes))
    log(f"C: наполнено {len(res)} узлов")
    return res


def F(text, hyp=False):
    d = {"text": text or "", "by_model": True}
    if hyp:
        d["hypothesis"] = True
    return d


# ───────────────────────── D. задания ─────────────────────────

SYS_D = """Ты составляешь задания курса по одному шагу. Ответ на каждое задание должен прямо следовать из утверждений шага.
Неверные варианты строй на типичных ошибках новичка (по возможности — на заблуждениях из списка, поле misconception).
Верни JSON {"tasks":[{"type":"single|multiple|order|gap|calc","kind":"recall|understanding|transfer|data",
 "claim_ids":["c12"],"prompt":"вопрос; для gap текст с пропусками {{1}}, {{2}}",
 "options":[{"id":"a","text":"...","misconception":"misc.… или null"}],
 "answer":"single: id; multiple: [id,…] (≥2); order: [id,…] в верном порядке; gap: [слово,…]; calc: число",
 "tolerance":null,"unit":null,"explanation":"одна фраза: почему так, со ссылкой «стр. N»"}]}
Типы: single — 3–4 варианта, один верный; multiple — 4–5 вариантов, 2–3 верных; order — 3–5 элементов процесса или ранжирования;
gap — 1–2 пропуска, слова-ответы дословно из утверждений; calc — только если в утверждениях есть числа и формула, ответ число с tolerance и unit.
Сделай ровно {n} задания(й) типов: {types}. Если тип невозможен по содержанию — замени на single или gap."""


def check_task(t: dict, valid_claims: set, node_text: str) -> bool:
    typ, ans = t.get("type"), t.get("answer")
    opts = {o.get("id") for o in t.get("options") or []}
    t["claim_ids"] = [c for c in t.get("claim_ids", []) if c in valid_claims]
    if not t["claim_ids"] or not t.get("prompt") or not t.get("explanation"):
        return False
    if typ == "single":
        return isinstance(ans, str) and ans in opts and len(opts) >= 2
    if typ == "multiple":
        return isinstance(ans, list) and len(ans) >= 2 and set(ans) <= opts and len(set(ans)) < len(opts)
    if typ == "order":
        return isinstance(ans, list) and len(ans) >= 3 and set(ans) == opts and len(ans) == len(opts)
    if typ == "gap":
        k = len(re.findall(r"\{\{\d+\}\}", t["prompt"]))
        return isinstance(ans, list) and k == len(ans) > 0 and all(norm(str(a)) in norm(node_text) for a in ans)
    if typ == "calc":
        try:
            t["answer"] = float(str(ans).replace(",", "."))
            t["tolerance"] = float(t.get("tolerance") or abs(t["answer"]) * 0.05 or 0.01)
            return True
        except ValueError:
            return False
    return False


def pass_d(ch, nodes, claims_by_tmp, pack):
    plan = ["single", "multiple", "gap", "order", "single", "calc", "multiple", "gap", "order"]
    misc = "\n".join(f'{m["id"]}: {m["text"]}' for m in pack["misconceptions"])

    def run(arg):
        k, node = arg
        cl = [claims_by_tmp[i] for i in node["_claims"]]
        has_num = any(re.search(r"\d", c["text"]) for c in cl)
        types = [plan[(2 * k) % len(plan)], plan[(2 * k + 1) % len(plan)]]
        types = [t if t != "calc" or has_num else "gap" for t in types]
        # не меньше MIN_TASKS заданий на главу даже при малом числе шагов
        need = -(-MIN_TASKS // max(1, len(nodes)))
        n = max(need, 2 if len(cl) >= 6 else 1)
        n = min(n, 5, max(1, len(cl)))
        types = (types + [plan[(2 * k + i) % len(plan)] for i in range(2, 6)])[:max(n, 2)]
        types = [t if t != "calc" or has_num else "gap" for t in types]
        user = (f"Шаг: {node['title']}\nУтверждения:\n" + "\n".join(f'{c["tmp_id"]} (стр.{c["page_pdf"]}): {c["text"]}' for c in cl)
                + f"\n\nЗаблуждения:\n{misc}")
        r = ask_json(SYS_D.replace("{n}", str(n)).replace("{types}", ", ".join(types[:n])), user, tier="bulk", tag=f"D:{ch.id}")
        text = " ".join(c["text"] + " " + c["quote"] for c in cl)
        ok = [t for t in r.get("tasks", []) if check_task(t, set(node["_claims"]), text)]
        return node, ok

    with ThreadPoolExecutor(WORKERS) as ex:
        res = list(ex.map(run, enumerate(nodes)))
    out = []
    for node, ts in res:
        for t in ts:
            t["step_id"] = node["id"]
            out.append(t)
    for i, t in enumerate(out, 1):
        t["id"] = f"t{i:02d}"
    log(f"D: {len(out)} заданий, типы {dict(Counter(t['type'] for t in out))}")
    return out


# ───────────────────────── сборка ─────────────────────────

def build(ch: Chapter, out_dir: Path, domain: str):
    t0, usd0 = time.time(), spent_usd()
    pack = domain_pack(domain)
    units = split_units(ch)
    by_id = {u.unit_id: u for u in units}
    prefix = slugify(ch.id.split("_")[0]) or "ch"

    claims, worked, book_tasks, skips = pass_a(ch, units)
    if not claims:
        sys.exit("Нет ни одного проверенного утверждения — остановка")
    claims_by_tmp = {c["tmp_id"]: c for c in claims}
    nodes = pass_b(ch, claims, prefix)
    fills = pass_c(ch, nodes, claims_by_tmp, by_id, pack)

    # финальные id утверждений и узлов
    tmp2id, unit2node = {}, defaultdict(set)
    for node in nodes:
        for n, tid in enumerate(node["_claims"], 1):
            tmp2id[tid] = f'{node["id"]}#c{n}'
            unit2node[claims_by_tmp[tid]["unit_id"]].add(node["id"])
    misc_ids = {m["id"] for m in pack["misconceptions"]}
    unit_order = {u.unit_id: i for i, u in enumerate(units)}

    def node_for_units(uids):
        for u in sorted(uids, key=lambda x: unit_order.get(x, 0)):
            if unit2node.get(u):
                return sorted(unit2node[u])[0]
        # ближайший предыдущий узел
        first = min((unit_order.get(u, 0) for u in uids), default=0)
        best = None
        for u in units:
            if unit_order[u.unit_id] <= first and unit2node.get(u.unit_id):
                best = sorted(unit2node[u.unit_id])[0]
        return best or nodes[0]["id"]

    node_by_id = {n["id"]: n for n in nodes}
    for w in worked:
        nid = node_for_units(w["unit_ids"])
        node_by_id[nid].setdefault("_worked", []).append(w)
        for u in w["unit_ids"]:
            unit2node[u].add(nid)
    for bt in book_tasks:
        nid = node_for_units([bt["unit_id"]])
        node_by_id[nid].setdefault("_tasks", []).append(bt)
        unit2node[bt["unit_id"]].add(nid)

    layer_nodes = []
    for node, f in zip(nodes, fills):
        cl = [claims_by_tmp[i] for i in node["_claims"]]
        misc = f.get("misconception") or {}
        ref = misc.get("ref") if misc.get("ref") in misc_ids else None
        links = [{"type": "prerequisite", "target": nodes[p - 1]["id"]} for p in node["_prereq"]]
        if ref:
            links.append({"type": "refutes_misconception", "target": ref})
        figs = sorted({(c["page_pdf"], by_id[c["unit_id"]].text[:120]) for c in cl if by_id[c["unit_id"]].kind == "figure"})
        ln = {
            "id": node["id"], "order": node["order"], "title": node["title"], "book_title": node.get("book_title"),
            "why_here": F(f.get("why_here")),
            "difficulty": node["difficulty"], "threshold": node["threshold"],
            "pages": sorted({c["page_pdf"] for c in cl}),
            "layers": {"topic": F(f.get("topic")), "main": F(f.get("main")), "mechanism": F(f.get("mechanism")),
                       "practice": F(f.get("practice")), "nuances": F(f.get("nuances")),
                       "deeper": [F(x) for x in f.get("deeper") or [] if x]},
            "claims": [{"id": tmp2id[c["tmp_id"]], "text": c["text"], "kind": c["kind"], "page_pdf": c["page_pdf"],
                        "page_print": None, "quote": c["quote"], "source_kind": c["source_kind"], "by_model": False,
                        "unit_id": c["unit_id"], "text_by_model": True} for c in cl],
            "links": links,
            "misconception": {"ref": ref, "text": misc.get("text") or "", "by_model": True} if misc.get("text") else None,
            "data_meaning": {"from_chapter": F(f.get("data_from_chapter")), "breaks": F(f.get("data_breaks")),
                             "hypotheses": [F(h, hyp=True) for h in f.get("hypotheses") or [] if h]},
            "it_analogy": {"text": f.get("it_analogy") or "", "breaks_where": f.get("it_breaks_where") or "", "by_model": True},
            "now_practice": F(f["now_practice"], hyp=True) if f.get("now_practice") else None,
            "inefficiency_lens": ({**f["inefficiency"], "by_model": True}
                                  if isinstance(f.get("inefficiency"), dict) and any(f["inefficiency"].values()) else None),
            "terms_introduced": f.get("terms_introduced") or [], "terms_used": f.get("terms_used") or [],
            "glossary": f.get("glossary") or [], "procedure": f.get("procedure") or [], "numbers": f.get("numbers") or [],
            "needs_scan": [{"page": p, "what": t} for p, t in figs],
            "decision_on_data": f["decision_on_data"] if isinstance(f.get("decision_on_data"), dict) and f["decision_on_data"].get("rule") else None,
            "worked_examples": [{"page_pdf": by_id[w["unit_ids"][0]].page_pdf if w["unit_ids"][0] in by_id else cl[0]["page_pdf"],
                                 "condition": w.get("condition", ""), "steps": w.get("steps") or [], "result": w.get("result", ""),
                                 "figure": w.get("figure")} for w in node.get("_worked", [])],
            "book_tasks": [{"number": str(b.get("number", "")), "page_pdf": by_id[b["unit_id"]].page_pdf,
                            "text": b.get("text", ""), "figure": b.get("figure")} for b in node.get("_tasks", [])],
            "video_scores": f.get("video_scores") or {},
        }
        layer_nodes.append(ln)

    layer = {"chapter": ch.id, "source": str(ch.meta.get("md")), "title": ch.meta.get("title"),
             "pdf_pages": [f"{a}-{b}" for a, b in ch.meta.get("pages", [])],
             "generated_by": f"layer.build; модели: {__import__('os').environ.get('LLM_MODEL_BULK')} / {__import__('os').environ.get('LLM_MODEL_STRONG')}",
             "nodes": layer_nodes}

    # D. задания
    for node in nodes:
        node["_claims_final"] = node["_claims"]
    tasks = pass_d(ch, nodes, claims_by_tmp, pack)
    for t in tasks:
        t["claim_ids"] = [tmp2id[c] for c in t["claim_ids"]]
    tasks_doc = {"chapter": ch.id, "tasks": tasks}

    # E. маршрутизация
    routes = []
    for ln in layer_nodes:
        vs = {k: int(max(0, min(2, (ln["video_scores"] or {}).get(k, 0) or 0)))
              for k in ("time", "invisible", "scale", "counterintuitive", "cause_effect")}
        prim = ln["difficulty"]["primary"]
        fmt = DIFF["lead_format"].get(prim, "текст")
        if fmt == "шортс" and sum(vs.values()) < 5:
            fmt = "текст"
        if ln["decision_on_data"] and fmt == "текст":
            fmt = "практикум на данных"
        routes.append({"node_id": ln["id"], "format": fmt, "secondary_format": "карточки", "video_scores": vs,
                       "hook_short": False, "template": DIFF["scene_template"].get(prim) if fmt in ("шортс", "длинное видео", "интерактив") else None,
                       "why": f"тип {prim}/{ln['difficulty']['secondary']}, видеоёмкость {sum(vs.values())}/10 → {fmt}"})
    routing = {"chapter": ch.id, "routes": routes}

    # E. покрытие
    rows = []
    for u in units:
        nids = sorted(unit2node.get(u.unit_id, []))
        reason = None
        if not u.in_scope:
            reason, nids = "вне главы: соседний раздел на той же странице", []
        elif u.kind == "running_header":
            reason = "колонтитул, номер страницы или обрывок распознавания"
        elif u.kind == "heading" and not nids:
            nxt = next((v for v in units[units.index(u) + 1:] if unit2node.get(v.unit_id)), None)
            nids = sorted(unit2node[nxt.unit_id]) if nxt else []
            reason = None if nids else "заголовок без содержания"
        elif not nids:
            i = units.index(u)
            prev = next((v for v in reversed(units[:i]) if v.in_scope and v.kind != "running_header" and unit2node.get(v.unit_id)), None)
            starts_lower = u.text[:1].islower()
            if prev and (starts_lower or u.kind == "formula"):
                # продолжение абзаца/задачи с прошлой страницы или формула, которую объясняет соседний абзац
                nids = sorted(unit2node[prev.unit_id])
            else:
                reason = skips.get(u.unit_id) or "НЕ ПОКРЫТО: утверждения не извлечены"
        rows.append({"unit_id": u.unit_id, "kind": COVERAGE_KIND[u.kind], "page_pdf": u.page_pdf,
                     "excerpt": u.excerpt, "node_ids": nids, "reason": reason})
    coverage = {"chapter": ch.id, "rows": rows}

    out_dir.mkdir(parents=True, exist_ok=True)
    for name, doc in [("layer", layer), ("tasks", tasks_doc), ("routing", routing), ("coverage", coverage)]:
        (out_dir / f"{name}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "units.json").write_text(json.dumps([u.to_dict() for u in units], ensure_ascii=False, indent=1), encoding="utf-8")

    if out_dir == ROOT / "runs" / ch.id:
        from layer.tables import attach
        attach(ch.id)   # таблицы книги целиком → book_tables шагов
    in_scope = [r for r in rows if r["reason"] is None or not r["reason"].startswith(("вне главы", "колонтитул"))]
    uncovered = [r for r in rows if (r["reason"] or "").startswith("НЕ ПОКРЫТО")]
    report = {"chapter": ch.id, "nodes": len(layer_nodes), "claims": len(claims), "tasks": len(tasks),
              "task_types": dict(Counter(t["type"] for t in tasks)), "units_in_scope": len(in_scope),
              "uncovered": len(uncovered), "coverage_pct": round(100 * (1 - len(uncovered) / max(1, len(in_scope))), 1),
              "formats": dict(Counter(r["format"] for r in routes)),
              "usd": round(spent_usd() - usd0, 3), "minutes": round((time.time() - t0) / 60, 1)}
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    log("ГОТОВО", json.dumps(report, ensure_ascii=False))
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("chapter")
    ap.add_argument("--md")
    ap.add_argument("--pages", help="например 12-30 или 9-14,20-30")
    ap.add_argument("--domain")
    ap.add_argument("--out")
    a = ap.parse_args()
    pages = [[int(x) for x in r.split("-")] for r in a.pages.split(",")] if a.pages else None
    ch = load_chapter(a.chapter, md_path=a.md, pages=pages)
    domain = a.domain or ch.meta.get("domain") or "geology"
    build(ch, Path(a.out) if a.out else ROOT / "runs" / a.chapter, domain)


if __name__ == "__main__":
    main()
