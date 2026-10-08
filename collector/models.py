"""BOSS job collection data contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from data.text import clean_job_description
from collector.salary import classify_salary


def classify_recruitment_type(title: str = "", experience: str = "", jd: str = "") -> str:
    """Classify explicit campus/social recruitment signals conservatively."""
    if any(marker in str(title or "").casefold() for marker in ("实习", "intern", "管培")):
        return "internship"
    if re.search(r"(?:招聘类型|岗位类型|职位类型)\s*[:：]?\s*(?:实习|intern)", str(jd or ""), re.IGNORECASE):
        return "internship"
    text = " ".join(str(value or "") for value in (title, experience, jd))
    if any(marker in text for marker in ("校招", "校园招聘", "应届", "毕业生", "管培生", "实习生")):
        return "campus"
    if any(marker in text for marker in ("社招", "社会招聘")):
        return "experienced"
    if re.search(r"\d+\s*(?:[-–~至]\s*\d+\s*)?年(?:以上|及以上)?(?:工作)?经验", text):
        return "experienced"
    if re.fullmatch(r"\s*\d+\s*(?:[-–~至]\s*\d+\s*)?年(?:以上|及以上)?\s*", str(experience or "")):
        return "experienced"
    return "unknown"


@dataclass(frozen=True)
class PlatformCollectionRequest:
    platform: str
    mode: str
    keywords: list[str]
    cities: list[str]
    city_codes: dict[str, str]
    max_pages: int = 3
    sort: str = "default"
    filters: dict[str, Any] = field(default_factory=dict)
    encrypt_expect_id: list[str] = field(default_factory=list)


@dataclass
class JobCandidate:
    """A BOSS job candidate emitted by the collector."""

    platform: str
    source_job_id: str
    title: str
    company: str
    salary: str = ""
    city: str = ""
    city_code: str = ""
    experience: str = ""
    education: str = ""
    recruitment_type: str = "unknown"
    jd: str = ""
    hr_name: str = ""
    hr_company: str = ""
    hr_title: str = ""
    hr_active: str = ""
    certifications: list[str] = field(default_factory=list)
    company_size: str = ""
    company_industry: str = ""
    url: str = ""
    source_keyword: str = ""

    @property
    def storage_id(self) -> str:
        return self.source_job_id

    def as_job_record(self) -> dict[str, Any]:
        return {
            "id": self.storage_id,
            "title": self.title,
            "company": self.company,
            "salary": self.salary,
            "salary_status": classify_salary(self.salary)[0],
            "city": self.city,
            "source_city_code": self.city_code,
            "experience": self.experience,
            "education": self.education,
            "recruitment_type": (
                self.recruitment_type
                if self.recruitment_type in {"campus", "experienced", "internship"}
                else classify_recruitment_type(self.title, self.experience, self.jd)
            ),
            "jd": clean_job_description(self.jd),
            "hr_name": self.hr_name,
            "hr_company": self.hr_company,
            "hr_title": self.hr_title,
            "hr_active": self.hr_active,
            "certifications": self.certifications,
            "company_size": self.company_size,
            "company_industry": self.company_industry,
            "url": self.url,
            "source_platform": self.platform,
            "source_job_id": self.source_job_id,
            "source_keyword": self.source_keyword,
        }


@dataclass
class PlatformCollectionResult:
    platform: str
    status: str
    reason_code: str = ""
    message: str = ""
    new_job_ids: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    error: str = ""
