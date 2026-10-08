# job-hunting-helper 采集器

## 现有模块结构

`cli/` 是命令行入口；`collector/`、`score.py`、`message.py` 分别放采集、评分和招呼语业务代码。
`ai/` 提供通用模型调用、任务包装与并发调度，`browser/` 提供浏览器访问，`config.py` 保存配置，
`data/` 放现有的数据准备代码与简历文件，`test/` 放测试。
业务模块使用能力模块；`ai/` 和 `data/` 不导入业务模块。
`collector/orchestrator.py` 只负责采集调度，`collector/platforms/boss.py` 组装 BOSS 请求；
根目录的 `run.py` 校验运行配置、执行可选预筛验证、管理 Chrome 采集会话并编排可选 AI 评分，
再把岗位结果交给调用方。
`cli/cli.py` 负责调试命令行的参数和终端输出。
`api/` 向 Web 页面提供岗位读取、招呼语和状态操作；`web/` 是 React 页面，所有数据操作都通过 API。

## 岗位数据页面

在项目根目录运行一个脚本即可安装缺失依赖，同时启动 Vite 前端开发服务和本地 API：

```bash
./web/start.sh
```

打开 `http://127.0.0.1:8000`。修改 `web/src/` 下的 React 或 CSS 文件会自动热更新页面，
无需重新运行启动脚本；修改 Python API 代码后仍需重启脚本。
前端通过 Vite 代理调用 API，所有系统操作仍由 `api/` 提供。
可用 `./web/start.sh --port 8767` 修改前端端口，API 默认使用下一个端口。
页面上的“开始采集”使用 `config.py` 的当前配置启动一轮后台采集，
显示运行状态和最终计数；运行期间不能重复启动，结束后自动刷新岗位列表。
采集中的发现、重复岗位、入库、预筛、评分与达标计数会同步更新到采集状态和“今日任务”。
“重复岗位”只统计本轮发现且此前已入库的不同岗位；本轮内多次出现的岗位仍会去重，但不计入该项。旧执行记录保留“重复（旧口径）”标识。
页面按入库时间列出岗位、AI 分数和理由，可展开 JD，也可点击删除并确认。
删除后，该岗位下次采集时可能再次入库。
列表顶部可用关键词表达式筛选全部入库岗位，支持 `&&`、`||` 和括号，`&&` 优先于 `||`。
`本科`、`3-5年`、`社招` 等词优先匹配对应字段；`25-50K`、`40K`、`14薪` 匹配薪资，英文不区分大小写；
无单位的 `3-5` 会精确匹配同区间的工作年限或薪资；其他词匹配岗位名称、公司、地点、JD、薪资、年限及学历。状态下拉框与关键词共同筛选，之后再分页。
“批量操作”只勾选当前页岗位；批量删除需确认，批量评分会重置未发送岗位的状态与招呼语草稿，
批量生成会覆盖已有草稿，批量发送只发送已有招呼语的岗位并跳过其余岗位。
批量流转到打招呼只处理当前为“已评分”且发送状态空闲的岗位；采集和监测的批量操作完成后播放提示音。
列表同时显示入库时间和更新时间；旧记录首次升级时，更新时间初始化为原入库时间。
列表的岗位状态显示已评分、打招呼、已招呼、已结束。低分岗位仍为“已评分”，以灰色标识。
开启 AI 评分时，达到门槛的岗位评分后自动进入“打招呼”阶段；未开启时，预筛通过的岗位也直接进入该阶段。旧记录中的“已过滤”统一为“已评分”，“监测中”统一为“已招呼”。
`config.py` 的 `ai.score_threshold` 默认是 71。低于门槛的岗位默认停在“已评分”；即使有预生成招呼语，也须点击“仍去打招呼”才进入下一阶段。
岗位详情中，已评分岗位展示“已评分 → 打招呼 → 已招呼”，未到达的“已招呼”置灰且不可点；已招呼岗位展示“已评分 → 已招呼”；已结束岗位展示“已评分 → 已结束”。
仅选中当前“打招呼”节点时可生成、手写、保存和发送招呼语。只有采集时启用过 AI 评分的岗位提供 AI 生成。
点击“发送招呼”会把当前文本保存并通过已登录 Chrome 发到 BOSS；只有在对应 BOSS 会话确认己方消息后才进入“已招呼”。
选中“已招呼”节点可查看已发送的招呼语。发送结果不明确时不会自动重发，需要人工核查。
发送逻辑已通过模拟浏览器验证；首次真实发送仍需用用户指定的岗位和文本验证当前 BOSS 页面交互。
强制结束会记录为“已结束（强制中断）”。普通会话扫描不再把岗位改为“监测中”。
API 使用 `config.py` 中的 `safety.state_db`；如采集时用了其他数据库路径，启动服务时传
`--state-db 路径`，页面启动的采集也会写入同一路径。服务只监听本机 `127.0.0.1`。
模型请求优先使用配置页保存并生效的 API Key；未配置时使用当前环境的 `DEEPSEEK_API_KEY`。
Web 启动脚本在环境变量未设置、相邻 BossHunter 项目存在 `.config.credentials.yaml` 时，
会从中读取现有 Key，不会将 Key 打印出来。启用 AI 评分但仍无可用 Key 时，页面会提示且不会启动采集。

