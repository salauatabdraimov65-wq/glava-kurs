"""Озвучка: текст фразы → wav. Основной голос edge-tts, запасной Silero (локально).

Наружу (в edge-tts) уходит только текст сценария. Произношение берётся из доменного пакета
(domain/<домен>/pronunciation.md): аббревиатуры заменяются чтением, ударения ставятся знаком U+0301.
Выбор голоса — в video/settings.json или переменной VIDEO_VOICE_ENGINE, без правки кода.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import subprocess
import sys
import wave
from functools import lru_cache
from pathlib import Path

import numpy as np

from .style import ROOT, domain_dir, load_settings

SR = 48000
CACHE = ROOT / "cache" / "tts"
ACUTE = "́"
VOWELS = "аеёиоуыэюяАЕЁИОУЫЭЮЯ"


def ffmpeg_exe() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


# ---------------------------------------------------------------- словарь произношений
@lru_cache(maxsize=None)
def pronunciation(domain: str) -> tuple[dict, dict]:
    """(аббревиатуры → чтение, слово → слово с ударением) из таблиц pronunciation.md."""
    path = domain_dir(domain) / "pronunciation.md"
    abbr: dict[str, str] = {}
    stress: dict[str, str] = {}
    if not path.exists():
        return abbr, stress
    section = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            section = line.lower()
            continue
        if not line.startswith("|") or "---" in line:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or cells[0] in ("Пишется", "Слово") or not cells[1]:
            continue
        if "ударен" in section:
            word = re.sub(r"\s*\(.*?\)", "", cells[0]).strip()
            if ACUTE in cells[1]:
                stress[word.lower()] = cells[1].lower()
        else:
            keys = [k.strip() for k in cells[0].split(",")]
            vals = [v.strip() for v in re.split(r",| или ", cells[1])]
            for i, k in enumerate(keys):
                if k:
                    abbr[k] = vals[i] if i < len(vals) else vals[0]
    return abbr, stress


def _sub_word(text: str, key: str, val: str) -> str:
    return re.sub(rf"(?<![\ẃ]){re.escape(key)}(?![\ẃ])", val, text)


def spoken_text(text: str, domain: str, stress_marks: bool) -> str:
    """Текст для синтеза: аббревиатуры словами, ударения (если голос их понимает)."""
    abbr, stress = pronunciation(domain)
    out = text
    for k in sorted(abbr, key=len, reverse=True):
        if len(k) >= 2 or not k.isalpha():
            out = _sub_word(out, k, abbr[k])
    if stress_marks:
        def accented(lw: str) -> str | None:
            if lw in stress:
                return stress[lw]
            # словоформа: «коллектора» ← «колле́ктор», если ударная гласная внутри общей основы
            for base in sorted((b for b in stress if len(b) >= 5 and lw.startswith(b[:-1])), key=len, reverse=True):
                acc = stress[base]
                pos = acc.index(ACUTE)
                if pos <= len(base) - 1 and len(lw) - len(base) <= 3:
                    return lw[:pos] + ACUTE + lw[pos:]
            return None

        def repl(m):
            w = m.group(0)
            if ACUTE in w:
                return w
            acc = accented(w.lower())
            if not acc:
                return w
            return acc[0].upper() + acc[1:] if w[0].isupper() else acc
        out = re.sub(r"[А-Яа-яЁё́-]+", repl, out)
    else:
        out = out.replace(ACUTE, "")
    out = out.replace("—", ",").replace("«", "").replace("»", "")
    return re.sub(r"\s+", " ", out).strip()


def numbers_to_words(text: str) -> str:
    try:
        from num2words import num2words
    except ImportError:
        return text

    def repl(m):
        s = m.group(0).replace(",", ".")
        try:
            return num2words(float(s) if "." in s else int(s), lang="ru")
        except (ValueError, NotImplementedError):
            return m.group(0)
    return re.sub(r"\d+(?:[.,]\d+)?", repl, text)


# ---------------------------------------------------------------- аудио-утилиты
def decode_to_array(path: Path) -> np.ndarray:
    """Любой аудиофайл → mono float32 48 кГц через ffmpeg."""
    cmd = [ffmpeg_exe(), "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", str(SR), "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def trim_silence(a: np.ndarray, thr: float = 0.01) -> np.ndarray:
    idx = np.where(np.abs(a) > thr)[0]
    if len(idx) == 0:
        return a
    lo = max(0, idx[0] - int(0.03 * SR))
    hi = min(len(a), idx[-1] + int(0.08 * SR))
    return a[lo:hi]


def write_wav(path: Path, a: np.ndarray):
    a16 = (np.clip(a, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(a16.tobytes())


# ---------------------------------------------------------------- движки
class TTSError(RuntimeError):
    pass


async def _edge_once(text: str, voice: str, rate: str, out: Path):
    import edge_tts
    c = edge_tts.Communicate(text, voice, rate=rate)
    data = bytearray()
    async for ch in c.stream():
        if ch["type"] == "audio":
            data += ch["data"]
    if not data:
        raise TTSError("пустой ответ edge-tts")
    out.write_bytes(bytes(data))


def edge_synth(text: str, voice: str, rate: str, out: Path) -> str:
    """edge-tts иногда не отдаёт звук на отдельных фразах — повторяем и слегка меняем пунктуацию."""
    variants = [text, text.rstrip(".!?") + "!", "… " + text, text.rstrip(".") + "…"]
    last = None
    for i, v in enumerate(variants):
        for attempt in range(2):
            try:
                asyncio.run(_edge_once(v, voice, rate, out))
                return v
            except Exception as e:  # сеть, пустой ответ
                last = e
    raise TTSError(f"edge-tts не озвучил фразу: {last}")


_silero_model = None


def silero_synth(text: str, speaker: str, model_id: str, out: Path, sample_rate: int = 48000):
    """Silero TTS локально. Ударение: U+0301 после гласной → «+» перед гласной (формат Silero)."""
    global _silero_model
    try:
        import torch
    except ImportError as e:
        raise TTSError("Silero недоступен: нет torch (uv sync --extra silero)") from e
    if _silero_model is None:
        torch.hub.set_dir(str(ROOT / "cache" / "torch_hub"))
        model, _ = torch.hub.load(repo_or_dir="snakers4/silero-models", model="silero_tts",
                                  language="ru", speaker=model_id, trust_repo=True)
        _silero_model = model
    t = numbers_to_words(text)
    t = re.sub(rf"([{VOWELS}]){ACUTE}", r"+\1", t)
    t = t.replace(ACUTE, "")
    audio = _silero_model.apply_tts(text=t, speaker=speaker, sample_rate=sample_rate, put_accent=True, put_yo=True)
    write_wav(out, np.asarray(audio, dtype=np.float32) if sample_rate == SR else
              np.interp(np.linspace(0, len(audio), int(len(audio) * SR / sample_rate)),
                        np.arange(len(audio)), np.asarray(audio, dtype=np.float32)))


def synth(text: str, domain: str, engine: str | None = None, rate: str | None = None) -> dict:
    """Озвучить фразу. Возвращает {audio: ndarray, engine, voice, spoken, fallback?}. Кэш в cache/tts/."""
    st = load_settings()["voice"]
    engine = engine or st["engine"]
    rate = rate or st["edge_rate"]
    words_silero = [w.lower() for w in st.get("silero_words", [])]
    if engine == "edge" and words_silero and any(w in text.lower() for w in words_silero):
        engine = "silero"  # слова, где edge-tts ошибается в ударении
    order = [engine] + [e for e in ("edge", "silero") if e != engine]
    errors = []
    CACHE.mkdir(parents=True, exist_ok=True)
    for eng in order:
        if eng == "edge":
            spoken = spoken_text(text, domain, st.get("edge_use_stress_marks", False))
            voice = st["edge_voice"]
            key = hashlib.sha1(json.dumps(["edge", voice, rate, spoken]).encode()).hexdigest()[:16]
            f = CACHE / f"edge_{key}.mp3"
        else:
            spoken = spoken_text(text, domain, True)
            voice = st["silero_speaker"]
            key = hashlib.sha1(json.dumps(["silero", st["silero_model"], voice, spoken]).encode()).hexdigest()[:16]
            f = CACHE / f"silero_{key}.wav"
        try:
            if not f.exists() or f.stat().st_size == 0:
                if eng == "edge":
                    edge_synth(spoken, voice, rate, f)
                else:
                    silero_synth(spoken, voice, st["silero_model"], f, st.get("silero_sample_rate", 48000))
            a = trim_silence(decode_to_array(f))
            return {"audio": a, "engine": eng, "voice": voice, "spoken": spoken,
                    "fallback": eng != engine, "errors": errors}
        except Exception as e:
            if f.exists() and f.stat().st_size == 0:
                f.unlink()
            errors.append(f"{eng}: {e}")
            print(f"[video] голос {eng} не сработал: {e}", file=sys.stderr)
    raise TTSError("; ".join(errors))


def loudness(path: Path) -> dict:
    """Интегральная громкость (LUFS) через ffmpeg loudnorm."""
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostats", "-i", str(path), "-af",
           "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"]
    err = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", err, re.S)
    return json.loads(m.group(0)) if m else {}


def normalize(src: Path, dst: Path, target: float = -16.0) -> dict:
    """Двухпроходная нормализация громкости к target LUFS."""
    meas = loudness(src)
    if meas:
        af = (f"loudnorm=I={target}:TP=-1.5:LRA=11:measured_I={meas['input_i']}:measured_TP={meas['input_tp']}:"
              f"measured_LRA={meas['input_lra']}:measured_thresh={meas['input_thresh']}:"
              f"offset={meas['target_offset']}:linear=true")
    else:
        af = f"loudnorm=I={target}:TP=-1.5:LRA=11"
    subprocess.run([ffmpeg_exe(), "-y", "-v", "error", "-i", str(src), "-af", af, "-ar", str(SR), "-ac", "1",
                    str(dst)], check=True)
    after = loudness(dst)
    return {"before_lufs": float(meas.get("input_i", "nan")) if meas else None,
            "after_lufs": float(after.get("input_i", "nan")) if after else None}
