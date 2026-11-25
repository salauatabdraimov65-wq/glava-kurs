# Тестовый день: порядок действий

Код заморожен тегом `freeze`. **Код не меняем.** Любая правка результата — через `glava.edit` (журнал + коммит). Цель — не больше 30 человеко-минут правок на главу.

## 0. До выдачи теста (все)
```
git checkout main && git pull && git log -1 --oneline     # на теге freeze или после него без изменений кода
uv sync
uv run python -m glava.llm                                # остаток бюджета
uv run python -m glava.check --all                        # всё зелёное на обучении
```
Ключ OpenAI есть у Лида и Друга 1 → эти два компьютера гоняют главы параллельно.

## 1. Что выдадут
- 3 главы-двойника (другие авторы, школа, возможно **английский**)
- 1 глава-сдвиг (другой домен нефтегаза, скорее всего **бурение**)
- узел для ролика (организаторы назначат, один на тестовую выборку)
- пакет с закладками по тестовой главе (`package.json` + кадры) без ключа

Положить главы в `../test/` (рядом с `книги/`). Для каждой: открыть md, найти первую и последнюю метку `<!-- page N -->` и заголовок, с которого глава начинается.

## 2. Прогон главы (≈3 мин без видео)
```
uv run python -m glava.pipeline test_1 --md ../test/<файл>.md --pages <N1>-<N2> \
    --title "Автор. Название главы" --start-at "<начало первого заголовка>" --end-at "<заголовок следующего раздела>" --no-video
uv run python -m glava.check test_1
```
- Английская глава: добавить `--lang en`.
- Сдвиг (бурение): сначала шаг 4, потом прогон с `--domain drilling`.
- `--start-at/--end-at` нужны, только если на первой/последней странице есть куски соседних разделов.

Распределение: **Лид** — test_1, test_2; **Друг 1** — test_3, сдвиг. **Друг 3** открывает каждый курс на телефоне и проходит 2 шага. **Друг 2** готовит ролик (шаг 3).

## 3. Ролик на назначенный узел (≈10 мин)
Найти id узла в `runs/<id>/layer.json` по названию, затем:
```
uv run python -m glava.pipeline test_1 --from scene --video-node <node_id> [--video-format short|long]
```
Друг 2 смотрит ролик со звуком и без. Ошибка в факте → правка `runs/<id>/layer.json` + `glava.edit` + `--from illustrate`.

## 4. Сдвиг: доменный пакет для нового домена (засечь время!)
```
mkdir domain/drilling
cp domain/geology/{styleguide.json,physics_checklist.json,misconceptions.json,pronunciation.md,audience.md} domain/drilling/
```
Поправить под домен (что не относится — убрать, добавить заблуждения и обозначения бурения в `notation.md`), прогнать главу с `--domain drilling`, затем `uv run python -m layer.domain_pack drilling`. Время записать в `domain/drilling/README.md`: сколько минут, какие шаблоны сцен подошли без изменений.

## 5. Пакет с закладками (Друг 1)
```
uv run python -m review.run ../test/package.json --chapter-md ../test/<глава>.md -o review/out/answer_test.json
```
Проверить, что ответ проходит `schemas/review_output.schema.json` и замечаний не больше ~20.

## 6. Ручные правки — только так
```
uv run python -m glava.edit --chapter test_1 --file runs/test_1/layer.json --what "что исправлено" --minutes 6 --people 1 --run 1 --why "почему"
uv run python -m glava.pipeline test_1 --from illustrate      # пересобрать зависящее
```
Правим только явные ошибки фактов. Не «улучшаем» текст.

## 7. Сдача
`git add runs/ manual_edits.csv metrics.csv domain/ && git commit && git push` → тег `test-submit`. Проверить, что курс каждой главы открывается двойным кликом.
