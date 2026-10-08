"""Generate a simple batch workflow preview from the current saved configuration.

Run from the project root with ``python3 design/generate_batch_workflow_preview.py``.
This is a design preview; it does not start either task.
"""

from __future__ import annotations

from html import escape
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config import DEFAULT_CONFIG, MONITORING_CONFIG  # noqa: E402


WIDTH = 1500
HEIGHT = 810
OUT = Path(__file__).with_name("workflow_batch_preview.svg")


def label(value: object) -> str:
    return escape(str(value), quote=True)


def card(x: int, y: int, width: int, title: str, detail: str, *, kind: str = "normal", planned: bool = False) -> str:
    colors = {
        "normal": ("#FFFFFF", "#C8DCF2"),
        "decision": ("#FFF9F0", "#E8CDA8"),
        "result": ("#F0FAF5", "#B6DDC7"),
        "manual": ("#F5F1FF", "#D7C9F2"),
        "off": ("#F5F6F8", "#D6DBE2"),
    }
    fill, stroke = colors[kind]
    dash = ' stroke-dasharray="6 5"' if planned else ""
    tag = '<text class="planned" x="{}" y="{}" text-anchor="end">规划</text>'.format(x + width - 13, y + 19) if planned else ""
    return (
        f'<g><rect x="{x}" y="{y}" width="{width}" height="102" rx="13" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"{dash}/>'
        f'{tag}<text class="card-title" x="{x + 16}" y="{y + 43}">{label(title)}</text>'
        f'<text class="card-detail" x="{x + 16}" y="{y + 72}">{label(detail)}</text></g>'
    )


def arrow(x1: int, x2: int, y: int) -> str:
    return (
        f'<line x1="{x1}" y1="{y}" x2="{x2 - 7}" y2="{y}" stroke="#8DAACB" stroke-width="2"/>'
        f'<path d="M {x2 - 9} {y - 5} L {x2} {y} L {x2 - 9} {y + 5} Z" fill="#8DAACB"/>'
    )


