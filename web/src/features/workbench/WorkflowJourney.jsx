const STAGES = [
  {
    number: "01",
    label: "找到岗位",
    detail: "采集任务读取职位信息，按你的条件预筛；开启 AI 评分后，还会给出匹配分数和理由。",
  },
  {
    number: "02",
    label: "发出招呼",
    detail: "为合适的岗位准备招呼语。你可以自己写，也可以让 AI 起草，确认内容后再发送。",
  },
  {
    number: "03",
    label: "跟进会话",
    detail: "发出招呼后会产生与招聘者的会话。监测任务读取最近的对话，帮你看清哪些需要处理。",
  },
];

export default function WorkflowJourney() {
  return <section className="workbench-journey" aria-labelledby="workbench-journey-title">
    <div className="workbench-journey-intro">
      <span className="workbench-journey-kicker">怎么使用</span>
      <h2 id="workbench-journey-title">从一个岗位开始，直到看清会话进展</h2>
      <p>面对一批岗位，你可以先让采集任务抓取、预筛，需要时再用 AI 评分。挑出合适的岗位，确认招呼语并发送，就会和招聘者形成会话；之后用监测任务查看最近的对话进展。</p>
    </div>
    <ol className="workbench-journey-stages">
      {STAGES.map((stage) => <li key={stage.number}>
        <span className="workbench-journey-number">{stage.number}</span>
        <div>
          <h3>{stage.label}</h3>
          <p>{stage.detail}</p>
        </div>
      </li>)}
    </ol>
    <p className="workbench-journey-footnote">每次启动采集或监测，就是运行一轮任务；它会批量处理岗位或会话，结果在对应页面查看。</p>
  </section>;
}
