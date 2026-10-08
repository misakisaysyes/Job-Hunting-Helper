"""Read and save workbench basics and the complete runtime configuration."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
from threading import Lock
import tempfile

from config import CITY_CODES, DEFAULT_CONFIG, MONITORING_CONFIG, PROJECT_ROOT, USER_CONFIG_PATH
from ai.credentials import (activate_api_key, current_api_key, key_pending_apply,
                            save_api_key, saved_key_configured)
from run import validate_run_config


class SettingsError(ValueError):
    pass


_LOCK = Lock()
_BASIC_KEYS = {
    "collection": {"mode", "encrypt_expect_id", "keywords"},
    "browser": {"cdp_url"},
    "profile": {"resume_path"},
    "ai": {"use_ai_score", "use_ai_greeting"},
    "monitoring": {"message_limit", "followup_enabled",
                   "followup_days", "followup_unread", "followup_read_no_reply",
                   "followup_cooldown_hours", "followup_max_count", "followup_review_required",
                   "filter_review_required"},
}
_EMPTY_STRINGS = {("collection", "city"), ("collection", "city_code"),
                  ("ai", "score_user_prompt"), ("ai", "greeting_user_prompt"),
                  ("ai", "greeting_template")}
RESUME_UPLOAD_DIR = PROJECT_ROOT / ".state" / "resumes"
MAX_RESUME_BYTES = 1024 * 1024


def _current_values() -> dict:
    return {
        section: {key: deepcopy((MONITORING_CONFIG if section == "monitoring"
                                else DEFAULT_CONFIG[section])[key]) for key in keys}
        for section, keys in _BASIC_KEYS.items()
    }


def read_basic_settings() -> dict:
    values = _current_values()
    return {
        "settings": values,
        "preview": {
            "collection": {
                "max_pages": DEFAULT_CONFIG["collection"]["max_pages"],
                "target_jobs": DEFAULT_CONFIG["collection"]["target_jobs"],
            },
            "ai": {
                "score_threshold": DEFAULT_CONFIG["ai"]["score_threshold"],
                "ai_api_concurrency": DEFAULT_CONFIG["ai"]["ai_api_concurrency"],
            },
        },
        "checks": {
            "resume_exists": Path(values["profile"]["resume_path"]).is_file(),
            "api_key_ready": bool(current_api_key(DEFAULT_CONFIG["ai"]["api_key_env"])),
            "api_key_env": DEFAULT_CONFIG["ai"]["api_key_env"],
        },
    }


def read_full_settings() -> dict:
    active = {**deepcopy(DEFAULT_CONFIG), "monitoring": deepcopy(MONITORING_CONFIG)}
    saved = deepcopy(active)
    if USER_CONFIG_PATH.is_file():
        overrides = json.loads(USER_CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(overrides, dict):
            raise SettingsError("已保存的配置格式错误")
        for section, fields in overrides.items():
            if section in saved and isinstance(fields, dict):
                saved[section].update({key: deepcopy(value) for key, value in fields.items()
                                       if key in saved[section]})
    return {"settings": saved, "city_options": list(CITY_CODES),
            "api_key_configured": saved_key_configured(),
            "pending_apply": saved != active or key_pending_apply()}


def save_resume_upload(filename: str, content: bytes) -> dict:
    """Save a UTF-8 Markdown resume and select it for subsequent AI tasks."""
    if not filename or filename != Path(filename).name or "\\" in filename \
            or Path(filename).suffix.lower() != ".md":
        raise SettingsError("请选择 .md 格式的简历文件")
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_RESUME_BYTES:
        raise SettingsError("简历文件须为非空且不超过 1 MB 的 .md 文件")
    try:
        resume_text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SettingsError("简历文件须使用 UTF-8 编码") from exc
    if not resume_text.strip():
        raise SettingsError("简历文件内容不能为空")

    digest = hashlib.sha256(content).hexdigest()
    destination = RESUME_UPLOAD_DIR / f"resume-{digest}.md"
    RESUME_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    if not destination.is_file():
        descriptor, temp_name = tempfile.mkstemp(prefix=".resume-", suffix=".tmp",
                                                 dir=RESUME_UPLOAD_DIR)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
            os.replace(temp_name, destination)
        finally:
            Path(temp_name).unlink(missing_ok=True)
    result = save_full_settings({"profile": {"resume_path": str(destination)}})
    DEFAULT_CONFIG["profile"]["resume_path"] = str(destination)
    return {"filename": filename, "resume_path": str(destination),
            "settings": result["settings"]}


def _validate_full_value(section: str, key: str, value: object, current: object) -> object:
    name = f"{section}.{key}"
    if isinstance(current, bool):
        if type(value) is not bool:
            raise SettingsError(f"{name} 必须是开关值")
        return value
    if isinstance(current, int):
        if type(value) is not int:
            raise SettingsError(f"{name} 必须是整数")
        return value
    if isinstance(current, float):
        if type(value) not in (int, float) or not math.isfinite(value):
            raise SettingsError(f"{name} 必须是有限数字")
        return float(value)
    if isinstance(current, list):
        return _string_list(value, name)
    if isinstance(current, str):
        if not isinstance(value, str):
            raise SettingsError(f"{name} 必须是文本")
        if not value.strip() and (section, key) not in _EMPTY_STRINGS:
            raise SettingsError(f"{name} 不能为空")
        return value if (section, key) in {
            ("ai", "score_user_prompt"), ("ai", "greeting_user_prompt"),
            ("ai", "greeting_template")
        } else value.strip()
    raise SettingsError(f"不支持的配置类型：{name}")


def save_full_settings(payload: object) -> dict:
    if not isinstance(payload, dict) or not payload:
        raise SettingsError("请提交需要保存的配置")
    payload = deepcopy(payload)
    secret = None
    if isinstance(payload.get("ai"), dict) and "api_key" in payload["ai"]:
        secret = payload["ai"].pop("api_key")
        if not isinstance(secret, str) or not secret.strip() or len(secret) > 512 \
                or "\n" in secret or "\r" in secret:
            raise SettingsError("API Key 须为不超过 512 字符的单行文本")
        if not payload["ai"]:
            del payload["ai"]
    with _LOCK:
        saved_settings = read_full_settings()["settings"]
        collection_config = {key: deepcopy(value) for key, value in saved_settings.items()
                             if key != "monitoring"}
        monitoring_config = deepcopy(saved_settings["monitoring"])
        normalized: dict[str, dict] = {}
        for section, fields in payload.items():
            target = monitoring_config if section == "monitoring" else collection_config.get(section)
            if target is None or not isinstance(fields, dict) or not fields:
                raise SettingsError(f"不支持的配置分组：{section}")
            normalized[section] = {}
            for key, value in fields.items():
                if key not in target:
                    raise SettingsError(f"不支持的配置项：{section}.{key}")
                target[key] = normalized[section][key] = _validate_full_value(
                    section, key, value, target[key])
        try:
            validate_run_config(collection_config)
        except (ValueError, KeyError, TypeError) as exc:
            raise SettingsError(str(exc)) from exc
        _validate_monitoring(monitoring_config)
        current: dict = {}
        if USER_CONFIG_PATH.is_file():
            current = json.loads(USER_CONFIG_PATH.read_text(encoding="utf-8"))
            if not isinstance(current, dict):
                raise SettingsError("已保存的配置格式错误")
        for section, fields in normalized.items():
            current.setdefault(section, {}).update(fields)
        USER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp_path = USER_CONFIG_PATH.with_suffix(".json.tmp")
        temp_path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp_path, USER_CONFIG_PATH)
        if secret is not None:
            save_api_key(secret)
        return read_full_settings()


def apply_full_settings() -> dict:
    """Activate the saved configuration for tasks started after this call."""
    with _LOCK:
        saved = read_full_settings()["settings"]
        collection = {key: value for key, value in saved.items() if key != "monitoring"}
        try:
            validate_run_config(collection)
        except (ValueError, KeyError, TypeError) as exc:
            raise SettingsError(str(exc)) from exc
        _validate_monitoring(saved["monitoring"])
        for section, fields in saved.items():
            (MONITORING_CONFIG if section == "monitoring" else DEFAULT_CONFIG[section]).update(
                deepcopy(fields))
        activate_api_key()
        return read_full_settings()


def _string_list(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise SettingsError(f"{name} 必须是非空字符串组成的列表")
    return list(dict.fromkeys(item.strip() for item in value))


def _validate_monitoring(config: dict) -> None:
    bounds = {"message_limit": (1, 1000),
              "followup_days": (1, 30), "followup_cooldown_hours": (1, 720),
              "followup_max_count": (1, 10)}
    for key, (lower, upper) in bounds.items():
        if type(config.get(key)) is not int or not lower <= config[key] <= upper:
            raise SettingsError(f"monitoring.{key} 必须是 {lower}～{upper} 的整数")
    for key in ("followup_enabled", "followup_unread", "followup_read_no_reply",
                "followup_review_required", "filter_review_required"):
        if type(config.get(key)) is not bool:
            raise SettingsError(f"monitoring.{key} 必须是开关值")


def _validate_value(section: str, key: str, value: object) -> object:
    name = f"{section}.{key}"
    if key in {"encrypt_expect_id", "keywords"}:
        values = _string_list(value, name)
        if key == "encrypt_expect_id" and any("," in item for item in values):
            raise SettingsError("求职期望 ID 请逐项填写，不要在单项内输入逗号")
        return values
    if key in {"use_ai_score", "use_ai_greeting", "followup_enabled", "followup_unread",
               "followup_read_no_reply", "followup_review_required", "filter_review_required"}:
        if type(value) is not bool:
            raise SettingsError(f"{name} 必须是开关值")
        return value
    if key in {"message_limit", "followup_days",
               "followup_cooldown_hours", "followup_max_count"}:
        if type(value) is not int or value < 1 or value > 1000:
            raise SettingsError(f"{name} 必须是 1～1000 的整数")
        return value
    if not isinstance(value, str) or not value.strip():
        raise SettingsError(f"{name} 不能为空")
    value = value.strip()
    if key == "mode" and value not in {"recommend", "search"}:
        raise SettingsError("采集模式只能是推荐流或搜索流")
    return value


def save_basic_settings(payload: object) -> dict:
    if not isinstance(payload, dict) or not payload:
        raise SettingsError("请提交需要保存的配置")
    with _LOCK:
        collection_config = deepcopy(DEFAULT_CONFIG)
        monitoring_config = deepcopy(MONITORING_CONFIG)
        normalized: dict[str, dict] = {}
        for section, fields in payload.items():
            if section not in _BASIC_KEYS or not isinstance(fields, dict):
                raise SettingsError(f"不支持的配置分组：{section}")
            normalized[section] = {}
            for key, value in fields.items():
                if key not in _BASIC_KEYS[section]:
                    raise SettingsError(f"不支持的配置项：{section}.{key}")
                checked = _validate_value(section, key, value)
                normalized[section][key] = checked
                (monitoring_config if section == "monitoring" else collection_config[section])[key] = checked
        try:
            validate_run_config(collection_config)
        except (ValueError, KeyError, TypeError) as exc:
            raise SettingsError(str(exc)) from exc
        _validate_monitoring(monitoring_config)
        current: dict = {}
        if USER_CONFIG_PATH.is_file():
            current = json.loads(USER_CONFIG_PATH.read_text(encoding="utf-8"))
            if not isinstance(current, dict):
                raise SettingsError("已保存的配置格式错误")
        for section, fields in normalized.items():
            current.setdefault(section, {}).update(fields)
        USER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp_path = USER_CONFIG_PATH.with_suffix(".json.tmp")
        temp_path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp_path, USER_CONFIG_PATH)
        for section, fields in normalized.items():
            (MONITORING_CONFIG if section == "monitoring" else DEFAULT_CONFIG[section]).update(fields)
        return read_basic_settings()