Web 使用 `GET /api/jobs?limit=20&offset=0&query=...&status=...` 先筛选再分页读取，用
`DELETE /api/jobs/{platform}/{source_job_id}` 删除单条记录。前端不直接访问数据库。
采集通过 `POST /api/collections` 启动，`GET /api/collections/current` 查看状态。
重新评分使用 `POST /api/jobs/{platform}/{id}/score`；批量操作逐个调用相应岗位 API，
跳过已发送或已结束岗位的重评，并对没有已保存招呼语的岗位跳过批量发送。
招呼语生成、保存和发送分别使用 `POST /api/jobs/{platform}/{id}/greeting/generate`、
`PUT /api/jobs/{platform}/{id}/greeting` 和 `POST /api/jobs/{platform}/{id}/greeting/send`。
强制结束使用 `POST /api/jobs/{platform}/{id}/force-end`。
工作台首页先说明岗位、会话等名词，再介绍两类任务的流程，并显示当前服务中的最近一轮任务状态。
“今日任务”的已招呼数按当天确认发送成功的招呼统计，不归属到某轮采集；执行记录不再统计招呼语生成数。
首页可用 `GET/PUT /api/settings/basic` 查看和保存运行所需的简版配置；保存到
`.state/user-config.json`，覆盖 `config.py` 中的默认值，下一轮 Web 任务立即生效，CLI 下次启动时也会读取。
当前运行中的任务继续使用启动时的配置。完整配置页的“保存配置”先写入本地，点击“生效配置”后供下一轮任务使用。
若修改了需要在启动时建立的配置（如 AI 并发数），生效操作会在采集、监测任务空闲时自动重启 API 服务；页面会等待服务恢复。
API Key 可在完整配置页填写，单独保存在权限为 `0600` 的 `.state/ai-api-key`，读取配置的接口不会返回原文；也可继续使用环境变量。
监测页通过 `POST /api/monitoring` 启动后台扫描，
`GET /api/monitoring/current` 读取进度，`GET /api/conversations?limit=20&offset=0`
读取去重后的会话列表；“查看会话”通过 `POST /api/conversations/{platform}/{conversation_id}/open`
在采集用 Chrome 打开会话链接；每条读取多少条消息的默认值由 `config.py` 的 `MONITORING_CONFIG` 提供；工作台保存的覆盖值无需重启服务。
直接修改 `config.py` 后仍需重启服务。
监测只处理 BOSS「仅沟通」列表中最近活动、且列表回执为「送达」或「已读」的会话，
分别记录为「未读」和「已读未回」。扫描天数和每条读取的消息数可配置；不会打开会话或读取其他状态的历史消息。
会话首次扫描的入库时间保持不变，再次扫描会更新当前回执；离开监测范围的记录保留在本地但不再出现在当前列表。
追问默认关闭；启用后可分别选择未读和已读未回招呼，并配置冷却小时数、每条会话追问上限和是否审核。
符合条件时原文复制招呼语，审核模式下在会话列表确认或跳过；关闭审核则自动发送。
冷却时间从最近一次我方发送算起；只有确认 BOSS 发出新消息后才计入追问次数。结果不明时锁定该会话，停止自动重发。
会话状态只包含「未读」和「已读未回」，不再分类 HR 回复，也不根据监测结果改写岗位终态。
同一轮监测还扫描「新招呼」列表，不打开会话；公司名命中 `profile.blocked_companies` 时，过滤候选单独入库并去重。
`monitoring.filter_review_required` 默认为开启，用户可在监测页审核删除或保留；关闭后会自动从 BOSS 列表删除命中会话。
删除前再次核对会话、公司和招聘者，删除结果未确认时停止自动重试。过滤候选接口为 `GET /api/filter-candidates`，
审核接口为 `POST /api/filter-candidates/{platform}/{conversation_id}/delete/confirm` 与 `/delete/skip`。
池外岗位的自动预筛仍未实现。
需要静态构建时，先使用 Node.js 20.19+ 或 22.12+，再在 `web/` 目录运行
`npm run build`，最后运行 `.venv/bin/python api/server.py`。

