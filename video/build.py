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
from .render import Renderer, build_timeline, encode, mix_audio
from .spec import load_spec
from .style import domain_for_chapter, load_settings, load_style
from .templates import get_template


def out_dir_for(spec_path: Path) -> Path:
    return spec_path.parent if spec_path.name == "spec.json" else spec_path.parent / spec_path.stem


def build(spec_path: str | Path, layouts=("9x16", "16x9"), voice: str | None = None,
          out: str | Path | None = None) -> dict:
    spec = load_spec(spec_path)
    for w in spec.warnings:
        print(f"[video] внимание: {w}", file=sys.stderr)
    domain = spec.get("domain") or domain_for_chapter(spec.get("chapter"))
    style = load_style(domain)
    settings = load_settings()
    out_dir = Path(out) if out else out_dir_for(spec.path)
    out_dir.mkdir(parents=True, exist_ok=True)

    tpl = get_template(spec.template)(spec.params, style)
    segs, tl_info = build_timeline(spec, domain, engine=voice)

    raw_wav, wav = out_dir / "voice_raw.wav", out_dir / "voice.wav"
    tts.write_wav(raw_wav, mix_audio(segs))
    audio_info = tts.normalize(raw_wav, wav, settings["voice"]["target_lufs"])
    raw_wav.unlink(missing_ok=True)

    fps, crf = settings["render"]["fps"], settings["render"]["crf"]
    results, passport_frames, key_times, checks = {}, [], [], []
    for kind in layouts:
        r = Renderer(spec, tpl, style, kind, segs)
        fdir = out_dir / ("frames" if kind == "9x16" else f"frames_{kind}")
        fdir.mkdir(exist_ok=True)
        for old in fdir.glob("F*.png"):
            old.unlink()
        names = []
        for i, t in enumerate(r.key_times()):
            name = f"F{i + 1:02d}.png"
            r.frame(t).save(fdir / name)
            names.append(f"{fdir.name}/{name}")
        mp4 = out_dir / ("video.mp4" if kind == "9x16" else f"video_{kind}.mp4")
        encode(r, wav, mp4, fps, crf)
        results[kind] = str(mp4)
        if not passport_frames:
            passport_frames, key_times = names, r.key_times()
            checks = [{**c, "ok": bool(c["ok"])} for c in tpl.check(r.states_for_check())]
    passport = build_passport(spec, domain, segs, key_times, passport_frames, tl_info, audio_info, checks, out_dir)
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
    ap.add_argument("--layout", choices=["9x16", "16x9", "both"], default="both")
    ap.add_argument("--voice", choices=["edge", "silero"], default=None,
                    help="движок голоса (по умолчанию из video/settings.json)")
    ap.add_argument("-o", "--out", default=None, help="папка результата (по умолчанию рядом со spec)")
    a = ap.parse_args(argv)
    layouts = ("9x16", "16x9") if a.layout == "both" else (a.layout,)
    res = build(a.spec, layouts, a.voice, a.out)
    for k, v in res["videos"].items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
