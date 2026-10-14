const SOURCE_ORDER = ["greeted", "hr_initiated", "earlier_or_unknown"];
const DELIVERY_ORDER = [true, false];
const DIRECT_DELIVERY_ORDER = [false, true];

export function buildConversionLayout(flows, { top = 106, height = 270, gap = 34 } = {}) {
  const total = flows.reduce((sum, flow) => sum + flow.count, 0);
  if (!total) return { nodes: [], links: [], total: 0 };
  const sourceCounts = Object.fromEntries(SOURCE_ORDER.map((key) => [key, 0]));
  let followupCount = 0;
  const deliveryCounts = { true: 0, false: 0 };
  for (const flow of flows) {
    sourceCounts[flow.source] += flow.count;
    if (flow.followed_up) followupCount += flow.count;
    deliveryCounts[String(flow.delivered)] += flow.count;
  }
  const groups = [
    SOURCE_ORDER.filter((key) => sourceCounts[key]).map((key) => ({ key: `source:${key}`, count: sourceCounts[key], column: 0 })),
    followupCount ? [{ key: "followup:true", count: followupCount, column: 1 }] : [],
    DELIVERY_ORDER.filter((key) => deliveryCounts[String(key)]).map((key) => ({ key: `delivery:${key}`, count: deliveryCounts[String(key)], column: 2 })),
  ];
  const scale = (height - (Math.max(...groups.map((group) => group.length)) - 1) * gap) / total;
  const nodes = groups.flatMap((group, column) => {
    const contentHeight = group.reduce((sum, item) => sum + item.count * scale, 0)
      + Math.max(0, group.length - 1) * gap;
    let y = top + (height - contentHeight) / 2;
    return group.map((item) => {
      const node = { ...item, column, x: [184, 534, 884][column], y, height: item.count * scale };
      y += node.height + gap;
      return node;
    });
  });
  const byKey = Object.fromEntries(nodes.map((node) => [node.key, node]));
  const followupNode = byKey["followup:true"];
  if (followupNode) {
    const followed = flows.filter((flow) => flow.followed_up);
    const weightedCenter = (keys, nodeKey, valueKey) => {
      const weighted = keys.reduce((sum, key) => {
        const node = byKey[nodeKey(key)];
        if (!node) return sum;
        const via = followed.filter((flow) => valueKey(flow) === key)
          .reduce((count, flow) => count + flow.count, 0);
        return sum + (node.y + via * scale / 2) * via;
      }, 0);
      return weighted / followupNode.count;
    };
    const incoming = weightedCenter(SOURCE_ORDER, (key) => `source:${key}`,
      (flow) => flow.source);
    const outgoing = weightedCenter(DELIVERY_ORDER, (key) => `delivery:${key}`,
      (flow) => flow.delivered);
    const preferred = (incoming + outgoing) / 2 - followupNode.height / 2;
    followupNode.y = Math.max(top, Math.min(top + height - followupNode.height, preferred));
  }
  const sourceOffsets = {}, followupIncoming = {}, followupOutgoing = {}, deliveryOffsets = {};
  const viaFollowup = [], afterFollowup = [], direct = [];
  function addLink(collection, key, from, to, count, stage, attributes = {}) {
    const width = count * scale;
    const fromOffsets = stage === 2 ? followupOutgoing : sourceOffsets;
    const toOffsets = stage === 1 ? followupIncoming : deliveryOffsets;
    const y1 = from.y + (fromOffsets[from.key] || 0) + width / 2;
    const y2 = to.y + (toOffsets[to.key] || 0) + width / 2;
    fromOffsets[from.key] = (fromOffsets[from.key] || 0) + width;
    toOffsets[to.key] = (toOffsets[to.key] || 0) + width;
    collection.push({ key, from, to, count, width, y1, y2, stage, ...attributes });
  }
  for (const source of SOURCE_ORDER) {
    const count = flows.filter((flow) => flow.source === source && flow.followed_up)
      .reduce((sum, flow) => sum + flow.count, 0);
    if (!count) continue;
    addLink(viaFollowup, `${source}:followup`, byKey[`source:${source}`],
      byKey["followup:true"], count, 1, { source });
  }
  for (const delivered of DELIVERY_ORDER) {
    const count = flows.filter((flow) => flow.followed_up && flow.delivered === delivered)
      .reduce((sum, flow) => sum + flow.count, 0);
    if (!count) continue;
    addLink(afterFollowup, `followup:${delivered}`, byKey["followup:true"],
      byKey[`delivery:${delivered}`], count, 2, { delivered });
  }
  for (const source of SOURCE_ORDER) for (const delivered of DIRECT_DELIVERY_ORDER) {
    const count = flows.filter((flow) => flow.source === source && !flow.followed_up
      && flow.delivered === delivered).reduce((sum, flow) => sum + flow.count, 0);
    if (!count) continue;
    addLink(direct, `${source}:direct:${delivered}`, byKey[`source:${source}`],
      byKey[`delivery:${delivered}`], count, 3, { source, delivered });
  }
  return { nodes, links: [...direct, ...viaFollowup, ...afterFollowup], total };
}
