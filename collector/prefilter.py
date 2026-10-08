"""Local hard filters for list cards and completed job details."""

from __future__ import annotations

import re
from typing import Any

from collector.models import classify_recruitment_type
from collector.salary import classify_salary


_INTERNSHIP_TERMS = ("实习", "intern", "internship", "管培")
_INTERNSHIP_LABEL = re.compile(r"(?:招聘类型|岗位类型|职位类型)\s*[:：]?\s*(?:实习|intern)", re.IGNORECASE)
_HEADHUNTER_PUBLISHER = re.compile(r"猎头|人才寻访|寻访顾问")
_RECRUITMENT_AGENCY = re.compile(r"人力资源|人才服务|招聘服务|猎头")
_CLIENT_RECRUITMENT = re.compile(
    r"(?:受|接受).{0,12}(?:客户|企业|公司).{0,6}委托"
    r"|(?:为|替)(?:客户|甲方|企业|公司).{0,12}(?:招聘|寻访)"
    r"|(?:客户|甲方)(?:公司|企业)?.{0,12}(?:招聘|岗位)"
    r"|代招"
)
_EXPERIENCE_LABELS = {
    "经验不限": "经验不限", "不限": "经验不限", "无需经验": "经验不限",
    "应届生": "应届生", "在校生": "在校生",
    "1年内": "1年内", "1年以内": "1年内",
    "1-3年": "1-3", "3-5年": "3-5", "5-10年": "5-10", "10年以上": "10年以上",
}
_COMPANY_SIZES = {"0-20人", "20-99人", "100-499人", "500-999人", "1000-9999人", "10000人以上"}


def _matching_terms(text: object, terms: object) -> str | None:
    haystack = str(text or "").casefold()
    if isinstance(terms, str):
        terms = [terms]
    if not isinstance(terms, (list, tuple, set)):
        return None
    needles = [piece.strip() for term in terms for piece in str(term or "").replace("，", ",").split(",")
               if piece.strip()]
    return next((needle for needle in needles if needle.casefold() in haystack), None)


def _education_buckets(value: object) -> set[str]:
    text = str(value or "").strip()
    if not text:
        return set()
    if "不限" in text or "无学历要求" in text:
        return {"学历不限"}
    result: set[str] = set()
    if "博士" in text:
        result.add("博士")
    if "硕士" in text or "研究生" in text:
        result.add("硕士")
    if "本科" in text:
        result.add("本科")
    if "大专" in text or "专科" in text:
        result.add("大专")
    if "高中" in text:
        result.add("高中")
    if "中专" in text or "中技" in text:
        result.add("中专/中技")
    if "初中" in text:
        result.add("初中及以下")
    return result


def _recruitment_type(job: dict[str, Any]) -> str:
    title = str(job.get("title") or "")
    if any(term in title.casefold() for term in _INTERNSHIP_TERMS):
        return "internship"
    if _INTERNSHIP_LABEL.search(str(job.get("jd") or "")):
        return "internship"
    explicit = str(job.get("recruitment_type") or "").strip()
    if explicit in {"campus", "experienced", "internship"}:
        return explicit
    return classify_recruitment_type(title, str(job.get("experience") or ""), str(job.get("jd") or ""))


def _experience_bucket(value: object) -> str | None:
    text = re.sub(r"\s+", "", str(value or "")).replace("–", "-").replace("~", "-").replace("至", "-")
    if text in {"经验不限", "不限", "无需经验"}:
        return "经验不限"
    text = re.sub(r"(?:工作)?经验$", "", text)
    return _EXPERIENCE_LABELS.get(text)


def _company_size_bucket(value: object) -> str | None:
    text = re.sub(r"\s+", "", str(value or "")).replace("–", "-").replace("—", "-")
    if text in {"少于20人", "20人以下"}:
        return "0-20人"
    if text == "10000人及以上":
        return "10000人以上"
    return text if text in _COMPANY_SIZES else None


