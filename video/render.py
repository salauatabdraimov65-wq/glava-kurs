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
from .style import font, hex_rgb, load_settings, mix

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
class Layout:
    def __init__(self, kind: str, spec: Spec, style: dict):
        self.kind = kind
        self.style = style
        sz = style.get("safe_zone", {"top": 220, "bottom": 380, "side": 70})
        if kind == "9x16":
            self.W, self.H = 1080, 1920
            self.safe = (sz["side"], sz["top"], 1080 - sz["side"], 1920 - sz["bottom"])
        else:
            self.W, self.H = 1920, 1080
            self.safe = (64, 56, 1920 - 64, 1080 - 56)
        x0, y0, x1, y1 = self.safe
        self.f_badge = font(40, True)
        self.f_sub = font(46, True)
        self.f_cap_size = 58
        title = spec.get("title", "")
        plaque = " ".join(spec.get("plaque") or []) or "схема, не в масштабе"
        if kind == "9x16":
            w = x1 - x0
            # заголовок по возможности в одну строку — экономим высоту для разреза
            self.f_title, self.title_lines = fit(title, w, 52, 44, 1, True)
            if len(self.title_lines) > 1 or self.f_title.getlength(self.title_lines[0]) > w:
                self.f_title, self.title_lines = fit(title, w, 50, 44, 2, True)
            self.f_plaque, self.plaque_lines = fit(plaque, w, 40, 36, 2)
            y = y0 + line_h(self.f_badge) + 26
            self.title_y = y
            y += len(self.title_lines) * line_h(self.f_title)
            self.plaque_y = y
            y += len(self.plaque_lines) * line_h(self.f_plaque) + 14
            self.f_cap_size = 54
            self.sub_h = line_h(self.f_sub) * 2 + 24
            self.sub_box = (x0, y1 - self.sub_h, x1, y1)
            self.cap_h = int(line_h(font(self.f_cap_size, True)) * 2 + 26)
            self.cap_box = (x0, self.sub_box[1] - 14 - self.cap_h, x1, self.sub_box[1] - 14)
            self.scene_box = (x0, y, x1, self.cap_box[1] - 16)
        else:
            panel_x = 1220
            w = x1 - panel_x
            self.f_title, self.title_lines = fit(title, w, 56, 44, 3, True)
            self.f_plaque, self.plaque_lines = fit(plaque, w, 44, 36, 4)
            y = y0 + line_h(self.f_badge) + 30
            self.title_y = y
            y += len(self.title_lines) * line_h(self.f_title) + 8
            self.plaque_y = y
            y += len(self.plaque_lines) * line_h(self.f_plaque) + 26
            self.sub_h = line_h(self.f_sub) * 2 + 28
            self.sub_box = (x0, y1 - self.sub_h, x1, y1)
            self.cap_h = int(line_h(font(self.f_cap_size, True)) * 3 + 30)
            self.cap_box = (panel_x, y, x1, min(y + self.cap_h, self.sub_box[1] - 20))
            self.scene_box = (x0, y0, panel_x - 50, self.sub_box[1] - 24)
        self.panel_x = x0 if kind == "9x16" else 1220


