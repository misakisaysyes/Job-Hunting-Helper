"""Regression checks for the collection prefilter's two passes."""

from __future__ import annotations

import argparse
import unittest
from copy import deepcopy
from pathlib import Path
import sys
from urllib.parse import parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import DEFAULT_CONFIG
from collector.models import JobCandidate
from collector.platforms.boss import build_boss_filter_query
from collector.prefilter import quick_score
from cli.cli import (
    COMPANY_SIZE_FILTERS, EDUCATION_FILTERS, EXPERIENCE_FILTERS,
    parse_company_size_options, parse_education_options, parse_experience_options,
    parse_keyword_terms,
)


class PrefilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = deepcopy(DEFAULT_CONFIG)
        self.job = {
            "title": "产品经理", "company": "示例科技", "education": "本科",
            "experience": "3-5年", "salary": "15-25K·13薪", "jd": "负责产品规划",
        }

    def test_defaults_keep_jobs(self) -> None:
        self.assertEqual(quick_score(self.job, self.config)[0], 100)
        self.assertEqual(quick_score({"title": "实习生", "salary": "面议"}, self.config)[0], 100)
        self.assertEqual(quick_score({"title": "实习生", "salary": ""}, self.config)[0], 100)

    def test_education_selection_does_not_compare_to_personal_degree(self) -> None:
        self.config["profile"]["education"] = ["本科", "硕士"]
        self.job["education"] = "硕士及以上"
        self.assertEqual(quick_score(self.job, self.config)[0], 100)
        self.job["education"] = "本科"
        self.assertEqual(quick_score(self.job, self.config)[0], 100)
        self.job["education"] = "大专及以上"
        self.assertEqual(quick_score(self.job, self.config)[0], 0)
        self.job["education"] = "不限"
        self.assertEqual(quick_score(self.job, self.config)[0], 0)
        self.job["education"] = ""
        self.assertEqual(quick_score(self.job, self.config)[0], 0)

    def test_education_options_use_or_in_local_filter_and_boss_query(self) -> None:
        self.config["profile"]["education"] = ["大专", "本科"]
        for degree in ("大专", "本科", "本科或硕士"):
            self.job["education"] = degree
            self.assertEqual(quick_score(self.job, self.config)[0], 100)
        for degree in ("硕士", "博士", ""):
            self.job["education"] = degree
            self.assertEqual(quick_score(self.job, self.config)[0], 0)
        self.assertEqual(
            parse_qs(build_boss_filter_query({"degree": [EDUCATION_FILTERS["大专"], EDUCATION_FILTERS["本科"]]})),
            {"degree": ["202,203"]},
        )
        self.assertEqual(parse_education_options("大专,本科"), ["大专", "本科"])
        self.assertEqual(parse_education_options("硕士， 博士"), ["硕士", "博士"])
        self.config["profile"]["education"] = "大专,本科"
        self.job["education"] = "本科"
        self.assertEqual(quick_score(self.job, self.config)[0], 100)
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_education_options("本科,未知")

    def test_company_size_options_use_or_and_defer_until_detail(self) -> None:
        self.assertEqual(quick_score({**self.job, "company_size": ""}, self.config)[0], 100)
        self.config["profile"]["company_sizes"] = ["500-999人", "1000-9999人", "10000人以上"]
        list_job = {key: value for key, value in self.job.items() if key != "jd"}
        self.assertEqual(quick_score(list_job, self.config)[0], 100)
        for size in ("500-999人", "1000-9999人", "10000人以上", "500–999人"):
            with self.subTest(size=size):
                self.assertEqual(quick_score({**self.job, "company_size": size}, self.config)[0], 100)
        for size in ("0-20人", "20-99人", "100-499人", ""):
            with self.subTest(size=size):
                self.assertEqual(quick_score({**self.job, "company_size": size}, self.config)[0], 0)
        self.assertEqual(
            parse_qs(build_boss_filter_query({"scale": [COMPANY_SIZE_FILTERS["500-999人"],
                                                   COMPANY_SIZE_FILTERS["1000-9999人"]]})),
            {"scale": ["304,305"]},
        )
        self.assertEqual(parse_company_size_options("500-999人,1000-9999人"), ["500-999人", "1000-9999人"])
        self.assertEqual(parse_company_size_options("500-999人， 10000人以上"), ["500-999人", "10000人以上"])
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_company_size_options("500-999人,未知")

    def test_recruitment_type_distinguishes_internship_from_campus(self) -> None:
        self.config["profile"]["recruitment_types"] = ["experienced"]
        self.assertEqual(quick_score(self.job, self.config)[0], 100)
        self.job.update(title="校招产品经理", experience="应届")
        self.assertEqual(quick_score(self.job, self.config)[0], 0)
        self.job.update(title="产品实习生", recruitment_type="campus")
        self.assertEqual(quick_score(self.job, self.config)[0], 0)
        self.config["profile"]["recruitment_types"] = ["internship"]
        self.assertEqual(quick_score(self.job, self.config)[0], 100)
        self.job.update(title="产品经理", experience="经验不限", recruitment_type="unknown")
        self.assertEqual(quick_score(self.job, self.config)[0], 100)
        self.job.update(recruitment_type="campus", jd="岗位类型：实习。负责产品规划")
        self.assertEqual(quick_score(self.job, self.config)[0], 100)
        record = JobCandidate(
            platform="boss", source_job_id="1", title="产品经理", company="示例科技",
            jd="岗位类型：实习。负责产品规划",
        ).as_job_record()
        self.assertEqual(record["recruitment_type"], "internship")

    def test_salary_range_must_be_contained_in_expected_range(self) -> None:
        self.config["profile"].update(salary_min=12, salary_max=20)
        self.job["salary"] = "12-20K"
        self.assertEqual(quick_score(self.job, self.config)[0], 100)
        self.job["salary"] = "15-18K"
        self.assertEqual(quick_score(self.job, self.config)[0], 100)
        self.job["salary"] = "8-12K"
        self.assertEqual(quick_score(self.job, self.config)[0], 0)
        self.job["salary"] = "20-40K"
        self.assertEqual(quick_score(self.job, self.config)[0], 0)
        self.config["profile"]["salary_max"] = 0
        self.job["salary"] = "12-40K"
        self.assertEqual(quick_score(self.job, self.config)[0], 100)
        self.config["profile"].update(salary_min=0, salary_max=20)
        self.job["salary"] = "8-20K"
        self.assertEqual(quick_score(self.job, self.config)[0], 100)
        self.job["salary"] = "8-21K"
        self.assertEqual(quick_score(self.job, self.config)[0], 0)
        self.job["salary"] = "面议"
        self.assertEqual(quick_score(self.job, self.config)[0], 100)
        self.config["profile"]["filter_unparsed_salary"] = True
        self.assertEqual(quick_score(self.job, self.config)[0], 0)

    def test_negotiable_salary_and_parse_failure_have_distinct_status_and_reason(self) -> None:
        self.assertEqual(quick_score({**self.job, "salary": "面议"}, self.config)[0], 100)
        self.assertEqual(quick_score({**self.job, "salary": "无法识别"}, self.config)[0], 100)
        self.config["profile"]["filter_unparsed_salary"] = True
        self.assertEqual(quick_score({**self.job, "salary": "面议"}, self.config), (0, "薪资面议"))
        self.assertEqual(quick_score({**self.job, "salary": "无法识别"}, self.config), (0, "薪资无法解析"))
        self.assertEqual(quick_score({**self.job, "salary": "15-\ue0310K"}, self.config), (0, "薪资无法解析"))
        for salary, status in (("面议", "negotiable"), ("", "unparsed"), ("15-25K", "parsed")):
            with self.subTest(salary=salary):
                record = JobCandidate(platform="boss", source_job_id="1", title="产品经理",
                                      company="示例科技", salary=salary).as_job_record()
                self.assertEqual(record["salary_status"], status)

    def test_experience_ranges_are_distinct_locally_and_in_boss_query(self) -> None:
        self.config["profile"]["experience_filters"] = ["1-3", "3-5"]
        self.job["experience"] = "1-3年"
        self.assertEqual(quick_score(self.job, self.config)[0], 100)
        self.job["experience"] = "3-5年工作经验"
        self.assertEqual(quick_score(self.job, self.config)[0], 100)
        self.job["experience"] = "5-10年"
        self.assertEqual(quick_score(self.job, self.config)[0], 0)
        self.job["experience"] = ""
        self.assertEqual(quick_score(self.job, self.config)[0], 0)
        self.config["profile"]["experience_filters"] = ["经验不限"]
        self.job["experience"] = "无需经验"
        self.assertEqual(quick_score(self.job, self.config)[0], 100)
        self.config["profile"]["experience_filters"] = ["1年内"]
        self.job["experience"] = "1年以内"
        self.assertEqual(quick_score(self.job, self.config)[0], 100)
        self.job["experience"] = "1年内"
        self.assertEqual(quick_score(self.job, self.config)[0], 100)
        self.job["experience"] = "1-3年"
        self.assertEqual(quick_score(self.job, self.config)[0], 0)
        self.assertEqual(
            parse_qs(build_boss_filter_query({"experience": [EXPERIENCE_FILTERS["1年内"]]})),
            {"experience": ["103"]},
        )
        self.config["profile"]["experience_filters"] = ["应届生"]
        self.job["experience"] = "应届生"
        self.assertEqual(quick_score(self.job, self.config)[0], 100)
        self.job["experience"] = "在校生"
        self.assertEqual(quick_score(self.job, self.config)[0], 0)
        self.config["profile"]["experience_filters"] = ["在校生"]
        self.assertEqual(quick_score(self.job, self.config)[0], 100)
        self.assertEqual(
            parse_qs(build_boss_filter_query({"experience": [EXPERIENCE_FILTERS["应届生"], EXPERIENCE_FILTERS["在校生"]]})),
            {"experience": ["102,108"]},
        )
        self.assertEqual(
            parse_qs(build_boss_filter_query({"experience": [EXPERIENCE_FILTERS["1-3"], EXPERIENCE_FILTERS["3-5"]]})),
            {"experience": ["104,105"]},
        )

    def test_experience_argument_accepts_comma_separated_values(self) -> None:
        self.assertEqual(parse_experience_options("1-3,3-5"), ["1-3", "3-5"])
        self.assertEqual(parse_experience_options("应届生， 在校生"), ["应届生", "在校生"])
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_experience_options("1-3,未知")
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_experience_options("1-3,,3-5")

    def test_title_company_and_jd_are_checked_at_available_stage(self) -> None:
        self.config["profile"].update(
            deal_breakers=["外包"], blocked_companies=["示例"], jd_deal_breakers=["出差"],
        )
        list_job = {"title": "产品经理", "company": "其他科技", "salary": "15-25K"}
        self.assertEqual(quick_score(list_job, self.config)[0], 100)
        self.assertEqual(quick_score({**list_job, "title": "外包产品经理"}, self.config)[0], 0)
        self.assertEqual(quick_score({**list_job, "company": "示例科技"}, self.config)[0], 0)
        self.assertEqual(quick_score({**list_job, "jd": "需要频繁出差"}, self.config)[0], 0)

    def test_exclusion_terms_ignore_case(self) -> None:
        self.config["profile"].update(
            deal_breakers=["OUTSOURCE"],
            jd_deal_breakers=["ReLoCaTe"],
            blocked_companies=["ACME"],
        )
        self.assertEqual(quick_score({**self.job, "title": "Outsource Engineer"}, self.config)[0], 0)
        self.assertEqual(quick_score({**self.job, "jd": "Must relocate"}, self.config)[0], 0)
        self.assertEqual(quick_score({**self.job, "company": "Acme Labs"}, self.config)[0], 0)

    def test_each_exclusion_group_matches_any_term(self) -> None:
        self.config["profile"]["deal_breakers"] = ["OUTSOURCE", "REMOTE"]
        self.assertEqual(quick_score({**self.job, "title": "Outsource Engineer"}, self.config),
                         (0, "职位名含排除词：OUTSOURCE"))
        self.assertEqual(quick_score({**self.job, "title": "Remote Engineer"}, self.config)[0], 0)
        self.assertEqual(quick_score({**self.job, "title": "Remote Outsource Engineer"}, self.config)[0], 0)
        self.assertEqual(quick_score(self.job, self.config)[0], 100)

        self.config["profile"].update(deal_breakers=[], jd_deal_breakers=["出差", "夜班"])
        self.assertEqual(quick_score({**self.job, "jd": "需要出差"}, self.config)[0], 0)
        self.assertEqual(quick_score({**self.job, "jd": "需要夜班"}, self.config)[0], 0)
        self.assertEqual(quick_score({**self.job, "jd": "需要出差及夜班"}, self.config)[0], 0)
        self.assertEqual(quick_score(self.job, self.config)[0], 100)

        self.config["profile"].update(jd_deal_breakers=[], blocked_companies=["示例", "科技"])
        self.assertEqual(quick_score({**self.job, "company": "示例集团"}, self.config)[0], 0)
        self.assertEqual(quick_score({**self.job, "company": "其他科技"}, self.config)[0], 0)
        self.assertEqual(quick_score({**self.job, "company": "示例科技"}, self.config)[0], 0)
        self.assertEqual(quick_score({**self.job, "company": "其他集团"}, self.config)[0], 100)

    def test_exclusion_arguments_accept_comma_separated_terms(self) -> None:
        self.assertEqual(parse_keyword_terms("外包, 驻场"), ["外包", "驻场"])
        self.assertEqual(parse_keyword_terms("出差，夜班"), ["出差", "夜班"])
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_keyword_terms("外包,,驻场")

    def test_exclude_headhunter_checks_publisher_evidence_on_both_passes(self) -> None:
        agency_job = {**self.job, "company": "某某猎头咨询"}
        self.config["profile"]["exclude_headhunter"] = False
        self.assertEqual(quick_score(agency_job, self.config)[0], 100)
        self.config["profile"]["exclude_headhunter"] = True

        score, reason = quick_score(agency_job, self.config)
        self.assertEqual(score, 0)
        self.assertIn("发布公司", reason)

        score, reason = quick_score({**self.job, "hr_title": "高级猎头顾问"}, self.config)
        self.assertEqual(score, 0)
        self.assertIn("招聘者头衔", reason)

        self.assertEqual(quick_score({**self.job, "hr_company": "某某猎头咨询"}, self.config)[0], 0)
        self.assertEqual(quick_score({**self.job, "hr_company": "北京猎英管理咨询...",
                                      "certifications": ["人力资源服务许可证"]}, self.config)[0], 0)

        agency_detail = {
            **self.job, "company": "某某人力资源", "company_industry": "人力资源服务",
            "jd": "受客户委托招聘产品经理",
        }
        score, reason = quick_score(agency_detail, self.config)
        self.assertEqual(score, 0)
        self.assertIn("代招", reason)

        for job in (
            {**self.job, "title": "猎头顾问"},
            {**self.job, "jd": "负责对接猎头渠道"},
            {**self.job, "company_industry": "人力资源服务"},
            {**self.job, "jd": "受客户委托招聘产品经理"},
            {**self.job, "certifications": ["人力资源服务许可证"]},
            {**self.job, "hr_company": "示例科技...", "certifications": ["人力资源服务许可证"]},
        ):
            with self.subTest(job=job):
                self.assertEqual(quick_score(job, self.config)[0], 100)

        self.config["profile"]["exclude_headhunter"] = False
        self.assertEqual(quick_score(agency_detail, self.config)[0], 100)


if __name__ == "__main__":
    unittest.main()
