"""Debug CLI for BOSS collection and optional AI scoring."""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import (CITY_CODES, COMPANY_SIZE_FILTERS, DEFAULT_CONFIG, EDUCATION_FILTERS,
                    EXPERIENCE_FILTERS)


def _parse_options(value: str, choices: dict[str, str], argument: str) -> list[str]:
    options = [item.strip() for item in value.replace("，", ",").split(",")]
    if not all(options):
        raise argparse.ArgumentTypeError(f"{argument} 不能包含空选项")
    invalid = [item for item in options if item not in choices]
    if invalid:
        raise argparse.ArgumentTypeError(
            f"{argument} 无效选项：{', '.join(invalid)}；可选：{', '.join(choices)}"
        )
    return options


def parse_experience_options(value: str) -> list[str]:
    return _parse_options(value, EXPERIENCE_FILTERS, "--experience")


def parse_education_options(value: str) -> list[str]:
    return _parse_options(value, EDUCATION_FILTERS, "--education")


def parse_company_size_options(value: str) -> list[str]:
    return _parse_options(value, COMPANY_SIZE_FILTERS, "--company-size")


def parse_keyword_terms(value: str) -> list[str]:
    terms = [item.strip() for item in value.replace("，", ",").split(",")]
    if not all(terms):
        raise argparse.ArgumentTypeError("排除词不能包含空关键词")
    return terms


def parse_expect_ids(value: str) -> list[str]:
    ids = [item.strip() for item in value.split(",")]
    if not all(ids):
        raise argparse.ArgumentTypeError("--encrypt-expect-id 不能包含空 ID")
    return ids


CONFIG_OPTIONS = {
    "platform": ("collection", "platform"),
    "mode": ("collection", "mode"),
    "encrypt_expect_id": ("collection", "encrypt_expect_id"),
    "test_prefilter": ("collection", "test_prefilter"),
    "keyword": ("collection", "keywords"),
    "search_city": ("collection", "cities"),
    "city": ("collection", "city"),
    "city_code": ("collection", "city_code"),
    "max_pages": ("collection", "max_pages"),
    "target_jobs": ("collection", "target_jobs"),
    "max_jobs": ("collection", "max_jobs"),
    "sort": ("collection", "sort"),
    "daily_search_page_limit": ("collection", "daily_search_page_limit"),
    "daily_detail_page_limit": ("collection", "daily_detail_page_limit"),
    "max_consecutive_page_failures": ("collection", "max_consecutive_page_failures"),
    "risk_pause_min_minutes": ("collection", "risk_pause_min_minutes"),
    "risk_pause_max_minutes": ("collection", "risk_pause_max_minutes"),
    "collection_delay_multiplier": ("collection", "collection_delay_multiplier"),
    "boss_salary_decode_failure": ("collection", "boss_salary_decode_failure"),
    "daily_platform_page_limit": ("safety", "daily_platform_page_limit"),
    "risk_lock_minutes": ("safety", "risk_lock_minutes"),
    "state_db": ("safety", "state_db"),
    "cdp_url": ("browser", "cdp_url"),
    "resume_path": ("profile", "resume_path"),
    "education": ("profile", "education"),
    "company_size": ("profile", "company_sizes"),
    "recruitment_type": ("profile", "recruitment_types"),
    "experience": ("profile", "experience_filters"),
    "salary_min": ("profile", "salary_min"),
    "salary_max": ("profile", "salary_max"),
    "filter_unparsed_salary": ("profile", "filter_unparsed_salary"),
    "deal_breaker": ("profile", "deal_breakers"),
    "jd_deal_breaker": ("profile", "jd_deal_breakers"),
    "blocked_company": ("profile", "blocked_companies"),
    "exclude_headhunter": ("profile", "exclude_headhunter"),
    "use_ai_score": ("ai", "use_ai_score"),
    "use_ai_greeting": ("ai", "use_ai_greeting"),
    "ai_score_user_prompt": ("ai", "score_user_prompt"),
    "ai_greeting_user_prompt": ("ai", "greeting_user_prompt"),
    "ai_greeting_template": ("ai", "greeting_template"),
    "ai_score_threshold": ("ai", "score_threshold"),
    "ai_provider": ("ai", "provider"),
    "ai_service": ("ai", "service"),
    "ai_model": ("ai", "model"),
    "ai_api_key_env": ("ai", "api_key_env"),
    "ai_base_url": ("ai", "base_url"),
    "ai_thinking": ("ai", "thinking"),
    "ai_thinking_budget": ("ai", "thinking_budget"),
    "ai_timeout_seconds": ("ai", "timeout_seconds"),
    "ai_retry_count": ("ai", "retry_count"),
    "ai_retry_delay_min_seconds": ("ai", "retry_delay_min_seconds"),
    "ai_retry_delay_max_seconds": ("ai", "retry_delay_max_seconds"),
    "ai_api_concurrency": ("ai", "ai_api_concurrency"),
}

