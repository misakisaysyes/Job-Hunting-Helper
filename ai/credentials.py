"""Local AI API key storage; never expose the key through settings responses."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from config import PROJECT_ROOT


API_KEY_PATH = PROJECT_ROOT / ".config.credentials.yaml"
LEGACY_API_KEY_PATH = PROJECT_ROOT / ".state" / "ai-api-key"


def _valid_key(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 512 \
        and "\n" not in value and "\r" not in value


def save_api_key(value: str) -> None:
    if not _valid_key(value):
        raise ValueError("API Key 须为不超过 512 字符的单行文本")
    API_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".config.credentials-", dir=API_KEY_PATH.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write("ai:\n  api_key: " + json.dumps(value.strip(), ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o600)
        os.replace(name, API_KEY_PATH)
        try:
            LEGACY_API_KEY_PATH.unlink(missing_ok=True)
        except OSError:
            pass
    finally:
        Path(name).unlink(missing_ok=True)


def _read_saved_key() -> str:
    if API_KEY_PATH.is_file():
        lines = API_KEY_PATH.read_text(encoding="utf-8").splitlines()
        if not lines or lines[0] != "ai:":
            return ""
        for line in lines[1:]:
            if line.startswith("  api_key: "):
                try:
                    value = json.loads(line.removeprefix("  api_key: "))
                except (TypeError, ValueError):
                    return ""
                return value.strip() if _valid_key(value) else ""
        return ""
    return LEGACY_API_KEY_PATH.read_text(encoding="utf-8").strip() \
        if LEGACY_API_KEY_PATH.is_file() else ""


def _migrate_legacy_key() -> None:
    if API_KEY_PATH.is_file() or not LEGACY_API_KEY_PATH.is_file():
        return
    try:
        save_api_key(LEGACY_API_KEY_PATH.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        # A failed migration must not prevent the existing key from working.
        pass


_migrate_legacy_key()
_active_key = _read_saved_key()


def activate_api_key() -> None:
    global _active_key
    _active_key = _read_saved_key()


def saved_key_configured() -> bool:
    return bool(_read_saved_key())


def key_pending_apply() -> bool:
    return _read_saved_key() != _active_key


def current_api_key(key_env: str) -> str:
    return _active_key or os.environ.get(key_env, "").strip()