当前仅接入 BOSS，采集调度直接调用 BOSS 采集器。
采集器在已登录 Chrome 中请求 BOSS 列表接口，并打开岗位详情页；采集本身不会发送招呼语。
列表请求由 Chrome 页面中的 `fetch` 发出，可在 Chrome 使用的代理中查看。采集器仅打开一个静态同源页面作为请求载体，不会触发推荐页自动发送的空 `encryptExpectId` 请求。

## 临时试运行

1. 启动带 CDP 调试端口 `9222` 的 Chrome，并在其中登录 BOSS。
2. 安装依赖：`python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`。
3. 默认用 `--mode recommend` 或 `--mode search` 选择抓取模式；也可在 `config.py` 设置 `collection.mode`。

采集平台由 `config.py` 的 `collection.platform` 配置，默认 `boss`；当前只接入 BOSS，也可显式传 `--platform boss`。

推荐流使用 `GET /wapi/zpgeek/pc/recommend/job/list.json`，需要非空的
`encrypt_expect_id` 列表中的至少一个 ID，可在 `config.py` 设置或通过 `--encrypt-expect-id` 覆盖。
多个 ID 用英文逗号分隔（也可重复指定参数）；采集器会依次请求每个 ID，并对重复岗位去重。
可在已登录 Chrome 的推荐页 `https://www.zhipin.com/web/geek/jobs`
选择求职期望，然后从 Network → Fetch/XHR → `recommend/job/list.json` 请求的
Query String Parameters 中读取 `encryptExpectId`。

```bash
.venv/bin/python cli/cli.py --mode recommend --encrypt-expect-id YOUR_EXPECT_ID \
  --city 北京 --max-pages 1 --max-jobs 3
```

多个求职期望：`--encrypt-expect-id FIRST_ID,SECOND_ID`。

搜索流使用 `POST /wapi/zpgeek/search/joblist.json`，需要至少一个搜索词，可在配置中设置或通过 `--keyword` 覆盖，
不使用 `--encrypt-expect-id`。搜索接口的表单中虽然有同名字段，但保持为空。

```bash
.venv/bin/python cli/cli.py --mode search --keyword 前端 \
  --city 上海 --max-pages 1 --max-jobs 3
```

Cookie 和会话请求头由 Chrome 处理，不需要写入配置文件或命令行。

