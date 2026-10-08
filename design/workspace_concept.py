"""Generate review-only vector mockups for the two-level navigation proposal."""

from html import escape
from pathlib import Path


OUT = Path(__file__).parent
W, H = 1720, 960
C = {
    "bg": "#f6f8fb", "white": "#ffffff", "ink": "#24334c",
    "muted": "#75859c", "line": "#e2e9f2", "blue": "#1978e6",
    "blue_pale": "#eaf3ff", "green": "#16875d", "green_pale": "#e3f5ed",
    "orange": "#dc7b22", "orange_pale": "#fff2df", "gray_pale": "#f3f6fa",
}


class Svg:
    def __init__(self, height=H):
        self.parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height}" viewBox="0 0 {W} {height}">',
                      f'<rect width="{W}" height="{height}" fill="{C["bg"]}"/>']
        self.groups = 0

    def group_start(self, x=0, y=0):
        self.parts.append(f'<g transform="translate({x},{y})">')
        self.groups += 1

    def rect(self, x, y, w, h, fill="white", stroke=None, r=0, sw=1):
        border = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}"{border}/>')

    def line(self, x1, y1, x2, y2, color=C["line"], sw=1):
        self.parts.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{sw}"/>')

    def text(self, x, y, value, size=14, color=C["ink"], weight=400, anchor="start", spacing=None):
        ls = f' letter-spacing="{spacing}"' if spacing is not None else ""
        self.parts.append(f'<text x="{x}" y="{y}" font-family="Inter, PingFang SC, Hiragino Sans GB, Microsoft YaHei, sans-serif" font-size="{size}" font-weight="{weight}" fill="{color}" text-anchor="{anchor}"{ls}>{escape(str(value))}</text>')

    def pill(self, x, y, w, label, fill, color, border=None, h=30):
        self.rect(x, y, w, h, fill, border, h / 2)
        self.text(x+w/2, y+h/2+5, label, 13, color, 650, "middle")

    def button(self, x, y, w, label, primary=False):
        self.rect(x, y, w, 40, C["blue"] if primary else C["white"], None if primary else C["line"], 9)
        self.text(x+w/2, y+26, label, 14, C["white"] if primary else C["ink"], 650, "middle")

    def finish(self, name):
        self.parts.extend("</g>" for _ in range(self.groups))
        self.parts.append("</svg>")
        OUT.joinpath(name).write_text("\n".join(self.parts), encoding="utf-8")


def chrome(s, current):
    s.rect(0, 0, 280, 1500, C["white"])
    s.line(280, 0, 280, 1500)
    if current == "配置":
        s.rect(280, 0, W-280, 72, C["white"])
        s.line(280, 72, W, 72)
    s.rect(23, 18, 36, 36, C["blue"], r=10)
    s.text(41, 43, "J", 21, C["white"], 700, "middle")
    s.text(72, 42, "Job Hunting Helper", 15, C["ink"], 730)
    s.line(0, 72, 280, 72)
    in_workbench = current in {"采集任务", "监测任务"}
    s.rect(16, 106, 248, 44, "#f3f7fc" if in_workbench else C["white"], r=10)
    s.text(36, 135, "工作台", 14, C["ink"], 700)
    s.line(43, 159, 43, 245, "#dce7f3", 2)
    for y, label in [(160, "采集任务"), (208, "监测任务")]:
        active = label == current
        if active:
            s.rect(52, y, 212, 40, C["blue_pale"], r=9)
        s.text(72, y+26, label, 13, C["blue"] if active else "#586c84", 700 if active else 550)
    active = current == "配置"
    if active:
        s.rect(16, 272, 248, 44, C["blue_pale"], r=10)
    s.text(36, 301, "配置", 14, C["blue"] if active else C["ink"], 700 if active else 550)
    if current == "配置":
        s.text(320, 44, current, 19, C["ink"], 730)


def heading(s, eyebrow, title, subtitle, action=None):
    s.text(80, 132, eyebrow, 11, C["blue"], 800, spacing=2)
    s.text(80, 183, title, 31, C["ink"], 760)
    s.text(80, 214, subtitle, 14, C["muted"])
    if action:
        s.button(1209, 162, 151, action, True)


