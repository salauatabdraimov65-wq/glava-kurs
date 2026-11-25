"""Спецификация сцены (формат организаторов): чтение, проверка, поиск учебного слоя.

Обязательные поля: template, params, beats[]. Каждый beat: say, claim_id, on_screen, label,
frame_class, action (+ необязательные pause, is_analogy). Остальные поля (invariants,
assumptions, exaggerations, plaque, prediction_question, transfer_question, data_it_bridge,
physics_checklist, listen_check) переносятся в паспорт.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .style import ROOT

LABELS = ("реальные данные", "симуляция", "схема")
CLASSES = ("A", "B", "C", "D")


@dataclass
class Beat:
    index: int
    say: str
    claim_id: str | None
    on_screen: str
    label: str
    frame_class: str
    action: dict
    pause: float = 0.0
    is_analogy: bool = False
    raw: dict = field(default_factory=dict)


@dataclass
class Spec:
    path: Path
    data: dict
    beats: list[Beat]
    warnings: list[str]

    @property
    def video_id(self) -> str:
        return self.data.get("video_id") or self.path.parent.name

    @property
    def template(self) -> str:
        return self.data["template"]

    @property
    def params(self) -> dict:
        return self.data.get("params", {})

    def get(self, key, default=None):
        return self.data.get(key, default)


def load_spec(path: str | Path) -> Spec:
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    # пакет организаторов: {"spec": {...}} внутри scenes[] — берём саму спецификацию
    if "spec" in data and "beats" not in data:
        data = data["spec"]
    warnings: list[str] = []
    for key in ("template", "params", "beats"):
        if key not in data:
            raise ValueError(f"spec.json: нет обязательного поля «{key}»")
    beats = []
    for i, b in enumerate(data["beats"]):
        label = b.get("label", "схема")
        if label not in LABELS:
            warnings.append(f"beat {i + 1}: метка «{label}» не из {LABELS}, ставлю «схема»")
            label = "схема"
        fc = b.get("frame_class", "B")
        if fc not in CLASSES:
            warnings.append(f"beat {i + 1}: класс кадра «{fc}» не из {CLASSES}, ставлю B")
            fc = "B"
        say = (b.get("say") or "").strip()
        if say and not b.get("claim_id") and not b.get("is_analogy"):
            warnings.append(f"beat {i + 1}: фраза без claim_id — голос не опирается на утверждение")
        if say and len(say.split()) > 18:
            warnings.append(f"beat {i + 1}: фраза длиннее 18 слов ({len(say.split())})")
        beats.append(Beat(
            index=i, say=say, claim_id=b.get("claim_id"), on_screen=(b.get("on_screen") or "").strip(),
            label=label, frame_class=fc, action=dict(b.get("action") or {}),
            pause=float(b.get("pause") or 0.0), is_analogy=bool(b.get("is_analogy", False)), raw=b,
        ))
    if not data.get("plaque") and data.get("exaggerations"):
        warnings.append("есть exaggerations, но нет plaque «не в масштабе» — плашка будет общей")
    return Spec(path=path, data=data, beats=beats, warnings=warnings)


def find_layer(spec: Spec) -> tuple[dict | None, Path | None]:
    """Учебный слой для узла: явный путь в spec.layer, runs/<chapter>/layer.json рядом, samples/*."""
    cands: list[Path] = []
    if spec.get("layer"):
        p = Path(spec.get("layer"))
        cands.append(p if p.is_absolute() else (ROOT / p))
    # runs/<chapter>/video/<vid>/spec.json → runs/<chapter>/layer.json
    for parent in spec.path.parents[:4]:
        cands.append(parent / "layer.json")
    if spec.get("chapter"):
        cands.append(ROOT / "runs" / spec.get("chapter") / "layer.json")
    cands += sorted((ROOT / "samples").glob("*/layer.json"))
    node_id = spec.get("node_id")
    for c in cands:
        if not c.exists():
            continue
        try:
            layer = json.loads(c.read_text(encoding="utf-8"))
        except ValueError:
            continue
        nodes = layer.get("nodes", layer if isinstance(layer, list) else [])
        if any(n.get("id") == node_id for n in nodes):
            return layer, c
    return None, None


def node_from_layer(layer: dict | None, node_id: str) -> dict | None:
    if not layer:
        return None
    for n in layer.get("nodes", []):
        if n.get("id") == node_id:
            return n
    return None


def claims_index(layer: dict | None) -> dict[str, dict]:
    idx: dict[str, dict] = {}
    if not layer:
        return idx
    for n in layer.get("nodes", []):
        for c in n.get("claims", []):
            idx[c["id"]] = c
    return idx
