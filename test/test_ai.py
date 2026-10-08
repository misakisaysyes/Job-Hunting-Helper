"""Verify API calls, task results, and shared concurrency without live requests."""

from __future__ import annotations

import json
import os
import sys
import threading
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai import (
    AIRequestError, AITaskScheduler, call_model, wrap_ai_task,
)
from config import DEFAULT_CONFIG
from data.job_input import load_resume_text
from message import make_greeting_task
from score import make_score_task


def job(job_id: str) -> dict[str, str]:
    return {
        "id": job_id, "title": "前端工程师", "company": "示例科技",
        "salary": "20-30K", "jd": "负责浏览器性能优化", "score_reason": "符合前端经验",
    }


class ClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = deepcopy(DEFAULT_CONFIG)
        self.config["ai"]["ai_api_concurrency"] = 2

    def test_deepseek_request_uses_config_and_returns_only_answer(self) -> None:
        calls = []

        def respond(url: str, **kwargs: object) -> httpx.Response:
            calls.append((url, kwargs))
            return httpx.Response(200, json={"choices": [{"message": {
                "reasoning_content": "内部推理", "content": "最终答案",
            }, "finish_reason": "stop"}]})

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}):
            with patch("ai.client.httpx.post", side_effect=respond):
                self.assertEqual(call_model(self.config, "系统要求", "用户输入"), "最终答案")
        url, kwargs = calls[0]
        self.assertEqual(url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-key")
        body = kwargs["json"]
        self.assertEqual(body["model"], self.config["ai"]["model"])
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["messages"][1]["content"], "用户输入")
        self.assertEqual(kwargs["timeout"], self.config["ai"]["timeout_seconds"])

    def test_missing_ai_config_does_not_use_module_defaults(self) -> None:
        for key in ("service", "thinking", "timeout_seconds", "retry_count"):
            with self.subTest(key=key):
                config = deepcopy(self.config)
                config["ai"].pop(key)
                with self.assertRaisesRegex(ValueError, f"ai.{key}"):
                    call_model(config, "系统", "用户")
        config = deepcopy(self.config)
        config["ai"].pop("ai_api_concurrency")
        with self.assertRaisesRegex(ValueError, "ai.ai_api_concurrency"):
            AITaskScheduler(config)

    def test_missing_key_and_http_error_fail_without_leaking_response(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(AIRequestError, "DEEPSEEK_API_KEY"):
                call_model(self.config, "系统", "用户")
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}):
            with patch("ai.client.httpx.post", return_value=httpx.Response(
                429, text="private error details",
            )):
                with patch("ai.client.time.sleep"):
                    with self.assertRaises(AIRequestError) as raised:
                        call_model(self.config, "系统", "用户")
        self.assertEqual(raised.exception.status_code, 429)
        self.assertTrue(raised.exception.retryable)
        self.assertNotIn("private error details", str(raised.exception))

    def test_retryable_http_error_waits_randomly_and_retries_once(self) -> None:
        success = httpx.Response(200, json={"choices": [{"message": {"content": "成功"}}]})
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}):
            with patch("ai.client.httpx.post", side_effect=[httpx.Response(429), success]) as post:
                with patch("ai.client.random.uniform", return_value=1.7) as jitter:
                    with patch("ai.client.time.sleep") as sleep:
                        self.assertEqual(call_model(self.config, "系统", "用户"), "成功")
        self.assertEqual(post.call_count, 2)
        jitter.assert_called_once_with(1.0, 3.0)
        sleep.assert_called_once_with(1.7)

    def test_retryable_http_error_stops_after_second_failure(self) -> None:
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}):
            with patch("ai.client.httpx.post", side_effect=[httpx.Response(503), httpx.Response(503)]) as post:
                with patch("ai.client.time.sleep") as sleep:
                    with self.assertRaises(AIRequestError) as raised:
                        call_model(self.config, "系统", "用户")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(sleep.call_count, 1)
        self.assertEqual(raised.exception.status_code, 503)
        self.assertTrue(raised.exception.retryable)

    def test_configured_retry_count_controls_extra_attempts(self) -> None:
        self.config["ai"]["retry_count"] = 2
        success = httpx.Response(200, json={"choices": [{"message": {"content": "成功"}}]})
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}):
            with patch("ai.client.httpx.post", side_effect=[
                httpx.Response(429), httpx.Response(503), success,
            ]) as post:
                with patch("ai.client.time.sleep") as sleep:
                    self.assertEqual(call_model(self.config, "系统", "用户"), "成功")
        self.assertEqual(post.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

        self.config["ai"]["retry_count"] = 0
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}):
            with patch("ai.client.httpx.post", return_value=httpx.Response(429)) as post:
                with patch("ai.client.time.sleep") as sleep:
                    with self.assertRaises(AIRequestError):
                        call_model(self.config, "系统", "用户")
        post.assert_called_once()
        sleep.assert_not_called()

    def test_nonretryable_http_error_does_not_retry(self) -> None:
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}):
            with patch("ai.client.httpx.post", return_value=httpx.Response(401)) as post:
                with patch("ai.client.time.sleep") as sleep:
                    with self.assertRaises(AIRequestError) as raised:
                        call_model(self.config, "系统", "用户")
        post.assert_called_once()
        sleep.assert_not_called()
        self.assertEqual(raised.exception.status_code, 401)
        self.assertFalse(raised.exception.retryable)

    def test_network_error_retries_once(self) -> None:
        success = httpx.Response(200, json={"choices": [{"message": {"content": "成功"}}]})
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}):
            with patch("ai.client.httpx.post", side_effect=[httpx.ConnectError("断线"), success]) as post:
                with patch("ai.client.time.sleep"):
                    self.assertEqual(call_model(self.config, "系统", "用户"), "成功")
        self.assertEqual(post.call_count, 2)


class SchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = deepcopy(DEFAULT_CONFIG)
        self.config["ai"]["ai_api_concurrency"] = 2

    def test_scoring_and_greetings_share_limit_and_queue(self) -> None:
        release = threading.Event()
        two_started = threading.Event()
        lock = threading.Lock()
        active = 0
        peak = 0

        def model_call(system: str, user: str) -> str:
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                if active == 2:
                    two_started.set()
            try:
                if not release.wait(timeout=3):
                    raise TimeoutError("worker was not released")
                return ('{"score": 86, "reason": "经验吻合"}'
                        if "评分" in system else "您好，我有浏览器优化经验，想聊聊这个岗位。")
            finally:
                with lock:
                    active -= 1

        with AITaskScheduler(self.config, model_call=model_call) as scheduler:
            futures = [
                scheduler.submit(make_score_task(job("1"), "做过前端性能优化", "看重性能")),
                scheduler.submit(make_greeting_task(job("2"), "做过前端性能优化")),
                scheduler.submit(make_score_task(job("3"), "做过前端性能优化")),
                scheduler.submit(make_greeting_task(job("4"), "做过前端性能优化")),
            ]
            try:
                self.assertTrue(two_started.wait(timeout=3))
                self.assertFalse(futures[2].done())
                self.assertFalse(futures[3].done())
            finally:
                release.set()
        self.assertEqual(peak, 2)
        self.assertEqual([future.result(timeout=1).job_id for future in futures], ["1", "2", "3", "4"])
        self.assertEqual(futures[0].result().score, 86)
        self.assertIn("您好", futures[1].result().text)

    def test_score_task_runs_through_scheduler_and_http_call(self) -> None:
        def respond(url: str, **kwargs: object) -> httpx.Response:
            self.assertIn("浏览器性能优化", kwargs["json"]["messages"][1]["content"])
            return httpx.Response(200, json={"choices": [{
                "message": {"content": '{"score": 91, "reason": "有性能优化经验"}'},
                "finish_reason": "stop",
            }]})

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}):
            with patch("ai.client.httpx.post", side_effect=respond):
                with AITaskScheduler(self.config) as scheduler:
                    result = scheduler.submit(make_score_task(
                        job("live-shape"), "做过浏览器性能优化",
                    )).result(timeout=2)
        self.assertEqual((result.job_id, result.score), ("live-shape", 91))

    def test_pending_task_can_be_cancelled_and_new_types_need_no_scheduler_change(self) -> None:
        started = threading.Event()
        release = threading.Event()
        self.config["ai"]["ai_api_concurrency"] = 1

        with AITaskScheduler(self.config, model_call=lambda system, user: "unused") as scheduler:
            try:
                first = scheduler.submit(wrap_ai_task(
                    "custom", "1", lambda call: started.set() or release.wait(timeout=3),
                ))
                self.assertTrue(started.wait(timeout=3))
                pending = scheduler.submit(wrap_ai_task("future_type", "2", lambda call: "unused"))
                self.assertTrue(pending.cancel())
            finally:
                release.set()
        self.assertTrue(first.result(timeout=1))

    def test_resume_loader_and_invalid_score(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "resume.md"
            path.write_text("有浏览器优化经验", encoding="utf-8")
            self.config["profile"]["resume_path"] = str(path)
            self.assertEqual(load_resume_text(self.config), "有浏览器优化经验")

        task = make_score_task(job("1"), "有浏览器优化经验")
        with self.assertRaisesRegex(AIRequestError, "有效分数"):
            task.run(lambda system, user: '{"score": 120, "reason": "越界"}')

    def test_task_prompts_include_resume_jd_and_user_instruction(self) -> None:
        captured = []

        def model_call(system: str, user: str) -> str:
            captured.append(json.loads(user))
            return ('{"score": 79, "reason": "具备相关经验"}'
                    if "评分" in system else "您好，我做过浏览器优化，想和您聊聊性能方向。")

        score = make_score_task(job("one"), "五年前端经验", "偏好性能方向").run(model_call)
        greeting = make_greeting_task(job("one"), "五年前端经验", "语气自然").run(model_call)
        self.assertEqual(score.score, 79)
        self.assertEqual(greeting.job_id, "one")
        self.assertEqual(captured[0]["简历"], "五年前端经验")
        self.assertIn("浏览器性能优化", captured[0]["岗位"]["jd"])
        self.assertEqual(captured[0]["用户补充要求"], "偏好性能方向")
        self.assertEqual(captured[1]["用户补充要求"], "语气自然")


if __name__ == "__main__":
    unittest.main()