def field(s, x, y, w, label, value, hint=None):
    s.text(x, y, label, 13, C["ink"], 650)
    s.rect(x, y+12, w, 42, C["white"], C["line"], 8)
    s.text(x+15, y+39, value, 14, C["ink"], 450)
    if hint:
        s.text(x, y+71, hint, 11, C["muted"])


def config_screen():
    s = Svg(); chrome(s, "配置")
    s.group_start(280)
    heading(s, "SETTINGS", "任务配置", "按任务设置运行方式；保存后，下一次启动任务时生效。", "保存配置")

    for x, title, subtitle, flow in [
        (80, "采集任务", "从 BOSS 获取岗位，预筛后评分并准备招呼语。", "抓取岗位  →  预筛  →  AI 评分  →  招呼语"),
        (734, "监测任务", "扫描最近会话，判断进展并准备待审核操作。", "扫描会话  →  判断进展  →  审核  →  发送")]:
        s.rect(x, 246, 626, 656, C["white"], C["line"], 14)
        s.rect(x+24, 270, 42, 42, C["blue_pale"], r=10)
        s.text(x+45, 297, "◉", 20, C["blue"], 700, "middle")
        s.text(x+79, 286, title, 20, C["ink"], 730)
        s.text(x+79, 308, subtitle, 12, C["muted"])
        s.rect(x+24, 331, 578, 44, C["gray_pale"], r=8)
        s.text(x+39, 359, flow, 13, C["muted"], 600)
        s.line(x+24, 400, x+602, 400)

    s.text(104, 433, "抓取范围", 14, C["ink"], 700)
    field(s, 104, 459, 274, "采集平台", "BOSS 直聘")
    field(s, 394, 459, 288, "抓取模式", "推荐流  ▾")
    field(s, 104, 549, 274, "城市", "不限")
    field(s, 394, 549, 288, "每轮最多采集", "1 页")
    s.line(104, 641, 682, 641)
    s.text(104, 673, "筛选与 AI", 14, C["ink"], 700)
    s.text(104, 708, "学历、经验、薪资、公司规模及排除词", 13, C["muted"])
    s.text(682, 708, "编辑筛选  ›", 13, C["blue"], 650, "end")
    s.line(104, 724, 682, 724)
    s.text(104, 759, "启用 AI 评分", 13, C["ink"], 600)
    s.rect(624, 739, 54, 30, C["blue"], r=15); s.rect(650, 743, 22, 22, C["white"], r=11)
    field(s, 104, 797, 274, "评分门槛", "71 分")
    field(s, 394, 797, 288, "自动生成招呼语", "关闭  ▾")

    s.text(758, 433, "扫描范围", 14, C["ink"], 700)
    field(s, 758, 459, 274, "最近活动天数", "4 天")
    field(s, 1048, 459, 288, "每条会话读取消息", "10 条")
    field(s, 758, 549, 274, "计划扫描时间", "每天 10:00")
    field(s, 1048, 549, 288, "最多追问", "2 次")
    s.line(758, 641, 1336, 641)
    s.text(758, 673, "判断与处理", 14, C["ink"], 700)
    s.rect(758, 693, 578, 57, C["gray_pale"], r=8)
    s.text(774, 716, "池内会话", 13, C["ink"], 650)
    s.text(774, 738, "拒绝 / 已投递结束；未读或已读未回准备追问。", 12, C["muted"])
    s.rect(758, 763, 578, 78, C["gray_pale"], r=8)
    s.text(774, 786, "池外会话", 13, C["ink"], 650)
    s.text(774, 808, "预筛通过：审核后发附件；否则审核后礼貌拒绝。", 12, C["muted"])
    s.text(774, 829, "所有发送操作均需人工审核。", 11, C["muted"])
    s.finish("config.svg")


def workbench_base(s, task, title, subtitle, button):
    chrome(s, task)
    s.group_start(280, -62)
    heading(s, "WORKBENCH", title, subtitle, button)


