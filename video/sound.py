"""Звук ролика без внешних файлов: тихая музыкальная подложка и эффекты, привязанные к событиям кадра.

Правила (hyperframes sound.md, vizavi-motion): у каждого эффекта есть причина на экране; повторяющиеся звуки
чередуют варианты; пик эффекта — на кадр позже картинки; один подъём с ударом — на ответе после паузы;
музыка под голосом примерно на 0,14 (≈ −17 дБ), между фразами чуть громче; итог −16 LUFS (ТЗ).
Важное в подложке лежит выше 150 Гц — иначе на телефоне её не слышно.
"""
from __future__ import annotations

import numpy as np

SR = 48000
FRAME = 1 / 30


def _lp(x: np.ndarray, k: int) -> np.ndarray:
    if k <= 1:
        return x
    return np.convolve(x, np.ones(k) / k, "same")


def _env(n: int, attack: float, release: float) -> np.ndarray:
    t = np.arange(n) / SR
    return np.minimum(1, t / max(attack, 1e-4)) * np.exp(-np.maximum(0, t - attack) / max(release, 1e-4))


def whoosh(variant: int = 0, dur: float = 0.55) -> np.ndarray:
    """Шорох смены сцены: шум, проходящий фильтром снизу вверх и обратно."""
    rng = np.random.default_rng(100 + variant)
    n = int(dur * SR)
    x = rng.standard_normal(n)
    out = np.zeros(n)
    seg = 8
    for i in range(seg):  # кусочно меняем ширину сглаживания = «развёртка» фильтра
        a, b = i * n // seg, (i + 1) * n // seg
        u = i / (seg - 1)
        k = int(60 - 50 * np.sin(np.pi * u) * (0.8 + 0.2 * variant % 2))
        out[a:b] = _lp(x, max(2, k))[a:b]
    env = np.sin(np.linspace(0, np.pi, n)) ** (1.6 + 0.4 * (variant % 3))
    return out * env / (np.abs(out).max() + 1e-9)


def pop(variant: int = 0) -> np.ndarray:
    """Мягкий щелчок появления плашки/рамки: короткий шумовой удар с телом 500–900 Гц (не тон, не «динь»)."""
    rng = np.random.default_rng(200 + variant)
    n = int(0.09 * SR)
    noise = _lp(rng.standard_normal(n), 6 + variant * 2)
    t = np.arange(n) / SR
    body = np.sin(2 * np.pi * (650 + 120 * variant) * t * (1 - 0.4 * t / t[-1]))
    x = (0.65 * noise + 0.5 * body) * _env(n, 0.002, 0.018)
    return x / (np.abs(x).max() + 1e-9)


def impact() -> np.ndarray:
    """Удар на ответе: низкое тело + шумовой хвост (энергия и выше 150 Гц — слышно на телефоне)."""
    rng = np.random.default_rng(7)
    n = int(0.9 * SR)
    t = np.arange(n) / SR
    low = np.sin(2 * np.pi * (95 - 45 * t) * t) * np.exp(-t * 5)
    mid = _lp(rng.standard_normal(n), 10) * np.exp(-t * 9)
    x = 0.8 * low + 0.45 * mid
    return x / (np.abs(x).max() + 1e-9)


def riser(dur: float = 1.6) -> np.ndarray:
    rng = np.random.default_rng(9)
    n = int(dur * SR)
    t = np.linspace(0, 1, n)
    x = _lp(rng.standard_normal(n), 30)
    hi = rng.standard_normal(n) - _lp(rng.standard_normal(n), 30)
    y = (x * (1 - t) + hi * t * 0.6) * t ** 2.2
    return y / (np.abs(y).max() + 1e-9)


def rumble(dur: float) -> np.ndarray:
    """Гул спуска по стволу (крючок): низкий шум + шорох, нарастает."""
    rng = np.random.default_rng(11)
    n = int(dur * SR)
    t = np.linspace(0, 1, n)
    x = 0.7 * _lp(rng.standard_normal(n), 120) * 6 + 0.3 * _lp(rng.standard_normal(n), 12)
    y = x * (0.25 + 0.75 * t) * np.minimum(1, (1 - t) * 8 + 0.3)
    return y / (np.abs(y).max() + 1e-9)


def whirr(speed: np.ndarray) -> np.ndarray:
    """Кабель и прибор в движении: тихий механический шорох, громкость — по скорости прибора."""
    rng = np.random.default_rng(13)
    n = len(speed)
    x = _lp(rng.standard_normal(n), 14)
    t = np.arange(n) / SR
    texture = 0.75 + 0.25 * np.sin(2 * np.pi * 23 * t)  # шаг катушки лебёдки
    s = speed / (speed.max() + 1e-9)
    return x * texture * _lp(s, int(0.08 * SR))


