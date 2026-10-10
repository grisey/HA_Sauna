// Independent raw-point oracle for continuous, pixel-reduced user history.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const test = require("node:test");
const source = fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8");
const context = {
  HTMLElement: class {},
  customElements: { get: () => null, define() {} },
};
vm.runInNewContext(
  source +
    "\nglobalThis.Panel = SaunaPanel; globalThis.bounds = { lowerBoundNumber, lowerBoundHistory };",
  context,
);
const { lowerBoundHistory } = context.bounds;
function expectedDisplayValues(position, quantity, start, end, _ttl, pixels) {
  const values = this.series(position, quantity).filter((point) => point.value != null);
  if (!values.length) return [];
  const width = Math.max(1, 2 ** Math.ceil(Math.log2((end - start) / pixels || 1)));
  const first = Math.max(0, lowerBoundHistory(values, start) - 1);
  const after = Math.min(values.length, lowerBoundHistory(values, end + 1) + 1);
  const bins = new Map();
  for (const point of values.slice(first, after)) {
    const key = Math.floor(point.time / width);
    const points = bins.get(key) || [];
    points.push(point);
    bins.set(key, points);
  }
  const output = [];
  for (const points of bins.values()) {
    const minimum = points.reduce((a, b) => (b.value < a.value ? b : a));
    const maximum = points.reduce((a, b) => (b.value > a.value ? b : a));
    const selected = [points[0], minimum, maximum, points.at(-1)].sort(
      (a, b) => a.time - b.time,
    );
    for (const point of selected)
      if (output.at(-1)?.source !== point.source) output.push(point);
  }
  return output;
}
const origin = 1700000000000;
const makeRecord = (id, time, value) => ({
  id,
  kind: "measurement",
  payload: {
    position: "upper",
    quantity: "temperature",
    received_at: new Date(origin + time).toISOString(),
    value,
    raw_value: String(value),
  },
});
const panel = (records) => {
  const p = Object.create(context.Panel.prototype);
  p.historyIndex(records);
  return p;
};
const shape = (points) => JSON.stringify(points);
const compare = (p, start, end, ttl, pixels) => {
  const args = ["upper", "temperature", origin + start, origin + end, ttl, pixels];
  const current = p.historyDisplayValues(...args);
  const expected = expectedDisplayValues.call(p, ...args);
  assert.equal(shape(current), shape(expected));
  return current;
};
test("cached continuous bins match raw original extrema across zoom, pan, nulls and updates", () => {
  let time = 0;
  const records = Array.from({ length: 3000 }, (_, id) => {
    time += id % 23 === 0 ? 12000 : id % 17 === 0 ? 0 : 1000;
    return makeRecord(
      id,
      time,
      id % 31 === 0 ? null : id % 13 === 0 ? 120 : 70 + (id % 7),
    );
  });
  const p = panel(records);
  for (let round = 0; round < 3; round++) {
    for (let i = 0; i < 40; i++) {
      const start = (i * 7357) % 200000;
      compare(
        p,
        start,
        start + 20000 + i * 91011,
        [0, 5000, 20000][i % 3],
        [1, 100, 1070][i % 3],
      );
    }
    const nextTime = round === 1 ? 145000 : time + 1000 + round;
    records.push(makeRecord(records.length, nextTime, 121 + round));
    p.historyIndex(records);
  }
});
test("unchanged interior bins retain point objects across a moving viewport", () => {
  const records = Array.from({ length: 4000 }, (_, id) =>
    makeRecord(id, id * 1000, 70 + (id % 19)),
  );
  const p = panel(records);
  const first = compare(p, -10000, 4000000, 5000, 1070);
  const previous = new Map(first.map((point) => [point.source, point]));
  records.push(makeRecord(4000, 4000000, 84));
  p.historyIndex(records);
  const next = compare(p, -10000, 4002000, 5000, 1070);
  const reused = next.filter((point) => previous.get(point.source) === point).length;
  assert.ok(reused > first.length * 0.95, `${reused} unchanged display points reused`);
  // TTL belongs to hover validity and does not split or expand drawn bins.
  compare(p, -10000, 4002000, 1, 1070);
  compare(p, -10000, 4002000, 5000, 1070);
});