def table_row(s, y, cols, status=None):
    s.line(104, y+59, 1336, y+59)
    for x, value, color, weight in cols:
        s.text(x, y+31, value, 13, color, weight)


def collect_screen():
    s = Svg(height=1190)
    workbench_base(s, "采集任务", "采集任务", "启动一轮岗位采集，查看任务进度与已入库岗位。", "开始采集")
    s.group_start(0, -74)
    s.rect(80, 328, 1280, 114, C["white"], C["line"], 12)
    s.text(104, 358, "本轮任务", 15, C["ink"], 700)
    s.pill(1238, 340, 96, "尚未开始", C["gray_pale"], C["muted"])
    s.text(104, 389, "按当前配置抓取推荐流，完成预筛、AI 评分与入库。", 13, C["muted"])
    s.text(104, 418, "采集 0", 12, C["muted"]); s.text(219, 418, "入库 0", 12, C["muted"]); s.text(329, 418, "重复 0", 12, C["muted"])

    s.text(80, 498, "已入库岗位", 23, C["ink"], 730)
    s.text(80, 524, "岗位状态、评分与详情集中在这里查看。", 13, C["muted"])
    s.rect(80, 550, 1280, 606, C["white"], C["line"], 12)
    s.text(104, 581, "岗位列表", 15, C["ink"], 700)
    s.text(1336, 581, "1–9 / 9 · 标色门槛 71 分", 12, C["muted"], 450, "end")
    s.line(80, 608, 1360, 608)
    s.rect(104, 621, 917, 36, C["white"], C["line"], 7)
    s.text(118, 644, "关键词；可用 &&、||、() 组合", 12, C["muted"])
    s.rect(1031, 621, 145, 36, C["white"], C["line"], 7)
    s.text(1045, 644, "全部状态  ▾", 12, C["ink"])
    s.rect(1186, 621, 150, 36, "#f4f9ff", "#b9d4f2", 7)
    s.text(1261, 644, "批量操作", 12, "#176cc3", 700, "middle")
    s.line(80, 670, 1360, 670)
    s.rect(80, 671, 1280, 44, "#fafbfc")
    for x, label in [(104,"岗位 / 公司"),(448,"薪资与要求"),(671,"岗位评分"),(813,"岗位状态"),(981,"变更时间"),(1217,"操作")]:
        s.text(x, 698, label, 11, "#8b96a6", 700, spacing=.4)

    rows = [
        ("全栈&前端工程师（J11588）", "钱信健康 · 北京", "boss · 4d608b453a6ce3220nN_3tW1FVVV", "25–35K", "5–10年 · 本科", "87 分", "已招呼", True, "2026/09/29 00:03", "2026/09/30 17:47"),
        ("高级前端开发工程师", "星云科技 · 北京", "boss · 7f83e9941c528a1d", "25–50K", "3–5年 · 本科", "68 分", "已过滤", False, "2026/09/29 00:03", "2026/09/30 14:32"),
        ("前端技术专家", "海际互联 · 上海", "boss · 6d72b19d22e96be1", "30–50K", "5–10年 · 本科", "90 分", "已评分", True, "2026/09/29 00:05", "2026/09/30 16:10"),
    ]
    for i, (title, company, job_id, salary, requirement, score, status, qualified, created, updated) in enumerate(rows):
        y = 715 + i * 126
        if i:
            s.line(80, y, 1360, y, "#f0f2f5")
        s.text(104, y+34, title, 14, C["ink"], 700)
        s.text(104, y+60, company, 12, "#6d7b8e")
        s.text(104, y+86, job_id, 11, "#a1aab7")
        s.text(448, y+42, salary, 14, "#65748b", 700)
        s.text(448, y+67, requirement, 12, "#6d7b8e")
        s.rect(671, y+39, 65, 27, "#e8f6ef" if qualified else "#f1f3f6", r=5)
        s.text(703, y+57, score, 12, "#168253" if qualified else "#79879a", 700, "middle")
        s.rect(813, y+39, 65, 27, "#e8f6ef" if qualified else "#f1f3f6", r=5)
        s.text(845, y+57, status, 12, "#168253" if qualified else "#79879a", 700, "middle")
        s.text(981, y+26, "入库时间", 11, "#98a4b3")
        s.text(981, y+46, created, 12, C["muted"])
        s.text(981, y+72, "更新时间", 11, "#98a4b3")
        s.text(981, y+92, updated, 12, C["muted"])
        s.text(1217, y+59, "详情", 12, "#2076d2", 650)
        s.text(1271, y+59, "删除", 12, "#d45151", 650)

    s.line(80, 1093, 1360, 1093)
    s.text(104, 1129, "显示 1–9 条", 12, "#8b97a6")
    s.rect(1114, 1108, 66, 31, C["white"], C["line"], 7)
    s.text(1147, 1129, "上一页", 12, C["muted"], 500, "middle")
    s.text(1236, 1129, "第 1 页", 12, "#8b97a6", 500, "middle")
    s.rect(1279, 1108, 66, 31, C["white"], C["line"], 7)
    s.text(1312, 1129, "下一页", 12, C["muted"], 500, "middle")
    s.finish("workbench_collection.svg")


