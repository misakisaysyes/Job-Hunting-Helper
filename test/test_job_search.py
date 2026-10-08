"""Search expressions filter stored jobs before pagination."""

from __future__ import annotations

import sqlite3
import unittest

from api.job_search import SearchSyntaxError, compile_search, matches_list_status
from data.job_store import JobStore


class JobSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.store = JobStore(self.conn, score_threshold=71)
        self.store.save({
            "source_platform": "boss", "source_job_id": "react", "title": "React 工程师",
            "company": "示例科技", "jd": "前端性能优化", "salary": "25-50K·14薪",
            "city": "北京", "experience": "3-5年", "education": "本科", "recruitment_type": "experienced",
            "ai_score_status": "scored", "ai_score": 88,
        })
        self.store.save({
            "source_platform": "boss", "source_job_id": "campus", "title": "产品助理",
            "company": "校园公司", "jd": "协助产品设计", "salary": "12-18K·13薪",
            "city": "上海", "experience": "应届生", "education": "硕士", "recruitment_type": "campus",
            "ai_score_status": "scored", "ai_score": 60,
        })

    def tearDown(self) -> None:
        self.conn.close()

    def find(self, expression: str, status: str = "") -> list[str]:
        predicate = compile_search(expression)
        jobs, _ = self.store.search_jobs(
            limit=20, offset=0,
            matches=lambda job: matches_list_status(job, status) and predicate(job),
        )
        return [job["source_job_id"] for job in jobs]

    def test_boolean_precedence_parentheses_and_casefold(self) -> None:
        self.assertEqual(self.find("react || 产品 && 硕士"), ["campus", "react"])
        self.assertEqual(self.find("(REACT || 产品) && 本科"), ["react"])
        self.assertEqual(self.find("性能 && 示例科技"), ["react"])

    def test_keyword_matches_displayed_job_fields(self) -> None:
        for expression, expected in (
            ("北京", ["react"]), ("上海", ["campus"]),
            ("50K·14", ["react"]), ("应届", ["campus"]), ("硕", ["campus"]),
            ("北京 && React", ["react"]),
        ):
            with self.subTest(expression=expression):
                self.assertEqual(self.find(expression), expected)

    def test_typed_terms_only_match_their_fields(self) -> None:
        for expression in ("本科", "3-5年", "4年", "社招", "25-50k", "40K", "14薪"):
            with self.subTest(expression=expression):
                self.assertEqual(self.find(expression), ["react"])
        self.assertEqual(self.find("校招 && 应届生 && 13薪"), ["campus"])
        self.assertEqual(self.find("1-3年"), [])
        self.assertEqual(self.find("25-40K"), [])

    def test_bare_range_matches_exact_experience_or_salary(self) -> None:
        self.assertEqual(self.find("3-5"), ["react"])
        self.assertEqual(self.find("25-50"), ["react"])
        self.assertEqual(self.find("12-18"), ["campus"])
        self.assertEqual(self.find("3-5 && 社招"), ["react"])
        self.assertEqual(self.find("(25-50 || 12-18) && 校招"), ["campus"])
        self.assertEqual(self.find("25-50年"), [])
        self.assertEqual(self.find("3-5K"), [])
        self.assertEqual(self.find("25-50k"), ["react"])
        self.assertEqual(self.find("25-50K"), ["react"])
        self.assertFalse(compile_search("3-5")({"title": "招 3-5 人", "jd": "3-5 个项目"}))

    def test_status_and_pagination_follow_filtered_results(self) -> None:
        self.assertEqual(self.find("", "scored"), ["campus"])
        self.assertEqual(self.find("", "greeting_ready"), ["react"])
        self.store.start_greeting("boss", "campus")
        self.assertEqual(self.find("", "scored"), [])
        self.assertEqual(self.find("", "greeting_ready"), ["campus", "react"])
        jobs, total = self.store.search_jobs(limit=1, offset=1, matches=compile_search("产品 || React"))
        self.assertEqual(total, 2)
        self.assertEqual([job["source_job_id"] for job in jobs], ["react"])

    def test_invalid_expressions_are_rejected(self) -> None:
        for expression in ("React &&", "(React || 本科", "React || && 本科", "()"):
            with self.subTest(expression=expression), self.assertRaises(SearchSyntaxError):
                compile_search(expression)


if __name__ == "__main__":
    unittest.main()