def main() -> None:
    collection = DEFAULT_CONFIG["collection"]
    ai = DEFAULT_CONFIG["ai"]
    mode = collection["mode"]
    if mode == "recommend":
        source_detail = f'推荐流 · {len(collection["encrypt_expect_id"])} 组期望'
    elif mode == "search":
        source_detail = f'搜索流 · {len(collection["keywords"])} 个关键词'
    else:
        source_detail = "来源待配置"
    job_limit = "不限" if collection["max_jobs"] == 0 else str(collection["max_jobs"])
    score_on = bool(ai["use_ai_score"])
    greeting_on = score_on and bool(ai["use_ai_greeting"])

    top_row = [
        ("获取岗位", f'{source_detail} · 每组 {collection["max_pages"]} 页', "normal", False),
        ("逐岗位抓取与预筛", "未通过则跳过", "decision", False),
        ("继续抓取下一条", "直到达到本轮上限", "normal", False),
    ]
    score_row = [
        ("评分任务" if score_on else "不运行 AI 评分",
         f'最多 {ai["ai_api_concurrency"]} 路并发' if score_on else "预筛通过直接入库",
         "normal" if score_on else "off", False),
        ("评分结果入库" if score_on else "岗位结果入库",
         f'达标 ≥ {ai["score_threshold"]} 分' if score_on else "保存预筛结果", "result", False),
        ("准备招呼语", "达标后 AI 生成" if greeting_on else "手写或详情页生成", "normal", False),
        ("人工确认发送", "发送成功形成会话", "manual", False),
    ]
    monitoring_row = [
        ("扫描仅沟通会话", f'最近活动 {MONITORING_CONFIG["followup_days"]} 天', "normal", False),
        ("逐会话读取", f'每条最近 {MONITORING_CONFIG["message_limit"]} 条消息', "normal", False),
        ("判断会话进展", "池内进展 / 池外岗位", "decision", False),
        ("审核后处理", "追问 / 附件 / 礼貌回复", "manual", True),
        ("回写进度", "会话与岗位状态", "result", False),
    ]

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title desc">',
        '<title id="title">批量任务工作流预览</title>',
        '<desc id="desc">上下两条独立的任务轨道：批量岗位采集和批量会话监测。配置值来自当前项目设置。</desc>',
        '<style>text{font-family:"PingFang SC","Microsoft YaHei",sans-serif}.title{font-size:26px;font-weight:700;fill:#263A55}.subtitle{font-size:14px;fill:#697F9B}.lane-title{font-size:19px;font-weight:700;fill:#294969}.lane-note{font-size:13px;fill:#657D98}.card-title{font-size:16px;font-weight:700;fill:#334A65}.card-detail{font-size:12px;fill:#6E839A}.planned{font-size:11px;fill:#9076C4}.bridge{font-size:13px;fill:#5D7391}.footer{font-size:12px;fill:#7387A0}</style>',
        f'<rect x="1" y="1" width="{WIDTH - 2}" height="{HEIGHT - 2}" rx="18" fill="#FFFFFF" stroke="#DAE5F0"/>',
        '<text class="title" x="36" y="49">批量任务工作流预览</text>',
        '<text class="subtitle" x="36" y="77">每个节点按单条岗位或会话处理；任务在设定范围内重复执行，并汇总结果。</text>',
        '<rect x="24" y="107" width="1452" height="380" rx="16" fill="#F6FAFF" stroke="#DFEAF6"/>',
        '<text class="lane-title" x="42" y="139">采集任务 · 批量岗位</text>',
        '<text class="lane-note" x="1450" y="139" text-anchor="end">本轮岗位上限：' + label(job_limit) + '</text>',
        '<text class="lane-note" x="42" y="231">JS 持续采集</text>',
    ]
    top_xs = [190, 430, 700]
    top_widths = [190, 210, 220]
    for index, ((title, detail, kind, planned), x, width) in enumerate(zip(top_row, top_xs, top_widths)):
        parts.append(card(x, 185, width, title, detail, kind=kind, planned=planned))
        if index:
            parts.append(arrow(top_xs[index - 1] + top_widths[index - 1], x, 236))
    parts.extend([
        '<line x1="810" y1="185" x2="810" y2="165" stroke="#8DAACB" stroke-width="2"/>',
        '<line x1="810" y1="165" x2="535" y2="165" stroke="#8DAACB" stroke-width="2"/>',
        '<line x1="535" y1="165" x2="535" y2="185" stroke="#8DAACB" stroke-width="2"/>',
        '<path d="M 530 177 L 535 185 L 540 177 Z" fill="#8DAACB"/>',
        '<text class="lane-note" x="673" y="158" text-anchor="middle">继续下一条</text>',
        '<rect x="970" y="185" width="466" height="102" rx="13" fill="#E9F3FF" stroke="#C8DCF2"/>',
        '<text class="card-title" x="990" y="224">' + ("JS 抓取与 AI 评分同时进行" if score_on else "当前未启用 AI 评分") + '</text>',
        '<text class="card-detail" x="990" y="253">' + ("预筛通过即提交评分，浏览器继续抓取下一条" if score_on else "预筛通过后直接进入岗位池") + '</text>',
        '<text class="lane-note" x="42" y="390">' + ("并发评分" if score_on else "岗位处理") + '</text>',
        '<line x1="535" y1="287" x2="535" y2="334" stroke="#8DAACB" stroke-width="2"/>',
        '<path d="M 530 333 L 535 342 L 540 333 Z" fill="#8DAACB"/>',
        '<text class="lane-note" x="558" y="321">预筛通过</text>',
    ])
    score_xs = [430, 690, 950, 1210]
    for index, ((title, detail, kind, planned), x) in enumerate(zip(score_row, score_xs)):
        parts.append(card(x, 342, 210, title, detail, kind=kind, planned=planned))
        if index:
            parts.append(arrow(score_xs[index - 1] + 210, x, 393))
    parts.extend([
        '<text class="lane-note" x="42" y="469">每条通过预筛的岗位独立进入任务池；AI 请求无需等待 JS 采集完成。</text>',
        '<line x1="24" y1="514" x2="1476" y2="514" stroke="#D9E4F0" stroke-dasharray="6 6"/>',
        '<text class="bridge" x="750" y="508" text-anchor="middle">发送成功可形成会话；监测任务独立启动，也会扫描岗位池外的会话</text>',
        '<rect x="24" y="534" width="1452" height="220" rx="16" fill="#F8FBFD" stroke="#DFEAF0"/>',
        '<text class="lane-title" x="42" y="568">监测任务 · 批量会话</text>',
        '<text class="lane-note" x="1450" y="568" text-anchor="end">每轮独立扫描</text>',
    ])
    monitor_xs = [42, 325, 608, 891, 1174]
    for index, ((title, detail, kind, planned), x) in enumerate(zip(monitoring_row, monitor_xs)):
        parts.append(card(x, 592, 224, title, detail, kind=kind, planned=planned))
        if index:
            parts.append(arrow(monitor_xs[index - 1] + 224, x, 643))
    parts.extend([
        '<text class="lane-note" x="42" y="732">逐会话处理直到扫描范围结束；再次扫描同一会话时更新进展。</text>',
        '<text class="footer" x="36" y="785">图中数值来自当前配置；虚线节点表示尚未接入的后续动作。此图只预览工作方式，不启动任务。</text>',
        '</svg>',
    ])
    OUT.write_text("\n".join(parts) + "\n", encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