连接已登录的 Chrome 进行预筛验证：

```bash
.venv/bin/python cli/cli.py --test-prefilter --mode search \
  --keyword 前端 --city 上海 \
  --max-pages 1 --max-jobs 3 --education 大专,本科 \
  --company-size 500-999人,1000-9999人 \
  --recruitment-type experienced --salary-min 10 --salary-max 20 \
  --deal-breaker 外包 --jd-deal-breaker 出差 --blocked-company 示例公司 \
  --exclude-headhunter
```

`--test-prefilter` 先运行本地预筛用例，通过后再连接 Chrome 采集真实岗位。
进度会报告列表和详情预筛的过滤原因，最终统计包含过滤数；没有新增岗位也可以通过验证。
命令行选项只影响本次运行，不会修改 `config.py`。招聘类型参数可以重复指定。
职位名、JD 和屏蔽公司三类排除词均可用逗号分隔或重复指定；同一类中的词
按 OR 关系匹配，例如 `--deal-breaker 外包,驻场` 会排除职位名包含任意一个词的岗位。
三类规则分别检查职位名、JD、公司名，任意一类命中就排除岗位。英文匹配不区分大小写。
`--education` 筛选岗位学历，多个选项用逗号隔开或重复指定，例如
`--education 大专,本科`，按 OR 关系匹配；也会提交给所选列表接口。
`--company-size` 筛选公司规模，可选 `0-20人`、`20-99人`、`100-499人`、
`500-999人`、`1000-9999人`、`10000人以上`；不指定则不限，多个选项用逗号
隔开或重复指定，按 OR 关系匹配，也会提交给所选列表接口。
列表和详情都会核对规模；有筛选条件而仍无法识别规模的岗位会被过滤。
岗位经验可用 `--experience` 筛选，多个选项可用逗号隔开，例如
`--experience 1-3,3-5`；也可重复指定 `--experience 1-3 --experience 3-5`。
可选值为 `经验不限`、`应届生`、`在校生`、
`1年内`、`1-3`、`3-5`、`5-10`、`10年以上`。多个经验选项同样按 OR 关系匹配。
经验范围也会写入所选列表接口请求；采集器还会在本地核对列表与详情中的经验值。
配置默认保留面议或无法解析薪资的岗位；加 `--filter-unparsed-salary` 后同时过滤这两类岗位。
两种情况会分别报告“薪资面议”和“薪资无法解析”；输出岗位的 `salary_status` 为
`negotiable`、`unparsed` 或 `parsed`。现有开关仍同时控制前两种情况。
`--exclude-headhunter` 排除有猎头发布证据的岗位：招聘者头衔或所属公司名含猎头、
招聘者所属公司与岗位公司不同且展示人力资源服务许可证，或人力资源服务机构的 JD
明确提及为客户代招。职位名是“猎头顾问”或 JD 仅提到
“对接猎头”不会单独触发。配置默认不过滤；可用 `--exclude-headhunter` 开启。

推荐流不接受搜索词；搜索流至少需要一个搜索词。
推荐流的 `--sort` 不生效。不传 `--city` 时接口传入空城市编码，由 BOSS 当前会话决定推荐范围；
输出中的岗位城市从接口数据读取。

默认每采到一个完整岗位，CLI 就输出一行 JSON。开启 AI 评分时，会在采集过程中并发提交评分，
等所有评分完成后输出包含评分状态的岗位 JSON；进度和最终统计写到标准错误输出。
`--max-jobs 0` 表示不限制本轮输出数量。访问次数和风险冷却保存在 `.state/boss-safety.sqlite3`。

当前简版只做本轮内的岗位去重，不保存岗位库或跨轮去重。预筛选配置在
`config.py` 的 `DEFAULT_CONFIG["profile"]` 中，运行 CLI 时会读取这份配置：
`profile.resume_path` 指向 `data/resumes/resume.md` 作为 AI 评分的测试简历；
该目录包含个人资料，已在 `.gitignore` 中排除。只有启用 AI 评分时才读取简历。

