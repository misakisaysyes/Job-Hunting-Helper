"""BOSS直聘 job collector."""

from __future__ import annotations

import hashlib
from itertools import cycle
import json
import random
import re
import time
from dataclasses import replace
from typing import Any, Callable
from urllib.parse import urlencode

import httpx

from collector.prefilter import quick_score
from collector.base import CollectorHooks
from collector.failure_log import detail_failure_message
from collector.models import JobCandidate, PlatformCollectionRequest, PlatformCollectionResult
from collector.platforms.boss_font import (
    JS_COLLECT_FONT_SOURCES,
    build_boss_digit_map,
    collect_font_sources,
    parse_font_face_urls,
)
from config import (CITY_CODES, COMPANY_SIZE_FILTERS, EDUCATION_FILTERS,
                    EXPERIENCE_FILTERS)
from collector.safety import BossAccessGuard, PlatformSafetyStop, add_risk_event
from collector.throttle import PageThrottle


RECOMMEND_API_PATH = "/wapi/zpgeek/pc/recommend/job/list.json"
SEARCH_API_PATH = "/wapi/zpgeek/search/joblist.json"


def build_boss_request(config: dict[str, Any]) -> PlatformCollectionRequest:
    """Translate configured filters into a BOSS collection request."""
    collection = config["collection"]
    profile = config["profile"]
    filters = {}
    for profile_key, api_key, labels in (
        ("experience_filters", "experience", EXPERIENCE_FILTERS),
        ("education", "degree", EDUCATION_FILTERS),
        ("company_sizes", "scale", COMPANY_SIZE_FILTERS),
    ):
        selected = list(dict.fromkeys(profile[profile_key]))
        if selected:
            filters[api_key] = [labels[value] for value in selected]

    city = collection["city"]
    city_code = (collection["city_code"] or CITY_CODES.get(city, "")) if city else ""
    selected_cities = collection["cities"] if collection["mode"] == "search" else []
    cities = list(dict.fromkeys([*selected_cities, *([city] if city else [])]))
    city_codes = {selected: CITY_CODES[selected] for selected in cities if selected in CITY_CODES}
    if city and city_code:
        city_codes[city] = city_code
    return PlatformCollectionRequest(
        platform=collection["platform"], mode=collection["mode"], keywords=collection["keywords"],
        cities=cities, city_codes=city_codes,
        max_pages=collection["max_pages"], sort=collection["sort"], filters=filters,
        encrypt_expect_id=collection["encrypt_expect_id"],
    )

BOSS_FILTER_OPTIONS: dict[str, dict[str, str]] = {
    "job_type": {"全职": "0", "兼职": "1", "实习": "2"},
    "experience": {
        "经验不限": "101", "应届生": "102", "1年以内": "103", "1-3年": "104",
        "3-5年": "105", "5-10年": "106", "10年以上": "107", "在校生": "108",
    },
    "degree": {
        "学历不限": "201", "大专": "202", "本科": "203", "硕士": "204",
        "博士": "205", "高中": "206", "中专/中技": "208", "初中及以下": "209",
    },
    "scale": {
        "0-20人": "301", "20-99人": "302", "100-499人": "303",
        "500-999人": "304", "1000-9999人": "305", "10000人以上": "306",
    },
    "salary": {
        "3K以下": "402", "3-5K": "403", "5-10K": "404",
        "10-20K": "405", "20-50K": "406", "50K以上": "407",
    },
}
BOSS_FILTER_PARAMS = {
    "job_type": "jobType",
    "experience": "experience",
    "degree": "degree",
    "scale": "scale",
    "salary": "salary",
    "industry": "industry",
}
_FILTER_SEPARATOR = re.compile(r"[,，、;；]")

# BOSS's kanzhun-mix font displays U+E031..U+E03A as 0..9.
# textContent retains these glyph codes even though Chrome shows normal digits.
_BOSS_DIGITS = str.maketrans({chr(0xE031 + n): str(n) for n in range(10)})
_PRIVATE_GLYPH = re.compile(r"[\ue000-\uf8ff]")
# Digit mapping rebuilt from the font the page actually loaded: BOSS reshuffles
# the PUA mapping on every font load, so the static table above goes stale and
# silently decodes wrong digits. Rebuilt lazily per loaded font file.
_BOSS_DYNAMIC_DIGITS: dict[str, str] = {}
_FONT_FETCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Referer": "https://www.zhipin.com/",
}


def install_boss_digit_map(mapping: dict[str, str]) -> bool:
    """Install a font-derived digit mapping; True when it replaces the previous one."""
    fresh = {code: digit for code, digit in (mapping or {}).items()
             if (isinstance(code, str) and len(code) == 1
                     and isinstance(digit, str) and len(digit) == 1 and digit in "0123456789")}
    if not fresh or fresh == _BOSS_DYNAMIC_DIGITS:
        return False
    _BOSS_DYNAMIC_DIGITS.clear()
    _BOSS_DYNAMIC_DIGITS.update(fresh)
    return True


