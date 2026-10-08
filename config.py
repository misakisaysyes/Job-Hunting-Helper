"""Project-level defaults shared by the CLI and collectors."""

import json
from pathlib import Path


# 项目根目录，用于构造不依赖当前工作目录的默认文件路径。
PROJECT_ROOT = Path(__file__).resolve().parent

# 内置城市名称与 BOSS 城市编码的对应表；未收录的城市可用 city_code 指定编码。
CITY_CODES = {
    "北京": "101010100", "上海": "101020100", "深圳": "101280600",
    "广州": "101280100", "杭州": "101210100", "成都": "101270100",
    "武汉": "101200100", "南京": "101190100", "西安": "101110100",
    "苏州": "101190400", "天津": "101030100", "重庆": "101040100",
    "郑州": "101180100", "长沙": "101250100", "东莞": "101281600",
    "佛山": "101280800", "合肥": "101220100", "厦门": "101230200",
    "青岛": "101120200", "大连": "101070200",
}

# 预筛经验选项到 BOSS 列表接口标签的映射。
EXPERIENCE_FILTERS = {
    "经验不限": "经验不限",
    "应届生": "应届生",
    "在校生": "在校生",
    "1年内": "1年以内",
    "1-3": "1-3年",
    "3-5": "3-5年",
    "5-10": "5-10年",
    "10年以上": "10年以上",
}

# 预筛学历选项到 BOSS 列表接口标签的映射。
EDUCATION_FILTERS = {
    "学历不限": "学历不限", "初中及以下": "初中及以下", "中专/中技": "中专/中技",
    "高中": "高中", "大专": "大专", "本科": "本科", "硕士": "硕士", "博士": "博士",
}

# 预筛公司规模选项到 BOSS 列表接口标签的映射。
COMPANY_SIZE_FILTERS = {
    "0-20人": "0-20人", "20-99人": "20-99人", "100-499人": "100-499人",
    "500-999人": "500-999人", "1000-9999人": "1000-9999人", "10000人以上": "10000人以上",
}

