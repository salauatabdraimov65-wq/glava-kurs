"""Паспорт ролика (schemas/passport.schema.json) — собирается автоматически из спецификации и рендера."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .spec import Spec, claims_index, find_layer, node_from_layer
from .style import ROOT, domain_dir
from . import tts


def _checklist(spec: Spec, domain: str) -> list[dict]:
    pc = spec.get("physics_checklist") or {}
    texts = {}
    f = domain_dir(domain) / "physics_checklist.json"
    if f.exists():
        texts = {x["id"]: x["item"] for x in json.loads(f.read_text(encoding="utf-8"))}
    if isinstance(pc, list):
        return [{"item": x.get("item", ""), "answer": x.get("answer", "н/п"), "note": x.get("note", "")} for x in pc]
    out = []
    for pid, v in pc.items():
        v = v if isinstance(v, dict) else {"answer": v}
        ans = v.get("answer", "н/п")
        out.append({"item": texts.get(pid, pid), "answer": ans if ans in ("да", "нет", "н/п") else "н/п",
                    "note": v.get("note", "")})
    return out


def build_passport(spec: Spec, domain: str, segs, key_times, frame_files, timeline_info, audio_info,
                   checks: list[dict], out_dir: Path) -> dict:
    layer, layer_path = find_layer(spec)
    claims = claims_index(layer)
    node = node_from_layer(layer, spec.get("node_id"))
    if node:
        card = {k: node.get(k) for k in ("id", "title", "difficulty", "threshold", "pages", "misconception",
                                          "layers") if k in node}
        card["layer_file"] = str(layer_path.relative_to(ROOT)) if layer_path else None
    else:
        card = {"id": spec.get("node_id"), "title": spec.get("title"), "found_in_layer": False}

    script, used = [], []
    for s in segs:
        b = s.beat
        if not b.say:
            continue
        script.append({"t_start": round(s.speech_start, 2), "t_end": round(s.speech_start + s.speech_dur, 2),
                       "text": b.say, "claim_id": b.claim_id, "is_analogy": b.is_analogy,
                       "on_screen": b.on_screen})
        if b.claim_id and b.claim_id not in used:
            used.append(b.claim_id)
    sources = []
    for cid in used:
        c = claims.get(cid)
        if c:
            src = {"claim_id": cid, "page_pdf": c.get("page_pdf"),
                   "page_or_dataset": f"стр. PDF {c.get('page_pdf')}"}
            if c.get("quote"):
                src["quote"] = c["quote"]
        else:
            src = {"claim_id": cid, "page_or_dataset": "НЕ НАЙДЕНО в учебном слое"}
        sources.append(src)

    frames = [{"t": round(t, 2), "class": s.beat.frame_class, "label": s.beat.label, "file": f,
               "on_screen": s.beat.on_screen}
              for s, t, f in zip(segs, key_times, frame_files)]

    # проверка звука
    _, stress = tts.pronunciation(domain)
    said = " ".join(s.beat.say for s in segs if s.beat.say)
    listen = list(spec.get("listen_check") or [])
    for w in re.findall(r"[А-Яа-яЁё]+", said):
        acc = stress.get(w.lower())
        if acc and acc not in listen:
            listen.append(acc)
    per = []
    for s in segs:
        if not s.beat.say:
            continue
        n = len(s.beat.say.split())
        per.append({"beat": s.beat.index + 1, "words": n, "sec": round(s.speech_dur, 2),
                    "wpm": round(n / s.speech_dur * 60, 1) if s.speech_dur else None,
                    "engine": s.tts_info.get("engine"), "fallback": s.tts_info.get("fallback", False),
                    "spoken": s.tts_info.get("spoken")})
    audio_check = {
        "engines": sorted({p["engine"] for p in per if p["engine"]}),
        "voice": sorted({s.tts_info.get("voice") for s in segs if s.tts_info.get("voice")}),
        "edge_rate": timeline_info.get("rate"),
        "wpm_overall": timeline_info.get("wpm"),
        "max_words_in_phrase": max((p["words"] for p in per), default=0),
        "loudness": audio_info,
        "sync": "длительность кадра фразы = длительность её аудио; голос начинается через "
                "0,15 с после смены кадра",
        "digits_in_script": sorted(set(re.findall(r"\d+(?:[.,]\d+)?", said))),
        "listen_check": listen,
        "phrases": per,
        "silent_phrases": [p["beat"] for p in per if p["engine"] == "none"],
    }
    rel_spec = str(spec.path.relative_to(ROOT)) if spec.path.is_relative_to(ROOT) else str(spec.path)
    return {
        "video_id": spec.video_id,
        "node_id": spec.get("node_id"),
        "node_card": card,
        "script": script,
        "sources": sources,
        "scene_spec": rel_spec,
        "frames": frames,
        "exaggerations": list(spec.get("exaggerations") or []),
        "prediction_question": spec.get("prediction_question", ""),
        "transfer_question": spec.get("transfer_question", ""),
        "data_it_bridge": spec.get("data_it_bridge", ""),
        "physics_checklist": _checklist(spec, domain),
        "audio_check": audio_check,
        "scene_check": checks,
        "template": spec.template,
        "warnings": spec.warnings,
    }