def decode_boss_text(value: Any) -> str:
    text = str(value or "")
    if _BOSS_DYNAMIC_DIGITS:
        text = text.translate({ord(code): digit for code, digit in _BOSS_DYNAMIC_DIGITS.items()})
    return text.translate(_BOSS_DIGITS).strip()


def _decode_fields(raw: dict) -> dict:
    return {key: decode_boss_text(value) if isinstance(value, str) else value
            for key, value in raw.items()}


_SALARY_DECODE_ACTIONS = ("stop", "skip_job")


def _salary_decode_failure_action(config: Any) -> str:
    """How to treat a BOSS salary font the decoder cannot map.

    BOSS remaps digits onto private-use codepoints and reshuffles that mapping
    on every font load, so an unknown font can never be guessed safely:
    ``stop`` (default) aborts the run instead of judging jobs by a salary we
    cannot read, while ``skip_job`` keeps collecting and leaves the salary
    empty so ``profile.filter_unparsed_salary`` decides as usual.
    """
    collection = config.get("collection") if isinstance(config, dict) else None
    value = collection.get("boss_salary_decode_failure") if isinstance(collection, dict) else None
    return value if value in _SALARY_DECODE_ACTIONS else "stop"


def _filter_values(value: Any) -> list[str]:
    values = value if isinstance(value, list) else _FILTER_SEPARATOR.split(value) if isinstance(value, str) else []
    result: list[str] = []
    for item in values:
        cleaned = str(item or "").strip()
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def normalize_boss_search_filters(filters: Any) -> dict[str, list[str]]:
    """Keep only documented BOSS filter labels and numeric industry codes."""
    if not isinstance(filters, dict):
        return {}
    normalized: dict[str, list[str]] = {}
    for key in BOSS_FILTER_PARAMS:
        values = _filter_values(filters.get(key))
        if key == "industry":
            values = [value for value in values if re.fullmatch(r"\d{1,12}", value)]
        else:
            values = [value for value in values if value in BOSS_FILTER_OPTIONS[key]]
        if values:
            normalized[key] = values
    return normalized


def build_boss_filter_query(filters: Any) -> str:
    """Build an encoded query fragment without allowing arbitrary parameters."""
    query: dict[str, str] = {}
    for key, values in normalize_boss_search_filters(filters).items():
        encoded_values = values if key == "industry" else [BOSS_FILTER_OPTIONS[key][value] for value in values]
        query[BOSS_FILTER_PARAMS[key]] = ",".join(encoded_values)
    return urlencode(query)


def build_boss_api_params(request: PlatformCollectionRequest, page: int, city_code: str,
                          keyword: str, encrypt_expect_id: str = "") -> dict[str, str]:
    """Build the observed recommendation GET or search POST form fields."""
    filters = normalize_boss_search_filters(request.filters)
    encoded = {
        BOSS_FILTER_PARAMS[key]: ",".join(
            values if key == "industry" else [BOSS_FILTER_OPTIONS[key][value] for value in values]
        )
        for key, values in filters.items()
    }
    params = {
        "page": str(page), "pageSize": "15", "city": city_code,
        "jobType": encoded.get("jobType", ""), "salary": encoded.get("salary", ""),
        "experience": encoded.get("experience", ""), "degree": encoded.get("degree", ""),
        "industry": encoded.get("industry", ""), "scale": encoded.get("scale", ""),
    }
    if request.mode == "recommend":
        current_expect_id = encrypt_expect_id or (
            request.encrypt_expect_id[0] if len(request.encrypt_expect_id) == 1 else ""
        )
        if not current_expect_id:
            raise ValueError("推荐流请求需要当前求职期望的 encryptExpectId")
        params.update(encryptExpectId=current_expect_id, mixExpectType="", expectInfo="")
    elif request.mode == "search":
        # The observed search POST includes this field empty; only recommendations use its value.
        params.update(query=keyword, expectInfo="", multiSubway="", multiBusinessDistrict="",
                      position="", stage="", scene="1", encryptExpectId="")
        if request.sort == "newest":
            params["sortType"] = "2"
    else:
        raise ValueError(f"未知 BOSS 采集模式：{request.mode}")
    return params


def boss_api_jobs(data: Any) -> list[dict[str, str]] | None:
    """Map both API list shapes to the existing list candidate contract."""
    if not isinstance(data, list):
        return None
    jobs: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        job_id = str(item.get("encryptJobId") or "").strip()
        if not job_id:
            continue
        jobs.append({
            "title": str(item.get("jobName") or "").strip(),
            "salary": str(item.get("salaryDesc") or "").strip(),
            "experience": str(item.get("jobExperience") or "").strip(),
            "education": str(item.get("jobDegree") or "").strip(),
            "company": str(item.get("brandName") or "").strip(),
            "company_size": str(item.get("brandScaleName") or "").strip(),
            "company_industry": str(item.get("brandIndustry") or "").strip(),
            "hr_name": str(item.get("bossName") or "").strip(),
            "hr_title": str(item.get("bossTitle") or "").strip(),
            "location": str(item.get("cityName") or "").strip(),
            "url": f"/job_detail/{job_id}.html",
        })
    return jobs