def _number(value: object, default: float = 0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _headhunter_publisher_reason(job: dict[str, Any]) -> str | None:
    """Use publisher evidence; a headhunter job title or JD mention alone is insufficient."""
    company = str(job.get("company") or "")
    hr_company = str(job.get("hr_company") or "")
    hr_title = str(job.get("hr_title") or "")
    industry = str(job.get("company_industry") or "")
    jd = str(job.get("jd") or "")
    certifications = job.get("certifications") or []
    if _HEADHUNTER_PUBLISHER.search(hr_title):
        return f"招聘者头衔：{hr_title}"
    if "猎头" in hr_company:
        return f"招聘者所属公司：{hr_company}"
    if "猎头" in company:
        return f"发布公司：{company}"
    if ("人力资源服务许可证" in certifications and hr_company and company
            and hr_company.rstrip(".… ") not in company and company not in hr_company):
        return "招聘者所属公司与岗位公司不同，且具有人力资源服务许可证"
    if _RECRUITMENT_AGENCY.search(f"{company} {hr_company} {industry}") and _CLIENT_RECRUITMENT.search(jd):
        return "人力资源服务机构代招"
    return None


def quick_score(job: dict[str, Any], config: dict[str, Any]) -> tuple[int, str]:
    """Return (100, reason) when retained, or (0, reason) when excluded.

    The same function runs on the list card and again after loading details.
    Fields available only on the detail page are checked on the second pass.
    Unknown recruitment type is retained instead of guessed.
    """
    profile = config.get("profile") or {}
    if not isinstance(profile, dict):
        profile = {}

    blocked = _matching_terms(job.get("company"), profile.get("blocked_companies"))
    if blocked:
        return 0, f"公司含屏蔽词：{blocked}"
    excluded = _matching_terms(job.get("title"), profile.get("deal_breakers"))
    if excluded:
        return 0, f"职位名含排除词：{excluded}"
    jd_excluded = _matching_terms(job.get("jd"), profile.get("jd_deal_breakers"))
    if jd_excluded:
        return 0, f"JD 含排除词：{jd_excluded}"
    if profile.get("exclude_headhunter", False):
        publisher_reason = _headhunter_publisher_reason(job)
        if publisher_reason:
            return 0, f"猎头发布岗位：{publisher_reason}"

    education_options = profile.get("education") or []
    if isinstance(education_options, str):
        education_options = [item.strip() for item in education_options.replace("，", ",").split(",") if item.strip()]
    if education_options and not _education_buckets(job.get("education")).intersection(education_options):
        return 0, f"岗位学历不符合设置：{job.get('education') or '未识别'}"

    company_sizes = profile.get("company_sizes") or []
    if isinstance(company_sizes, str):
        company_sizes = [item.strip() for item in company_sizes.replace("，", ",").split(",") if item.strip()]
    # List cards do not reliably include company size; check it after detail extraction.
    if company_sizes and "company_size" in job and _company_size_bucket(job.get("company_size")) not in company_sizes:
        return 0, f"公司规模不符合设置：{job.get('company_size') or '未识别'}"

    allowed_types = profile.get("recruitment_types")
    if isinstance(allowed_types, (list, tuple, set)):
        allowed = set(allowed_types)
        detected = _recruitment_type(job)
        if allowed and detected != "unknown" and detected not in allowed:
            return 0, f"招聘类型不符合设置：{detected}"

    experience_filters = profile.get("experience_filters") or []
    if experience_filters and _experience_bucket(job.get("experience")) not in experience_filters:
        return 0, f"岗位经验不符合设置：{job.get('experience') or '未识别'}"

    salary_min = max(_number(profile.get("salary_min")), 0)
    salary_max = max(_number(profile.get("salary_max")), 0)
    salary_status, salary = classify_salary(job.get("salary"))
    if salary is None:
        if profile.get("filter_unparsed_salary", False):
            return 0, "薪资面议" if salary_status == "negotiable" else "薪资无法解析"
    else:
        job_min, job_max = salary
        if salary_min > 0 and job_min < salary_min:
            return 0, f"岗位薪资下限 {job_min:g}K 低于期望下限 {salary_min:g}K"
        if salary_max > 0 and job_max > salary_max:
            return 0, f"岗位薪资上限 {job_max:g}K 高于期望上限 {salary_max:g}K"

    return 100, "通过"
