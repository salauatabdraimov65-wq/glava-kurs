# video/ — станок «spec.json → ролик»

```
uv run python -m video.build runs/<chapter>/video/<vid>/spec.json            # 9:16 и 16:9
uv run python -m video.build spec.json --layout 9x16 --voice silero -o out/  # варианты
```
Рядом со spec.json появятся `video.mp4` (1080×1920), `video_16x9.mp4`, `frames/F01..png` (ключевой кадр каждой фразы),
`frames_16x9/`, `voice.wav`, `passport.json` (по `schemas/passport.schema.json`).

## Формат spec.json
Как у организаторов: `template`, `params`, `beats[]` (`say`, `claim_id`, `on_screen`, `label`, `frame_class`, `action`, `pause`),
`plaque`, `invariants`, `assumptions`, `exaggerations`, `prediction_question`, `transfer_question`, `data_it_bridge`,
`physics_checklist`, `listen_check`. Необязательно: `layer` — путь к layer.json (иначе ищется в runs/<chapter>/ и samples/).

- `on_screen`: ключевые слова в `*звёздочках*` выделяются в заголовке.
- Последняя фраза показывается карточкой «вопрос на перенос»; фраза без `say` с `pause` — пауза на предсказание (сцена
  приглушается, идёт таймер). Первая фраза — крючок: спуск с поверхности со счётчиком глубины.

### Шаблон `well_tool_curve`
`params`: `depth_top`, `depth_bottom`, `depth_tick`, `tool_start`, `tool_resolution_m` (окно прибора), `bore_width`,
`layers[]` (`top`, `bottom`, `lith`, `label`, `props{}`, `mud_cake`, `invasion_width`, `fluid?`, `pattern?`),
`curves[]` (`id`, `key` — цвет из стайлгайда, `prop`, `label`, `unit`, `min`, `max`, `track?`, `dashed?`, `scale?`).

`action`: `tool_to`, `tool_jump`, `mark` {top, bottom, label}, `clear_marks`, `show_mud_cake`, `show_invasion`,
`show_curves` [id], `hide_curves` [id], `callout` [{target: tool|cake|invasion|bore|curve:<id>|{depth}|{layer}, text}],
`readout` (id кривой — плашка «≈ значение» из модели).

Кривая вычисляется из `props` тех же слоёв с окном прибора; конец кривой всегда на глубине прибора.

## Голос
Настройки в `video/settings.json` или переменных `VIDEO_VOICE_ENGINE=edge|silero`, `VIDEO_EDGE_VOICE`, `VIDEO_SILERO_SPEAKER`.
Основной — edge-tts (наружу уходит только текст). Запасной — Silero локально: `uv sync --extra silero`
(первый запуск скачивает модель в `cache/torch_hub`). Слова, где edge-tts ошибается в ударении, внесите в
`voice.silero_words` — такие фразы озвучит Silero. Аббревиатуры и ударения — из `domain/<домен>/pronunciation.md`.
Темп подгоняется к 110–150 сл/мин, громкость −16 LUFS, длительность кадра = длительность фразы.

## Узоры пород
Берутся из `params.layers[].pattern` или `styleguide.json → lithology.<lith>.pattern`
(`dots`, `lines`, `brick`, `rhomb`, `hatch`, `cross`, `grain`, `none`). Без поля — мелкое зерно.