DEFAULT_CONFIG = {
    "collection": {
        # 采集平台：当前仅支持 "boss"。
        "platform": "boss",
        # 抓取模式：None、"recommend"（推荐流）、"search"（搜索流）；None 时运行须传 --mode。
        "mode": "recommend",
        # 推荐流的求职期望 ID 列表；每个 ID 分别抓取，recommend 模式至少一个，search 模式须为 []。
        "encrypt_expect_id": [],
        # 是否先运行本地预筛用例；可选 True / False。
        "test_prefilter": False,
        # 搜索流的搜索词列表；search 模式至少一个，recommend 模式必须为空。
        "keywords": [],
        # 搜索流可多选内置城市；空列表表示按 BOSS 当前会话的城市搜索。
        "cities": [],
        # 目标城市名称；空字符串表示不指定城市，由 BOSS 当前会话决定范围。
        "city": "",
        # city 对应的 BOSS 城市编码；空字符串时从 CITY_CODES 查找。
        "city_code": "",
        # 每个搜索词与城市组合最多读取的列表页数，范围 1～10。
        "max_pages": 1,
        # 本轮希望获得的预筛通过且（启用 AI 评分时）达到评分门槛的岗位数，正整数。
        "target_jobs": 40,
        # 本轮最多输出的岗位数；0 表示不限制，不能为负数。
        "max_jobs": 0,
        # 列表排序："default"（接口默认）、"newest"（最新）；推荐流不使用此项。
        "sort": "newest",
        # 单日最多请求的 BOSS 列表页数，正整数。
        "daily_search_page_limit": 60,
        # 单日最多请求的 BOSS 岗位详情页数，正整数。
        "daily_detail_page_limit": 150,
        # 连续列表页失败达到此次数时停止采集，正整数。
        "max_consecutive_page_failures": 3,
        # 触发风险控制后的随机暂停下限，单位分钟，正整数。
        "risk_pause_min_minutes": 5,
        # 风险暂停上限，单位分钟，须大于等于下限。
        "risk_pause_max_minutes": 10,
        # 采集请求间隔的倍率，范围 1～5。
        "collection_delay_multiplier": 1.5,
        # 薪资字体无法解码时："stop"（终止采集）、"skip_job"（继续采集，薪资留空）。
        "boss_salary_decode_failure": "skip_job",
    },
    "safety": {
        # 单日对同一平台允许的总页面访问次数，正整数。
        "daily_platform_page_limit": 500,
        # 触发平台风险锁定时的默认冷却时间，单位分钟，正整数。
        "risk_lock_minutes": 10,
        # 访问计数、风险状态和已采集岗位（含 AI 评分）的 SQLite 数据库路径。
        "state_db": str(PROJECT_ROOT / ".state" / "boss-safety.sqlite3"),
    },
    "browser": {
        # 已登录 Chrome 的 CDP 调试服务地址。
        "cdp_url": "http://127.0.0.1:9222",
    },
    "profile": {
        # 临时测试简历文件路径；启用 AI 评分时读取。
        "resume_path": str(PROJECT_ROOT / "data" / "resumes" / "resume.md"),
        # 接受的岗位学历标签，多个值按 OR 匹配，[] 表示不限。
        # 可选：学历不限、初中及以下、中专/中技、高中、大专、本科、硕士、博士。
        "education": [],
        # 接受的岗位经验标签，多个值按 OR 匹配，[] 表示不限。
        # 可选：经验不限、应届生、在校生、1年内、1-3、3-5、5-10、10年以上。
        "experience_filters": [],
        # 接受的公司规模，多个值按 OR 匹配，[] 表示不限。
        # 可选：0-20人、20-99人、100-499人、500-999人、1000-9999人、10000人以上。
        "company_sizes": [],
        # 接受的招聘类型："experienced"（社招）、"campus"（校招）、"internship"（实习）。
        # 多个值按 OR 匹配；[] 或全部三项均表示不限，无法识别类型的岗位会保留。
        "recruitment_types": ["experienced", "campus", "internship"],
        # 可接受岗位月薪的下限，单位 K；0 表示不限制下限，不能为负数。
        "salary_min": 0,
        # 可接受岗位月薪的上限，单位 K；0 表示不限制上限，不能为负数。
        # 岗位自身的薪资上下限须落在这里设置的区间内。
        "salary_max": 0,
        # 是否过滤薪资面议和无法解析的岗位；可选 True / False。
        "filter_unparsed_salary": False,
        # 职位名排除词；包含任意词即排除，英文匹配不区分大小写，[] 表示不用此规则。
        "deal_breakers": [],
        # 岗位 JD 排除词；包含任意词即排除，英文匹配不区分大小写。
        "jd_deal_breakers": [],
        # 公司名屏蔽词；包含任意词即排除，英文匹配不区分大小写。
        "blocked_companies": [],
        # 是否排除有猎头发布或代招证据的岗位；可选 True / False。
        "exclude_headhunter": True,
    },
    "ai": {
        # 是否在采集时为预筛通过的完整岗位运行 AI 评分；可选 True / False。
        "use_ai_score": True,
        # 是否为评分达到 score_threshold 的岗位自动生成招呼语；可选 True / False，默认关闭。
        "use_ai_greeting": False,
        # 评分时传给模型的用户补充要求；空字符串表示没有补充要求。
        "score_user_prompt": "",
        # 生成招呼语时传给模型的补充提示词；空字符串表示使用默认规则。
        "greeting_user_prompt": "",
        # 未启用 AI 招呼语时，预筛通过且达到评分门槛的岗位使用的固定招呼语；空字符串表示手动填写。
        "greeting_template": "",
        # AI 评分门槛，范围 0～100；低于此值的岗位标记为“已过滤”，达到或超过时为“已评分”并在 Web 标色。
        "score_threshold": 71,
        # 以下配置供 AI 请求及并发调度使用。
        # API 协议标识；当前预置 "openai_compatible"，不是有限枚举。
        "provider": "openai_compatible",
        # 服务商标识；当前预置 "deepseek"，不是有限枚举。
        "service": "deepseek",
        # 请求使用的模型名称，由服务商决定；当前预置 "deepseek-flash"。
        "model": "deepseek-v4-pro",
        # 存放 API Key 的环境变量名；不在配置文件中保存密钥。
        "api_key_env": "DEEPSEEK_API_KEY",
        # AI 服务的基础 URL。
        "base_url": "https://api.deepseek.com",
        # 思考模式："auto"（自动）、"enabled"（开启）、"disabled"（关闭）。
        "thinking": "disabled",
        # 预留的思考 Token 预算，正整数；当前 Chat Completions 调用不提交此字段。
        "thinking_budget": 2048,
        # 单次 AI 请求的超时时间，单位秒，正整数。
        "timeout_seconds": 120,
        # 可重试错误的额外重试次数；0 表示不重试，默认 1 次。
        "retry_count": 1,
        # 可重试的 AI API 错误在重试前随机等待的最短时间，单位秒，不能为负数。
        "retry_delay_min_seconds": 1.0,
        # 可重试的 AI API 错误在重试前随机等待的最长时间，单位秒，须不小于下限。
        "retry_delay_max_seconds": 3.0,
        # 同一 AI 调度器中所有任务共享的 API 请求最大同时调用数，正整数。
        "ai_api_concurrency": 100,
    },
}

# 监测任务的 Web 配置。监测尚无 CLI 入口，避免混入采集 CLI 的参数集合。
MONITORING_CONFIG = {
    # 每条会话读取的最近消息数，正整数。
    "message_limit": 10,
    # 追问只扫描「仅沟通」中最近活动的会话；默认关闭自动追问功能。
    "followup_enabled": False,
    "followup_days": 4,
    "followup_unread": True,
    "followup_read_no_reply": True,
    "followup_cooldown_hours": 24,
    "followup_max_count": 2,
    "followup_review_required": True,
    # 新招呼会话命中 profile.blocked_companies 时，默认先审核再删除。
    "filter_review_required": True,
}

# Web 保存的用户设置覆盖上述默认值；CLI 未指定的参数也读取这些设置。
USER_CONFIG_PATH = PROJECT_ROOT / ".state" / "user-config.json"
if USER_CONFIG_PATH.is_file():
    try:
        _saved_config = json.loads(USER_CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(_saved_config, dict):
            raise ValueError("根节点必须是对象")
        for _section, _values in _saved_config.items():
            _target = MONITORING_CONFIG if _section == "monitoring" else DEFAULT_CONFIG.get(_section)
            if _target is None or not isinstance(_values, dict):
                continue
            for _key, _value in _values.items():
                if _key in _target:
                    _target[_key] = _value
    except (OSError, ValueError) as _exc:
        raise ValueError(f"无法读取用户配置 {USER_CONFIG_PATH}: {_exc}") from _exc
