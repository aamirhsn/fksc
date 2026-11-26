from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv


@dataclass
class Config:
    raw: dict
    base_dir: Path

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
    return Config(raw=raw, base_dir=path.parent)


def env(name: str, required: bool = False) -> str:
    val = os.environ.get(name, "").strip()
    if required and not val:
        raise SystemExit(f"Environment variable {name} is not set (see .env.example)")
    return val
