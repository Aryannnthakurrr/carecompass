"""Shared helpers for smoke tests: load config.yaml and .env, output dir."""
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)

load_dotenv(ROOT / ".env")
CFG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def require_env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        sys.exit(f"{name} is not set. Copy .env.example to .env and fill it in.")
    return val