JS_EXTRACT_DETAIL = """
(() => {
    const info = {};
    info.title = document.querySelector('.info-primary .name h1')?.textContent?.trim()
        || document.querySelector('.name h1')?.textContent?.trim()
        || document.title.split('-')[0]?.trim();
    info.salary = document.querySelector('.info-primary .salary')?.textContent?.trim()
        || document.querySelector('.salary')?.textContent?.trim() || '';
    const tagItems = document.querySelectorAll('.info-primary .tag-list span');
    const tagTexts = Array.from(tagItems).map(t => t.textContent.trim());
    info.experience = tagTexts[0] || '';
    info.education = tagTexts[1] || '';
    info.jd = document.querySelector('.job-sec-text')?.textContent?.trim() || '';
    const jobText = [info.title, ...tagTexts, info.jd].join(' ');
    info.recruitment_type = /实习|intern|管培/i.test(info.title)
        || /(?:招聘类型|岗位类型|职位类型)\\s*[:：]?\\s*(?:实习|intern)/i.test(info.jd)
        ? 'internship'
        : (/校招|校园招聘|应届|毕业生|管培生|实习生/.test(jobText)
            ? 'campus'
            : (/社招|社会招聘/.test(jobText) ? 'experienced' : 'unknown'));
    const companyLinks = document.querySelectorAll('.sider-company .company-info a');
    info.company = '';
    for (const link of companyLinks) {
        const text = link.textContent.trim();
        if (text && !text.includes('http')) { info.company = text; break; }
    }
    if (!info.company) {
        const titleMatch = document.title.match(/_(.+?)招聘/);
        info.company = titleMatch ? titleMatch[1] : '';
    }
    const companyTags = document.querySelectorAll('.sider-company .res-industry-item, .company-info-item');
    info.company_size = document.querySelector('.sider-company .icon-scale')?.parentElement?.textContent?.trim() || '';
    info.company_industry = document.querySelector('.sider-company .icon-industry')?.parentElement?.textContent?.trim() || '';
    companyTags.forEach(tag => {
        const text = tag.textContent.trim();
        if (!info.company_size && /^(?:\\d+\\s*[-–]\\s*\\d+人|\\d+人以上)$/.test(text)) info.company_size = text;
        else if (!info.company_industry && !/^(?:\\d+\\s*[-–]\\s*\\d+人|\\d+人以上)$/.test(text)) info.company_industry = text;
    });
    const bossSection = document.querySelector('.job-boss-info');
    const bossAttr = bossSection?.querySelector('.boss-info-attr')
        || document.querySelector('.boss-info-attr');
    const bossAttrText = bossAttr?.textContent?.replace(/\\s+/g, ' ').trim() || '';
    const separator = bossAttrText.lastIndexOf('·');
    info.hr_name = bossSection?.querySelector('.name')?.textContent?.trim() || '';
    info.hr_company = separator >= 0 ? bossAttrText.slice(0, separator).trim() : '';
    info.hr_title = separator >= 0 ? bossAttrText.slice(separator + 1).trim()
        : (bossAttr?.querySelector('.title')?.textContent?.trim() || '');
    info.certifications = Array.from(document.querySelectorAll(
        '.company-certification .certification-tags li'
    )).map(item => item.textContent.trim()).filter(Boolean);
    info.hr_active = document.querySelector('.boss-active-time')?.textContent?.trim() || '';
    info.url = window.location.pathname;
    return JSON.stringify(info);
})()
"""

