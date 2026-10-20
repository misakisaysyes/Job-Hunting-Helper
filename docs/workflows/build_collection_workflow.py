"""Generate the standalone collection guide and SVG using Python's standard library."""
import argparse
import html
import json
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", type=Path, default=HERE / "collection-workflow.json")
parser.add_argument("--output-dir", type=Path, default=HERE)
args = parser.parse_args()
workflow = json.loads(args.source.read_text(encoding="utf-8"))
panels = workflow["steps"]
if [step["id"] for step in panels] != ["fetch", "filter", "score", "greeting", "send"]:
    raise ValueError("采集工作流必须按顺序包含获取岗位、预筛、AI评分、生成招呼、发送招呼。")
args.output_dir.mkdir(parents=True, exist_ok=True)

SVG_CSS = """
svg { font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }
.node-surface { fill:#f5f9ff; stroke:#c4d9f1; stroke-width:1.25; }
.node-title { fill:#26364d; font-size:16px; font-weight:650; }
.node-subtitle { fill:#71829b; font-size:11px; }
.node-number { fill:#829ab6; font-size:10px; font-weight:600; }
.connector { fill:none; stroke:#9fb4cd; stroke-width:1.5; }
.arrow-tip { fill:#9fb4cd; }
"""

SVG_INTERACTION_CSS = """
.workflow-node { cursor:pointer; outline:none; }
.workflow-node:hover .node-surface,.workflow-node:focus-visible .node-surface { stroke:#1677ed; fill:#edf5ff; stroke-width:1.8; }
.workflow-node[aria-pressed="true"] .node-surface { stroke:#1677ed; fill:#eaf3ff; stroke-width:1.8; }
.workflow-node[aria-pressed="true"] .node-title { fill:#1269cb; }
"""


def draw_workflow(steps, static=False):
    node_width, node_height, gap, margin, top = 152, 84, 40, 22, 20
    subtitle_lines = [step["subtitle"].splitlines() for step in steps]
    node_height += 16 * (max(map(len, subtitle_lines)) - 1)
    width = margin * 2 + len(steps) * node_width + (len(steps) - 1) * gap
    height = top * 2 + node_height
    center_y = top + node_height / 2
    arrows, nodes = [], []
    for index, (step, lines) in enumerate(zip(steps, subtitle_lines)):
        x = margin + index * (node_width + gap)
        center_x = x + node_width / 2
        if index < len(steps) - 1:
            start, end = x + node_width + 2, x + node_width + gap - 4
            arrows.append(f'<path class="connector" d="M {start} {center_y:g} H {end}" marker-end="url(#collection-arrow)"/>')
        controls = "" if static else (
            f' tabindex="0" role="button" aria-label="{esc(step["title"])}"'
            f' aria-controls="panel-{esc(step["id"])}"'
            f' aria-pressed="{"true" if index == 0 else "false"}"'
        )
        subtitle = "".join(
            f'<tspan x="{center_x:g}" dy="{0 if line_index == 0 else 16}">{esc(line)}</tspan>'
            for line_index, line in enumerate(lines)
        )
        nodes.append(
            f'<g class="{"workflow-step" if static else "workflow-node"}" id="node-{esc(step["id"])}" data-node-id="{esc(step["id"])}"'
            f'{controls}>'
            f'<title>{esc(step["title"])} · {esc(step["subtitle"])}</title>'
            f'<rect class="node-surface" x="{x}" y="{top}" width="{node_width}" height="{node_height}" rx="11"/>'
            f'<text class="node-number" x="{x+13}" y="{top+19}">{index+1:02}</text>'
            f'<text class="node-title" x="{center_x:g}" y="{top+45}" text-anchor="middle">{esc(step["title"])}</text>'
            f'<text class="node-subtitle" x="{center_x:g}" y="{top+66}" text-anchor="middle">{subtitle}</text>'
            '</g>'
        )
    description = " → ".join(step["title"] for step in steps)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}"'
        f' role="{"img" if static else "group"}" aria-labelledby="collection-title collection-description">'
        f'<title id="collection-title">{esc(workflow["title"])}</title>'
        f'<desc id="collection-description">{esc(description)}</desc><style>{SVG_CSS}{"" if static else SVG_INTERACTION_CSS}</style>'
        '<defs><marker id="collection-arrow" markerWidth="8" markerHeight="7" refX="7" refY="3.5" orient="auto">'
        '<path class="arrow-tip" d="M 0 0 L 8 3.5 L 0 7 Z"/></marker></defs>'
        + "".join(arrows) + "".join(nodes) + '</svg>'
    )

