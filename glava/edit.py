"""Записать ручную правку: строка в manual_edits.csv + отдельный коммит. Журнал всегда совпадает с историей git.

Сначала исправьте файл (руками или через ассистента), затем:
    uv run python -m glava.edit --chapter <id> --file runs/<id>/layer.json --what "что изменено" \
        --minutes 6 --people 1 --run 2 --why "почему понадобилась правка"
После правки факта в layer.json пересоберите зависящее:
    uv run python -m glava.pipeline <id> --from illustrate
"""
from __future__ import annotations

import argparse
import csv
import subprocess
import time

from glava.chapter import ROOT


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chapter", required=True)
    ap.add_argument("--file", required=True, action="append", help="можно несколько раз")
    ap.add_argument("--what", required=True)
    ap.add_argument("--minutes", required=True, type=float, help="человеко-минуты: 2 человека по 10 мин = 20")
    ap.add_argument("--people", type=int, default=1)
    ap.add_argument("--run", type=int, default=1, help="номер прогона конвейера, результат которого правили")
    ap.add_argument("--why", required=True)
    a = ap.parse_args()

    path = ROOT / "manual_edits.csv"
    with path.open("a", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow([time.strftime("%Y-%m-%dT%H:%M"), a.chapter, ";".join(a.file), a.what,
                                a.minutes, a.people, a.run, a.why])
    files = [*a.file, "manual_edits.csv"]
    subprocess.run(["git", "add", *files], cwd=ROOT, check=True)
    msg = f"Ручная правка [{a.chapter}]: {a.what} ({a.minutes} чел.-мин)\n\nПричина: {a.why}\nПрогон: {a.run}"
    subprocess.run(["git", "commit", "-m", msg], cwd=ROOT, check=True)
    print("Записано в manual_edits.csv и закоммичено. Не забудьте: git push")


if __name__ == "__main__":
    main()
