"""Render the collection batch preview as aligned job rows and one manual step."""

from __future__ import annotations

from html import escape
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import DEFAULT_CONFIG  # noqa: E402

OUT = Path(__file__).with_name("workflow_collection_timeline.svg")


def e(value: object) -> str:
    return escape(str(value), quote=True)


def main() -> None:
    collection = DEFAULT_CONFIG["collection"]
    ai = DEFAULT_CONFIG["ai"]
    limit = collection["max_jobs"]
    source = (f'推荐流 · {len(collection["encrypt_expect_id"])} 组期望'
              if collection["mode"] == "recommend"
              else f'搜索流 · {len(collection["keywords"])} 个关键词')
    score_on = bool(ai["use_ai_score"])
    greeting_on = score_on and bool(ai["use_ai_greeting"])
    phases = [
        ("数据抓取", 150, "#E6F2FF", "#4183CF", True),
        ("预筛", 125, "#FFF0D8", "#C38731", True),
        ("AI 评分", 145, "#ECEBFF", "#6B63BD", score_on),
        ("生成招呼", 155, "#E8F7EE", "#4F9B6A", greeting_on),
    ]
    if limit == 1:
        rows = [("岗位 1", 205)]
    elif limit == 2:
        rows = [("岗位 1", 205), ("岗位 2", 285)]
    else:
        last = f"岗位 {limit}" if limit else "后续岗位"
        rows = [("岗位 1", 205), ("岗位 2", 285), (last, 425)]
    summary = (f'{source}  ·  每组最多 {collection["max_pages"]} 页  ·  '
               f'本轮岗位上限：{limit if limit else "不限"}  ·  '
               f'AI 评分：{"开启" if score_on else "关闭"}'
               + (f'（最多 {ai["ai_api_concurrency"]} 路并发，门槛 {ai["score_threshold"]} 分）' if score_on else "")
               + f'  ·  自动生成招呼：{"开启" if greeting_on else "关闭"}')
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1500" height="620" viewBox="0 0 1500 620" role="img" aria-labelledby="title desc">',
        '<title id="title">采集任务：批量岗位工作流预览</title>',
        '<desc id="desc">纵轴为批量处理容量，横轴为时间；多条岗位按相同阶段对齐，采集结束后由人工审核、修改并发送招呼。</desc>',
        '<style>text{font-family:"PingFang SC","Microsoft YaHei",sans-serif}.heading{font-size:26px;font-weight:700;fill:#263A55}.sub{font-size:13px;fill:#647A94}.axis{font-size:14px;fill:#4D6481}.row{font-size:13px;font-weight:700;fill:#3B526E}.phase{font-size:12px;font-weight:700}.manual{font-size:18px;font-weight:700;fill:#725AAB}.hint{font-size:12px;fill:#72869C}</style>',
        '<rect x="1" y="1" width="1498" height="618" rx="18" fill="white" stroke="#DAE5F0"/>',
        '<text class="heading" x="34" y="47">采集任务 · 批量岗位时间线</text>',
        '<text class="sub" x="34" y="76">每一行代表一条岗位；相同阶段对齐，展示一轮采集如何批量处理岗位。</text>',
        '<rect x="25" y="106" width="1450" height="480" rx="16" fill="#F8FBFF" stroke="#E0EAF4"/>',
        f'<text class="sub" x="38" y="139">{e(summary)}</text>',
        '<line x1="105" y1="530" x2="105" y2="170" stroke="#6B829D" stroke-width="2"/>',
        '<path d="M 100 178 L 105 166 L 110 178 Z" fill="#6B829D"/>',
        '<line x1="105" y1="530" x2="1435" y2="530" stroke="#6B829D" stroke-width="2"/>',
        '<path d="M 1424 525 L 1437 530 L 1424 535 Z" fill="#6B829D"/>',
        '<text class="axis" x="30" y="165">批量处理容量</text>',
        '<text class="axis" x="76" y="192" text-anchor="end">' + e(limit if limit else "不限") + '</text>',
        '<text class="axis" x="92" y="535" text-anchor="end">0</text>',
        '<text class="axis" x="1448" y="535">时间</text>',
        '<line x1="820" y1="165" x2="820" y2="530" stroke="#8CA1B8" stroke-width="2" stroke-dasharray="7 6"/>',
        '<text class="axis" x="820" y="158" text-anchor="middle">采集结束</text>',
    ]
    if len(rows) == 3 and (limit == 0 or limit > 3):
        parts.append('<text class="axis" x="470" y="365" text-anchor="middle">···</text>')
    for name, center in rows:
        y = center - 22
        parts.append(f'<text class="row" x="130" y="{center + 5}">{e(name)}</text>')
        x = 180
        for title, width, fill, color, active in phases:
            if not active:
                fill, color = "#F1F4F7", "#9AA9B9"
            dash = '' if active else ' stroke-dasharray="5 3"'
            parts.append(f'<rect x="{x}" y="{y}" width="{width}" height="44" fill="{fill}" stroke="{color}" stroke-width="1"{dash}/>')
            parts.append(f'<text class="phase" x="{x + width / 2}" y="{center + 5}" text-anchor="middle" fill="{color}">{e(title)}</text>')
            x += width
    parts += [
        '<text class="manual" x="1120" y="300" text-anchor="middle">人工审核、修改、发送招呼</text>',
        '<line x1="910" y1="330" x2="1350" y2="330" stroke="#725AAB" stroke-width="3"/>',
        '<path d="M 1338 322 L 1354 330 L 1338 338 Z" fill="#725AAB"/>',
        '<text class="hint" x="190" y="560">横条按流程对齐；实际逐条抓取，AI 评分可与后续岗位抓取并发。</text>' if score_on else
        '<text class="hint" x="190" y="560">横条按流程对齐；实际逐条抓取并预筛。灰色虚线阶段表示未启用。</text>',
        '<text class="hint" x="34" y="604">图中只示意进入人工处理的岗位；预筛未通过的岗位退出，低分岗位默认过滤（可手动放行）。灰色虚线阶段表示未启用。</text>' if score_on else
        '<text class="hint" x="34" y="604">图中只示意进入人工处理的岗位；预筛未通过的岗位在预筛阶段退出。行数为示意，实际数量由配置决定。</text>',
        '</svg>',
    ]
    OUT.write_text("\n".join(parts) + "\n", encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