GROUPED_OPTIONS = {"encrypt_expect_id", "education", "company_size", "experience", "deal_breaker",
                   "jd_deal_breaker", "blocked_company"}
LIST_OPTIONS = GROUPED_OPTIONS | {"keyword", "search_city", "recruitment_type"}
CLEARABLE_OPTIONS = {
    "encrypt_expect_id": "encrypt_expect_id", "keyword": "keywords",
    "search_city": "cities",
    "education": "education", "company_size": "company_sizes",
    "recruitment_type": "recruitment_types", "experience": "experience_filters",
    "deal_breaker": "deal_breakers", "jd_deal_breaker": "jd_deal_breakers",
    "blocked_company": "blocked_companies",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从已登录的 Chrome 采集 BOSS 岗位")
    parser.add_argument("--platform", choices=["boss"],
                        help="采集平台；当前支持 boss，默认从 config.py 读取")
    parser.add_argument("--mode", choices=["recommend", "search"],
                        help="推荐流 recommend 或搜索流 search；可在 config.py 设置默认值")
    parser.add_argument("--encrypt-expect-id", action="append", type=parse_expect_ids,
                        metavar="ID[,ID...]", help="推荐流求职期望 ID；英文逗号分隔或重复指定，搜索流不使用")
    parser.add_argument("--test-prefilter", "--test", action=argparse.BooleanOptionalAction,
                        help="先运行预筛用例，再连接 Chrome 采集真实岗位验证过滤")
    parser.add_argument("--keyword", action="append", help="搜索流的搜索词，可重复")
    parser.add_argument("--search-city", action="append", choices=list(CITY_CODES),
                        help="搜索流城市，可重复指定；省略时按 BOSS 当前会话城市搜索")
    parser.add_argument("--city", help="城市名称；不提供则不指定城市")
    parser.add_argument("--city-code", help="不在内置城市表时指定 BOSS 城市编码")
    parser.add_argument("--max-pages", type=int, help="最多读取的列表页数，1～10")
    parser.add_argument("--target-jobs", type=int, help="本轮目标采集数；未达到时轮询来源组合")
    parser.add_argument("--max-jobs", type=int, help="最多输出的岗位数；0 表示不限")
    parser.add_argument("--sort", choices=["default", "newest"])
    parser.add_argument("--education", action="append", type=parse_education_options,
                        metavar="选项[,选项...]",
                        help="岗位学历要求，多个选项为 OR；支持 学历不限、初中及以下、中专/中技、高中、大专、本科、硕士、博士")
    parser.add_argument("--company-size", action="append", type=parse_company_size_options,
                        metavar="选项[,选项...]",
                        help="公司规模，多个选项为 OR；支持 0-20人、20-99人、100-499人、500-999人、1000-9999人、10000人以上；不指定则不限")
    parser.add_argument("--recruitment-type", action="append",
                        choices=["experienced", "campus", "internship"],
                        help="接受的招聘类型，可重复指定")
    parser.add_argument("--experience", action="append", type=parse_experience_options,
                        metavar="选项[,选项...]",
                        help="岗位经验筛选，可用逗号分隔或重复指定；支持 经验不限、应届生、在校生、1年内、1-3、3-5、5-10、10年以上")
    parser.add_argument("--salary-min", type=float, help="期望月薪下限，单位 K")
    parser.add_argument("--salary-max", type=float, help="期望月薪上限，单位 K")
    parser.add_argument("--filter-unparsed-salary", action=argparse.BooleanOptionalAction,
                        help="过滤薪资面议或无法解析的岗位；可用 --no-filter-unparsed-salary 关闭")
    parser.add_argument("--deal-breaker", action="append", type=parse_keyword_terms,
                        help="职位名排除词，逗号分隔或重复指定；命中任意词即排除")
    parser.add_argument("--jd-deal-breaker", action="append", type=parse_keyword_terms,
                        help="JD 排除词，逗号分隔或重复指定；命中任意词即排除")
    parser.add_argument("--blocked-company", action="append", type=parse_keyword_terms,
                        help="公司名屏蔽词，逗号分隔或重复指定；命中任意词即排除")
    parser.add_argument("--exclude-headhunter", action=argparse.BooleanOptionalAction,
                        help="过滤有猎头发布或代招证据的岗位；可用 --no-exclude-headhunter 关闭")
    parser.add_argument("--cdp-url")
    parser.add_argument("--state-db", type=Path)
    parser.add_argument("--resume-path", type=Path)
    parser.add_argument("--daily-search-page-limit", type=int)
    parser.add_argument("--daily-detail-page-limit", type=int)
    parser.add_argument("--max-consecutive-page-failures", type=int)
    parser.add_argument("--risk-pause-min-minutes", type=int)
    parser.add_argument("--risk-pause-max-minutes", type=int)
    parser.add_argument("--collection-delay-multiplier", type=float)
    parser.add_argument("--boss-salary-decode-failure", choices=["stop", "skip_job"])
    parser.add_argument("--daily-platform-page-limit", type=int)
    parser.add_argument("--risk-lock-minutes", type=int)
    parser.add_argument("--use-ai-score", action=argparse.BooleanOptionalAction,
                        help="对预筛通过的岗位并发运行 AI 评分；可用 --no-use-ai-score 关闭")
    parser.add_argument("--use-ai-greeting", action=argparse.BooleanOptionalAction,
                        help="为评分达到门槛的岗位自动生成招呼语；可用 --no-use-ai-greeting 关闭")
    parser.add_argument("--ai-score-user-prompt", help="AI 评分的用户补充要求")
    parser.add_argument("--ai-greeting-user-prompt", help="AI 招呼语的补充提示词")
    parser.add_argument("--ai-greeting-template", help="未启用 AI 招呼语时使用的固定招呼语")
    parser.add_argument("--ai-score-threshold", type=float,
                        help="Web 评分标色门槛，0～100；默认从 config.py 读取")
    parser.add_argument("--ai-provider")
    parser.add_argument("--ai-service")
    parser.add_argument("--ai-model")
    parser.add_argument("--ai-api-key-env")
    parser.add_argument("--ai-base-url")
    parser.add_argument("--ai-thinking", choices=["auto", "enabled", "disabled"])
    parser.add_argument("--ai-thinking-budget", type=int)
    parser.add_argument("--ai-timeout-seconds", type=int)
    parser.add_argument("--ai-retry-count", type=int,
                        help="可重试的 AI API 错误额外重试次数；0 表示不重试")
    parser.add_argument("--ai-retry-delay-min-seconds", type=float)
    parser.add_argument("--ai-retry-delay-max-seconds", type=float)
    parser.add_argument("--ai-api-concurrency", type=int,
                        help="所有 AI API 请求共享的最大同时调用数，正整数")
    for option in CLEARABLE_OPTIONS:
        parser.add_argument(f"--clear-{option.replace('_', '-')}", action="store_true",
                            help="本次运行清空 config.py 中对应的列表")
    return parser


def resolve_config(args: argparse.Namespace, parser: argparse.ArgumentParser) -> dict:
    """Apply only arguments explicitly supplied on the CLI to config.py defaults."""
    config = deepcopy(DEFAULT_CONFIG)
    for option, (section, key) in CONFIG_OPTIONS.items():
        value = getattr(args, option)
        if value is None:
            continue
        if option in GROUPED_OPTIONS:
            value = [item for group in value for item in group]
        if option in LIST_OPTIONS:
            value = list(dict.fromkeys(value))
        if isinstance(value, Path):
            value = str(value)
        config[section][key] = value
    for option in CLEARABLE_OPTIONS:
        if getattr(args, f"clear_{option}"):
            if getattr(args, option) is not None:
                parser.error(f"--clear-{option.replace('_', '-')} 不能与 --{option.replace('_', '-')} 同时使用")
            section, key = CONFIG_OPTIONS[option]
            config[section][key] = []

    from run import validate_run_config

    try:
        return validate_run_config(config)
    except ValueError as exc:
        parser.error(str(exc))


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    config = resolve_config(args, parser)
    from run import PrefilterTestFailure, run_collection

    try:
        result = run_collection(
            config,
            on_record=lambda record: print(json.dumps(record, ensure_ascii=False), flush=True),
            on_event=lambda message: print(message, file=sys.stderr, flush=True),
        )
    except KeyboardInterrupt:
        print("用户中断采集", file=sys.stderr)
        return 130
    except PrefilterTestFailure as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"采集失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": result.status, "reason": result.reason_code,
                      "message": result.message, "counts": result.counts}, ensure_ascii=False),
          file=sys.stderr)
    if config["collection"]["test_prefilter"]:
        return 0 if result.status in {"completed", "completed_with_shortage"} else 2
    return 0 if result.counts["new"] > 0 and result.status in {"completed", "completed_with_shortage"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
