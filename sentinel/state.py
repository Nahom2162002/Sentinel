"""Last-seen state per target, stored as one JSON file per target (no DB)."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, asdict


@dataclass
class TargetState:
    hash: str
    content: str
    last_checked: str


def _state_path(state_dir: str, target_name: str) -> str:
    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", target_name).strip("_").lower()
    os.makedirs(state_dir, exist_ok=True)
    return os.path.join(state_dir, f"{safe_name}.json")


def load_state(state_dir: str, target_name: str) -> TargetState | None:
    path = _state_path(state_dir, target_name)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return TargetState(**data)


def save_state(state_dir: str, target_name: str, state: TargetState) -> None:
    path = _state_path(state_dir, target_name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(state), f, indent=2)
