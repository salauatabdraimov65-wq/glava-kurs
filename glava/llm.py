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
import time
from pathlib import Path

from glava.chapter import ROOT, materials_dir

materials_dir()  # подгружает .env
CACHE = ROOT / "cache" / "llm"
LEDGER = ROOT / "cache" / "spend.jsonl"

# Цена за 1 млн токенов (вход, выход), USD. Переопределяется в .env: LLM_PRICE_BULK=0.25,2.0
DEFAULT_PRICE = {"bulk": (0.10, 0.50), "strong": (1.25, 10.0)}


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
    img_bytes = [Path(p).read_bytes() for p in (images or [])]
    key = hashlib.sha256(json.dumps([model, effort, system, user, [hashlib.sha256(b).hexdigest() for b in img_bytes]],
                                    ensure_ascii=False).encode()).hexdigest()
    cpath = CACHE / f"{key}.json"
    if cpath.exists():
        return json.loads(cpath.read_text(encoding="utf-8"))["data"]

    budget = float(os.environ.get("LLM_BUDGET_USD", "25"))
    if spent_usd() >= budget:
        raise BudgetExceeded(f"Потрачено {spent_usd():.2f} из {budget} USD — стоп")

    from openai import OpenAI
    client = OpenAI()
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
                f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": model, "tag": tag,
                                    "in": r.usage.prompt_tokens, "out": r.usage.completion_tokens,
                                    "usd": round(usd, 5)}, ensure_ascii=False) + "\n")
            CACHE.mkdir(parents=True, exist_ok=True)
            cpath.write_text(json.dumps({"model": model, "data": data}, ensure_ascii=False), encoding="utf-8")
            return data
        except (json.JSONDecodeError, KeyError) as e:
            last_err = e
        except Exception as e:  # сеть, лимиты
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"LLM не ответил корректным JSON: {last_err}")


if __name__ == "__main__":
    print(f"Потрачено: {spent_usd():.2f} USD из {os.environ.get('LLM_BUDGET_USD', '25')}")
