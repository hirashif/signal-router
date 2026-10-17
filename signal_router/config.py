from __future__ import annotations

import os
from pathlib import Path

import yaml


def load_env(path: str = ".env") -> None:
    """Minimal .env loader (KEY=value lines). Real env vars win."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_config(path: str = "config.yaml") -> dict:
    p = Path(path)
    if not p.exists():
        p = Path("config.example.yaml")
    return yaml.safe_load(p.read_text())