CSS = """
:root { color-scheme:light; --ink:#26364d; --muted:#71829b; --blue:#1677ed; --line:#dce6f2; --paper:#fff; }
* { box-sizing:border-box; } [hidden] { display:none!important; }
body { margin:0; background:#f6f8fb; color:var(--ink); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; line-height:1.7; }
main { max-width:1240px; margin:0 auto; padding:48px 40px 30px; }
header { display:flex; justify-content:space-between; align-items:center; gap:20px; margin-bottom:25px; }
h1 { margin:0 0 8px; font-size:30px; line-height:1.35; letter-spacing:-.5px; }
header p { margin:0; color:var(--muted); font-size:14px; }
.project { color:var(--muted); text-decoration:none; font-size:12px; white-space:nowrap; }
.workflow { padding:30px; border:1px solid var(--line); border-radius:16px; background:var(--paper); }
.diagram-scroll { overflow-x:auto; padding:7px 0 27px; scrollbar-width:thin; scrollbar-color:#c5d1df transparent; }
.diagram-scroll svg { display:block; width:100%; min-width:680px; height:auto; overflow:visible; font-family:inherit; }
.detail-panels { position:relative; --anchor:9%; border:1px solid #c8dcf5; border-radius:12px; background:#fff; }
.detail-panels::before { content:""; position:absolute; top:-7px; left:calc(var(--anchor) - 7px); width:12px; height:12px; transform:rotate(45deg); border-top:1px solid #c8dcf5; border-left:1px solid #c8dcf5; background:#fff; }
.step-panel { padding:25px 27px 19px; }
.panel-body { display:grid; grid-template-columns:300px 1fr; gap:38px; min-height:143px; }
.step-heading { display:flex; align-items:center; gap:10px; margin:0 0 10px; font-size:20px; line-height:1.5; }
.step-number { display:inline-flex; align-items:center; justify-content:center; width:29px; height:29px; border-radius:8px; background:#eaf3ff; color:#1677ed; font-size:12px; font-weight:700; }
.summary { margin:0; font-size:14px; color:#506781; line-height:1.9; }
dl { margin:3px 0 0; display:grid; gap:13px; align-content:start; font-size:13px; }
.fact { display:grid; grid-template-columns:72px 1fr; gap:10px; } dt { font-weight:650; } dd { margin:0; color:#61748f; }
.config-links { display:flex; gap:9px; align-items:center; flex-wrap:wrap; border-top:1px solid #e8eef6; padding-top:17px; margin-top:16px; }
.links-label { margin-right:4px; color:var(--muted); font-size:12px; }
.config-link { display:inline-flex; align-items:center; gap:10px; text-decoration:none; border:1px solid #cadcf1; border-radius:7px; padding:6px 11px; background:#f7faff; color:#176dcc; font-size:12px; font-weight:600; }
.config-link:hover { border-color:#1677ed; background:#eef6ff; } a:focus-visible,input:focus-visible { outline:2px solid #1677ed; outline-offset:3px; }
.footer { margin-top:17px; display:flex; align-items:flex-start; justify-content:space-between; gap:20px; font-size:11px; color:#8997a9; }
.footer a { color:#7a8ca4; text-decoration:none; } .footer a:hover { color:#1677ed; }
@media (max-width:900px) { main{padding:30px 20px;} .workflow{padding:20px;} .panel-body{grid-template-columns:1fr;gap:18px;min-height:0;} .step-panel{padding:21px;} .footer{flex-wrap:wrap;} header{align-items:flex-start;} .project{display:none;} }
@media print { main{padding:0;} .project,.footer,.config-links{display:none;} .workflow{padding:16px;border:0;} .diagram-scroll svg{min-width:0;} .step-panel[hidden]{display:block!important;} .detail-panels::before{display:none;} .step-panel + .step-panel{border-top:1px solid #ddd;} }
"""


def esc(value):
    return html.escape(str(value), quote=True)


compact_svg = draw_workflow(panels)
repo = workflow["repository"]
repo_url = repo["url"].removesuffix(".git")


def code_url(ref):
    return f'{repo_url}/blob/{repo["revision"]}/{quote(ref["path"])}#L{ref["line"]}-L{ref["end_line"]}'