| 字段 | 含义 |
| --- | --- |
| `resume_path` | 测试简历的绝对路径；后续供 AI 评分读取 |
| `education` | 接受的岗位学历要求；多个值按 OR 关系匹配，空列表不限制 |
| `company_sizes` | 接受的公司规模；多个值按 OR 关系匹配，空列表不限制 |
| `recruitment_types` | 接受的类型，可从 `experienced`（社招）、`campus`（校招）、`internship`（实习）中选择；无法识别类型的岗位会保留 |
| `salary_min`、`salary_max` | 期望月薪范围，单位 K；岗位薪资上下限须完全落在范围内，0 表示不设该边界 |
| `filter_unparsed_salary` | 是否过滤面议或无法解析薪资，默认关闭 |
| `deal_breakers` | 职位名排除词；命中任意词即排除 |
| `jd_deal_breakers` | JD 正文排除词；命中任意词即排除 |
| `blocked_companies` | 公司名屏蔽词；命中任意词即排除 |
| `exclude_headhunter` | 是否排除有猎头发布或代招证据的岗位，默认关闭 |

所有命令行参数的默认值都在 `config.py` 的 `DEFAULT_CONFIG` 中。未指定的选项使用配置值；
指定的选项只覆盖本次运行的对应值。`--encrypt-expect-id`、`--keyword`、`--education`、`--company-size`、
`--recruitment-type`、`--experience` 和三种排除词会整体替换配置中的列表；可用相应的
`--clear-...` 选项清空列表，例如 `--clear-education`、`--clear-keyword`。
若配置将布尔值设为 `True`，可通过 `--no-test-prefilter`、
`--no-filter-unparsed-salary` 或 `--no-exclude-headhunter` 在本次运行关闭。

| 配置位置 | 默认值 | 对应命令行选项 |
| --- | --- | --- |
| `collection.mode` | `None`，运行时须指定 | `--mode` |
| `collection.encrypt_expect_id` | 空列表 `[]`；推荐流至少需一个 ID | `--encrypt-expect-id ID[,ID...]` |
| `collection.test_prefilter` | `False` | `--test-prefilter` / `--test` |
| `collection.keywords` | 空列表 | `--keyword` |
| `collection.city`、`city_code` | 空字符串 | `--city`、`--city-code` |
| `collection.max_pages`、`target_jobs`、`sort` | `1`、`40`、`newest` | `--max-pages`、`--target-jobs`、`--sort` |
| `collection.max_jobs` | `0`（不限制；网页采集固定使用 0） | `--max-jobs` |
| `browser.cdp_url` | `http://127.0.0.1:9222` | `--cdp-url` |
| `safety.state_db` | `.state/boss-safety.sqlite3` 的绝对路径 | `--state-db` |
| `profile.experience_filters` | 空列表 | `--experience` |

`DEFAULT_CONFIG` 中其余采集限额、安全限制、简历路径和 AI 预置项也可通过同名的
连字符形式覆盖，例如 `--daily-search-page-limit`、`--risk-lock-minutes`、
`--resume-path`、`--ai-model` 和 `--ai-api-concurrency`。全部选项可查看
`.venv/bin/python cli/cli.py --help`。

采集器读取 BOSS 列表接口后先按职位、公司、公司规模、学历、招聘类型、工作经验及薪资预筛；读取详情后
用详情字段复查，并执行 JD 排除词。设置学历或公司规模选项时，无法识别对应字段的岗位会被排除；
未知招聘类型仍会保留，避免误删。