def music(total: float, seed: int = 4) -> np.ndarray:
    """Тихая подложка: мягкие аккорды (ля минор — фа — до — соль) с медленной атакой и
    негромкое арпеджио «щипком» в середине частот. 70 уд/мин, смена аккорда раз в такт."""
    n = int(total * SR)
    t = np.arange(n) / SR
    out = np.zeros(n)
    chords = [(57, 60, 64, 69), (53, 57, 60, 65), (48, 55, 60, 64), (55, 59, 62, 67)]
    bar = 60 / 70 * 4
    for i in range(int(total / bar) + 2):
        a = int(i * bar * SR)
        if a >= n:
            break
        b = min(n, int((i + 1) * bar * SR + 1.2 * SR))
        tt = t[a:b] - i * bar
        env = np.minimum(1, tt / 1.6) * np.exp(-np.maximum(0, tt - bar) * 2.5)
        for j, m in enumerate(chords[i % 4]):
            f = 440 * 2 ** ((m - 69) / 12)
            det = 1 + 0.0025 * (j - 1.5)
            out[a:b] += (np.sin(2 * np.pi * f * det * tt) + 0.35 * np.sin(2 * np.pi * 2 * f * tt)) * env * 0.22
        # арпеджио: восьмые, тихо
        eighth = bar / 8
        for k in range(8):
            m = chords[i % 4][k % 4] + 12
            f = 440 * 2 ** ((m - 69) / 12)
            s0 = int((i * bar + k * eighth) * SR)
            L = int(0.5 * SR)
            if s0 >= n:
                break
            tt2 = np.arange(min(L, n - s0)) / SR
            out[s0:s0 + len(tt2)] += np.sin(2 * np.pi * f * tt2) * np.exp(-tt2 * 7) * 0.12 * (0.7 + 0.3 * (k % 2 == 0))
    out = _lp(out, 6)
    fade = np.minimum(1, np.minimum(t / 1.5, (total - t) / 2.0))
    return out * fade / (np.abs(out).max() + 1e-9)


def design(segs, renderer, total: float) -> np.ndarray:
    """Эффекты по событиям кадра (те же времена, что у рендера)."""
    n = int(total * SR) + SR
    out = np.zeros(n)

    def put(t, sig, gain):
        i = int((t + FRAME) * SR)  # звук на кадр позже картинки
        if 0 <= i < n:
            j = min(n, i + len(sig))
            out[i:j] += sig[: j - i] * gain

    wv = pv = 0
    # крючок: гул спуска и шорох перехода в разрез
    intro = getattr(renderer, "intro_dur", 0) or 0
    if intro > 0:
        put(0.0, rumble(intro), 0.5)
        put(intro - 0.25, whoosh(0, 0.6), 0.55)
    prev_cap = None
    pause_idx = [i for i, s in enumerate(segs) if not s.beat.say and s.beat.pause]
    answer_idx = (pause_idx[0] + 1) if pause_idx and pause_idx[0] + 1 < len(segs) else None
    for i, s in enumerate(segs):
        a = s.beat.action
        cap = s.beat.on_screen
        if i > 0 and cap and cap != prev_cap:
            put(s.start - 0.12, whoosh(1 + wv % 3, 0.4), 0.22)
            wv += 1
        prev_cap = cap or prev_cap
        if a.get("mark") or a.get("readout") or a.get("callout") or a.get("highlight_layer") is not None \
                or a.get("mark_x") is not None:
            put(s.start + 0.05, pop(pv % 3), 0.32)
            pv += 1
        if a.get("tool_jump") is not None or a.get("show_view"):
            put(s.start - 0.1, whoosh(2 + wv % 2, 0.45), 0.3)
            wv += 1
        if i == answer_idx:
            put(max(0, s.start - 1.6), riser(1.6), 0.28)
            put(s.start, impact(), 0.5)
        if i == len(segs) - 1:
            put(s.start - 0.15, whoosh(4, 0.7), 0.35)
    # движение прибора/времени модели: шорох по скорости
    sp = np.zeros(n)
    step = 0.02
    prev = None
    for k in range(int(total / step)):
        tt = k * step
        i = renderer.seg_at(tt)
        st = renderer.state_at(i, tt)
        v = st.get("tool", st.get("t", 0.0))
        if prev is not None:
            sp[int(tt * SR):int((tt + step) * SR)] = abs(v - prev)
        prev = v
    if sp.max() > 0:
        out += whirr(sp) * 0.16
    return out[: int(total * SR)]


def mix(voice: np.ndarray, sfx: np.ndarray, bed: np.ndarray, music_db: float = -20.0,
        sfx_db: float = -8.0) -> np.ndarray:
    """Голос + эффекты + музыка с приглушением под речью. Уровни относительно пика голоса."""
    n = len(voice)
    sfx = np.pad(sfx, (0, max(0, n - len(sfx))))[:n]
    bed = np.pad(bed, (0, max(0, n - len(bed))))[:n]
    vpk = np.abs(voice).max() + 1e-9
    env = _lp(np.abs(voice), int(0.3 * SR))
    env = np.clip(env / (env.max() + 1e-9) * 5, 0, 1)
    duck = 1 - 0.55 * env  # под речью музыка ниже ещё на ~7 дБ
    g_m = vpk * 10 ** (music_db / 20)
    g_s = vpk * 10 ** (sfx_db / 20)
    return voice + bed * g_m * duck + sfx * g_s
