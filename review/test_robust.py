"""Тесты устойчивости модуля проверки: ответ создаётся и проходит схему при любых сбоях. Без сети и без денег (модель подменена).

    uv run python -m review.test_robust
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import traceback
from pathlib import Path

import jsonschema

from glava.llm import BudgetExceeded

from . import llm_check, run

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "schemas" / "review_output.schema.json").read_text(encoding="utf-8"))
CHAPTER = ("<!-- page 1 -->\nПри прочих условиях метод обычно применяют только к чистым пластам. Порог составляет 5 мД для района А.\n"
           "<!-- page 2 -->\nСопротивление растёт с глубиной в нефтеносной части, а в водоносной падает. Условие применимости: пласт однородный.\n")
os.environ["REVIEW_NO_HARD_EXIT"] = "1"


def sandbox():
    d = Path(tempfile.mkdtemp(prefix="review_test_"))
    (d / "ch.md").write_text(CHAPTER, encoding="utf-8")
    return d


def good_claim(i=1, text="Метод всегда применяют к любым пластам.", page=1, quote="метод обычно применяют только к чистым пластам"):
    return {"id": f"C{i:03d}", "type": "claim", "text": text, "kind": "observation", "page_pdf": page, "quote": quote}


def go(pkg, extra=None, chapter=True, raw=None):
    """Запуск review.run на пакете; возвращает ответ и проверяет его по схеме."""
    d = sandbox()
    p = d / "package.json"
    p.write_text(raw if raw is not None else json.dumps(pkg, ensure_ascii=False), encoding="utf-8")
    out = d / "sub" / "answer.json"  # папки sub нет: модуль обязан создать
    args = [str(p), "-o", str(out)] + (["--chapter-md", str(d / "ch.md")] if chapter else []) + (extra or [])
    rc = run.main(args)
    assert rc == 0, f"код возврата {rc}"
    answer = json.loads(out.read_text(encoding="utf-8"))
    jsonschema.validate(answer, SCHEMA)
    return answer


def base_pkg(items):
    return {"package_id": "t", "chapter": "t", "chapter_file": "ch.md", "items": items, "scenes": [], "frames": []}


def with_model(fake):
    class Ctx:
        def __enter__(self):
            self.old = llm_check.ask_json
            llm_check.ask_json = fake

        def __exit__(self, *a):
            llm_check.ask_json = self.old
    return Ctx()


def fake_ok(system, user, tier="bulk", images=None, tag="", max_retries=3, effort=None):
    return {"has_error": False}


def fake_flag(system, user, tier="bulk", images=None, tag="", max_retries=3, effort=None):
    return {"has_error": True, "category": "overgeneralization", "severity": "significant", "problem": "p", "fix": "f", "page_pdf": 1, "quote": "метод обычно применяют", "confidence": 0.9}


# ---------- тесты ----------

def test_empty_and_missing_fields():
    items = [
        {"id": "C001", "type": "claim"},                                   # нет text, quote, page_pdf
        {"id": "C002", "type": "claim", "text": None, "quote": None, "page_pdf": None},
        {"id": "C003", "type": "claim", "text": "", "quote": "", "page_pdf": "abc"},
        {"id": "C004", "type": "analogy", "text": "x"},                    # нет breaks_where
        {"id": "C005", "type": "script_line", "text": "y", "claim_ref": "НЕТ"},
        {"id": "C006", "type": "scene_fact", "text": "z", "video_id": "nope"},
        {"id": "C007", "type": "ВЫМЫШЛЕННЫЙ", "text": 5},                  # неизвестный тип и текст не строка
        {"type": "claim", "text": "без id"}, None, "строка", 7,            # мусор вместо элементов
        good_claim(8),
    ]
    pkg = {"package_id": "t", "chapter_file": "ch.md", "items": items, "frames": [{"id": "F01"}, {"nofile": 1}, None]}  # нет scenes, chapter
    for extra in (["--no-llm"], []):
        with with_model(fake_ok):
            go(pkg, extra)
    with with_model(fake_flag):
        a = go(pkg, [])
    assert all(r["target"] for r in a["remarks"])


def test_package_without_items_and_without_fields():
    for pkg in ({}, {"package_id": "x"}, {"items": None}, {"items": []}, []):
        with with_model(fake_ok):
            go(pkg)


def test_api_error_falls_back_to_code():
    def boom(*a, **k):
        raise RuntimeError("API недоступен")
    with with_model(boom):
        a = go(base_pkg([good_claim()]))
    assert a["remarks"] and a["remarks"][0]["target"] == "C001", a  # проверки кодом остались


def test_budget_exhausted():
    def over(*a, **k):
        raise BudgetExceeded("Потрачено 25 из 25 USD — стоп")
    with with_model(over):
        a = go(base_pkg([good_claim()]))
    assert a["remarks"], a


def test_missing_api_key():
    def nokey(*a, **k):
        raise RuntimeError("Не задана LLM_MODEL_BULK в .env")
    with with_model(nokey):
        go(base_pkg([good_claim()]))


def test_garbage_model_output():
    outs = iter([])

    def junk(system, user, tier="bulk", images=None, tag="", max_retries=3, effort=None):
        return [1, 2, 3] if "C001" in tag else {"has_error": True, "category": "foo", "severity": 5, "page_pdf": "abc", "confidence": "x",
                                                  "fix": ["не", "строка"], "problem": None, "quote": 7}
    with with_model(junk):
        go(base_pkg([good_claim(1), good_claim(2), good_claim(3, text="Сопротивление падает с глубиной в нефтеносной части.", page=2,
                                                          quote="Сопротивление растёт с глубиной в нефтеносной части")]))


def test_partial_model_failure():
    n = {"i": 0}

    def flaky(system, user, tier="bulk", images=None, tag="", max_retries=3, effort=None):
        n["i"] += 1
        if n["i"] % 2:
            raise TimeoutError("сеть")
        return {"has_error": False}
    with with_model(flaky):
        go(base_pkg([good_claim(i) for i in range(1, 20)]))


def test_missing_chapter():
    a = go({"package_id": "t", "chapter": "нет_такой_главы", "chapter_file": "nope.md", "items": [good_claim()], "scenes": [], "frames": []}, chapter=False)
    assert a["remarks"] == [] and a["package_id"] == "t"


def test_empty_chapter_file():
    d = sandbox()
    (d / "ch.md").write_text("", encoding="utf-8")
    p = d / "package.json"
    p.write_text(json.dumps(base_pkg([good_claim()])), encoding="utf-8")
    out = d / "a.json"
    with with_model(fake_ok):
        assert run.main([str(p), "--chapter-md", str(d / "ch.md"), "-o", str(out)]) == 0
    jsonschema.validate(json.loads(out.read_text(encoding="utf-8")), SCHEMA)


def test_broken_package_file():
    a = go(None, raw="это не json {{{")
    assert a == {"package_id": "unknown", "remarks": []}


def test_300_items_in_time():
    items = []
    for i in range(1, 301):
        items.append(good_claim(i) if i % 3 else good_claim(i, text="Порог составляет 50 мД для района А.", quote="Порог составляет 5 мД для района А."))

    def slow(system, user, tier="bulk", images=None, tag="", max_retries=3, effort=None):
        time.sleep(0.02)
        return fake_flag(system, user, tier) if hash(tag) % 3 == 0 else {"has_error": False}
    t = time.time()
    with with_model(slow):
        a = go(base_pkg(items), ["--workers", "8"])
    assert time.time() - t < 120, "300 элементов обработаны слишком долго"
    assert len(a["remarks"]) <= run.MAX_REMARKS


def test_deadline_does_not_hang():
    def hang(system, user, tier="bulk", images=None, tag="", max_retries=3, effort=None):
        time.sleep(3)
        return {"has_error": False}
    t = time.time()
    with with_model(hang):
        go(base_pkg([good_claim(i) for i in range(1, 25)]), ["--workers", "2", "--deadline-s", "2"])
    assert time.time() - t < 40, "дедлайн не сработал"


def test_response_always_valid_with_huge_text():
    big = "слово " * 50000
    with with_model(fake_ok):
        go(base_pkg([good_claim(1, text=big, quote=big[:200])]))


ALL = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]


def main() -> int:
    fails = 0
    for t in ALL:
        t0 = time.time()
        try:
            t()
            print(f"OK    {t.__name__} ({time.time() - t0:.1f} с)")
        except Exception:
            fails += 1
            print(f"FAIL  {t.__name__}")
            traceback.print_exc()
    print(f"\nтестов: {len(ALL)}, упало: {fails}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
