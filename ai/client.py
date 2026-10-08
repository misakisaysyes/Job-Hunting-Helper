"""One OpenAI-compatible API request per call."""

from __future__ import annotations

import math
import random
import time

import httpx

from ai.credentials import current_api_key


RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})


class AIRequestError(RuntimeError):
    """A request failed without exposing its API key or raw response body."""

    def __init__(self, message: str, *, status_code: int | None = None,
                 retryable: bool = False) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


def api_concurrency(config: dict) -> int:
    value = (config.get("ai") or {}).get("ai_api_concurrency")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("ai.ai_api_concurrency 必须是正整数")
    return value


def call_model(config: dict, system_prompt: str, user_prompt: str) -> str:
    """Call the configured model and return its final answer text."""
    ai = config.get("ai") or {}
    if ai.get("provider") != "openai_compatible":
        raise ValueError("当前仅支持 provider=openai_compatible")
    model = str(ai.get("model") or "").strip()
    base_url = str(ai.get("base_url") or "").strip().rstrip("/")
    key_env = str(ai.get("api_key_env") or "").strip()
    if not model or not base_url or not key_env:
        raise ValueError("ai.model、ai.base_url 和 ai.api_key_env 必须配置")
    service = str(ai.get("service") or "").strip().lower()
    if not service:
        raise ValueError("ai.service 必须配置")
    thinking = str(ai.get("thinking") or "").strip().lower()
    if thinking not in {"auto", "enabled", "disabled"}:
        raise ValueError("ai.thinking 只能是 auto、enabled 或 disabled")
    timeout = ai.get("timeout_seconds")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError("ai.timeout_seconds 必须大于 0")
    retry_count = ai.get("retry_count")
    if isinstance(retry_count, bool) or not isinstance(retry_count, int) or retry_count < 0:
        raise ValueError("ai.retry_count 必须是非负整数")
    retry_min = ai.get("retry_delay_min_seconds")
    retry_max = ai.get("retry_delay_max_seconds")
    if (any(isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0 for value in (retry_min, retry_max))
            or retry_max < retry_min):
        raise ValueError("ai.retry_delay_min_seconds 和 ai.retry_delay_max_seconds 必须是有效等待范围")
    api_key = current_api_key(key_env)
    if not api_key:
        raise AIRequestError(f"缺少 AI API Key：请在配置页填写，或设置环境变量 {key_env}")
    if not system_prompt.strip() or not user_prompt.strip():
        raise ValueError("AI 提示词不能为空")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
    }
    if service == "deepseek" and thinking != "auto":
        payload["thinking"] = {"type": thinking}

    for attempt in range(retry_count + 1):
        try:
            response = httpx.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
                timeout=timeout,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            if attempt < retry_count:
                time.sleep(random.uniform(retry_min, retry_max))
                continue
            raise AIRequestError(f"AI API 请求失败：{type(exc).__name__}", retryable=True) from exc
        except httpx.RequestError as exc:
            raise AIRequestError(f"AI API 请求失败：{type(exc).__name__}") from exc
        if response.status_code >= 400:
            retryable = response.status_code in RETRYABLE_STATUS_CODES
            if retryable and attempt < retry_count:
                time.sleep(random.uniform(retry_min, retry_max))
                continue
            raise AIRequestError(
                f"AI API 返回 HTTP {response.status_code}",
                status_code=response.status_code,
                retryable=retryable,
            )
        break
    try:
        choice = response.json()["choices"][0]
        if choice.get("finish_reason") == "length":
            raise AIRequestError("AI 回复超过输出长度限制")
        content = choice["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise AIRequestError("AI API 未返回有效文本")
        return content.strip()
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise AIRequestError("AI API 响应格式无效") from exc
