"""Единственная точка вызова языковой модели. Кэш + учёт денег + стоп по бюджету.

    from glava.llm import ask_json
    data = ask_json(system="...", user="...", tier="bulk")          # -> dict
    data = ask_json(system, user, tier="strong", images=[png_path])  # картинки для кадров/сканов

Модель задаётся в .env (LLM_MODEL_BULK / LLM_MODEL_STRONG), поставщик заменяем:
весь код ходит только через этот файл. Повторный вызов с теми же аргументами берётся из cache/ бесплатно.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from glava.chapter import ROOT, materials_dir

materials_dir()  # подгружает .env
CACHE = ROOT / "cache" / "llm"
# ответы модели, сохранённые в репозитории (только чтение): пересборка обучающих и контрольных глав
# после правки факта идёт без ключа OpenAI — конвейер берёт уже полученные ответы
REPLAY = ROOT / "llm_cache"
LEDGER = ROOT / "cache" / "spend.jsonl"

# Цена за 1 млн токенов (вход, выход), USD. Переопределяется в .env: LLM_PRICE_BULK=0.25,2.0
DEFAULT_PRICE = {"bulk": (0.10, 0.50), "strong": (1.0, 5.0)}


class BudgetExceeded(RuntimeError):
    pass


def _price(tier: str) -> tuple[float, float]:
    v = os.environ.get(f"LLM_PRICE_{tier.upper()}")
    return tuple(map(float, v.split(","))) if v else DEFAULT_PRICE[tier]


def spent_usd() -> float:
    if not LEDGER.exists():
        return 0.0
    return sum(json.loads(l)["usd"] for l in LEDGER.read_text(encoding="utf-8").splitlines() if l.strip())


def _model(tier: str) -> str:
    m = os.environ.get(f"LLM_MODEL_{tier.upper()}")
    if not m:
        raise RuntimeError(f"Не задана LLM_MODEL_{tier.upper()} в .env")
    return m


def ask_json(system: str, user: str, tier: str = "bulk", images: list[str | Path] | None = None,
             tag: str = "", max_retries: int = 3, effort: str | None = None) -> dict:
    model = _model(tier)
    if "json" not in (system + user).lower():   # требование API для response_format=json_object
        system = system + "\nОтвет — строго JSON."
    img_bytes = [Path(p).read_bytes() for p in (images or [])]
    key = hashlib.sha256(json.dumps([model, effort, system, user, [hashlib.sha256(b).hexdigest() for b in img_bytes]],
                                    ensure_ascii=False).encode()).hexdigest()
    cpath = CACHE / f"{key}.json"
    for p in (cpath, REPLAY / f"{key}.json"):
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))["data"]
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError(f"Нужен новый ответ модели ({tag or tier}), а в .env нет OPENAI_API_KEY. "
                           "Готовые ответы для глав из репозитория лежат в llm_cache/; для новой главы или правки, "
                           "меняющей запрос к модели, впишите ключ в .env.")

    budget = float(os.environ.get("LLM_BUDGET_USD", "25"))
    if spent_usd() >= budget:
        raise BudgetExceeded(f"Потрачено {spent_usd():.2f} из {budget} USD — стоп")

    from openai import OpenAI
    # предел ожидания одного запроса: зависший запрос не должен держать конвейер (по умолчанию клиент ждёт до 10 мин
    # и сам повторяет) — повторы делаем сами, с паузой и сообщением в консоль
    timeout = float(os.environ.get(f"LLM_TIMEOUT_{tier.upper()}") or (150 if tier == "bulk" else 300))
    client = OpenAI(timeout=timeout, max_retries=0)
    content = [{"type": "text", "text": user}]
    for b in img_bytes:
        content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(b).decode()}})
    messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]

    last_err = None
    for attempt in range(max_retries):
        try:
            kw = {}
            eff = effort or os.environ.get(f"LLM_EFFORT_{tier.upper()}") or ("low" if tier == "bulk" else "medium")
            if model.startswith(("gpt-5", "gpt-6", "o")):
                kw["reasoning_effort"] = eff
            r = client.chat.completions.create(model=model, messages=messages,
                                               response_format={"type": "json_object"}, **kw)
            data = json.loads(r.choices[0].message.content)
            pin, pout = _price(tier)
            usd = (r.usage.prompt_tokens * pin + r.usage.completion_tokens * pout) / 1e6
            LEDGER.parent.mkdir(parents=True, exist_ok=True)
            with LEDGER.open("a", encoding="utf-8") as f:
                # глава и этап — из окружения (их ставит glava.pipeline): по ним glava.metrics строит metrics.csv
                f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": model, "tag": tag,
                                    "chapter": os.environ.get("GLAVA_CHAPTER"), "stage": os.environ.get("GLAVA_STAGE"),
                                    "in": r.usage.prompt_tokens, "out": r.usage.completion_tokens,
                                    "usd": round(usd, 5)}, ensure_ascii=False) + "\n")
            CACHE.mkdir(parents=True, exist_ok=True)
            cpath.write_text(json.dumps({"model": model, "data": data}, ensure_ascii=False), encoding="utf-8")
            return data
        except (json.JSONDecodeError, KeyError) as e:
            last_err = e
        except Exception as e:  # сеть, лимиты, предел ожидания
            last_err = e
            print(f"⚠ модель: {type(e).__name__} ({str(e)[:80]}) — повтор {attempt + 2}/{max_retries} [{tag}]",
                  file=sys.stderr, flush=True)
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"LLM не ответил корректным JSON: {last_err}")


if __name__ == "__main__":
    print(f"Потрачено: {spent_usd():.2f} USD из {os.environ.get('LLM_BUDGET_USD', '25')}")
