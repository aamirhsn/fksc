from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv


RUNTIME_FILE = "runtime.json"  # settings changed from the dashboard, layered over config.yaml


@dataclass
class Config:
    raw: dict
    base_dir: Path

    def set(self, dotted: str, value):
        parts = dotted.split(".")
        cur = self.raw
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[parts[-1]] = value
        overlay_path = self.base_dir / RUNTIME_FILE
        overlay = json.loads(overlay_path.read_text()) if overlay_path.exists() else {}
        overlay[dotted] = value
        overlay_path.write_text(json.dumps(overlay, indent=2))

    def get(self, dotted: str, default=None):
        cur = self.raw
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def path(self, dotted: str, default: str) -> Path:
        p = Path(self.get(dotted, default))
        return p if p.is_absolute() else self.base_dir / p


def load_config(path: str | os.PathLike = "config.yaml") -> Config:
    path = Path(path).resolve()
    load_dotenv(path.parent / ".env")
    if not path.exists():
        raise SystemExit(f"{path} not found. Copy config.example.yaml to config.yaml and edit it.")
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    cfg = Config(raw=raw, base_dir=path.parent)
    overlay_path = path.parent / RUNTIME_FILE
    if overlay_path.exists():
        for dotted, value in json.loads(overlay_path.read_text()).items():
            parts, cur = dotted.split("."), cfg.raw
            for part in parts[:-1]:
                cur = cur.setdefault(part, {})
            cur[parts[-1]] = value
    return cfg


def env(name: str, required: bool = False) -> str:
    val = os.environ.get(name, "").strip()
    if required and not val:
        raise SystemExit(f"Environment variable {name} is not set (see .env.example)")
    return val
