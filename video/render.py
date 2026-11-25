"""Движок ролика: фразы → тайминг по длительности аудио → кадры (Pillow) → ffmpeg.

Одна модель состояния сцены; раскладки 9:16 и 16:9 — только разная геометрия тех же видов.
Длительность кадра каждой фразы = длительность её аудио (+ короткие паузы) → голос и кадр синхронны.
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from . import tts
from .draw import SDraw, fit, line_h, rounded_box, text_block, wrap
from .spec import Spec
from .style import display_font, font, hex_rgb, load_settings, mix

GREEK = {"α": "альфа", "β": "бета", "γ": "гамма", "δ": "дельта", "Δ": "дельта", "ε": "эпсилон",
         "η": "эта", "θ": "тета", "κ": "каппа", "ϰ": "каппа", "λ": "лямбда", "μ": "мю", "ν": "ню",
         "π": "пи", "ρ": "ро", "σ": "сигма", "τ": "тау", "φ": "фи", "χ": "хи", "ψ": "пси", "ω": "омега",
         "Ω": "омега", "Σ": "сумма"}


def subtitle_text(s: str) -> str:
    """Субтитры без греческих букв и формул."""
    s = s.replace(tts.ACUTE, "")
    for g, name in GREEK.items():
        s = s.replace(g, name)
    s = re.sub(r"[=^_*/\\]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


@dataclass
class Segment:
    beat: object
    start: float
    dur: float
    speech_start: float
    speech_dur: float
    audio: np.ndarray | None
    tts_info: dict


# ---------------------------------------------------------------- тайминг и звук
def build_timeline(spec: Spec, domain: str, engine: str | None = None) -> tuple[list[Segment], dict]:
    st = load_settings()["voice"]
    rate = st["edge_rate"]
    info: dict = {"rate": rate, "rate_adjusted": False}

    def synth_all(rate):
        out = []
        for b in spec.beats:
            if b.say:
                print(f"[video] голос {b.index + 1}/{len(spec.beats)}: {b.say[:50]}…", file=sys.stderr)
                try:
                    out.append(tts.synth(b.say, domain, engine=engine, rate=rate))
                except tts.TTSError as e:
                    print(f"[video] ФРАЗА БЕЗ ГОЛОСА (тишина по длине текста): {e}", file=sys.stderr)
                    n = len(b.say.split())
                    out.append({"audio": np.zeros(int(tts.SR * max(1.5, n / 2.2))), "engine": "none",
                                "voice": None, "spoken": b.say, "fallback": True, "errors": [str(e)]})
            else:
                out.append(None)
        return out

    res = synth_all(rate)
    words = sum(len(b.say.split()) for b in spec.beats if b.say)
    secs = sum(len(r["audio"]) / tts.SR for r in res if r)
    wpm = words / secs * 60 if secs else 0
    lo, hi = st.get("target_wpm", [110, 150])
    if st.get("auto_rate", True) and secs and not (lo <= wpm <= hi) and any(r and r["engine"] == "edge" for r in res):
        target = hi - 8 if wpm > hi else lo + 10
        base = int(re.sub(r"[^\d-]", "", rate) or 0)
        new = int(round((1 + base / 100) * target / wpm * 100 - 100))
        rate = f"{new:+d}%"
        print(f"[video] темп {wpm:.0f} сл/мин вне {lo}–{hi}, edge rate → {rate}", file=sys.stderr)
        res = synth_all(rate)
        info.update(rate=rate, rate_adjusted=True)
        secs = sum(len(r["audio"]) / tts.SR for r in res if r)
        wpm = words / secs * 60 if secs else 0
    info["wpm"] = round(wpm, 1)

    pad_b, pad_a = st["pad_before_s"], st["pad_after_s"]
    segs, t = [], 0.0
    for b, r in zip(spec.beats, res):
        if r is not None:
            sd = len(r["audio"]) / tts.SR
            dur = pad_b + sd + pad_a + b.pause
            segs.append(Segment(b, t, dur, t + pad_b, sd, r["audio"], r))
        else:
            dur = b.pause if b.pause > 0 else 1.5
            segs.append(Segment(b, t, dur, t, 0.0, None, {}))
        t += dur
    if segs:  # финальный кадр держим, чтобы вопрос на перенос успели прочитать
        segs[-1].dur += 1.2
    return segs, info


def mix_audio(segs: list[Segment]) -> np.ndarray:
    total = sum(s.dur for s in segs)
    out = np.zeros(int(np.ceil(total * tts.SR)) + tts.SR // 10, dtype=np.float32)
    for s in segs:
        if s.audio is not None:
            i = int(round(s.speech_start * tts.SR))
            out[i:i + len(s.audio)] += s.audio
    return out[: int(round(total * tts.SR))]


def sfx_track(segs: list[Segment], n: int, gain_db: float = -24.0) -> np.ndarray:
    """Тихие процедурные звуки на событиях кадра (без внешних файлов): смена подписи — короткий шорох,
    рамка — мягкий щелчок, начало движения прибора — низкий «тук». Под голосом, речь не маскируют."""
    sr = tts.SR
    out = np.zeros(n, dtype=np.float32)
    rng = np.random.default_rng(7)

    def put(t, sig):
        i = int(t * sr)
        j = min(n, i + len(sig))
        if i < n:
            out[i:j] += sig[: j - i]

    def tick(f=1400.0, d=0.07):
        tt = np.arange(int(d * sr)) / sr
        return np.sin(2 * np.pi * f * tt) * np.exp(-tt * 60)

    def swish(d=0.22):
        m = int(d * sr)
        x = rng.standard_normal(m)
        x = np.convolve(x, np.ones(24) / 24, mode="same")
        env = np.sin(np.linspace(0, np.pi, m)) ** 2
        return x * env * 2.5

    def thump(d=0.18):
        tt = np.arange(int(d * sr)) / sr
        return np.sin(2 * np.pi * (90 - 120 * tt) * tt) * np.exp(-tt * 22)

    prev_cap = None
    for s in segs:
        a = s.beat.action
        if s.beat.on_screen and s.beat.on_screen != prev_cap:
            put(s.start, swish())
        prev_cap = s.beat.on_screen or prev_cap
        if a.get("mark"):
            put(s.start + 0.05, tick())
            put(s.start + 0.12, tick(1900, 0.05) * 0.6)
        if "tool_to" in a:
            put(s.start + 0.1, thump())
    peak = np.max(np.abs(out)) or 1.0
    return out / peak * (10 ** (gain_db / 20))


def subtitle_chunks(seg: Segment, fnt, max_w: float, max_lines: int = 2) -> list[tuple[float, float, list[str]]]:
    """Фраза → куски по ≤2 строки; время куска пропорционально числу букв."""
    text = subtitle_text(seg.beat.say)
    if not text:
        return []
    words, chunks, cur = text.split(), [], []
    for w in words:
        cand = cur + [w]
        if len(wrap(" ".join(cand), fnt, max_w)) > max_lines and cur:
            chunks.append(cur)
            cur = [w]
        else:
            cur = cand
    if cur:
        chunks.append(cur)
    total = sum(len(" ".join(c)) for c in chunks)
    t = seg.speech_start
    out = []
    for c in chunks:
        d = seg.speech_dur * len(" ".join(c)) / total
        out.append((t, t + d, wrap(" ".join(c), fnt, max_w)))
        t += d
    # последний кусок держим до конца сегмента
    out[-1] = (out[-1][0], seg.start + seg.dur, out[-1][2])
    return out


# ---------------------------------------------------------------- раскладки
HL = re.compile(r"\*([^*]+)\*")


def caption_tokens(cap: str) -> list[tuple[str, bool]]:
    """«Коллектор: *аномалия ПС*» → слова с признаком выделения (выделение — инверсией, без нового цвета)."""
    out, pos = [], 0
    for m in HL.finditer(cap):
        out += [(w, False) for w in cap[pos:m.start()].split()]
        out += [(w, True) for w in m.group(1).split()]
        pos = m.end()
    out += [(w, False) for w in cap[pos:].split()]
    return out


def plain(cap: str) -> str:
    return HL.sub(r"\1", cap or "")


class Layout:
    """Геометрия кадра. 9:16: тег → крупный заголовок → сцена → плашка → субтитры (всё в безопасной зоне).
    16:9: сцена слева, справа тег, заголовок, плашка; субтитры внизу."""

    def __init__(self, kind: str, spec: Spec, style: dict, beats):
        self.kind = kind
        sz = style.get("safe_zone", {"top": 220, "bottom": 380, "side": 70})
        if kind == "9x16":
            self.W, self.H = 1080, 1920
            self.safe = (sz["side"], sz["top"], 1080 - sz["side"], 1920 - sz["bottom"])
        else:
            self.W, self.H = 1920, 1080
            self.safe = (64, 52, 1920 - 64, 1080 - 48)
        x0, y0, x1, y1 = self.safe
        self.f_tag = font(36, True)
        self.f_sub = font(44, True)
        plaque = " ".join(spec.get("plaque") or []) or "схема, не в масштабе"
        self.tag_h = line_h(self.f_tag) + 14
        if kind == "9x16":
            w = x1 - x0
            self.head_x, self.head_w = x0, w
            self.head_size, self.head_lines = 92, 2
            self.head_y = y0 + self.tag_h + 18
            hh = int(line_h(display_font(self.head_size)) * 1.02) * 2
            self.f_plaque, self.plaque_lines = fit(plaque, w, 38, 34, 2)
            self.sub_h = line_h(self.f_sub) * 2 + 22
            self.sub_box = (x0, y1 - self.sub_h, x1, y1)
            pl_h = len(self.plaque_lines) * line_h(self.f_plaque)
            self.plaque_y = self.sub_box[1] - 18 - pl_h
            self.scene_box = (x0, self.head_y + hh + 20, x1, self.plaque_y - 22)
            self.plaque_x = x0
        else:
            panel_x = 1240
            w = x1 - panel_x
            self.head_x, self.head_w = panel_x, w
            self.head_size, self.head_lines = 100, 4
            self.head_y = y0 + self.tag_h + 26
            self.f_plaque, self.plaque_lines = fit(plaque, w, 38, 32, 4)
            self.sub_h = line_h(self.f_sub) * 2 + 22
            self.sub_box = (x0, y1 - self.sub_h, x1, y1)
            pl_h = len(self.plaque_lines) * line_h(self.f_plaque)
            self.plaque_y = self.sub_box[1] - 30 - pl_h
            self.plaque_x = panel_x
            self.scene_box = (x0, y0, panel_x - 56, self.sub_box[1] - 30)
        self.tag_x = self.head_x


class Renderer:
    def __init__(self, spec: Spec, template, style: dict, kind: str, segs: list[Segment]):
        self.spec, self.tpl, self.style, self.kind, self.segs = spec, template, style, kind, segs
        self.L = Layout(kind, spec, style, [s.beat for s in segs])
        self.T = template.layout(self.L.scene_box, compact=(kind == "9x16"))
        rs = load_settings()["render"]
        self.fade = rs["fade_s"]
        self.k = int(rs.get("ssaa", 2))
        self.intro_on = rs.get("intro", True) and hasattr(template, "draw_intro")
        self.intro_dur = min(float(rs.get("intro_s", 2.6)), segs[0].dur * 0.7) if segs else 0
        bg = hex_rgb(style["background"])
        self.base = Image.new("RGB", (self.L.W * self.k, self.L.H * self.k), bg)
        self.base.info["ssaa"] = self.k
        template.draw_static(self.base, self.T)
        d = SDraw(self.base)
        text_block(d, (self.L.plaque_x, self.L.plaque_y), self.L.plaque_lines, self.L.f_plaque,
                   hex_rgb(style["muted"]))
        self.starts = []
        s = template.initial_state()
        for seg in segs:
            s0 = template.begin_beat(s, seg.beat.action, seg.start)
            self.starts.append(s0)
            s = template.at(s0, seg.beat.action, 1.0)
        sx0, _, sx1, _ = self.L.sub_box
        self.subs = [subtitle_chunks(seg, self.L.f_sub, sx1 - sx0 - 60, max_lines=1) for seg in segs]
        self.last = len(segs) - 1

    def seg_at(self, t: float) -> int:
        for i, s in enumerate(self.segs):
            if t < s.start + s.dur:
                return i
        return len(self.segs) - 1

    def state_at(self, i: int, t: float) -> dict:
        seg = self.segs[i]
        if "tool_to" not in seg.beat.action:
            return self.starts[i]
        a = seg.start + 0.1 + (self.intro_dur if i == 0 and self.intro_on else 0)
        b = seg.start + max(a - seg.start + 0.3, seg.dur - 0.25)
        u = (t - a) / (b - a)
        return self.tpl.at(self.starts[i], seg.beat.action, min(max(u, 0.0), 1.0))

    def _is_pause(self, i):
        b = self.segs[i].beat
        return not b.say and b.pause > 0

    def _dim(self, img, amount):
        if amount <= 0:
            return
        k = self.k
        x0, y0, x1, y1 = self.L.scene_box
        box = (int(x0 * k), int((y0 - 10) * k), int(x1 * k), int((y1 + 30) * k))
        dark = Image.new("RGB", (box[2] - box[0], box[3] - box[1]), hex_rgb(self.style["background"]))
        img.paste(Image.blend(img.crop(box), dark, amount), box[:2])

    # -------------------------------------------------------------- кадр
    def frame(self, t: float) -> Image.Image:
        i = self.seg_at(t)
        seg = self.segs[i]
        img = self.base.copy()
        state = self.state_at(i, t)
        self.tpl.draw_dynamic(img, self.T, state, t, self.fade)
        if hasattr(self.tpl, "draw_overlays"):
            self.tpl.draw_overlays(img, self.T, state, t, self.fade)
        # крючок: спуск с поверхности к интервалу, затем растворение в разрез
        if i == 0 and self.intro_on and t < self.intro_dur + 0.45:
            intro = img.copy()
            self.tpl.draw_intro(intro, self.T, min(1.0, t / self.intro_dur))
            a = 0.0 if t < self.intro_dur else (t - self.intro_dur) / 0.45
            img = Image.blend(intro, img, min(1.0, a))
            img.info["ssaa"] = self.k
        if self._is_pause(i):
            self._dim(img, 0.5 * min(1.0, (t - seg.start) / 0.3))
        final = i == self.last
        if final:
            self._dim(img, 0.62 * min(1.0, (t - seg.start) / 0.4))
        d = SDraw(img)
        self._tag(d, seg)
        if final:
            self._final_card(d, seg, t)
        else:
            self._headline(d, i, seg, t)
        for (a0, a1, lines) in self.subs[i]:
            if a0 <= t < a1:
                self._subtitle(d, lines, a0, a1, seg, t)
                break
        if self.k != 1:
            img = img.resize((self.L.W, self.L.H), Image.LANCZOS)
        return img

    def _tag(self, d, seg):
        """Метка происхождения и масштаб — как тег «ПЛАСТ · УВЕЛИЧЕНО» в референсах."""
        st = self.style
        lab = seg.beat.label.upper()
        col = hex_rgb(st.get("provenance", {}).get(seg.beat.label, st["muted"]))
        x, y = self.L.tag_x, self.L.safe[1]
        f = self.L.f_tag
        tw = f.getlength(lab)
        rounded_box(d, (x, y, x + tw + 30, y + self.L.tag_h), fill=col, radius=10)
        d.text((x + 15, y + 6), lab, font=f, fill=(255, 255, 255))
        rest = "  ·  НЕ В МАСШТАБЕ" if self.spec.get("plaque") or self.spec.get("exaggerations") else ""
        if rest:
            d.text((x + tw + 30, y + 6), rest, font=f, fill=hex_rgb(st["muted"]))

    def _headline(self, d, i, seg, t):
        """Крупный заголовок фразы: слова выезжают по одному, выделенные *словами* — в инверсной плашке."""
        cap = seg.beat.on_screen
        if not cap:
            return
        st = self.style
        bg, text = hex_rgb(st["background"]), hex_rgb(st["text"])
        same = i > 0 and self.segs[i - 1].beat.on_screen == cap
        toks = [(w.upper(), h) for w, h in caption_tokens(cap)]
        f, lines = fit(" ".join(w for w, _ in toks), self.L.head_w - 30, self.L.head_size, 52, self.L.head_lines,
                       fnt_fn=display_font)
        lh = int(line_h(f) * 0.98)
        y, n, ti = self.L.head_y, 0, 0
        for ln in lines:
            words = ln.split(" ")
            flags = []
            for _ in words:
                flags.append(toks[ti][1] if ti < len(toks) else False)
                ti += 1
            # подряд идущие выделенные слова — одна плашка; плашка проявляется, текст инвертируется
            x = self.L.head_x
            xs = []
            for w in words:
                xs.append(x)
                x += f.getlength(w + " ")
            j = 0
            while j < len(words):
                if flags[j]:
                    k2 = j
                    while k2 + 1 < len(words) and flags[k2 + 1]:
                        k2 += 1
                    g = 1.0 if same else min(1.0, max(0.0, (t - seg.start - 0.05 * (n + j) - 0.12) / 0.3))
                    x_a, x_b = xs[j] - 10, xs[k2] + f.getlength(words[k2]) + 10
                    d.rounded_rectangle((x_a, y + 4, x_a + (x_b - x_a) * (1 - (1 - g) ** 3), y + lh - 2),
                                        radius=8, fill=mix(bg, text, g))
                    j = k2 + 1
                else:
                    j += 1
            for j, w in enumerate(words):
                u = 1.0 if same else min(1.0, max(0.0, (t - seg.start - 0.05 * (n + j)) / 0.32))
                e = 1 - (1 - u) ** 3
                dy = (1 - e) * 26
                if flags[j]:
                    g = 1.0 if same else min(1.0, max(0.0, (t - seg.start - 0.05 * (n + j) - 0.12) / 0.3))
                    col = mix(mix(bg, text, e), bg, g)
                else:
                    col = mix(bg, text, e)
                d.text((xs[j], y + dy), w, font=f, fill=col)
            n += len(words)
            y += lh

    def _final_card(self, d, seg, t):
        """Вопрос на перенос: сцена приглушена, вопрос крупно поверх неё."""
        st = self.style
        bg, text, muted = hex_rgb(st["background"]), hex_rgb(st["text"]), hex_rgb(st["muted"])
        x0, y0, x1, y1 = self.L.scene_box
        u = min(1.0, max(0.0, (t - seg.start) / 0.45))
        e = 1 - (1 - u) ** 3
        cap = plain(seg.beat.on_screen) or plain(self.spec.get("transfer_question", ""))
        f, ls = fit(cap.upper(), x1 - x0 - 80, 76, 46, 4, fnt_fn=display_font)
        lh = int(line_h(f) * 1.04)
        f_s = font(36, True)
        h = len(ls) * lh + line_h(f_s) + 70
        top = (y0 + y1) / 2 - h / 2 + (1 - e) * 40
        rounded_box(d, (x0, top, x1, top + h), fill=mix(bg, hex_rgb(st["panel"]), e), radius=26)
        d.text((x0 + 40, top + 26), "ВОПРОС НА ПЕРЕНОС", font=f_s, fill=mix(bg, muted, e))
        text_block(d, (x0 + 40, top + 36 + line_h(f_s)), ls, f, mix(bg, text, e), spacing=1.04)

    def _subtitle(self, d, lines, a0, a1, seg, t):
        """Субтитры — светлая плашка по ширине текста, тёмный текст; сказанные слова темнее (виден голос)."""
        st = self.style
        bg, text, muted = hex_rgb(st["background"]), hex_rgb(st["text"]), hex_rgb(st["muted"])
        sx0, sy0, sx1, sy1 = self.L.sub_box
        f = self.L.f_sub
        lh = line_h(f)
        tw = max(f.getlength(ln) for ln in lines)
        h = len(lines) * lh + 18
        cx = (sx0 + sx1) / 2
        top = sy1 - h
        rounded_box(d, (cx - tw / 2 - 26, top, cx + tw / 2 + 26, sy1), fill=text, radius=18)
        speech_end = min(a1, seg.speech_start + seg.speech_dur)
        total = sum(len(ln) for ln in lines) or 1
        spoken = (t - a0) / max(0.1, speech_end - a0) * total
        done, y = 0, top + 7
        for ln in lines:
            x = cx - f.getlength(ln) / 2
            for w in ln.split(" "):
                d.text((x, y), w, font=f, fill=bg if done < spoken else mix(text, muted, 0.85))
                x += f.getlength(w + " ")
                done += len(w) + 1
            y += lh

    def total(self) -> float:
        return self.segs[-1].start + self.segs[-1].dur

    def key_times(self) -> list[float]:
        """Ключевой кадр фразы: когда прибор доехал и подпись видна."""
        return [s.start + max(0.3, s.dur - 0.3) for s in self.segs]

    def states_for_check(self) -> list[dict]:
        out = []
        for i, seg in enumerate(self.segs):
            for u in (0.0, 0.5, 1.0):
                out.append(self.state_at(i, seg.start + u * seg.dur))
        return out


def encode(renderer: Renderer, audio_wav: Path, out_mp4: Path, fps: int, crf: int):
    total = renderer.total()
    n = int(np.ceil(total * fps))
    W, H = renderer.L.W, renderer.L.H
    cmd = [tts.ffmpeg_exe(), "-y", "-v", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
           "-i", str(audio_wav),
           "-c:v", "libx264", "-preset", "medium", "-tune", "animation", "-crf", str(crf), "-pix_fmt", "yuv420p",
           "-profile:v", "high", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
           "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-shortest", "-movflags", "+faststart", str(out_mp4)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    t0 = time.time()
    try:
        for k in range(n):
            p.stdin.write(renderer.frame(k / fps).tobytes())
            if k % (fps * 10) == 0:
                print(f"[video] {renderer.kind}: кадр {k}/{n}", file=sys.stderr)
    finally:
        p.stdin.close()
        p.wait()
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg завершился с кодом {p.returncode}")
    print(f"[video] {out_mp4.name}: {n} кадров за {time.time() - t0:.0f} с", file=sys.stderr)