预筛通过的岗位会写入 `safety.state_db` 指定的 SQLite 数据库 `collected_jobs` 表。
启用 AI 评分时，在评分完成后连同分数、理由或失败状态一起保存；未启用评分时保存岗位本身。
本轮目标采集数统计预筛通过的岗位；启用 AI 评分时只统计达到评分门槛的岗位。
推荐流按求职期望 ID 顺序、搜索流按随机排列的城市与关键词组合轮询，每个来源每轮最多读取 `max_pages` 页。
达到目标即停止；整轮未发现新岗位或触及安全额度时结束并报告目标未达成。
下一次运行会按平台和岗位 ID 跳过已保存的岗位，不再打开详情页或重复评分。

`config.py` 的 `DEFAULT_CONFIG["ai"]` 预置了 DeepSeek 测试配置：
`deepseek-flash`、`https://api.deepseek.com`、关闭 Thinking、预算 2048 Token、
超时 120 秒、AI API 最大同时调用数 100（`ai_api_concurrency`）。API Key 预留从 `DEEPSEEK_API_KEY` 环境变量读取，
不写入配置文件。`thinking_budget` 当前为预留配置，不会提交到 Chat Completions 接口。
AI 模块直接读取这些配置；缺少 `service`、`thinking`、`timeout_seconds` 或
`ai_api_concurrency` 时会报错，不在模块内另设默认值。
`ai.use_ai_score` 默认 `False`；可以在配置中开启，或在本轮使用 `--use-ai-score` 开启、
`--no-use-ai-score` 关闭。`ai.score_user_prompt` 是评分时传给模型的补充要求，
可用 `--ai-score-user-prompt` 覆盖。
模型接口遇到 HTTP `408`、`429`、`500`、`502`、`503`、`504` 或网络超时、连接错误时，
会在 `retry_delay_min_seconds`～`retry_delay_max_seconds` 指定的范围内随机等待，
最多额外重试 `retry_count` 次（默认 1，设为 0 则不重试）。其他 HTTP 错误不会重试；
最终失败会保留在对应任务的 `Future` 中。可用 `--ai-retry-count`、
`--ai-retry-delay-min-seconds` 和 `--ai-retry-delay-max-seconds` 覆盖配置。

## AI 任务模块

`ai.call_model` 调用 OpenAI 兼容的 `/chat/completions` 接口，`ai.AITaskScheduler` 按
`ai.ai_api_concurrency` 控制同时执行的任务数，多余任务进入等待队列。
所有评分和招呼语任务应提交给同一个调度器实例；调度器关闭时会等待已提交任务完成。
`make_score_task` 返回 0～100 分及理由，`make_greeting_task` 返回招呼语草稿。
两种包装函数都接受岗位、简历文本和可选的用户提示词。新任务类型可通过
`wrap_ai_task(kind, job_id, run)` 包装后提交，不需要修改调度器。

```python
from copy import deepcopy
from ai import AITaskScheduler
from config import DEFAULT_CONFIG
from data.job_input import load_resume_text
from message import make_greeting_task
from score import make_score_task

config = deepcopy(DEFAULT_CONFIG)
resume = load_resume_text(config)
job = {"id": "example-1", "title": "前端工程师", "company": "示例科技", "jd": "负责浏览器性能优化"}

scheduler = AITaskScheduler(config)
try:
    score = scheduler.submit(make_score_task(job, resume, "重点关注性能优化经验"))
    greeting = scheduler.submit(make_greeting_task(job, resume))
    print(score.result(), greeting.result())
finally:
    scheduler.shutdown()
```

开启采集时的 AI 评分前需设置 `DEEPSEEK_API_KEY`，并确保 `profile.resume_path` 指向可读取的简历文本。
例如：

```bash
.venv/bin/python cli/cli.py --mode search --keyword 前端 --use-ai-score
```

完整岗位通过预筛后即提交评分，JS 采集继续运行；所有评分结束后本轮任务才完成。
输出中的 `ai_score_status` 为 `scored` 或 `score_failed`；失败时附带 `ai_score_error`，
最终统计包含 `ai_scored`、`ai_score_failed`。当前仍只输出 JSON，不持久化岗位或发送招呼语。