JS_DETECT_COLLECTION_RISK = """
(() => {
    const text = (document.body?.innerText || '').slice(0, 10000);
    const url = String(location.href || '');
    const title = String(document.title || '');
    const hasExpectedContent = Boolean(document.querySelector(
        '.job-card-wrap, .job-sec-text, .job-detail, .job-primary'
    ));
    // The login page itself contains "验证码登录". Identify it before testing
    // captcha text so an expired session is reported as a login problem.
    if (/\\/web\\/user(?:\\/login)?\\/?(?:[?#]|$)/i.test(url)) {
        return JSON.stringify({risk: 'login_required', evidence: 'login_url'});
    }
    if (!hasExpectedContent && (
        /BOSS直聘注册登录|登录.*BOSS直聘/.test(title)
        || /请使用微信扫描二维码登录|微信扫码\\s*安全登录|验证码登录\\/注册/.test(text)
    )) {
        return JSON.stringify({risk: 'login_required', evidence: 'login_page'});
    }
    const captcha = document.querySelector(
        '.geetest_panel, .captcha, [class*="captcha"], [id*="captcha"], iframe[src*="captcha"], iframe[src*="verify"]'
    );
    if (captcha) return JSON.stringify({risk: 'captcha', evidence: 'captcha_element'});
    if (/captcha|security-check|\\/verify/i.test(url)) return JSON.stringify({risk: 'captcha', evidence: 'captcha_url'});
    if (/(?:^|[\\/?#=_-])(?:403|forbidden|access-denied)(?:$|[\\/?#=&_-])/i.test(url)) {
        return JSON.stringify({risk: 'blocked', evidence: 'blocked_url'});
    }
    if (/^(?:403(?:\\s+forbidden)?|forbidden|access denied|访问被拒绝|账号异常|账号受限)/i.test(title.trim())) {
        return JSON.stringify({risk: 'blocked', evidence: 'blocked_title'});
    }
    if (!hasExpectedContent && /验证码|安全验证|完成验证/.test(text)) {
        return JSON.stringify({risk: 'captcha', evidence: 'captcha_page'});
    }
    if (!hasExpectedContent && /(?:^|\\n)\\s*403(?:\\s+forbidden)?\\s*(?:\\n|$)|访问被拒绝|账号异常|账号受限/i.test(text)) {
        return JSON.stringify({risk: 'blocked', evidence: 'blocked_page'});
    }
    if (!hasExpectedContent && /操作频繁|访问频繁|请求频繁|稍后再试|频率限制/.test(text)) {
        return JSON.stringify({risk: 'rate_limit', evidence: 'rate_limit_page'});
    }
    return JSON.stringify({risk: null});
})()
"""


def generate_boss_job_id(url: str) -> str:
    match = re.search(r"/job_detail/([^.]+)", str(url or ""))
    if match:
        return match.group(1)
    return hashlib.md5(str(url or "").encode()).hexdigest()[:16]


def _wait_or_stop(stop_event, seconds: float, sleep: Callable[[float], None] = time.sleep) -> bool:
    if stop_event is not None:
        return stop_event.wait(seconds)
    sleep(seconds)
    return False


def _positive_int(value: object, default: int) -> int:
    try:
        return max(int(value), 1)
    except (TypeError, ValueError):
        return default


def _bounded_float(value: object, default: float, minimum: float, maximum: float) -> float:
    try:
        return min(max(float(value), minimum), maximum)
    except (TypeError, ValueError):
        return default


