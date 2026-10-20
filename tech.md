# 技术架构

项目采用本地 Web 架构：React 提供操作界面，Python 执行采集、监测和 AI 任务，SQLite 保存结果；浏览器与模型服务分别负责访问 BOSS 直聘和生成内容。

## 整体结构

```text
React / Vite（工作台、采集、监测、配置）
                  │ /api
Python HTTP API（ThreadingHTTPServer）
                  │
采集 / 监测 / AI 调度
  ├─ Patchright + CDP → 已登录 Chrome → BOSS 直聘
  ├─ HTTPX → OpenAI 兼容模型服务
  └─ SQLite → 岗位、会话、回执、任务与统计
```

通过 `./web/start.sh` 启动时，前端默认使用 8000 端口，API 使用 8001 端口；Vite 将 `/api` 请求代理给后端。

## 模块分工

| 模块 | 主要位置 | 职责 |
| --- | --- | --- |
| Web 界面 | [web/src/features/](web/src/features/) | 展示任务、岗位、会话、配置与转化图表 |
| HTTP API | [api/](api/) | 校验请求、启动任务、返回状态和业务数据 |
| 采集流程 | [run.py](run.py)、[collector/](collector/) | 编排岗位获取、去重、预筛和 AI 处理 |
| 浏览器操作 | [browser/](browser/) | 通过 CDP 连接 Chrome，读取页面、发送招呼与追问 |
| AI 调用 | [ai/](ai/)、[score.py](score.py)、[message.py](message.py) | 共用模型客户端和线程池，执行评分、招呼与追问生成 |
| 数据与统计 | [data/](data/)、[api/conversion.py](api/conversion.py) | 保存岗位、会话及回执，聚合转化数据 |
| 配置与命令行 | [config.py](config.py)、[api/settings.py](api/settings.py)、[cli/](cli/) | 管理默认值、Web 配置和 CLI 入口 |

## 核心数据流

1. **采集**：推荐流或搜索流 → 去重、预筛 → 可选 AI 评分 → 准备招呼语 → 保存岗位 → 审核后批量发送。
2. **监测**：“仅沟通”读取会话、准备追问；“有交换”读取简历回执、确认投递。处理结果写入 SQLite。
3. **统计**：API 按日期聚合岗位、沟通与投递记录，前端绘制桑基图和趋势图。

## 任务与状态

- **后台执行**：采集、扫描和批量追问在后台线程运行，页面轮询进度；SQLite 任务记录限制这些任务同时运行。
- **AI 并发**：评分、招呼和追问共用 `AITaskScheduler` 线程池，由最大并发数约束模型请求。
- **状态恢复**：任务、草稿和发送结果持久保存；服务重启时将未完成任务标记为中断，无法确认的发送结果保留为未知状态。

## 配置与数据

| 文件或目录 | 内容 |
| --- | --- |
| `config.py` | 项目默认配置 |
| `.state/user-config.json` | Web 保存的配置，覆盖默认值；CLI 显式参数再覆盖本次采集配置 |
| `.config.credentials.yaml` | 本地 API Key，文件权限为 `0600`；无本地密钥时读取配置指定的环境变量 |
| `.state/resumes/` | Web 上传的 Markdown 简历 |
| `.state/boss-safety.sqlite3` | 默认 SQLite 数据库，保存岗位、会话、回执、任务及访问额度 |

Web 配置经校验后保存，点击“生效配置”应用；改变 AI 并发数时重启 API，以重新建立线程池。具体选项见 [Web 端配置](config.md)。
