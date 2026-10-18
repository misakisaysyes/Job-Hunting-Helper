// Paths refer to existing config.py fields; layout does not change saved keys.
export const CONFIG_SECTIONS = [
  {
    key: "collection", title: "采集任务", description: "先选岗位来源，再设置本轮采集的范围与处理方式。",
    groups: [
      {
        key: "source", title: "岗位来源", description: "采集方式决定下方需要填写的来源信息。",
        fields: ["collection.platform", "collection.mode"],
        branches: {
          recommend: {
            title: "推荐流", description: "按 BOSS 求职期望读取推荐岗位。",
            fields: ["collection.encrypt_expect_id"],
          },
          search: {
            title: "搜索流", description: "按城市与关键词组合搜索岗位。",
            fields: ["collection.cities", "collection.keywords", "collection.sort"],
          },
          both: {
            title: "推荐流 + 搜索流", description: "每轮先采集推荐流，再采集搜索流；未达目标时按顺序重复。",
            fields: ["collection.encrypt_expect_id", "collection.cities", "collection.keywords", "collection.sort"],
          },
        },
      },
      {
        key: "range", title: "本轮范围与结果", description: "控制每组页数和目标采集数。",
        fields: ["collection.max_pages", "collection.target_jobs"],
      },
    ],
  },
  {
    key: "safety", title: "反爬安全", description: "将访问额度、采集节奏和风险后的暂停集中配置。",
    groups: [
      {
        key: "quota", title: "每日访问额度", description: "总额度约束整个 BOSS 平台，列表页与详情页分别有子额度。",
        fields: ["safety.daily_platform_page_limit", "collection.daily_search_page_limit",
          "collection.daily_detail_page_limit"],
      },
      {
        key: "pace", title: "采集节奏与失败保护", description: "调整请求间隔，并在连续页面失败时停止本轮采集。",
        fields: ["collection.collection_delay_multiplier", "collection.max_consecutive_page_failures"],
      },
      {
        key: "risk", title: "风险暂停与锁定", description: "触发风控后随机暂停；平台锁定时间用于控制再次访问。",
        fields: ["collection.risk_pause_min_minutes", "collection.risk_pause_max_minutes",
          "safety.risk_lock_minutes"],
      },
    ],
  },
  {
    key: "profile", title: "岗位预筛与简历", description: "先设置候选范围，再设置排除条件和 AI 使用的简历。",
    groups: [
      { key: "requirements", title: "岗位要求", description: "同类选项支持多个值，岗位满足任一选项即可。",
        fields: ["profile.education", "profile.experience_filters", "profile.company_sizes",
          "profile.recruitment_types", "profile.salary_min", "profile.salary_max",
          "profile.filter_unparsed_salary"] },
      { key: "exclusions", title: "排除条件", description: "命中排除词或猎头规则的岗位会被预筛排除。",
        fields: ["profile.deal_breakers", "profile.jd_deal_breakers",
          "profile.blocked_companies", "profile.exclude_headhunter"] },
      { key: "resume", title: "简历", description: "AI 评分与招呼生成读取当前上传的 Markdown 简历。",
        fields: ["profile.resume_path"] },
    ],
  },
  {
    key: "ai", title: "AI 评分与招呼", description: "评分与招呼共用同一模型服务和请求设置。",
    groups: [
      { key: "decisions", title: "评分与招呼", description: "分别选择岗位评分和招呼语的准备方式。",
        fields: ["ai.use_ai_score", "ai.score_threshold", "ai.score_user_prompt",
          "ai.use_ai_greeting", "ai.greeting_user_prompt", "ai.greeting_template"] },
      { key: "model", title: "模型服务", description: "服务地址、模型与 API Key。",
        fields: ["ai.provider", "ai.service", "ai.model", "ai.base_url",
          "ai.api_key", "ai.thinking", "ai.thinking_budget"] },
      { key: "requests", title: "请求与并发", description: "超时、重试等待和同时发出的请求数。",
        fields: ["ai.timeout_seconds", "ai.retry_count", "ai.retry_delay_min_seconds",
          "ai.retry_delay_max_seconds", "ai.ai_api_concurrency"] },
    ],
  },
  {
    key: "monitoring", title: "监测任务", description: "按已沟通、有交换、新会话分别查看对应任务的配置。",
    groups: [
      { key: "communicated", title: "已沟通", description: "扫描 BOSS「仅沟通」中的会话，并设置追问范围、冷却时间与次数。",
        fields: ["monitoring.message_limit", "monitoring.followup_days", "monitoring.followup_enabled", "monitoring.followup_valid_days",
          "monitoring.followup_types", "monitoring.followup_cooldown_hours", "monitoring.followup_max_count"] },
      { key: "exchanged", title: "有交换", description: "从 BOSS「更多 → 有交换」读取会话，不打开会话，池外岗位保持池外。",
        fields: ["monitoring.exchanged_active_days"] },
      { key: "new_greetings", title: "新会话", description: "扫描 BOSS「新招呼」；公司命中排除词时，可选择先审核再删除。",
        fields: ["monitoring.new_greetings_active_days", "monitoring.filter_review_required"] },
    ],
  },
  {
    key: "browser", title: "运行配置", description: "控制采集、监测和批量操作使用浏览器的方式。",
    groups: [
      { key: "background", title: "浏览器后台运行",
        description: "使用当前已登录的 Chrome，在后台工作标签页执行任务。",
        fields: ["browser.silent_background"] },
    ],
  },
];
