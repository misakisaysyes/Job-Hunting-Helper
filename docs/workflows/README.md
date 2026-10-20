# 工作流文档

[采集交互流程图](collection-guide.html) 使用原生 SVG、CSS 和 JavaScript，页面可独立打开，无需安装绘图库或启动服务。

- [流程数据](collection-workflow.json)：五个节点的名称、说明面板、配置与实现代码链接。
- [静态预览](collection-workflow.svg)：与交互页面由同一份数据生成，供 Markdown 展示。
- [生成脚本](build_collection_workflow.py)：只使用 Python 标准库。

修改流程数据后，在项目根目录运行：

    python3 docs/workflows/build_collection_workflow.py

生成脚本只读取 collection-workflow.json，输出 collection-guide.html 和 collection-workflow.svg，不依赖历史图表或外部生成器。浏览器中的节点点击、方向键选择、说明面板和源码链接均由页面自带代码实现。