panel_html = []
for index, panel in enumerate(panels, 1):
    facts = "".join(
        f'<div class="fact"><dt>{esc(label)}</dt><dd>{esc(text)}</dd></div>'
        for label, text in panel["items"]
    )
    links = "".join(
        f'<a class="config-link" href="{esc(code_url(ref))}" target="_blank" rel="noopener noreferrer" title="{esc(ref["path"])}:{ref["line"]}–{ref["end_line"]}">{esc(ref["label"])} <span aria-hidden="true">↗</span></a>'
        for ref in panel["links"]
    )
    panel_html.append(
        f'<section class="step-panel" id="panel-{panel["id"]}" role="region" aria-labelledby="title-{panel["id"]}"{"" if index == 1 else " hidden"}>'
        f'<div class="panel-body"><div><h2 class="step-heading" id="title-{panel["id"]}"><span class="step-number">{index:02}</span>{esc(panel["title"])}</h2>'
        f'<p class="summary">{esc(panel["summary"])}</p></div><dl>{facts}</dl></div>'
        f'<nav class="config-links" aria-label="{esc(panel["title"])}相关配置代码"><span class="links-label">{esc(panel.get("codeLabel", "相关配置代码"))}</span>{links}</nav>'
        '</section>'
    )

SCRIPT = """
const nodes = Array.from(document.querySelectorAll('[data-node-id]'));
const panels = Array.from(document.querySelectorAll('.step-panel'));
const details = document.querySelector('.detail-panels');
let selected = 'fetch';
function positionAnchor() {
  const node = nodes.find(item => item.dataset.nodeId === selected);
  const rect = node.getBoundingClientRect();
  const panelRect = details.getBoundingClientRect();
  const center = rect.left + rect.width / 2 - panelRect.left;
  details.style.setProperty('--anchor', Math.max(16, Math.min(panelRect.width - 16, center)) + 'px');
}
function selectStep(id, updateUrl = true) {
  if (!nodes.some(item => item.dataset.nodeId === id)) id = 'fetch';
  selected = id;
  nodes.forEach(item => item.setAttribute('aria-pressed', String(item.dataset.nodeId === id)));
  panels.forEach(item => { item.hidden = item.id !== 'panel-' + id; });
  positionAnchor();
  if (updateUrl) history.replaceState(null, '', '#' + id);
}
nodes.forEach((node, index) => {
  node.addEventListener('click', () => selectStep(node.dataset.nodeId));
  node.addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      selectStep(node.dataset.nodeId);
    } else if (['ArrowRight', 'ArrowLeft', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? nodes.length - 1
        : (index + (event.key === 'ArrowRight' ? 1 : -1) + nodes.length) % nodes.length;
      nodes[next].focus();
      selectStep(nodes[next].dataset.nodeId);
    }
  });
});
window.addEventListener('hashchange', () => selectStep(location.hash.slice(1), false));
window.addEventListener('resize', positionAnchor);
document.querySelector('.diagram-scroll').addEventListener('scroll', positionAnchor);
selectStep(location.hash.slice(1), false);
document.fonts.ready.then(positionAnchor);
"""

reader = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="获取岗位、预筛、AI评分、生成招呼、发送招呼：点击节点查看说明与配置代码。">
<title>采集工作流 · Job Hunting Helper</title><style>{CSS}</style></head>
<body><main>
<header><div><h1>采集工作流</h1><p>点击节点查看说明，面板中的链接可定位到相关配置代码。</p></div><a class="project" href="{esc(repo_url)}" target="_blank" rel="noopener noreferrer">Job Hunting Helper ↗</a></header>
<div class="workflow"><div class="diagram-scroll">{compact_svg}</div>
<div class="detail-panels">{"".join(panel_html)}</div></div>
<div class="footer"><div><a href="collection-workflow.json">流程源文件</a></div>
<span>配置链接指向 GitHub 源码的对应行</span></div>
<noscript><style>.step-panel[hidden]{{display:block!important}}.detail-panels::before{{display:none}}</style></noscript>
</main><script>{SCRIPT}</script></body></html>"""
(args.output_dir / "collection-guide.html").write_text(reader, encoding="utf-8")
(args.output_dir / "collection-workflow.svg").write_text(draw_workflow(panels, static=True), encoding="utf-8")
print(f"Built {args.output_dir / 'collection-guide.html'}")
