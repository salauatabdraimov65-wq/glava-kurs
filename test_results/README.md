# Результаты теста 26.11.2026

Всё собрано замороженным конвейером (тег `freeze`, коммит `f109d44`). Код, промпты и доменные пакеты после тега не менялись:
`git diff --stat freeze HEAD -- glava layer course video review domain config/difficulty.json` пуст.
Рабочие копии этих же глав лежат в `runs/<глава>/`, оттуда работает пересборка. Здесь — копия для сдачи.

## Что где лежит

| Что | Где |
| --- | --- |
| Учебный слой | `<глава>/layer.json` |
| Маршрутизация | `<глава>/routing.json` |
| Демо-курс | `<глава>/course/index.html` — открывается двойным кликом, сервер и интернет не нужны |
| Покрытие, задания, иллюстрации | `<глава>/coverage.json`, `tasks.json`, `illustrations.json` |
| Ролики | `test_slatt/video/test_long_river_dominated_delta/video.mp4` (16:9, 2:10), `shift_basarygin/video/shift_short_kick_warning_signs/video.mp4` (9:16, 0:59); паспорт ролика — `passport.json` рядом |
| Доменный пакет «бурение» и время его сборки, покрытие шаблонами | `shift_basarygin/template_coverage.md`; сам пакет — `domain/drilling/` |
| Журнал ручных правок | `manual_edits.csv` (только тестовые главы; полный журнал — `../manual_edits.csv`) |
| Время и деньги по этапам | `metrics.csv`, `<глава>/stage_times.json` |
| Ответ модуля проверки | `review_answer.json` |
| Сводка «что конвейер сделал сам» | `REPORT.html` |

## Главы

| Папка | Глава | Страницы (метки md) | Флаги прогона |
| --- | --- | --- | --- |
| `test_asquith` | Asquith, Krygowski, «Basic Well Log Analysis», гл. 7 | 126–136, 140–146 | `--lang en --start-at "Log Interpretation"` |
| `test_dake` | Dake, «Fundamentals of Reservoir Engineering», гл. 5 и начало §7.3 | 75–79, 87–89 | `--lang en --start-at "CHAPTER 5"` |
| `test_slatt` | Slatt, конец §9.3 и §9.4 | 410–423 | `--lang en --end-at "9.5 WAVE-DOMINATED DELTAS"` |
| `shift_basarygin` | Басарыгин и др., §4.1.1 и §4.2.14 (бурение) | 231–239, 312–318 | `--domain drilling --start-at "4.1.1. ПРИЗНАКИ ПРОЯВЛЕНИЙ"` |

Все главы прогнаны одной командой `uv run python -m glava.pipeline <папка> --md ../test/<файл>.md --pages … --no-video …`,
ролики — `… --from scene --video-node <узел> --video-format long|short`.

## Проверка
```
uv run python -m glava.check test_asquith test_dake test_slatt shift_basarygin
```
Все проверки зелёные: цитаты и страницы сверены с главой 840 из 840.
