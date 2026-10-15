"""A collection run rotates sources until its qualifying-job target is reached."""

from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch

from ai import AIRequestError, AITaskScheduler
from collector.platforms.boss import JS_DETECT_COLLECTION_RISK, JS_EXTRACT_DETAIL
from config import DEFAULT_CONFIG
from data.job_store import JobStore
from run import run_collection


def api_job(job_id: str) -> dict:
    return {"encryptJobId": job_id, "jobName": "前端工程师", "brandName": "示例公司"}


class FakeBrowser:
    def __init__(self, job_list, detail_salaries=None):
        self.job_list = job_list
        self.detail_salaries = detail_salaries or {}
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
                               "jd": "负责浏览器性能优化",
                               "salary": self.detail_salaries.get(
                                   self.current_url.rsplit("/", 1)[-1].removesuffix(".html"), "")})
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
        self.config["ai"]["use_ai_greeting"] = False
        self.config["profile"].update(
            education=[], experience_filters=[], company_sizes=[],
            recruitment_types=["experienced", "campus", "internship"],
            salary_min=0, salary_max=0, filter_unparsed_salary=False,
            deal_breakers=[], jd_deal_breakers=[], blocked_companies=[],
            exclude_headhunter=False,
        )

    def run_with_browser(self, browser, scheduler=None, on_progress=None):
        with patch("collector.platforms.boss.random.SystemRandom.shuffle", lambda _rng, values: None):
            with patch("run.ChromeBrowser", return_value=browser):
                with patch("collector.platforms.boss.PageThrottle.wait", return_value=False):
                    with patch("collector.platforms.boss._wait_or_stop", return_value=False):
                        with patch("collector.platforms.boss.BossCollector._refresh_font_digits", return_value=False):
                            return run_collection(self.config, on_record=lambda _: None,
                                                  ai_scheduler=scheduler,
                                                  on_progress=on_progress)

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

    def test_stop_event_preserves_collected_job_and_skips_remaining_jobs(self):
        self.config["ai"]["use_ai_score"] = False
        stop = Event()
        records = []
        browser = FakeBrowser(lambda *_: [api_job("first"), api_job("second")])

        def keep_first(record):
            records.append(record)
            stop.set()

        with patch("collector.platforms.boss.random.SystemRandom.shuffle", lambda _rng, values: None), \
                patch("run.ChromeBrowser", return_value=browser), \
                patch("collector.platforms.boss.PageThrottle.wait", return_value=False), \
                patch("collector.platforms.boss._wait_or_stop", return_value=False), \
                patch("collector.platforms.boss.BossCollector._refresh_font_digits", return_value=False):
            result = run_collection(self.config, on_record=keep_first, stop_event=stop)

        self.assertEqual(result.status, "stopped")
        self.assertEqual([record["source_job_id"] for record in records], ["first"])
        with closing(sqlite3.connect(self.config["safety"]["state_db"])) as conn:
            jobs, _ = JobStore(conn).list_jobs(limit=10, offset=0)
        self.assertEqual([job["source_job_id"] for job in jobs], ["first"])

    def test_undecodable_list_and_detail_salary_skip_only_affected_jobs(self):
        self.config["ai"]["use_ai_score"] = False
        self.config["collection"]["target_jobs"] = 1
        browser = FakeBrowser(
            lambda _method, _params, _number: [
                {**api_job("bad-list"), "salaryDesc": "20-\ue999K"},
                {**api_job("bad-detail"), "salaryDesc": "20-30K"},
                {**api_job("good"), "salaryDesc": "20-30K"},
            ],
            detail_salaries={"bad-detail": "20-\ue999K"},
        )
        result = self.run_with_browser(browser)
        self.assertEqual(result.reason_code, "target_reached")
        self.assertEqual(result.counts["parse_failed"], 2)
        self.assertEqual(result.counts["new"], 1)
        with closing(sqlite3.connect(self.config["safety"]["state_db"])) as conn:
            store = JobStore(conn)
            self.assertIsNone(store.get_job("boss", "bad-list"))
            self.assertIsNone(store.get_job("boss", "bad-detail"))
            self.assertIsNotNone(store.get_job("boss", "good"))

    def test_cli_stop_salary_mode_still_stops_collection(self):
        self.config["ai"]["use_ai_score"] = False
        self.config["collection"].update(target_jobs=1, boss_salary_decode_failure="stop")
        browser = FakeBrowser(lambda _method, _params, _number: [
            {**api_job("bad"), "salaryDesc": "20-\ue999K"}, api_job("good"),
        ])
        result = self.run_with_browser(browser)
        self.assertEqual(result.reason_code, "salary_decode_failed")
        self.assertEqual(result.counts["new"], 0)

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
            progress = []
            result = self.run_with_browser(browser, scheduler, progress.append)
        self.assertEqual([params["encryptExpectId"] for _, params in browser.requests], ["a", "b"])
        self.assertEqual(result.reason_code, "target_reached")
        self.assertEqual(result.counts["qualified"], 1)
        self.assertEqual(result.counts["ai_scored"], 2)
        self.assertTrue(any(item.get("ai_scored") == 1 for item in progress))
        self.assertTrue(any(item.get("qualified") == 1 for item in progress))

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

    def test_greeting_only_target_counts_prefiltered_jobs_even_if_generation_fails(self):
        self.config["ai"].update(use_ai_score=False, use_ai_greeting=True, score_threshold=100)
        resume = Path(self.temp.name) / "resume.md"
        resume.write_text("前端开发经验", encoding="utf-8")
        self.config["profile"]["resume_path"] = str(resume)
        browser = FakeBrowser(lambda _method, params, _number:
                              [api_job("first" if params["encryptExpectId"] == "a" else "second")])

        def model(system, user):
            self.assertNotIn("岗位匹配评估员", system)
            if '"source_job_id": "second"' in user:
                raise AIRequestError("模型暂不可用")
            return "您好，想了解岗位。"

        with AITaskScheduler(self.config, model_call=model) as scheduler:
            progress = []
            result = self.run_with_browser(browser, scheduler, progress.append)
        self.assertEqual([params["encryptExpectId"] for _, params in browser.requests], ["a", "b"])
        self.assertEqual(result.reason_code, "target_reached")
        self.assertEqual(result.counts["qualified"], 2)
        self.assertEqual(result.counts["ai_greeting_generated"], 1)
        self.assertEqual(result.counts["ai_greeting_failed"], 1)
        self.assertTrue(any(item.get("qualified") == 2 for item in progress))
        self.assertTrue(all("ai_scored" not in item for item in progress))
        with closing(sqlite3.connect(self.config["safety"]["state_db"])) as conn:
            store = JobStore(conn)
            for job_id in ("first", "second"):
                job = store.get_job("boss", job_id)
                self.assertEqual(job["job_status"], "greeting_ready")
                self.assertEqual(job["ai_score_status"], "not_scored")
                self.assertIsNone(job["ai_score"])
            self.assertEqual(store.get_job("boss", "second")["ai_greeting_error"], "模型暂不可用")

    def test_duplicate_jobs_count_only_distinct_jobs_already_in_database(self):
        self.config["ai"]["use_ai_score"] = False
        self.config["collection"].update(mode="search", encrypt_expect_id=[],
                                         keywords=["前端"], cities=["北京"])
        with closing(sqlite3.connect(self.config["safety"]["state_db"])) as conn:
            JobStore(conn).save({"source_platform": "boss", "source_job_id": "old"})
        browser = FakeBrowser(lambda _method, _params, _number:
                              [api_job("old"), api_job("old"),
                               api_job("fresh"), api_job("fresh")])

        progress = []
        result = self.run_with_browser(browser, on_progress=progress.append)

        self.assertEqual(result.counts["duplicate_jobs"], 1)
        self.assertEqual(result.counts["new"], 1)
        self.assertEqual(result.counts["seen"], 8)
        self.assertTrue(any(item.get("duplicate_jobs") == 1 for item in progress))
        self.assertTrue(any(item.get("new") == 1 and item.get("qualified") == 1
                            for item in progress))


if __name__ == "__main__":
    unittest.main()
