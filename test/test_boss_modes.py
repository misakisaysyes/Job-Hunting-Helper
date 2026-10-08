"""Request construction and mode validation for the two BOSS list flows."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from collector.models import PlatformCollectionRequest
from collector.base import CollectorHooks
from collector.platforms.boss import BossCollector, boss_api_jobs, build_boss_api_params
from browser.browser import ChromeBrowser


def request(mode: str, *, expect_ids: list[str] | None = None) -> PlatformCollectionRequest:
    return PlatformCollectionRequest(
        platform="boss", mode=mode, keywords=["前端"] if mode == "search" else [],
        cities=["北京"], city_codes={"北京": "101010100"},
        filters={"degree": ["本科", "硕士"], "scale": ["500-999人", "1000-9999人"]},
        encrypt_expect_id=expect_ids or [],
    )


class BossModeTests(unittest.TestCase):
    def test_chrome_page_requests_recommendation_without_opening_recommend_page(self) -> None:
        opened = []
        calls = []
        browser = ChromeBrowser.__new__(ChromeBrowser)
        browser._list_worker = None
        browser._tabs = {"worker": object()}
        def new_tab(url, background=False):
            opened.append(url)
            return "worker"
        def evaluate(expression, payload):
            calls.append(payload)
            return {"http_status": 200, "body": {"code": 0}}
        browser.new_tab = new_tab
        browser._tabs["worker"] = type("Page", (), {"evaluate": staticmethod(evaluate)})()
        fields = {"page": "1", "encryptExpectId": "example-id"}
        response = browser.request_boss_json(
            "/wapi/zpgeek/pc/recommend/job/list.json", "GET", fields,
        )
        self.assertEqual(response, {"http_status": 200, "body": {"code": 0}})
        self.assertEqual(opened, ["https://www.zhipin.com/robots.txt"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["fields"]["encryptExpectId"], "example-id")
        self.assertEqual(fields, {"page": "1", "encryptExpectId": "example-id"})

    def test_recommendation_get_parameters_keep_filters_and_expect_id(self) -> None:
        params = build_boss_api_params(
            request("recommend", expect_ids=["first-id", "example-id"]),
            2, "101010100", "", "example-id",
        )
        self.assertEqual(params["page"], "2")
        self.assertEqual(params["encryptExpectId"], "example-id")
        self.assertEqual(params["degree"], "203,204")
        self.assertEqual(params["scale"], "304,305")
        self.assertNotIn("query", params)

    def test_multiple_expect_ids_require_selecting_one_for_a_request(self) -> None:
        with self.assertRaisesRegex(ValueError, "当前求职期望"):
            build_boss_api_params(
                request("recommend", expect_ids=["first-id", "second-id"]),
                1, "101010100", "",
            )
        params = build_boss_api_params(
            request("recommend", expect_ids=["only-id"]), 1, "101010100", "",
        )
        self.assertEqual(params["encryptExpectId"], "only-id")

    def test_search_post_parameters_leave_expect_id_empty(self) -> None:
        params = build_boss_api_params(request("search"), 1, "101010100", "前端")
        self.assertEqual(params["query"], "前端")
        self.assertEqual(params["encryptExpectId"], "")
        self.assertEqual(params["city"], "101010100")
        self.assertEqual(params["scene"], "1")
        self.assertEqual(params["scale"], "304,305")

    def test_recommendation_requests_each_expect_id_separately(self) -> None:
        calls = []
        checked = []
        completed = []

        class Browser:
            def request_boss_json(self, path, method, params):
                calls.append((path, method, params.copy()))
                return {"http_status": 200, "body": {
                    "code": 0, "zpData": {"jobList": [], "hasMore": False},
                }}

        hooks = CollectorHooks(
            stop_event=None, on_list_candidate=lambda job: True,
            on_candidate=lambda job: True, on_parse_failed=lambda message: None,
            on_event=lambda **event: None,
            completed_page=lambda city, keyword: checked.append((city, keyword)) or 0,
            on_page_complete=lambda city, keyword, page: completed.append((city, keyword, page)),
        )
        result = BossCollector(browser=Browser()).collect(
            request("recommend", expect_ids=["first-id", "second-id"]), hooks,
        )
        self.assertEqual(result.reason_code, "no_jobs_extracted")
        self.assertEqual([call[2]["encryptExpectId"] for call in calls], ["first-id", "second-id"])
        self.assertTrue(all(call[1] == "GET" for call in calls))
        self.assertEqual(checked, [("北京", "first-id"), ("北京", "second-id")])
        self.assertEqual(completed, [])  # Empty pages are not checkpointed.

    def test_recommendation_response_error_keeps_boss_code_and_message(self) -> None:
        class Browser:
            def request_boss_json(self, path, method, params):
                return {"http_status": 200, "body": {"code": 1001, "message": "列表暂不可用"}}

        errors = []
        hooks = CollectorHooks(
            stop_event=None, on_list_candidate=lambda job: True,
            on_candidate=lambda job: True, on_parse_failed=errors.append,
            on_event=lambda **event: None, completed_page=lambda city, keyword: 0,
        )
        result = BossCollector(browser=Browser()).collect(
            request("recommend", expect_ids=["example-id"]), hooks,
        )
        self.assertEqual(result.reason_code, "api_response_failed")
        self.assertIn("HTTP 200，code=1001，列表暂不可用", result.message)
        self.assertIn("code=1001", errors[0])

    def test_recommendation_http_429_is_rate_limit(self) -> None:
        class Browser:
            def request_boss_json(self, path, method, params):
                return {"http_status": 429, "body": None}

        hooks = CollectorHooks(
            stop_event=None, on_list_candidate=lambda job: True,
            on_candidate=lambda job: True, on_parse_failed=lambda message: None,
            on_event=lambda **event: None, completed_page=lambda city, keyword: 0,
        )
        result = BossCollector(browser=Browser(), randint=lambda low, high: low).collect(
            request("recommend", expect_ids=["example-id"]), hooks,
        )
        self.assertEqual(result.reason_code, "rate_limit")
        self.assertEqual(result.status, "blocked")

    def test_api_jobs_include_company_size_for_list_prefilter(self) -> None:
        jobs = boss_api_jobs([{
            "encryptJobId": "opaque-job-id", "jobName": "前端开发", "salaryDesc": "20-30K",
            "jobExperience": "3-5年", "jobDegree": "本科", "brandName": "示例科技",
            "brandScaleName": "500-999人", "brandIndustry": "互联网", "cityName": "北京",
            "bossTitle": "招聘者",
        }])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["company_size"], "500-999人")
        self.assertEqual(jobs[0]["url"], "/job_detail/opaque-job-id.html")

    def test_cli_requires_mode_and_recommend_expect_id(self) -> None:
        commands = (
            ([], "--mode"),
            (["--mode", "recommend"], "需要非空的 --encrypt-expect-id"),
            (["--mode", "search", "--keyword", "前端", "--encrypt-expect-id", "unused"],
             "不使用 --encrypt-expect-id"),
            (["--mode", "recommend", "--encrypt-expect-id", "first-id,"],
             "不能包含空 ID"),
            (["--mode", "recommend", "--keyword", "前端"], "不接受 --keyword"),
            (["--mode", "search"], "需要至少一个 --keyword"),
        )
        for options, expected in commands:
            with self.subTest(options=options):
                run = subprocess.run([sys.executable, str(ROOT / "cli" / "cli.py"), *options],
                                     text=True, capture_output=True, timeout=10)
                self.assertNotEqual(run.returncode, 0)
                self.assertIn(expected, run.stderr)


if __name__ == "__main__":
    unittest.main()
