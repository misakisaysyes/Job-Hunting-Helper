"""Classify the salary text retained from a job card or detail page."""

from __future__ import annotations

import re


_SALARY_RANGE = re.compile(r"(\d+(?:\.\d+)?)\s*[kK]?\s*[-–~至]\s*(\d+(?:\.\d+)?)\s*[kK]")
_SALARY_SINGLE = re.compile(r"(\d+(?:\.\d+)?)\s*[kK](?!\w)")
_PRIVATE_GLYPH = re.compile(r"[\ue000-\uf8ff]")


def classify_salary(value: object) -> tuple[str, tuple[float, float] | None]:
    """Return parsed, negotiable, or unparsed with an optional monthly K range."""
    salary = str(value or "").strip()
    if "面议" in salary:
        return "negotiable", None
    if _PRIVATE_GLYPH.search(salary):
        return "unparsed", None
    match = _SALARY_RANGE.search(salary)
    if match:
        low, high = (float(part) for part in match.groups())
        return "parsed", (min(low, high), max(low, high))
    match = _SALARY_SINGLE.search(salary)
    if match:
        amount = float(match.group(1))
        return "parsed", (amount, amount)
    return "unparsed", None
