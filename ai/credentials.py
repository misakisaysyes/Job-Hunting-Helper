"""Local AI API key storage; never expose the key through settings responses."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

from config import PROJECT_ROOT


API_KEY_PATH = PROJECT_ROOT / ".state" / "ai-api-key"


def _read_saved_key() -> str:
    if not API_KEY_PATH.is_file():
        return ""
    return API_KEY_PATH.read_text(encoding="utf-8").strip()


_active_key = _read_saved_key()


def save_api_key(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 512 \
            or "\n" in value or "\r" in value:
        raise ValueError("API Key 须为不超过 512 字符的单行文本")
    API_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".ai-api-key-", dir=API_KEY_PATH.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(value.strip())
        os.chmod(name, 0o600)
        os.replace(name, API_KEY_PATH)
    finally:
        Path(name).unlink(missing_ok=True)


def activate_api_key() -> None:
    global _active_key
    _active_key = _read_saved_key()


def saved_key_configured() -> bool:
    return bool(_read_saved_key())


def key_pending_apply() -> bool:
    return _read_saved_key() != _active_key


def current_api_key(key_env: str) -> str:
    return _active_key or os.environ.get(key_env, "").strip()
