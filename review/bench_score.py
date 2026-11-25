"""Оценка ответа по своему ключу с разбивкой по типам элементов (те же правила, что в score_review.py организаторов).

    uv run python -m review.bench_score <answer.json> <key.json> [<answer2> <key2> ...]
"""
from __future__ import annotations

import json
import sys

W = {"critical": 3, "significant": 2, "minor": 1}
NAMES = {"C": "утверждения", "A": "аналогии", "L": "строки сценария", "S": "факты сцены", "F": "кадры"}


def score(answer: dict, key: dict) -> dict:
    errs = {e["target"]: e for e in key["errors"]}
    seen, rem = set(), []
    for r in answer.get("remarks", []):
        if r.get("target") not in seen:
            seen.add(r.get("target"))
            rem.append(r)
    stat = {}
    def st(t):
        return stat.setdefault(NAMES.get(t[:1], "другое"), {"n": 0, "w": 0, "got": 0.0, "full": 0, "half": 0, "fa": 0})
    for e in errs.values():
        s = st(e["target"]); s["n"] += 1; s["w"] += W[e["severity"]]
    for r in rem:
        e = errs.get(r.get("target"))
        if e is None:
            st(r.get("target") or "?")["fa"] += 1
            continue
        s = st(e["target"])
        if r.get("page_pdf") in e["pages"] and str(r.get("fix") or "").strip():
            s["got"] += W[e["severity"]]; s["full"] += 1
        else:
            s["got"] += W[e["severity"]] / 2; s["half"] += 1
    return stat


def main(argv):
    pairs = list(zip(argv[0::2], argv[1::2]))
    total = {}
    for a, k in pairs:
        stat = score(json.load(open(a, encoding="utf-8")), json.load(open(k, encoding="utf-8")))
        for name, s in stat.items():
            t = total.setdefault(name, {"n": 0, "w": 0, "got": 0.0, "full": 0, "half": 0, "fa": 0})
            for f in s:
                t[f] += s[f]
        print(a)
        for name, s in stat.items():
            print(f"  {name:16} ошибок {s['n']:2}  полностью {s['full']:2}  наполовину {s['half']}  доля по весу {s['got'] / s['w']:.0%}  ложных {s['fa']}" if s["w"] else f"  {name:16} ошибок 0  ложных {s['fa']}")
    print("ИТОГО по всем ответам:")
    tw = tg = 0
    for name, s in total.items():
        tw += s["w"]; tg += s["got"]
        print(f"  {name:16} ошибок {s['n']:2}  полностью {s['full']:2}  наполовину {s['half']}  доля по весу {(s['got'] / s['w']) if s['w'] else 0:.0%}  ложных {s['fa']}")
    print(f"  ВСЕ ТИПЫ: доля по весу {tg / tw:.0%}, ложных тревог {sum(s['fa'] for s in total.values())}")


if __name__ == "__main__":
    main(sys.argv[1:])
