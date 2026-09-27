// Frozen 7de9317 display selection: cache reuse must preserve exact points and gaps.
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
const { lowerBoundNumber, lowerBoundHistory } = context.bounds;
function frozenDisplayValues(position, quantity, start, end, ttl, pixels) {
  const values = this.series(position, quantity);
  if (!values.length) return values;
  // A dyadic bucket is at most two display pixels wide.  Its first, last
  // and extrema remain visible; a null or timeout descends to raw points.
  const width = Math.max(1, 2 ** Math.ceil(Math.log2((end - start) / pixels || 1)));
  const display = this.historyDisplay(values),
    level = this.historyDisplayLevel(display, width),
    first = Math.max(0, lowerBoundNumber(level.keys, Math.floor(start / width)) - 1),
    after = Math.min(
      level.keys.length,
      lowerBoundNumber(level.keys, Math.floor(end / width) + 1) + 1,
    ),
    output = [];
  let previous = null;
  for (let offset = first; offset < after; offset++) {
    const node = level.nodes.get(level.keys[offset]);
    if (!node) continue;
    const displayGap = !!previous && !!ttl && node.first.time - previous.time > ttl;
    const edge = node.first.time < start || node.last.time > end;
    if (edge || node.missing || (ttl && node.maximumGap > ttl)) {
      const firstRaw = edge
          ? Math.max(node.firstIndex, lowerBoundHistory(values, start) - 1)
          : node.firstIndex,
        afterRaw = edge
          ? Math.min(node.lastIndex + 1, lowerBoundHistory(values, end + 1) + 1)
          : node.lastIndex + 1;
      for (let index = firstRaw; index < afterRaw; index++) {
        const point = values[index];
        output.push({
          time: point.time,
          value: point.value,
          source: point.source,
          displayGap:
            index === firstRaw
              ? displayGap
              : !!ttl && point.time - values[index - 1].time > ttl,
        });
      }
      previous = node.last;
      continue;
    }
    const points = [node.first, node.minimum, node.maximum, node.last]
      .filter(Boolean)
      .sort((a, b) => a.time - b.time);
    for (const point of points)
      if (output.at(-1)?.source !== point.source)
        output.push({
          time: point.time,
          value: point.value,
          source: point.source,
          displayGap: point === points[0] && displayGap,
        });
    previous = node.last;
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
  const legacy = frozenDisplayValues.call(p, ...args);
  assert.equal(shape(current), shape(legacy));
  return current;
};
test("cached bucket selection matches original across zoom, pan, gaps, duplicates and updates", () => {
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
  // Changing TTL may switch to raw fallback; it must never retain an old gap flag.
  compare(p, -10000, 4002000, 1, 1070);
  compare(p, -10000, 4002000, 5000, 1070);
});