def monitor_screen():
    s = Svg()
    workbench_base(s, "监测任务", "监测任务", "扫描最近会话，审核追问、附件投递和礼貌拒绝。", "开始监测")
    s.group_start(0, -74)
    s.rect(80, 328, 1280, 114, C["white"], C["line"], 12)
    s.text(104, 358, "本轮任务", 15, C["ink"], 700)
    s.pill(1238, 340, 96, "尚未开始", C["gray_pale"], C["muted"])
    s.text(104, 389, "扫描最近 200 条会话，每条读取最近 10 条消息；按会话去重入库。", 13, C["muted"])
    s.text(104, 418, "已扫描 0", 12, C["muted"]); s.text(219, 418, "待审核 0", 12, C["muted"]); s.text(329, 418, "人工处理 0", 12, C["muted"])

    s.text(80, 498, "会话进展", 23, C["ink"], 730)
    s.text(80, 524, "先查看判断结果，再审核需要发送的内容。", 13, C["muted"])
    s.rect(80, 550, 1280, 362, C["white"], C["line"], 12)
    s.text(104, 581, "会话列表", 15, C["ink"], 700)
    s.text(1336, 581, "池内与池外会话统一查看", 12, C["muted"], 450, "end")
    s.line(80, 599, 1360, 599)
    s.rect(104, 616, 792, 42, C["white"], C["line"], 7)
    s.text(120, 642, "搜索岗位、公司或 HR", 13, C["muted"])
    s.rect(911, 616, 194, 42, C["white"], C["line"], 7)
    s.text(933, 642, "全部判断结果  ▾", 13, C["ink"])
    s.button(1120, 617, 105, "刷新")
    s.button(1237, 617, 99, "配置")
    s.rect(80, 677, 1280, 42, C["gray_pale"])
    for x, label in [(104,"岗位 / 公司 / HR"),(500,"岗位池"),(632,"判断结果"),(925,"操作"),(1190,"会话链接")]:
        s.text(x, 703, label, 12, C["muted"], 650)
    table_row(s, 719, [(104,"前端开发 · 云帆科技 · 王女士",C["ink"],650),(500,"池内",C["muted"],500),(632,"已读未回，待追问",C["orange"],600),(925,"审核追问",C["blue"],650),(1190,"查看会话 ↗",C["blue"],650)])
    table_row(s, 779, [(104,"高级前端 · 星源科技 · 李先生",C["ink"],650),(500,"池外",C["muted"],500),(632,"预筛通过，待投递",C["green"],600),(925,"审核附件",C["blue"],650),(1190,"查看会话 ↗",C["blue"],650)])
    table_row(s, 839, [(104,"产品经理 · 山海互联 · 陈女士",C["ink"],650),(500,"池外",C["muted"],500),(632,"预筛未通过",C["muted"],600),(925,"审核拒绝",C["blue"],650),(1190,"查看会话 ↗",C["blue"],650)])
    s.finish("workbench_monitoring.svg")


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    config_screen()
    collect_screen()
    monitor_screen()
