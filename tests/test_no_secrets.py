"""Ensure no secrets / .env committed in the project tree."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SECRET_PATTERNS = [
    re.compile(r"(?i)(?:api[_-]?key|secret[_-]?key)\s*=\s*['\"][^'\"]{8,}['\"]"),
    re.compile(r"(?i)binance[_-]?(?:api[_-]?)?(?:secret|key)\s*[:=]\s*\S+"),
    re.compile(r"-----BEGIN (RSA |EC )?PRIVATE KEY-----"),
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS-like
]

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "egg-info"}
SKIP_FILES = {"test_no_secrets.py"}  # avoid matching this file's own patterns


def _iter_text_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if path.name in SKIP_FILES:
            continue
        if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in path.parts):
            continue
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".pyc", ".db", ".so"}:
            continue
        yield path


def test_no_dotenv_file_committed():
    env_path = ROOT / ".env"
    assert not env_path.exists(), ".env must not exist in the repo tree"


def test_env_example_has_no_secrets():
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "MODE=PAPER" in example
    assert "LIVE_TRADING=FALSE" in example
    for pat in SECRET_PATTERNS:
        assert not pat.search(example), f"Secret-like pattern in .env.example: {pat}"


def test_tree_has_no_secret_patterns():
    offenders = []
    for path in _iter_text_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pat in SECRET_PATTERNS:
            if pat.search(text):
                offenders.append(f"{path.relative_to(ROOT)} :: {pat.pattern}")
    assert offenders == [], "Potential secrets found:\n" + "\n".join(offenders)


def test_gitignore_includes_env_and_venv():
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gi
    assert ".venv/" in gi or "venv/" in gi
    assert "*.db" in gi

