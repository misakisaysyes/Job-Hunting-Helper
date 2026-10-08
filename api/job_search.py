"""Parse list search expressions and match stored job fields."""

from __future__ import annotations

import re
from typing import Any, Callable


class SearchSyntaxError(ValueError):
    pass


_DEGREES = frozenset({
    "学历不限", "初中及以下", "中专/中技", "高中", "大专", "本科", "硕士", "博士",
})
_EXPERIENCE_WORDS = frozenset({"经验不限", "应届生", "在校生"})
_RECRUITMENT_TYPES = {
    "社招": "experienced", "社会招聘": "experienced",
    "校招": "campus", "校园招聘": "campus",
    "实习": "internship", "实习生": "internship",
}
_EXPERIENCE = re.compile(r"^(\d+)(?:\s*[-–~至]\s*(\d+))?年(以内|内|以上|及以上)?$")
_SALARY = re.compile(r"^(\d+(?:\.\d+)?)(?:\s*[-–~至]\s*(\d+(?:\.\d+)?))?\s*k$", re.I)
_BARE_RANGE = re.compile(r"^(\d+(?:\.\d+)?)\s*[-–~至]\s*(\d+(?:\.\d+)?)$")
_PAY_COUNT = re.compile(r"^(\d+)薪$")
_JOB_EXPERIENCE = re.compile(r"(\d+)(?:\s*[-–~至]\s*(\d+))?年(以内|内|以上|及以上)?")
_JOB_SALARY = re.compile(r"(\d+(?:\.\d+)?)(?:\s*[-–~至]\s*(\d+(?:\.\d+)?))?\s*k", re.I)


def _number_range(text: str, pattern: re.Pattern[str]) -> tuple[float, float, str] | None:
    match = pattern.search(text)
    if not match:
        return None
    low = float(match.group(1))
    high = float(match.group(2)) if match.group(2) else low
    suffix = match.group(3) if pattern is _JOB_EXPERIENCE else ""
    if suffix in {"以上", "及以上"}:
        high = float("inf")
    elif suffix in {"以内", "内"}:
        low = 0
    return low, high, suffix or ""


def _matches_term(job: dict[str, Any], raw_term: str) -> bool:
    term = raw_term.strip()
    if term in _DEGREES:
        education = str(job.get("education") or "")
        return term in education or (term == "学历不限" and education in {"不限", "学历不限"})
    if term in _EXPERIENCE_WORDS:
        return term in str(job.get("experience") or "")
    if term in _RECRUITMENT_TYPES:
        return job.get("recruitment_type") == _RECRUITMENT_TYPES[term]

    bare_range = _BARE_RANGE.fullmatch(term)
    if bare_range:
        wanted = (float(bare_range.group(1)), float(bare_range.group(2)))
        experience = _number_range(str(job.get("experience") or ""), _JOB_EXPERIENCE)
        salary = _number_range(str(job.get("salary") or ""), _JOB_SALARY)
        return ((experience is not None and experience[:2] == wanted)
                or (salary is not None and salary[:2] == wanted))

    experience_term = _EXPERIENCE.fullmatch(term)
    if experience_term:
        job_range = _number_range(str(job.get("experience") or ""), _JOB_EXPERIENCE)
        if job_range is None:
            return False
        wanted_low = float(experience_term.group(1))
        wanted_high = float(experience_term.group(2)) if experience_term.group(2) else wanted_low
        suffix = experience_term.group(3)
        if suffix in {"以上", "及以上"}:
            return job_range[0] == wanted_low and job_range[1] == float("inf")
        if suffix in {"以内", "内"}:
            return job_range[0] == 0 and job_range[1] == wanted_low
        if experience_term.group(2):
            return job_range[0] == wanted_low and job_range[1] == wanted_high
        return job_range[0] <= wanted_low <= job_range[1]

    salary_term = _SALARY.fullmatch(term)
    if salary_term:
        job_range = _number_range(str(job.get("salary") or ""), _JOB_SALARY)
        if job_range is None:
            return False
        wanted_low = float(salary_term.group(1))
        wanted_high = float(salary_term.group(2)) if salary_term.group(2) else wanted_low
        if salary_term.group(2):
            return job_range[:2] == (wanted_low, wanted_high)
        return job_range[0] <= wanted_low <= job_range[1]
    pay_count = _PAY_COUNT.fullmatch(term)
    if pay_count:
        return re.search(rf"(?<!\d){pay_count.group(1)}薪", str(job.get("salary") or "")) is not None

    needle = term.casefold()
    return any(needle in str(job.get(field) or "").casefold()
               for field in ("title", "company", "city", "jd", "salary", "experience", "education"))


def compile_search(expression: str) -> Callable[[dict[str, Any]], bool]:
    """Support parentheses, AND precedence, and OR over field-aware terms."""
    source = expression.strip()
    if not source:
        return lambda _job: True
    if len(source) > 500:
        raise SearchSyntaxError("筛选表达式不能超过 500 字")
    tokens = [part.strip() for part in re.split(r"(&&|\|\||[()])", source) if part.strip()]
    if len(tokens) > 100:
        raise SearchSyntaxError("筛选表达式过长")
    position = 0

    def atom():
        nonlocal position
        if position >= len(tokens):
            raise SearchSyntaxError("筛选表达式缺少关键词")
        token = tokens[position]
        position += 1
        if token == "(":
            node = disjunction()
            if position >= len(tokens) or tokens[position] != ")":
                raise SearchSyntaxError("筛选表达式的括号不匹配")
            position += 1
            return node
        if token in {"&&", "||", ")"}:
            raise SearchSyntaxError("筛选表达式缺少关键词")
        return ("term", token)

    def conjunction():
        nonlocal position
        node = atom()
        while position < len(tokens) and tokens[position] == "&&":
            position += 1
            node = ("and", node, atom())
        return node

    def disjunction():
        nonlocal position
        node = conjunction()
        while position < len(tokens) and tokens[position] == "||":
            position += 1
            node = ("or", node, conjunction())
        return node

    tree = disjunction()
    if position != len(tokens):
        raise SearchSyntaxError("关键词之间请使用 && 或 ||")

    def evaluate(job: dict[str, Any], node) -> bool:
        if node[0] == "term":
            return _matches_term(job, node[1])
        if node[0] == "and":
            return evaluate(job, node[1]) and evaluate(job, node[2])
        return evaluate(job, node[1]) or evaluate(job, node[2])

    return lambda job: evaluate(job, tree)


LIST_STATUSES = frozenset({"scored", "greeting_ready", "greeted", "ended"})


def matches_list_status(job: dict[str, Any], status: str) -> bool:
    if not status:
        return True
    current = str(job.get("job_status") or "")
    if status == "scored":
        return current in {"collected", "filtered", "scored"}
    if status == "greeted":
        return current in {"greeted", "monitoring"}
    if status == "ended":
        return current.startswith("ended_")
    return current == status