class BossCollector:
    platform = "boss"

    def __init__(
        self,
        *,
        browser: Any,
        throttle_factory: Callable[..., Any] = PageThrottle,
        sleep: Callable[[float], None] = time.sleep,
        randint: Callable[[int, int], int] | None = None,
        config: dict[str, Any] | None = None,
        safety_conn: Any | None = None,
    ):
        self.browser = browser
        self.throttle_factory = throttle_factory
        self.sleep = sleep
        self.randint = randint or random.SystemRandom().randint
        self.config = config or {}
        self.safety_conn = safety_conn

    @staticmethod
    def resolve_city_code(city: str, request: PlatformCollectionRequest) -> str | None:
        return str(request.city_codes.get(city) or CITY_CODES.get(city) or "") or None

    def collect(self, request: PlatformCollectionRequest, hooks: CollectorHooks) -> PlatformCollectionResult:
        if request.mode not in {"recommend", "search"}:
            raise ValueError("BOSS 采集必须指定 recommend 或 search 模式")
        if request.mode == "search" and not request.keywords:
            raise ValueError("BOSS 搜索流需要关键词")
        if request.mode == "search" and request.encrypt_expect_id:
            raise ValueError("BOSS 搜索流不使用 encryptExpectId")
        if request.mode == "recommend" and request.keywords:
            raise ValueError("BOSS 推荐流不接受搜索关键词")
        if request.mode == "recommend" and not request.encrypt_expect_id:
            raise ValueError("BOSS 推荐流需要非空 encryptExpectId")
        if not isinstance(request.encrypt_expect_id, list) or any(
            not isinstance(value, str) or not value.strip() for value in request.encrypt_expect_id
        ):
            raise ValueError("BOSS 求职期望 ID 必须是非空字符串列表")
        collection_cfg = self.config.get("collection", {}) if isinstance(self.config.get("collection"), dict) else {}
        delay_multiplier = _bounded_float(
            collection_cfg.get("collection_delay_multiplier", 1.5),
            1.5,
            1.0,
            5.0,
        )
        throttle = self.throttle_factory(
            delay_min=2.0 * delay_multiplier,
            delay_max=5.0 * delay_multiplier,
        )
        guard = BossAccessGuard(self.safety_conn, self.config, "collection") if self.safety_conn is not None else None
        search_limit = _positive_int(collection_cfg.get("daily_search_page_limit", 60), 60)
        detail_limit = _positive_int(collection_cfg.get("daily_detail_page_limit", 150), 150)
        failure_limit = _positive_int(collection_cfg.get("max_consecutive_page_failures", 3), 3)
        risk_pause_min = _positive_int(collection_cfg.get("risk_pause_min_minutes", 5), 5)
        risk_pause_max = max(
            risk_pause_min,
            _positive_int(collection_cfg.get("risk_pause_max_minutes", 10), 10),
        )
        detail_worker: str | None = None
        page_failures = 0
        seen_jobs = 0
        incomplete_combos = 0
        undecodable_salary: set[str] = set()
        salary_decode_action = _salary_decode_failure_action(self.config)

        def limited(reason: str) -> PlatformCollectionResult:
            return PlatformCollectionResult(
                self.platform, "completed_with_shortage", reason,
                (
                    "BOSS 采集暂时暂停：此前检测到风险提示，请稍后重试。"
                    if reason == "persistent_risk_lock"
                    else f"BOSS 采集已达安全上限：{reason}"
                ),
            )

        def risk(kind: str, evidence: str = "") -> PlatformCollectionResult:
            labels = {
                "captcha": "BOSS 采集检测到验证码",
                "blocked": "BOSS 当前采集页连续检测到请求拦截（不代表账号封禁）",
                "rate_limit": "BOSS 采集检测到频率限制",
                "login_required": "BOSS 登录状态已失效，请先在浏览器重新登录",
            }
            pause_minutes = self.randint(risk_pause_min, risk_pause_max)
            label = labels.get(kind, "BOSS 采集检测到风险")
            evidence_note = f"；证据 {evidence}" if evidence else ""
            if self.safety_conn is not None:
                add_risk_event(
                    self.safety_conn,
                    f"collection_{kind}",
                    f"{label}{evidence_note}；冷却 {pause_minutes} 分钟",
                )
            if guard is not None:
                guard.lock(kind, minutes=pause_minutes)
            return PlatformCollectionResult(
                self.platform,
                "blocked",
                kind,
                f"{label}{evidence_note}；本轮已停止，冷却 {pause_minutes} 分钟后可重新开始",
            )

        def inspect_risk(target_id: str) -> dict[str, str] | None:
            raw = self.browser.evaluate(target_id, JS_DETECT_COLLECTION_RISK)
            try:
                value = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except (json.JSONDecodeError, TypeError):
                value = {}
            if not isinstance(value, dict) or not value.get("risk"):
                return None
            return {
                "kind": str(value.get("risk")),
                "evidence": str(value.get("evidence") or "unspecified"),
            }

        def confirm_risk(target_id: str) -> dict[str, str] | None:
            first = inspect_risk(target_id)
            if first is None:
                return None
            if _wait_or_stop(hooks.stop_event, 1.0 * delay_multiplier, self.sleep):
                return {"kind": "user_stopped", "evidence": ""}
            second = inspect_risk(target_id)
            if second is None or second["kind"] != first["kind"]:
                return None
            return second

        def page_failure_stop() -> PlatformCollectionResult:
            if self.safety_conn is not None:
                add_risk_event(
                    self.safety_conn,
                    "collection_consecutive_page_failures",
                    "BOSS 连续页面失败，本轮采集已结束；未写入账号风险锁",
                )
            return PlatformCollectionResult(
                self.platform,
                "completed_with_shortage",
                "consecutive_page_failures",
                "BOSS 连续页面失败，本轮采集已结束",
            )

        def list_ids(jobs: list) -> set[str]:
            return {generate_boss_job_id(job["url"]) for job in jobs
                    if isinstance(job, dict) and job.get("url")}

        combos: list[tuple[str, str, str, str]] = []
        for city in request.cities or [""]:
            city_code = self.resolve_city_code(city, request) if city else ""
            if city and not city_code:
                hooks.on_event(phase="searching", city=city, reason_code="no_valid_city", message=f"未识别的 BOSS 城市：{city}")
                continue
            if request.mode == "search":
                combos.extend((city, city_code, keyword, "") for keyword in request.keywords)
            else:
                combos.extend((city, city_code, "", expect_id)
                              for expect_id in dict.fromkeys(request.encrypt_expect_id))
        if not combos:
            return PlatformCollectionResult(
                self.platform, "completed_with_shortage", "no_valid_city", "没有有效的 BOSS 搜索组合"
            )
        if request.mode == "search":
            random.SystemRandom().shuffle(combos)

        def target_met() -> PlatformCollectionResult:
            return PlatformCollectionResult(
                self.platform, "completed", "target_reached",
                f"本轮已达到目标采集数 {hooks.target_count}",
            )

        if guard is not None:
            try:
                guard.ensure_unlocked()
            except PlatformSafetyStop as exc:
                return limited(exc.reason)

        cycle_new = 0
        try:
            for combination_index, (city, city_code, keyword, expect_id) in enumerate(cycle(combos)):
                if combination_index and combination_index % len(combos) == 0:
                    if not hooks.target_count:
                        break
                    hooks.on_cycle_complete()
                    if hooks.target_reached():
                        return target_met()
                    if cycle_new == 0:
                        return PlatformCollectionResult(
                            self.platform, "completed_with_shortage", "target_not_reached",
                            "来源组合已轮询，未发现新岗位；本轮目标未达成",
                        )
                    cycle_new = 0
                if hooks.target_count and hooks.target_reached():
                    return target_met()
                if hooks.stop_event is not None and hooks.stop_event.is_set():
                    return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                # Only the explicitly resumed run supplies completed pages.
                # New runs always start at page 1, regardless of older tasks.
                combo_key = expect_id if request.mode == "recommend" else keyword
                start_page = hooks.completed_page(city, combo_key) + 1
                if start_page > request.max_pages:
                    hooks.on_event(phase="resuming", keyword=combo_key, city=city,
                                   message="继续原任务：该搜索组合已完成")
                    continue
                combo_complete = True
                page_fingerprints: set[frozenset[str]] = set()
                for page in range(start_page, request.max_pages + 1):
                    if hooks.target_count and hooks.target_reached():
                        return target_met()
                    if hooks.stop_event is not None and hooks.stop_event.is_set():
                        return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                    hooks.on_event(phase="loading_list", keyword=combo_key, city=city, page=page)
                    try:
                        if guard is not None:
                            guard.reserve("search_page", daily_limit=search_limit)
                        params = build_boss_api_params(request, page, city_code, keyword, expect_id)
                        if request.mode == "recommend":
                            response = self.browser.request_boss_json(RECOMMEND_API_PATH, "GET", params)
                        else:
                            response = self.browser.request_boss_json(SEARCH_API_PATH, "POST", params)
                    except PlatformSafetyStop as exc:
                        return limited(exc.reason)
                    if isinstance(response, dict) and response.get("error") == "missing_encrypt_expect_id":
                        hooks.on_parse_failed("推荐流请求缺少 encryptExpectId")
                        return PlatformCollectionResult(
                            self.platform, "completed_with_shortage", "missing_encrypt_expect_id",
                            "BOSS 推荐流请求缺少非空 encryptExpectId，已停止本轮采集",
                        )
                    if not isinstance(response, dict) or response.get("error"):
                        hooks.on_parse_failed("BOSS 列表接口请求失败")
                        return PlatformCollectionResult(self.platform, "completed_with_shortage", "api_request_failed",
                                                        "BOSS 列表接口请求失败，已停止本轮采集")
                    status = response.get("http_status")
                    body = response.get("body")
                    if status in {401, 403}:
                        return risk("login_required" if status == 401 else "blocked", f"HTTP {status}")
                    if status == 429:
                        return risk("rate_limit", "HTTP 429")
                    if not isinstance(body, dict) or body.get("code") != 0:
                        code = body.get("code") if isinstance(body, dict) else None
                        message = str(body.get("message") or body.get("msg") or "").strip()[:120] if isinstance(body, dict) else ""
                        detail = f"HTTP {status if status is not None else '未知'}，code={code if code is not None else '未知'}"
                        if message:
                            detail += f"，{message}"
                        hooks.on_parse_failed(f"BOSS 列表接口响应异常：{detail}")
                        return PlatformCollectionResult(self.platform, "completed_with_shortage", "api_response_failed",
                                                        f"BOSS 列表接口返回异常（{detail}），已停止本轮采集")
                    zp_data = body.get("zpData")
                    jobs = boss_api_jobs(zp_data.get("jobList")) if isinstance(zp_data, dict) else None
                    has_more = bool(zp_data.get("hasMore")) if isinstance(zp_data, dict) else False
                    if not isinstance(jobs, list):
                        combo_complete = False
                        page_failures += 1
                        hooks.on_parse_failed("BOSS 列表接口岗位解析失败")
                        if page_failures >= failure_limit: return page_failure_stop()
                        continue
                    page_failures = 0
                    if not jobs:
                        if has_more:
                            combo_complete = False
                            hooks.on_parse_failed("BOSS 列表接口返回空岗位，但仍提示存在下一页")
                        else:
                            hooks.on_event(message="BOSS 列表接口已无更多岗位")
                        break
                    fingerprint = frozenset(list_ids(jobs))
                    if fingerprint and fingerprint in page_fingerprints:
                        hooks.on_parse_failed("BOSS 翻页未生效：岗位 ID 与之前页面完全相同")
                        return PlatformCollectionResult(
                            self.platform, "completed_with_shortage", "repeated_search_page",
                            f"BOSS {city} · {combo_key} 第 {page} 页与之前页面重复，已停止；未将重复页面计入扫描",
                        )
                    if fingerprint:
                        page_fingerprints.add(fingerprint)
                    seen_jobs += len(jobs)
                    for raw in jobs:
                        if hooks.target_count and hooks.target_reached():
                            return target_met()
                        if hooks.stop_event is not None and hooks.stop_event.is_set():
                            return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                        raw = _decode_fields(raw) if isinstance(raw, dict) else raw
                        candidate = self._list_candidate(raw, city, city_code, keyword)
                        if not candidate:
                            combo_complete = False
                            hooks.on_parse_failed("BOSS 列表岗位缺少有效链接")
                            continue
                        if not hooks.on_list_candidate(candidate): continue
                        cycle_new += 1
                        if _PRIVATE_GLYPH.search(candidate.salary):
                            if salary_decode_action != "skip_job":
                                hooks.on_parse_failed("BOSS 薪资包含未识别的字体字符")
                                return PlatformCollectionResult(
                                    self.platform, "completed_with_shortage", "salary_decode_failed",
                                    "BOSS 薪资字体暂时无法解析，已停止；未将岗位判为薪资不匹配",
                                )
                            # Never keep a number we could not decode: an empty
                            # salary is classified as unparsed by the prefilter.
                            undecodable_salary.add(candidate.source_job_id)
                            raw["salary"] = ""
                            candidate = replace(candidate, salary="")
                            hooks.on_event(
                                message="BOSS 薪资字体无法解析，薪资留空并交由预筛处理"
                            )
                        score, filter_reason = quick_score(raw, self.config) if self.config else (100, "")
                        if score <= 0:
                            hooks.on_event(message=f"BOSS 列表预筛：{filter_reason}", increment_filtered=True)
                            continue
                        if throttle.wait(hooks.stop_event):
                            return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                        detail_url = f"https://www.zhipin.com{candidate.url}"
                        try:
                            if guard is not None: guard.reserve("detail_page", daily_limit=detail_limit)
                        except PlatformSafetyStop as exc:
                            return limited(exc.reason)
                        opened = (self.browser.new_tab(detail_url, background=True) if detail_worker is None
                                  else self.browser.navigate(detail_worker, detail_url))
                        if opened and detail_worker is None:
                            detail_worker = str(opened)
                        detail_target = detail_worker
                        if not opened or not detail_target:
                            combo_complete = False
                            hooks.on_parse_failed(detail_failure_message("无法打开 BOSS 详情页", candidate, detail_url))
                            # A single job can disappear or refuse to open while
                            # the search page remains healthy. Do not count this
                            # as a search-page failure and abort later keywords.
                            continue
                        if _wait_or_stop(hooks.stop_event, 2 * delay_multiplier, self.sleep):
                            return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                        self.browser.wait_for_load(detail_target, timeout=10)
                        signal = confirm_risk(detail_target)
                        if signal and signal["kind"] == "user_stopped":
                            return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                        if signal: return risk(signal["kind"], signal["evidence"])
                        self._refresh_font_digits(detail_target)
                        detail_result = self.browser.evaluate(detail_target, JS_EXTRACT_DETAIL)
                        try:
                            detail = json.loads(detail_result) if detail_result else None
                        except (json.JSONDecodeError, TypeError):
                            detail = None
                        if not isinstance(detail, dict):
                            combo_complete = False
                            hooks.on_parse_failed(detail_failure_message("BOSS 详情解析失败", candidate, detail_url))
                            # Detail extraction failures belong to this job, not
                            # the city/keyword search queue. Keep scanning the
                            # remaining combinations and report the skipped job.
                            continue
                        page_failures = 0
                        merged = self._merge_detail(candidate, detail, detail_url)
                        if (merged.source_job_id in undecodable_salary
                                and _PRIVATE_GLYPH.search(merged.salary)):
                            merged = replace(merged, salary="")
                        if not merged.title or not merged.company or not merged.url or not merged.jd:
                            combo_complete = False
                            hooks.on_parse_failed(detail_failure_message(
                                "BOSS 详情缺少职位、公司、链接或 JD", candidate, detail_url,
                            ))
                            continue
                        score, filter_reason = quick_score(merged.as_job_record(), self.config) if self.config else (100, "")
                        if score <= 0:
                            hooks.on_event(message=f"BOSS 详情预筛：{filter_reason}", increment_filtered=True)
                            continue
                        continue_collecting = hooks.on_candidate(merged)
                        # The run entry point submits scoring here when use_ai_score
                        # is enabled; the collector can continue loading details.
                        hooks.on_prefilter_pass(merged)
                        if hooks.target_count and hooks.target_reached():
                            return target_met()
                        if not continue_collecting:
                            return PlatformCollectionResult(self.platform, "completed", "callback_stopped", "采集回调已停止")
                    if not hooks.can_checkpoint():
                        combo_complete = False
                    if combo_complete:
                        hooks.on_page_complete(city, combo_key, page)
                    if not has_more:
                        break
                    if page < request.max_pages and _wait_or_stop(hooks.stop_event, 0.2 * delay_multiplier, self.sleep):
                        return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                if not combo_complete:
                    incomplete_combos += 1
        finally:
            if detail_worker:
                self.browser.close_tab(detail_worker)
        if not seen_jobs:
            return PlatformCollectionResult(
                self.platform, "completed_with_shortage", "no_jobs_extracted",
                "BOSS 本轮未读取到岗位，请检查列表页是否加载完成、登录状态及采集条件",
            )
        if incomplete_combos:
            return PlatformCollectionResult(
                self.platform, "completed_with_shortage", "incomplete_search",
                f"BOSS 本轮搜索结束，{incomplete_combos} 个搜索组合未完整读取，可再次采集",
            )
        if request.mode == "recommend":
            return PlatformCollectionResult(self.platform, "completed", "recommendations_exhausted", "BOSS 本轮推荐页采集已结束")
        return PlatformCollectionResult(self.platform, "completed", "search_exhausted", "BOSS 本轮搜索已结束")

    def _refresh_font_digits(self, target_id: str) -> bool:
        """Rebuild the digit mapping from the font the tab actually loaded.

        Never raises and never interrupts collection: on any failure the
        previous static-table behavior stands and residual PUA glyphs keep
        flowing into the existing salary_decode_failed handling."""
        try:
            urls, css_sources = collect_font_sources(self.browser.evaluate(target_id, JS_COLLECT_FONT_SOURCES))
            css = chr(10).join(source for source in css_sources if not source.startswith("http"))
            for href in [source for source in css_sources if source.startswith("http")][:3]:
                try:
                    response = httpx.get(href, timeout=10, trust_env=False, headers=_FONT_FETCH_HEADERS)
                    if response.status_code == 200:
                        css += chr(10) + response.text
                except httpx.HTTPError:
                    continue
            for url in (urls + [found for found in parse_font_face_urls(css) if found not in urls])[:3]:
                try:
                    response = httpx.get(url, timeout=10, trust_env=False, headers=_FONT_FETCH_HEADERS)
                except httpx.HTTPError:
                    continue
                if response.status_code != 200 or not response.content:
                    continue
                if install_boss_digit_map(build_boss_digit_map(response.content)):
                    return True
        except Exception:
            return False
        return False

    @staticmethod
    def _list_candidate(raw: Any, city: str, city_code: str, keyword: str) -> JobCandidate | None:
        if not isinstance(raw, dict):
            return None
        raw = _decode_fields(raw)
        url = str(raw.get("url") or "").strip()
        if not url:
            return None
        location = str(raw.get("location") or "").strip()
        actual_city = city or re.split(r"[·•・]", location, maxsplit=1)[0].strip()
        return JobCandidate(
            platform="boss",
            source_job_id=generate_boss_job_id(url),
            title=str(raw.get("title") or "").strip(),
            company=str(raw.get("company") or "").strip(),
            salary=str(raw.get("salary") or "").strip(),
            city=actual_city,
            city_code=city_code or CITY_CODES.get(actual_city, ""),
            experience=str(raw.get("experience") or "").strip(),
            education=str(raw.get("education") or "").strip(),
            hr_name=str(raw.get("hr_name") or "").strip(),
            hr_title=str(raw.get("hr_title") or "").strip(),
            company_size=str(raw.get("company_size") or "").strip(),
            company_industry=str(raw.get("company_industry") or "").strip(),
            url=url,
            source_keyword=keyword,
        )

    @staticmethod
    def _merge_detail(candidate: JobCandidate, detail: dict[str, Any], detail_url: str) -> JobCandidate:
        detail = _decode_fields(detail)
        return JobCandidate(
            platform="boss",
            source_job_id=candidate.source_job_id,
            title=str(detail.get("title") or candidate.title).strip(),
            company=str(detail.get("company") or candidate.company).strip(),
            salary=str(detail.get("salary") or candidate.salary).strip(),
            city=candidate.city,
            city_code=candidate.city_code,
            experience=str(detail.get("experience") or candidate.experience).strip(),
            education=str(detail.get("education") or candidate.education).strip(),
            recruitment_type=str(detail.get("recruitment_type") or "unknown").strip(),
            jd=str(detail.get("jd") or "").strip(),
            hr_name=str(detail.get("hr_name") or candidate.hr_name).strip(),
            hr_company=str(detail.get("hr_company") or "").strip(),
            hr_title=str(detail.get("hr_title") or candidate.hr_title).strip(),
            hr_active=str(detail.get("hr_active") or "").strip(),
            certifications=[str(item).strip() for item in detail.get("certifications", [])
                            if isinstance(item, str) and item.strip()]
            if isinstance(detail.get("certifications"), list) else [],
            company_size=str(detail.get("company_size") or candidate.company_size).strip(),
            company_industry=str(detail.get("company_industry") or candidate.company_industry).strip(),
            url=detail_url,
            source_keyword=candidate.source_keyword,
        )