class Renderer:
    def __init__(self, spec: Spec, template, style: dict, kind: str, segs: list[Segment]):
        self.spec, self.tpl, self.style, self.kind, self.segs = spec, template, style, kind, segs
        self.L = Layout(kind, spec, style)
        self.T = template.layout(self.L.scene_box, compact=(kind == "9x16"))
        self.fade = load_settings()["render"]["fade_s"]
        bg = hex_rgb(style["background"])
        self.k = int(load_settings()["render"].get("ssaa", 2))
        self.base = Image.new("RGB", (self.L.W * self.k, self.L.H * self.k), bg)
        self.base.info["ssaa"] = self.k
        template.draw_static(self.base, self.T)
        self._draw_static_text(self.base)
        # состояния сцены на начало и конец каждой фразы
        self.starts, self.ends = [], []
        s = template.initial_state()
        for seg in segs:
            s0 = template.begin_beat(s, seg.beat.action, seg.start)
            s1 = template.at(s0, seg.beat.action, 1.0)
            self.starts.append(s0)
            self.ends.append(s1)
            s = s1
        sx0, _, sx1, _ = self.L.sub_box
        self.subs = [subtitle_chunks(seg, self.L.f_sub, sx1 - sx0 - 48) for seg in segs]

    def _draw_static_text(self, img):
        d = SDraw(img)
        st = self.style
        text, muted = hex_rgb(st["text"]), hex_rgb(st["muted"])
        x = self.L.panel_x
        text_block(d, (x, self.L.title_y), self.L.title_lines, self.L.f_title, text)
        text_block(d, (x, self.L.plaque_y), self.L.plaque_lines, self.L.f_plaque, muted)

    def seg_at(self, t: float) -> int:
        for i, s in enumerate(self.segs):
            if t < s.start + s.dur:
                return i
        return len(self.segs) - 1

    def state_at(self, i: int, t: float) -> dict:
        seg = self.segs[i]
        if "tool_to" not in seg.beat.action:
            return self.starts[i]
        a, b = seg.start + 0.1, seg.start + max(0.4, seg.dur - 0.25)
        u = (t - a) / (b - a)
        return self.tpl.at(self.starts[i], seg.beat.action, min(max(u, 0.0), 1.0))

    def frame(self, t: float) -> Image.Image:
        i = self.seg_at(t)
        seg = self.segs[i]
        img = self.base.copy()
        self.tpl.draw_dynamic(img, self.T, self.state_at(i, t), t, self.fade)
        d = SDraw(img)
        st = self.style
        bg, text, panel = hex_rgb(st["background"]), hex_rgb(st["text"]), hex_rgb(st["panel"])
        # метка происхождения
        lab = seg.beat.label
        bcol = hex_rgb(st.get("provenance", {}).get(lab, st["muted"]))
        x, y = self.L.panel_x, self.L.safe[1]
        tw = self.L.f_badge.getlength(lab.upper())
        rounded_box(d, (x, y, x + tw + 36, y + line_h(self.L.f_badge) + 14), fill=bcol, radius=14)
        d.text((x + 18, y + 7), lab.upper(), font=self.L.f_badge, fill=(255, 255, 255))
        # подпись на экране (on_screen): выезжает снизу и проявляется, если текст сменился
        cap = seg.beat.on_screen
        if cap:
            same = i > 0 and self.segs[i - 1].beat.on_screen == cap
            u = 1.0 if same else min(1.0, max(0.0, (t - seg.start) / 0.35))
            a = 1 - (1 - u) ** 3  # ease-out
            dy = (1 - a) * 28
            cx0, cy0, cx1, cy1 = self.L.cap_box
            max_lines = 2 if self.kind == "9x16" else 3
            f, ls = fit(cap, cx1 - cx0 - 56, self.L.f_cap_size, 44, max_lines, True)
            h = len(ls) * line_h(f) + 26
            top = (cy0 if self.kind == "16x9" else cy1 - h) + dy
            accent = hex_rgb(st["measurement"]["color"])
            rounded_box(d, (cx0, top, cx1, top + h), fill=mix(bg, panel, a), radius=22)
            d.rectangle((cx0, top + 14, cx0 + 8, top + h - 14), fill=mix(bg, accent, a))
            text_block(d, (cx0 + 30, top + 10), ls, f, mix(bg, text, a))
        # субтитры: сказанные слова белые, впереди — приглушённые (видно, где голос)
        for (a0, a1, lines) in self.subs[i]:
            if a0 <= t < a1:
                self._subtitle(d, lines, a0, a1, seg, t)
                break
        if self.k != 1:
            img = img.resize((self.L.W, self.L.H), Image.LANCZOS)
        return img

    def _subtitle(self, d, lines, a0, a1, seg, t):
        sx0, sy0, sx1, sy1 = self.L.sub_box
        f = self.L.f_sub
        lh = line_h(f)
        h = len(lines) * lh + 20
        top = sy1 - h
        rounded_box(d, (sx0, top, sx1, sy1), fill=(0, 0, 0), radius=16)
        speech_end = min(a1, seg.speech_start + seg.speech_dur)
        total = sum(len(ln) for ln in lines) or 1
        spoken = (t - a0) / max(0.1, speech_end - a0) * total
        done, y = 0, top + 8
        white, dim = (255, 255, 255), (150, 156, 165)
        for ln in lines:
            x = sx0 + (sx1 - sx0 - f.getlength(ln)) / 2
            for w in ln.split(" "):
                col = white if done < spoken else dim
                d.text((x, y), w, font=f, fill=col)
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
