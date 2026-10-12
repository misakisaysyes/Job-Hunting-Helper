import test from "node:test";
import assert from "node:assert/strict";
import { buildConversionLayout } from "./conversionLayout.js";

test("Sankey routes unasked contacts directly while preserving totals", () => {
  const layout = buildConversionLayout([
    { source: "greeted", followed_up: true, delivered: true, count: 3 },
    { source: "greeted", followed_up: false, delivered: false, count: 5 },
    { source: "hr_initiated", followed_up: false, delivered: true, count: 2 },
  ]);
  assert.equal(layout.total, 10);
  assert.equal(layout.nodes.some((node) => node.key === "followup:false"), false);
  assert.equal(layout.nodes.find((node) => node.key === "followup:true").count, 3);
  assert.equal(layout.links.filter((link) => link.stage === 1)
    .reduce((sum, link) => sum + link.count, 0), 3);
  assert.equal(layout.links.filter((link) => link.stage === 2)
    .reduce((sum, link) => sum + link.count, 0), 3);
  assert.equal(layout.links.filter((link) => link.stage === 3)
    .reduce((sum, link) => sum + link.count, 0), 7);
  assert.equal(layout.links.find((link) => link.key === "hr_initiated:direct:true").count, 2);
  assert.equal(layout.nodes.find((node) => node.key === "delivery:true").count, 5);
});

test("Sankey with no follow-ups connects sources directly to outcomes", () => {
  const layout = buildConversionLayout([
    { source: "hr_initiated", followed_up: false, delivered: true, count: 67 },
    { source: "greeted", followed_up: false, delivered: false, count: 120 },
  ]);
  assert.equal(layout.nodes.some((node) => node.column === 1), false);
  assert.equal(layout.links.length, 2);
  assert.ok(layout.links.every((link) => link.stage === 3));
  assert.equal(layout.links.find((link) => link.source === "hr_initiated").count, 67);
});

test("greeted direct exchange leaves from the bottom of the source node", () => {
  const layout = buildConversionLayout([
    { source: "greeted", followed_up: true, delivered: false, count: 39 },
    { source: "greeted", followed_up: false, delivered: false, count: 138 },
    { source: "greeted", followed_up: false, delivered: true, count: 10 },
    { source: "hr_initiated", followed_up: false, delivered: true, count: 67 },
  ]);
  const source = layout.nodes.find((node) => node.key === "source:greeted");
  const noExchange = layout.links.find((link) => link.key === "greeted:direct:false");
  const exchange = layout.links.find((link) => link.key === "greeted:direct:true");
  assert.ok(exchange.y1 > noExchange.y1);
  assert.ok(Math.abs(exchange.y1 + exchange.width / 2 - source.y - source.height) < 1e-9);
});
