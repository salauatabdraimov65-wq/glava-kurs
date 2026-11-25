"""spec.json → video.mp4 (9:16), video_16x9.mp4, frames/, passport.json.

    uv run python -m video.build runs/<chapter>/video/<vid>/spec.json
    uv run python -m video.build spec.json --layout 9x16 --voice silero -o out/

Результат кладётся рядом со spec.json (если файл называется иначе — в папку <имя_файла>/ рядом).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import tts
from .passport import build_passport
import numpy as np

from .render import Renderer, build_timeline, encode, mix_audio
from .cine import CineRenderer
from .spec import load_spec
from .style import domain_for_chapter, load_settings, load_style
from .templates import get_template


def out_dir_for(spec_path: Path) -> Path:
    return spec_path.parent if spec_path.name == "spec.json" else spec_path.parent / spec_path.stem


def build(spec_path: str | Path, layouts=None, voice: str | None = None,
          out: str | Path | None = None, until: float | None = None, fps: int | None = None) -> dict:
    spec = load_spec(spec_path)
    if not layouts:
        asp = (spec.get("aspect") or "").replace(":", "x")
        layouts = (asp,) if asp in ("9x16", "16x9") else ("9x16", "16x9")
    for w in spec.warnings:
        print(f"[video] внимание: {w}", file=sys.stderr)
    domain = spec.get("domain") or domain_for_chapter(spec.get("chapter"))
    style = load_style(domain)
    settings = load_settings()
    out_dir = Path(out) if out else out_dir_for(spec.path)
    out_dir.mkdir(parents=True, exist_ok=True)

    tpl = get_template(spec.template)(spec.params, style)
    segs, tl_info = build_timeline(spec, domain, engine=voice)
    if until:  # отрывок для оценки: целые фразы, начавшиеся до until (фразу не режем посередине)
        segs = [sg for sg in segs if sg.start < until]
        segs[-1].dur += 0.8

    # рендереры раньше звука: эффекты привязаны к событиям кадра (движение прибора, рамки, смена сцен)
    renderers = {}
    for kind in layouts:
        use_cine = (kind == "9x16" and settings["render"].get("cine", True) and hasattr(tpl, "cine_world"))
        renderers[kind] = (CineRenderer(spec, tpl, style, segs, Renderer) if use_cine
                           else Renderer(spec, tpl, style, kind, segs))
    raw_wav, wav = out_dir / "voice_raw.wav", out_dir / "voice.wav"
    voice_mix = mix_audio(segs)
    tts.write_wav(raw_wav, voice_mix)
    audio_info = tts.normalize(raw_wav, wav, settings["voice"]["target_lufs"])   # громкость одного голоса
    rs = settings["render"]
    if rs.get("sfx", True) or rs.get("music", True):
        from . import sound
        v = tts.decode_to_array(wav)
        total = len(v) / tts.SR
        fx = sound.design(segs, renderers[layouts[0]], total) if rs.get("sfx", True) else np.zeros(len(v))
        bed = sound.music(total) if rs.get("music", True) else np.zeros(len(v))
        tts.write_wav(raw_wav, sound.mix(v, fx, bed, rs.get("music_db", -20.0), rs.get("sfx_db", -8.0)))
        res = tts.normalize(raw_wav, wav, settings["voice"]["target_lufs"])
        audio_info.update({"mix_lufs": res.get("after_lufs"),
                           "music": "тихая подложка, синтез в коде; под речью приглушается" if rs.get("music", True) else None,
                           "sfx": "эффекты на событиях кадра (смена сцены, рамки, движение прибора, ответ)" if rs.get("sfx", True) else None,
                           "heard": False})
    raw_wav.unlink(missing_ok=True)

    fps, crf = fps or settings["render"]["fps"], settings["render"]["crf"]
    results, passport_frames, key_times, checks, notes = {}, [], [], [], {}
    for kind in layouts:
        r = renderers[kind]
        fdir = out_dir / ("frames" if kind == layouts[0] else f"frames_{kind}")
        fdir.mkdir(exist_ok=True)
        for old in fdir.glob("F*.png"):
            old.unlink()
        names = []
        for i, t in enumerate(r.key_times()):
            name = f"F{i + 1:02d}.png"
            r.frame(t).save(fdir / name)
            names.append(f"{fdir.name}/{name}")
        mp4 = out_dir / ("video.mp4" if kind == layouts[0] else f"video_{kind}.mp4")
        encode(r, wav, mp4, fps, crf)
        results[kind] = str(mp4)
        if r.scale_note:
            notes[kind] = r.scale_note
        if not passport_frames:
            passport_frames, key_times = names, r.key_times()
            checks = [{**c, "ok": bool(c["ok"])} for c in tpl.check(r.states_for_check())]
    passport = build_passport(spec, domain, segs, key_times, passport_frames, tl_info, audio_info, checks, out_dir)
    kw = getattr(tpl, "scale_keyword", "")
    if notes:  # преувеличение — числом, посчитанным по нарисованному кадру
        kws = (kw,) if isinstance(kw, str) else tuple(kw or ())
        ex = [x for x in passport["exaggerations"] if not any(k and k in x.lower() for k in kws)]
        passport["exaggerations"] = ex + [f"{v} (раскладка {k.replace('x', ':')})" for k, v in notes.items()]
    (out_dir / "passport.json").write_text(json.dumps(passport, ensure_ascii=False, indent=2), encoding="utf-8")
    dur = segs[-1].start + segs[-1].dur
    print(f"[video] готово: {dur:.1f} с, {tl_info.get('wpm')} сл/мин, громкость "
          f"{audio_info.get('after_lufs')} LUFS → {out_dir}", file=sys.stderr)
    bad = [c["item"] for c in checks if not c["ok"]]
    if bad:
        print(f"[video] ИНВАРИАНТЫ НАРУШЕНЫ: {bad}", file=sys.stderr)
    return {"out_dir": str(out_dir), "videos": results, "duration_s": round(dur, 1), "passport": passport}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Ролик из спецификации сцены")
    ap.add_argument("spec", help="путь к spec.json")
    ap.add_argument("--layout", choices=["auto", "9x16", "16x9", "both"], default="auto",
                    help="auto: по полю aspect в spec (9:16 или 16:9), без него — обе")
    ap.add_argument("--voice", choices=["edge", "silero"], default=None,
                    help="движок голоса (по умолчанию из video/settings.json)")
    ap.add_argument("-o", "--out", default=None, help="папка результата (по умолчанию рядом со spec)")
    ap.add_argument("--until", type=float, default=None, help="собрать только первые N секунд (отрывок)")
    ap.add_argument("--fps", type=int, default=None, help="кадров в секунду (по умолчанию из settings.json)")
    a = ap.parse_args(argv)
    if a.layout == "auto":
        asp = (json.loads(Path(a.spec).read_text(encoding="utf-8")).get("aspect") or "").replace(":", "x")
        layouts = (asp,) if asp in ("9x16", "16x9") else ("9x16", "16x9")
    else:
        layouts = ("9x16", "16x9") if a.layout == "both" else (a.layout,)
    res = build(a.spec, layouts, a.voice, a.out, a.until, a.fps)
    for k, v in res["videos"].items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
