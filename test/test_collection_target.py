"""A collection run rotates sources until its qualifying-job target is reached."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ai import AITaskScheduler
from collector.platforms.boss import JS_DETECT_COLLECTION_RISK, JS_EXTRACT_DETAIL
from config import DEFAULT_CONFIG
from run import run_collection


def api_job(job_id: str) -> dict:
    return {"encryptJobId": job_id, "jobName": "前端工程师", "brandName": "示例公司"}


class FakeBrowser:
    def __init__(self, job_list):
        self.job_list = job_list
        self.requests = []
        self.current_url = ""

    def request_boss_json(self, path, method, params):
        self.requests.append((method, params.copy()))
        return {"http_status": 200, "body": {"code": 0, "zpData": {
            "jobList": self.job_list(method, params, len(self.requests)), "hasMore": False,
        }}}

    def new_tab(self, url, background=False):
        self.current_url = url
        return "detail"

    def navigate(self, tab, url):
        self.current_url = url
        return tab

    def wait_for_load(self, tab, timeout):
        return None

    def evaluate(self, tab, script):
        if script == JS_DETECT_COLLECTION_RISK:
            return '{"risk": null}'
        if script == JS_EXTRACT_DETAIL:
            return json.dumps({"title": "前端工程师", "company": "示例公司",
                               "jd": "负责浏览器性能优化"})
        raise AssertionError("unexpected browser evaluation")

    def close_tab(self, tab):
        return None

    def close(self):
        return None


class CollectionTargetTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = deepcopy(DEFAULT_CONFIG)
        self.config["safety"]["state_db"] = str(Path(self.temp.name) / "jobs.sqlite3")
        self.config["collection"].update(mode="recommend", encrypt_expect_id=["a", "b"],
                                         max_pages=1, max_jobs=0, target_jobs=2)

    def run_with_browser(self, browser, scheduler=None):
        with patch("run.ChromeBrowser", return_value=browser):
            with patch("collector.platforms.boss.PageThrottle.wait", return_value=False):
                with patch("collector.platforms.boss._wait_or_stop", return_value=False):
                    with patch("collector.platforms.boss.BossCollector._refresh_font_digits", return_value=False):
                        return run_collection(self.config, on_record=lambda _: None,
                                              ai_scheduler=scheduler)

    def test_recommendation_ids_rotate_until_prefilter_target(self):
        self.config["ai"]["use_ai_score"] = False
        browser = FakeBrowser(lambda _method, params, number:
                              [api_job("first" if number == 1 else "second")]
                              if params["encryptExpectId"] == "a" else [])
        result = self.run_with_browser(browser)
        self.assertEqual([params["encryptExpectId"] for _, params in browser.requests],
                         ["a", "b", "a"])
        self.assertEqual(result.reason_code, "target_reached")
        self.assertEqual(result.counts["qualified"], 2)

    def test_ai_target_counts_only_scores_at_or_above_threshold(self):
        self.config["collection"]["target_jobs"] = 1
        self.config["ai"].update(use_ai_score=True, ai_api_concurrency=1,
                                 score_threshold=71)
        resume = Path(self.temp.name) / "resume.md"
        resume.write_text("前端开发经验", encoding="utf-8")
        self.config["profile"]["resume_path"] = str(resume)
        browser = FakeBrowser(lambda _method, params, _number:
                              [api_job("low" if params["encryptExpectId"] == "a" else "high")])

        def model(_system, user):
            score = 60 if '"source_job_id": "low"' in user else 88
            return json.dumps({"score": score, "reason": "测试评分"})

        with AITaskScheduler(self.config, model_call=model) as scheduler:
            result = self.run_with_browser(browser, scheduler)
        self.assertEqual([params["encryptExpectId"] for _, params in browser.requests], ["a", "b"])
        self.assertEqual(result.reason_code, "target_reached")
        self.assertEqual(result.counts["qualified"], 1)
        self.assertEqual(result.counts["ai_scored"], 2)

    def test_search_combinations_repeat_and_stop_after_no_new_jobs(self):
        self.config["ai"]["use_ai_score"] = False
        self.config["collection"].update(mode="search", encrypt_expect_id=[],
                                         keywords=["前端", "后端"], cities=["北京", "上海"])
        browser = FakeBrowser(lambda _method, _params, _number: [api_job("same")])
        result = self.run_with_browser(browser)
        combos = [(params["city"], params["query"]) for _, params in browser.requests]
        self.assertEqual(len(combos), 8)
        self.assertEqual(set(combos[:4]), {("101010100", "前端"), ("101010100", "后端"),
                                          ("101020100", "前端"), ("101020100", "后端")})
        self.assertEqual(combos[:4], combos[4:])
        self.assertEqual(result.reason_code, "target_not_reached")
        self.assertEqual(result.counts["qualified"], 1)


if __name__ == "__main__":
    unittest.main()
