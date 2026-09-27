// This fixture freezes the former SVG cubic equations as a test oracle.  It
// intentionally has no dependency on a private worktree or retired panel
// helper, so it remains valid after the SVG implementation is removed.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class FakePath2D {
  constructor() {
    this.commands = [];
  }
  moveTo(...values) {
    this.commands.push(["M", ...values]);
  }
  bezierCurveTo(...values) {
    this.commands.push(["C", ...values]);
  }
}

const sandbox = {
  HTMLElement: class {},
  customElements: { get: () => undefined, define() {} },
  Path2D: FakePath2D,
  Date,
  Map,
  Set,
  Math,
  Number,
  String,
  Object,
  Array,
  Infinity,
};
const source = fs.readFileSync("custom_components/ha_sauna/panel.js", "utf8");
vm.runInNewContext(
  `${source}\nglobalThis.curveExports = { monotoneHistoryCommands, historySegments, reduceHistorySegment };`,
  sandbox,
);
const { monotoneHistoryCommands, historySegments, reduceHistorySegment } =
  sandbox.curveExports;

const stamp = (value) =>
  typeof value === "number" ? value : new Date(value).getTime();
const frozenSvgPath = (points, x, y) => {
  if (!points.length) return "";
  const xy = points.map((point) => [
    x(point.time ?? stamp(point.received_at)),
    y(point.value),
  ]);
  if (xy.length === 1) return `M${xy[0][0].toFixed(2)},${xy[0][1].toFixed(2)}`;
  const slopes = xy
    .slice(1)
    .map(
      ([nextX, nextY], index) => (nextY - xy[index][1]) / (nextX - xy[index][0] || 1),
    );
  const tangents = xy.map((_, index) => {
    if (index === 0) return slopes[0];
    if (index === xy.length - 1) return slopes.at(-1);
    const previous = slopes[index - 1],
      next = slopes[index];
    if (previous * next <= 0) return 0;
    const before = xy[index][0] - xy[index - 1][0],
      after = xy[index + 1][0] - xy[index][0],
      w1 = 2 * after + before,
      w2 = after + 2 * before;
    return (w1 + w2) / (w1 / previous + w2 / next);
  });
  let path = `M${xy[0][0].toFixed(2)},${xy[0][1].toFixed(2)}`;
  for (let index = 0; index < xy.length - 1; index++) {
    const [x0, y0] = xy[index],
      next = xy[index + 1],
      dx = (next[0] - x0) / 3;
    path += ` C${(x0 + dx).toFixed(2)},${(y0 + tangents[index] * dx).toFixed(2)} ${(next[0] - dx).toFixed(2)},${(next[1] - tangents[index + 1] * dx).toFixed(2)} ${next[0].toFixed(2)},${next[1].toFixed(2)}`;
  }
  return path;
};
const svgControls = (path) =>
  [...path.matchAll(/([MC])([^MC]+)/g)].map(([, command, body]) => [
    command,
    ...body.trim().split(/[ ,]+/).map(Number),
  ]);
const compare = (points, x, y) => {
  const expected = JSON.stringify(svgControls(frozenSvgPath(points, x, y)));
  assert.equal(JSON.stringify(monotoneHistoryCommands(points, x, y)), expected);
  const target = new FakePath2D();
  monotoneHistoryCommands(points, x, y, target);
  assert.equal(JSON.stringify(target.commands), expected);
};
const point = (time, value, displayGap) => ({
  time,
  value,
  ...(displayGap === undefined ? {} : { displayGap }),
});
const x = (time) => 65 + time * 10;
const y = (value) => 435 - value * 3;

compare([point(0, 70), point(1, 92), point(2, 72)], x, y);
compare([point(0, 70), point(0, 71), point(1, 72)], x, y);
compare([point(-1, 69), point(0, 70), point(1, 71)], x, y);
compare([point(0, 70), point(1, 90), point(1.5, 75), point(2, 72)], x, y);
compare([], x, y);
compare([point(0, 0)], x, y);

for (const values of [
  [point(0, 70), point(1, null), point(2, 72)],
  [point(0, 70), point(1, 71), point(5, 72, true)],
]) {
  const actual = historySegments(values, 0, 5, 3).flatMap((segment) =>
    monotoneHistoryCommands(segment, x, y),
  );
  const expected = historySegments(values, 0, 5, 3).flatMap((segment) =>
    svgControls(frozenSvgPath(segment, x, y)),
  );
  assert.equal(JSON.stringify(actual), JSON.stringify(expected));
  const target = new FakePath2D();
  for (const segment of historySegments(values, 0, 5, 3))
    monotoneHistoryCommands(segment, x, y, target);
  assert.equal(JSON.stringify(target.commands), JSON.stringify(expected));
}

console.log("history curve numeric controls passed");

const frozenReduction = (segment, x) => {
  const output = [];
  let bucket = [],
    key = null;
  const flush = () => {
    if (!bucket.length) return;
    const keep = new Set([
      bucket[0],
      bucket.at(-1),
      bucket.reduce((a, b) => (a.value < b.value ? a : b)),
      bucket.reduce((a, b) => (a.value > b.value ? a : b)),
    ]);
    output.push(...bucket.filter((point) => keep.has(point)));
    bucket = [];
  };
  for (const point of segment) {
    const next = Math.floor(x(point.time ?? stamp(point.received_at)));
    if (key !== null && next !== key) flush();
    key = next;
    bucket.push(point);
  }
  flush();
  return output;
};

// Exercise changing pixel buckets and equal-value tie order; compare the
// actual selected identities before comparing the original cubic controls.
let seed = 0x7a1d9e3;
const random = () => {
  seed = (seed * 1664525 + 1013904223) >>> 0;
  return seed / 0x100000000;
};
for (let sample = 0; sample < 240; sample++) {
  let time = 0;
  const points = Array.from({ length: sample % 41 }, (_, index) => {
    if (index && random() > 0.27) time += Math.floor(random() * 4);
    return { time, value: index % 11 === 0 ? 1000000 : Math.floor(random() * 9) - 4 };
  });
  const mapX = (v) => (v * ((sample % 9) - 4)) / 7 + (sample % 5) / 10;
  const mapY = (v) => v * ((sample % 7) - 3.5) + 0.005;
  const expected = frozenReduction(points, mapX);
  const actual = reduceHistorySegment(points, mapX);
  assert.equal(JSON.stringify(actual), JSON.stringify(expected));
  assert.ok(actual.every((p, i) => p === expected[i]));
  compare(actual, mapX, mapY);
}
const repeatedMax = { time: 0, value: 8 },
  repeatedMin = { time: 0, value: -8 };
const repeated = [repeatedMax, repeatedMin, repeatedMax, repeatedMin];
assert.equal(
  JSON.stringify(reduceHistorySegment(repeated, () => 0)),
  JSON.stringify(frozenReduction(repeated, () => 0)),
);
